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
            print("Error: No se encontró la variable GOOGLE_CREDENTIALS")
            return None
        
        creds_dict = json.loads(creds_raw)
        scopes = [
            "https://www.googleapis.com/auth/spreadsheets",
            "https://www.googleapis.com/auth/drive"
        ]
        credentials = Credentials.from_service_account_info(creds_dict, scopes=scopes)
        gc = gspread.authorize(credentials)
        
        # Debe coincidir exactamente con el nombre de tu hoja en Google Drive
        sheet = gc.open("Control de Gastos").sheet1
        return sheet
    except Exception as e:
        print(f"Error al conectar con Google Sheets: {e}")
        return None

# -------------------------------------------------------------
# SUBAGENTE DE FINANZAS (CONECTOR GOOGLE SHEETS)
# -------------------------------------------------------------
def subagente_finanzas(peticion):
    prompt_finanzas = f"""
    Extrae la información financiera del siguiente texto.
    Texto: "{peticion}"

    Responde ÚNICAMENTE con un JSON válido que tenga este formato exacto:
    {{
        "monto": 0.00,
        "categoria": "Comida / Transporte / Servicios / Entretenimiento / Otros",
        "descripcion": "descripción corta del gasto"
    }}
    Sin bloques de código markdown extra como ```json, solo el objeto JSON plano.
    """
    
    try:
        response = client.models.generate_content(
            model='gemini-flash-latest',
            contents=prompt_finanzas,
        )
        
        # Limpiar posible formato markdown en la respuesta
        texto_limpio = response.text.replace("```json", "").replace("```", "").strip()
        datos_gasto = json.loads(texto_limpio)
        
        monto = datos_gasto.get("monto", 0.0)
        categoria = datos_gasto.get("categoria", "Otros")
        descripcion = datos_gasto.get("descripcion", peticion)
        fecha_actual = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        sheet = obtener_hoja_sheets()
        if sheet:
            sheet.append_row([fecha_actual, monto, categoria, descripcion])
            return f"✅ **Gasto guardado en Google Sheets**\n📅 Fecha: {fecha_actual}\n💰 Monto: ${monto}\n🏷️ Categoría: {categoria}\n📝 Descripción: {descripcion}"
        else:
            return "⚠️ El gasto se procesó, pero no se pudo conectar con Google Sheets. Revisa la variable GOOGLE_CREDENTIALS en Render."
            
    except Exception as e:
        return f"❌ Error al procesar el gasto: {e}"

# -------------------------------------------------------------
# SUBAGENTES RESTANTES
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
    - FINANZAS (si habla de dinero, gastos, compras, pagos, consumo)
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
    await update.message.reply_text("👋 ¡Hola! Envíame un gasto (ej: 'Gasté $12 en supermercado') y lo registraré automáticamente en Google Sheets.")

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
