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
        return "⚠️ No se pudo conectar con Google Sheets. Verifica los permisos o credenciales."

    # Prompt para calificar la intención
    prompt_intencion = f"""
    Determina si la petición del usuario es para REGISTRAR un nuevo gasto o para CONSULTAR información guardada.
    Petición: "{peticion}"

    Responde ÚNICAMENTE con un JSON plano sin bloques markdown:
    Si es REGISTRO:
    {{"accion": "registrar", "monto": 0.00, "categoria": "Categoría", "descripcion": "detalle"}}
    
    Si es CONSULTA (preguntas por último gasto, total gastado, resumen, etc.):
    {{"accion": "consultar"}}
    """

    try:
        response = client.models.generate_content(
            model='gemini-flash-latest',
            contents=prompt_intencion,
        )
        texto_limpio = response.text.replace("```json", "").replace("```", "").strip()
        datos = json.loads(texto_limpio)

        # DIRECCIÓN 1: LECTURA (Consultar la hoja de Google Sheets)
        if datos.get("accion") == "consultar":
            registros = sheet.get_all_values()
            
            # Si solo están los encabezados o la hoja está vacía
            if len(registros) <= 1:
                return "ℹ️ Aún no tienes ningún gasto registrado en tu hoja de cálculo."
            
            headers = registros[0]
            filas_datos = registros[1:]

            # Le pasamos todo el contexto de la hoja a Gemini para que responda la pregunta exacta
            prompt_respuesta = f"""
            Eres un asistente financiero. El usuario te hace la siguiente consulta: "{peticion}"

            Aquí están los datos de sus gastos guardados en la hoja de cálculo (Encabezados: {headers}):
            {filas_datos}

            Responde de forma clara, directa y formal usando formato Markdown para resaltar datos clave.
            """
            
            resp_gemini = client.models.generate_content(
                model='gemini-flash-latest',
                contents=prompt_respuesta,
            )
            return resp_gemini.text

        # DIRECCIÓN 2: ESCRITURA (Guardar en Google Sheets)
        else:
            monto = datos.get("monto", 0.0)
            categoria = datos.get("categoria", "Otros")
            descripcion = datos.get("descripcion", peticion)
            fecha_actual = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            sheet.append_row([fecha_actual, monto, categoria, descripcion])
            return f"✅ **Gasto guardado en Google Sheets**\n📅 Fecha: {fecha_actual}\n💰 Monto: ${monto}\n🏷️ Categoría: {categoria}\n📝 Descripción: {descripcion}"

    except Exception as e:
        return f"❌ Ocurrió un inconveniente al procesar la solicitud: {e}"

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
