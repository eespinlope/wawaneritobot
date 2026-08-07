import os
import json
from datetime import datetime
from threading import Thread
from flask import Flask
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from google import genai
import gspread
from google.oauth2.service_account import Credentials

# -------------------------------------------------------------
# SERVIDOR WEB MÍNIMO PARA RENDER
# -------------------------------------------------------------
app_flask = Flask(__name__)

@app_flask.route('/')
def home():
    return "Bot en ejecución y saludable", 200

def run_flask():
    port = int(os.environ.get("PORT", 10000))
    app_flask.run(host="0.0.0.0", port=port)

# -------------------------------------------------------------
# CLIENTE GEMINI
# -------------------------------------------------------------
client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY"))

# -------------------------------------------------------------
# AUTENTICACIÓN GOOGLE SHEETS
# -------------------------------------------------------------
def obtener_hoja_sheets():
    try:
        creds_raw = os.environ.get("GOOGLE_CREDENTIALS")
        if not creds_raw:
            print("Error: No se encontró GOOGLE_CREDENTIALS")
            return None
        
        creds_dict = json.loads(creds_raw)
        scopes = [
            "https://www.googleapis.com/auth/spreadsheets",
            "https://www.googleapis.com/auth/drive"
        ]
        credentials = Credentials.from_service_account_info(creds_dict, scopes=scopes)
        gc = gspread.authorize(credentials)
        
        sheet = gc.open("Control de Gastos").sheet1
        return sheet
    except Exception as e:
        print(f"Error al conectar con Google Sheets: {e}")
        return None

# -------------------------------------------------------------
# SUBAGENTE DE FINANZAS (BIDIRECCIONAL)
# -------------------------------------------------------------
def subagente_finanzas(peticion):
    sheet = obtener_hoja_sheets()
    if not sheet:
        return "⚠️ No se pudo conectar con Google Sheets."

    registros = sheet.get_all_values()
    headers = registros[0] if len(registros) > 0 else []
    filas_datos = registros[1:] if len(registros) > 1 else []

    fecha_hoy = datetime.now().strftime("%Y-%m-%d")

    prompt_orquestador = f"""
    Hoy es {fecha_hoy}.
    El usuario dice: "{peticion}"

    Base de datos actual (9 columnas: ID, Fecha Registro, Tipo, Monto, Categoria, Descripción, Fecha Compromiso, Fecha Pago, Estado):
    {filas_datos}

    Analiza el mensaje. El usuario puede mencionar UNA O MÁS transacciones en el mismo texto.
    Responde ÚNICAMENTE con un arreglo JSON plano de objetos `[ {{...}}, {{...}} ]` sin bloques de código markdown:

    Estructura de cada objeto dentro del arreglo:
    - Si es REGISTRAR (crear nuevo gasto, ingreso o préstamo):
      {{"accion": "registrar", "tipo": "Ingreso / Egreso / Préstamo", "monto": 0.00, "categoria": "Categoría", "descripcion": "detalle", "fecha_compromiso": "YYYY-MM-DD o N/A", "fecha_pago": "YYYY-MM-DD o N/A", "estado": "Pendiente / Pagado"}}

    - Si es EDITAR / MARCAR PAGADO:
      {{"accion": "editar", "id_transaccion": "ID_ENCONTRADO", "campo_a_cambiar": "estado / fecha_compromiso / fecha_pago / monto / descripcion", "nuevo_valor": "valor"}}

    - Si es CONSULTAR:
      [{{"accion": "consultar"}}]
    """

    try:
        response = client.models.generate_content(
            model='gemini-flash-latest',
            contents=prompt_orquestador,
        )
        texto_limpio = response.text.replace("```json", "").replace("```", "").strip()
        
        datos = json.loads(texto_limpio)
        # Si Gemini devuelve un solo dict en vez de una lista, lo convertimos a lista
        if isinstance(datos, dict):
            datos = [datos]

        respuestas_log = []

        import random

        for item in datos:
            accion = item.get("accion")

            if accion == "consultar":
                prompt_resp = f"El usuario pregunta: '{peticion}'. Responde analizando estas filas: {filas_datos}"
                resp = client.models.generate_content(model='gemini-flash-latest', contents=prompt_resp)
                return resp.text

            elif accion == "editar":
                id_buscar = str(item.get("id_transaccion", "")).upper()
                fila_index = next((idx for idx, fila in enumerate(registros, start=1) if len(fila) > 0 and fila[0].upper() == id_buscar), None)

                if fila_index:
                    mapa = {"monto": 4, "categoria": 5, "descripcion": 6, "fecha_compromiso": 7, "fecha_pago": 8, "estado": 9}
                    campo = item.get("campo_a_cambiar", "estado")
                    nuevo_val = item.get("nuevo_valor", "Pagado")
                    
                    if campo == "estado" and "pagado" in str(nuevo_val).lower():
                        sheet.update_cell(fila_index, 8, fecha_hoy)
                    
                    sheet.update_cell(fila_index, mapa.get(campo, 9), nuevo_val)
                    respuestas_log.append(f"✏️ **`{id_buscar}` actualizado:** `{campo}` -> **{nuevo_val}**")

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

                sheet.append_row([tx_id, f_reg, tipo, monto, cat, desc, f_comp, f_pago, estado])
                respuestas_log.append(f"✅ **{tipo} (`{tx_id}`):** ${monto} | {desc} | Estado: {estado}")

        return "\n".join(respuestas_log) if respuestas_log else "👍 Procesado con éxito."

    except Exception as e:
        return f"❌ Error al procesar la solicitud: {e}"

