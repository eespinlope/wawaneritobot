import os
import asyncio
from threading import Thread
from flask import Flask
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from google import genai

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
# SUBAGENTES
# -------------------------------------------------------------
def subagente_finanzas(peticion):
    return f"💰 [Subagente Finanzas]: Procesando registro -> '{peticion}'"

def subagente_agenda(peticion):
    return f"📅 [Subagente Agenda]: Procesando evento/tarea -> '{peticion}'"

def subagente_publicador(peticion):
    return f"📢 [Subagente Publicador]: Procesando publicación -> '{peticion}'"

# -------------------------------------------------------------
# ROUTER / ORQUESTADOR
# -------------------------------------------------------------
def orquestador_router(mensaje_usuario):
    prompt_router = f"""
    Clasifica el siguiente mensaje en una de estas palabras exactas:
    - FINANZAS (si habla de dinero, gastos, compras, pagos)
    - AGENDA (si habla de tareas, recordatorios, fechas, citas)
    - PUBLICAR (si habla de redacción, posts, redes sociales)
    - GENERAL (para conversación casual o preguntas generales)

    Mensaje: "{mensaje_usuario}"
    Responde ÚNICAMENTE con una sola palabra clave.
    """
    
    response = client.models.generate_content(
        model='gemini-2.5-flash',
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
            model='gemini-2.5-flash',
            contents=mensaje_usuario,
        )
        return resp_general.text

# -------------------------------------------------------------
# MANEJADORES DE TELEGRAM
# -------------------------------------------------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("👋 ¡Hola! Soy tu asistente personal. Envíame tus gastos, tareas o publicaciones.")

async def responder(update: Update, context: ContextTypes.DEFAULT_TYPE):
    texto_usuario = update.message.text
    respuesta = orquestador_router(texto_usuario)
    await update.message.reply_text(respuesta)

# -------------------------------------------------------------
# EJECUCIÓN PRINCIPAL
# -------------------------------------------------------------
if __name__ == '__main__':
    # Iniciar Flask en un hilo secundario para cumplir con la verificación de Render
    Thread(target=run_flask, daemon=True).start()
    
    # Iniciar el bot de Telegram
    TOKEN = os.environ.get("TELEGRAM_TOKEN")
    bot_app = Application.builder().token(TOKEN).build()
    
    bot_app.add_handler(CommandHandler("start", start))
    bot_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, responder))
    
    print("Bot y Servidor Web activos...")
    bot_app.run_polling()
