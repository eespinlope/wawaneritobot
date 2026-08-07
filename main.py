import os
import json
import random
import io
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
    return "NeroBot Multi-Agente activo", 200

def run_flask():
    port = int(os.environ.get("PORT", 10000))
    app_flask.run(host="0.0.0.0", port=port)

# -------------------------------------------------------------
# CONEXIÓN CON GROQ Y GOOGLE SHEETS
# -------------------------------------------------------------
client_groq = Groq(api_key=os.environ.get("GROQ_API_KEY"))

def obtener_libro_sheets():
    try:
        creds_raw = os.environ.get("GOOGLE_CREDENTIALS")
        if not creds_raw:
            return None
        creds_dict = json.loads(creds_raw)
        scopes = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
        credentials = Credentials.from_service_account_info(creds_dict, scopes=scopes)
        gc = gspread.authorize(credentials)
        # Abre el libro general centralizado
        return gc.open("dbBrain_NeroBot")
    except Exception as e:
        print(f"Error Sheets: {e}")
        return None

# -------------------------------------------------------------
# SUBAGENTE 1: FINANZAS
# -------------------------------------------------------------
def subagente_finanzas(peticion):
    libro = obtener_libro_sheets()
    if not libro:
        return "⚠️ No se pudo conectar con la base de datos `dbBrain_NeroBot`."
    
    try:
        sheet = libro.worksheet("subagente_finanzas")
    except Exception as e:
        return f"⚠️ No se encontró la pestaña 'subagente_finanzas': {e}"

    registros = sheet.get_all_values()
    filas_datos = registros[1:] if len(registros) > 1 else []

    # Optimización de tokens
    filas_pendientes = [f for f in filas_datos if len(f) >= 9 and f[8].strip().lower() == "pendiente"]
    filas_recientes = filas_datos[-15:]
    filas_contexto = list({tuple(f): f for f in (filas_pendientes + filas_recientes)}.values())

    fecha_hoy = datetime.now().strftime("%Y-%m-%d")

    prompt = f"""
    Hoy es {fecha_hoy}. Usuario dice: "{peticion}"
    DB Finanzas (ID, Fecha, Tipo, Monto, Categoria, Desc, F.Compromiso, F.Pago, Estado): {filas_contexto}

    REGLAS DE INTERPRETACIÓN:
    - Si el usuario dice "le presté a [Nombre]" o "prestamo a [Nombre]", la descripción DEBE ser "Préstamo a [Nombre]".
    - Si el usuario dice "me prestaron" o "prestamo de [Nombre]", la descripción DEBE ser "Préstamo de [Nombre]".
    - Ignora errores tipográficos (ej. "Tegistra" = "Registra").

    Responde ÚNICAMENTE en JSON plano:
    {{
      "transacciones": [
        {{
          "accion": "registrar",
          "tipo": "Ingreso / Egreso / Préstamo",
          "monto": 0.00,
          "categoria": "Categoría",
          "descripcion": "detalle",
          "fecha_compromiso": "YYYY-MM-DD o N/A",
          "fecha_pago": "YYYY-MM-DD o N/A",
          "estado": "Pendiente / Pagado"
        }},
        {{
          "accion": "editar",
          "id_transaccion": "TX-XXX",
          "campo_a_cambiar": "estado / monto / descripcion",
          "nuevo_valor": "valor"
        }},
        {{
          "accion": "consultar"
        }}
      ]
    }}
    """

    try:
        chat = client_groq.chat.completions.create(
            messages=[{"role": "user", "content": prompt}],
            model="llama-3.1-8b-instant",
            response_format={"type": "json_object"}
        )
        datos = json.loads(chat.choices[0].message.content)
        items = datos.get("transacciones", []) if isinstance(datos, dict) else datos
        respuestas = []

        for item in items:
            accion = item.get("accion")

            if accion == "consultar":
                prompt_c = f"Usuario: '{peticion}'. Responde brevemente en Markdown con estos datos: {filas_contexto}"
                res_c = client_groq.chat.completions.create(
                    messages=[{"role": "user", "content": prompt_c}],
                    model="llama-3.1-8b-instant"
                )
                return res_c.choices[0].message.content

            elif accion == "editar":
                id_b = str(item.get("id_transaccion", "")).upper()
                fila_idx = next((i for i, f in enumerate(registros, start=1) if len(f) > 0 and f[0].upper() == id_b), None)
                if fila_idx:
                    mapa = {"monto": 4, "categoria": 5, "descripcion": 6, "fecha_compromiso": 7, "fecha_pago": 8, "estado": 9}
                    campo = item.get("campo_a_cambiar", "estado")
                    nuevo_val = item.get("nuevo_valor", "Pagado")
                    if campo == "estado" and "pagado" in str(nuevo_val).lower():
                        sheet.update_cell(fila_idx, 8, fecha_hoy)
                    sheet.update_cell(fila_idx, mapa.get(campo, 9), nuevo_val)
                    respuestas.append(f"✏️ **`{id_b}` actualizado:** `{campo}` -> **{nuevo_val}**")

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
                respuestas.append(f"✅ **{tipo} (`{tx_id}`):** S/ {monto} | {desc} | Estado: {estado}")

        return "\n".join(respuestas) if respuestas else "👍 Operación financiera procesada."
    except Exception as e:
        return f"❌ Error Finanzas: {e}"

