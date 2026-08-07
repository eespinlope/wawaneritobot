import os
import json
import random
from datetime import datetime
from threading import Thread
from flask import Flask
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
import gspread
from google.oauth2.service_account import Credentials
from groq import Groq

# -------------------------------------------------------------
# SERVIDOR FLASK (Keep-alive para Render)
# -------------------------------------------------------------
app_flask = Flask(__name__)

@app_flask.route('/')
def home():
    return "Bot en ejecución con Groq", 200

def run_flask():
    port = int(os.environ.get("PORT", 10000))
    app_flask.run(host="0.0.0.0", port=port)

# -------------------------------------------------------------
# CLIENTE GROQ & GOOGLE SHEETS
# -------------------------------------------------------------
client_groq = Groq(api_key=os.environ.get("GROQ_API_KEY"))

def obtener_hoja_sheets():
    try:
        creds_raw = os.environ.get("GOOGLE_CREDENTIALS")
        if not creds_raw:
            return None
        creds_dict = json.loads(creds_raw)
        scopes = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
        credentials = Credentials.from_service_account_info(creds_dict, scopes=scopes)
        gc = gspread.authorize(credentials)
        return gc.open("Control de Gastos").sheet1
    except Exception as e:
        print(f"Error Sheets: {e}")
        return None

# -------------------------------------------------------------
# SUBAGENTE FINANZAS (GROQ LLAMA-3.1-8B)
# -------------------------------------------------------------
def subagente_finanzas(peticion):
    sheet = obtener_hoja_sheets()
    if not sheet:
        return "⚠️ No se pudo conectar con Google Sheets."

    registros = sheet.get_all_values()
    headers = registros[0] if len(registros) > 0 else []
    filas_datos = registros[1:] if len(registros) > 1 else []

    # OPTIMIZACIÓN DE TOKENS: Solo enviamos pendientes + últimas 15 filas
    filas_pendientes = [f for f in filas_datos if len(f) >= 9 and f[8].strip().lower() == "pendiente"]
    filas_recientes = filas_datos[-15:]
    
    # Combinar sin duplicados para reducir consumo
    filas_contexto = list({tuple(f): f for f in (filas_pendientes + filas_recientes)}.values())

    fecha_hoy = datetime.now().strftime("%Y-%m-%d")

    prompt = f"""
    Hoy es {fecha_hoy}.
    Mensaje del usuario: "{peticion}"

    Base de datos actual (9 columnas: ID, Fecha Registro, Tipo, Monto, Categoria, Descripción, Fecha Compromiso, Fecha Pago, Estado):
    {filas_contexto}

    Analiza la intención del usuario. Puede haber una o más transacciones en el mismo texto.
    Responde ÚNICAMENTE con un objeto JSON en este formato exacto:
    {{
      "transacciones": [
        {{
          "accion": "registrar",
          "tipo": "Ingreso / Egreso / Préstamo",
          "monto": 0.00,
          "categoria": "Comida / Transporte / Servicios / Préstamo / Otros",
          "descripcion": "detalle del gasto o persona",
          "fecha_compromiso": "YYYY-MM-DD o N/A",
          "fecha_pago": "YYYY-MM-DD o N/A",
          "estado": "Pendiente / Pagado"
        }},
        {{
          "accion": "editar",
          "id_transaccion": "TX-XXX",
          "campo_a_cambiar": "estado / fecha_compromiso / fecha_pago / monto / descripcion",
          "nuevo_valor": "valor actualizado"
        }},
        {{
          "accion": "consultar"
        }}
      ]
    }}
    """

    try:
        chat_completion = client_groq.chat.completions.create(
            messages=[{"role": "user", "content": prompt}],
            model="llama-3.1-8b-instant",
            response_format={"type": "json_object"}
        )
        
        res_text = chat_completion.choices[0].message.content.strip()
        datos_json = json.loads(res_text)
        
        # Extraer la lista de transacciones
        items = datos_json.get("transacciones", []) if isinstance(datos_json, dict) else datos_json

        respuestas_log = []

        for item in items:
            accion = item.get("accion")

            if accion == "consultar":
                prompt_cons = f"El usuario pregunta: '{peticion}'. Responde brevemente en Markdown usando estos datos: {filas_contexto}"
                res_cons = client_groq.chat.completions.create(
                    messages=[{"role": "user", "content": prompt_cons}],
                    model="llama-3.1-8b-instant"
                )
                return res_cons.choices[0].message.content

            elif accion == "editar":
                id_buscar = str(item.get("id_transaccion", "")).upper()
                fila_idx = next((i for i, f in enumerate(registros, start=1) if len(f) > 0 and f[0].upper() == id_buscar), None)

                if fila_idx:
                    mapa = {"monto": 4, "categoria": 5, "descripcion": 6, "fecha_compromiso": 7, "fecha_pago": 8, "estado": 9}
                    campo = item.get("campo_a_cambiar", "estado")
                    nuevo_val = item.get("nuevo_valor", "Pagado")

                    if campo == "estado" and "pagado" in str(nuevo_val).lower():
                        sheet.update_cell(fila_idx, 8, fecha_hoy) # Fecha de pago a hoy

                    sheet.update_cell(fila_idx, mapa.get(campo, 9), nuevo_val)
                    respuestas_log.append(f"✏️ **`{id_buscar}` actualizado:** `{campo}` -> **{nuevo_val}**")
                else:
                    respuestas_log.append(f"🔍 No encontré la transacción `{id_buscar}` para editar.")

            elif accion == "registrar":
                tx_id = f"TX-{random.randint(100, 999)}"
                tipo = item.get("tipo", "Egreso")
                monto = item.get("monto", 0.0)
                cat = item.get("categoria", "Otros")
                desc = item.get("descripcion", peticion)
                f_comp = item.get("fecha_compromiso") or "N/A"
                estado = item.get("estado", "Pagado")
                f_pago = item.get("fecha_pago") or (fecha_hoy if estado == "Pagado" else "N/A")
                f_reg = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

                # Insertar en Google Sheets (9 columnas)
                sheet.append_row([tx_id, f_reg, tipo, monto, cat, desc, f_comp, f_pago, estado])
                respuestas_log.append(f"✅ **{tipo} (`{tx_id}`):** ${monto} | {desc} | Estado: {estado}")

        return "\n".join(respuestas_log) if respuestas_log else "👍 Procesado con éxito."

    except Exception as e:
        return f"❌ Error al procesar: {e}"

# -------------------------------------------------------------
# TELEGRAM BOT HANDLERS
# -------------------------------------------------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("👋 ¡Bot financiero activo y funcionando con Groq!")

async def responder(update: Update, context: ContextTypes.DEFAULT_TYPE):
    respuesta = subagente_finanzas(update.message.text)
    await update.message.reply_text(respuesta, parse_mode="Markdown")

if __name__ == '__main__':
    Thread(target=run_flask, daemon=True).start()
    bot_app = Application.builder().token(os.environ.get("TELEGRAM_TOKEN")).build()
    bot_app.add_handler(CommandHandler("start", start))
    bot_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, responder))
    bot_app.run_polling()