# -------------------------------------------------------------
# SUBAGENTES SECUNDARIOS
# -------------------------------------------------------------
def subagente_agenda(peticion):
    return f"📅 [Subagente Agenda]: Tarea recibida -> '{peticion}'"

def subagente_publicador(peticion):
    return f"📢 [Subagente Publicador]: Solicitud recibida -> '{peticion}'"

# -------------------------------------------------------------
# ROUTER / ORQUESTADOR
# -------------------------------------------------------------
def orquestador_router(mensaje_usuario):
    prompt_router = f"""
    Clasifica el siguiente mensaje en una de estas palabras exactas:
    - FINANZAS (si habla de dinero, gastos, compras, pagos, histórico de gastos, consultas de dinero)
    - AGENDA (si habla de tareas, recordatorios, fechas, citas)
    - PUBLICAR (si habla de redacción, posts, redes sociales)
    - GENERAL (para conversación casual o preguntas generales)

    Mensaje: "{mensaje_usuario}"
    Responde ÚNICAMENTE con una sola palabra clave.
    """
    
    response = client.models.generate_content(
        model='gemini-flash-latest',
        contents=prompt_router,
    )
    
    categoria = response.text.strip().upper()
    
    if "FINANZAS" in categoria:
        return subagente_finanzas(mensaje_usuario)
    elif "AGENDA" in categoria:
        return subagente_agenda(mensaje_usuario)
    elif "PUBLICAR" in categoria:
        return subagente_publicador(mensaje_usuario)
    else:
        resp_general = client.models.generate_content(
            model='gemini-flash-latest',
            contents=mensaje_usuario,
        )
        return resp_general.text

# -------------------------------------------------------------
# MANEJADORES DE TELEGRAM
# -------------------------------------------------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("👋 ¡Hola! Puedo registrar tus gastos o responder consultas sobre tu historial en Google Sheets.")

async def responder(update: Update, context: ContextTypes.DEFAULT_TYPE):
    texto_usuario = update.message.text
    respuesta = orquestador_router(texto_usuario)
    await update.message.reply_text(respuesta, parse_mode="Markdown")

# -------------------------------------------------------------
# EJECUCIÓN PRINCIPAL
# -------------------------------------------------------------
if __name__ == '__main__':
    Thread(target=run_flask, daemon=True).start()
    
    TOKEN = os.environ.get("TELEGRAM_TOKEN")
    bot_app = Application.builder().token(TOKEN).build()
    
    bot_app.add_handler(CommandHandler("start", start))
    bot_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, responder))
    
    print("Bot y Servidor Web activos...")
    bot_app.run_polling()