# -------------------------------------------------------------
# SUBAGENTE 2: NOTAS E IDEAS
# -------------------------------------------------------------
def subagente_notas(peticion):
    libro = obtener_libro_sheets()
    if not libro:
        return "⚠️ No se pudo conectar con la base de datos `dbBrain_NeroBot`."

    try:
        sheet_notas = libro.worksheet("subagente_notas")
    except:
        sheet_notas = libro.add_worksheet(title="subagente_notas", rows="100", cols="4")
        sheet_notas.append_row(["ID", "Fecha", "Categoría", "Nota"])

    registros = sheet_notas.get_all_values()
    filas = registros[1:] if len(registros) > 1 else []
    fecha_hoy = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    prompt = f"""
    Usuario dice: "{peticion}"
    Notas guardadas previamente: {filas[-10:]}

    Responde en JSON con la acción:
    - Si quiere guardar una nota: {{"accion": "guardar", "categoria": "Idea / Tarea / Lista / Recordatorio", "contenido": "texto de la nota"}}
    - Si quiere consultar notas: {{"accion": "consultar"}}
    """

    try:
        chat = client_groq.chat.completions.create(
            messages=[{"role": "user", "content": prompt}],
            model="llama-3.1-8b-instant",
            response_format={"type": "json_object"}
        )
        datos = json.loads(chat.choices[0].message.content)
        accion = datos.get("accion")

        if accion == "consultar":
            prompt_c = f"Usuario: '{peticion}'. Resume estas notas guardadas en Markdown: {filas}"
            res_c = client_groq.chat.completions.create(
                messages=[{"role": "user", "content": prompt_c}],
                model="llama-3.1-8b-instant"
            )
            return res_c.choices[0].message.content

        elif accion == "guardar":
            note_id = f"NT-{random.randint(100, 999)}"
            cat = datos.get("categoria", "General")
            cont = datos.get("contenido", peticion)
            sheet_notas.append_row([note_id, fecha_hoy, cat, cont])
            return f"📌 **Nota guardada (`{note_id}`):** [{cat}] {cont}"

    except Exception as e:
        return f"❌ Error Notas: {e}"

# -------------------------------------------------------------
# ROUTER ORQUESTADOR (DIRECTOR CENTRAL)
# -------------------------------------------------------------
def router_orquestador(peticion):
    prompt_router = f"""
    Clasifica el mensaje del usuario en UNA de las siguientes tres categorías:

    REGLA STRICTA: Si el mensaje contiene palabras como "prestar", "presté", "gasté", "pagué", "compré", "debo", "soles", "$" o cualquier monto numérico asociado a una transacción, DEBES clasificarlo obligatoriamente como FINANZAS, ignorando saludos iniciales como "Hola" o "NeroBot".

    - FINANZAS: Si habla de dinero, deudas, préstamos, compras, gastos, ingresos, precios, pagos o cuentas.
    - NOTAS: Si solicita guardar ideas, listas de pendientes, contraseñas, apuntes o textos que no involucran dinero.
    - GENERAL: ÚNICAMENTE para saludos simples ("hola", "buenos días") o conversación casual sin datos para registrar.

    Mensaje del usuario: "{peticion}"

    Responde ESTRICTAMENTE con un JSON: {{"categoria": "FINANZAS / NOTAS / GENERAL"}}
    """

    try:
        chat = client_groq.chat.completions.create(
            messages=[{"role": "user", "content": prompt_router}],
            model="llama-3.1-8b-instant",
            response_format={"type": "json_object"}
        )
        res = json.loads(chat.choices[0].message.content)
        cat = res.get("categoria", "GENERAL").upper()

        if "FINANZA" in cat:
            return subagente_finanzas(peticion)
        elif "NOTA" in cat:
            return subagente_notas(peticion)
        else:
            prompt_gen = f"Eres NeroBot, un asistente personal útil y directo. Responde brevemente al usuario: '{peticion}'"
            res_gen = client_groq.chat.completions.create(
                messages=[{"role": "user", "content": prompt_gen}],
                model="llama-3.1-8b-instant"
            )
            return res_gen.choices[0].message.content

    except Exception as e:
        return f"❌ Error Router: {e}"

# -------------------------------------------------------------
# TELEGRAM BOT HANDLERS
# -------------------------------------------------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("👋 ¡NeroBot activo con dbBrain centralizado! ¿En qué te ayudo hoy?")

async def responder(update: Update, context: ContextTypes.DEFAULT_TYPE):
    respuesta = router_orquestador(update.message.text)
    await update.message.reply_text(respuesta, parse_mode="Markdown")

async def procesar_audio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msj_espera = await update.message.reply_text("🎙️ *Procesando nota de voz...*", parse_mode="Markdown")
    try:
        file_id = update.message.voice.file_id
        nuevo_archivo = await context.bot.get_file(file_id)
        
        # Guardar archivo en memoria RAM usando BytesIO
        audio_bytes = io.BytesIO()
        await nuevo_archivo.download_to_memory(out=audio_bytes)
        audio_bytes.seek(0)
        audio_bytes.name = "audio.ogg"

        # Transcripción directa usando Whisper en Groq
        transcripcion = client_groq.audio.transcriptions.create(
            file=(audio_bytes.name, audio_bytes.read()),
            model="whisper-large-v3-turbo",
            prompt="Transcripción de nota de voz sobre finanzas, notas personales, gastos o préstamos en soles (S/).",
            response_format="text",
            language="es"
        )

        texto_transcrito = str(transcripcion).strip()

        # Envío del texto transcrito al router orquestador
        respuesta = router_orquestador(texto_transcrito)
        
        await msj_espera.edit_text(
            f"🗣️ *Escuché:* \"_{texto_transcrito}_\"\n\n{respuesta}", 
            parse_mode="Markdown"
        )
    except Exception as e:
        await msj_espera.edit_text(f"❌ Error al procesar el audio: {e}")

if __name__ == '__main__':
    Thread(target=run_flask, daemon=True).start()
    bot_app = Application.builder().token(os.environ.get("TELEGRAM_TOKEN")).build()
    bot_app.add_handler(CommandHandler("start", start))
    bot_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, responder))
    bot_app.add_handler(MessageHandler(filters.VOICE, procesar_audio))
    bot_app.run_polling()
