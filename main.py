import os
import asyncio
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from google import genai

# Inicializar cliente de Gemini con la librería oficial actualizada
client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY"))

# -------------------------------------------------------------
# SUBAGENTES (Especialistas)
# -------------------------------------------------------------
def subagente_finanzas(peticion):
    # Aquí irá la conexión a Google Sheets / Supabase
    return f"💰 [Subagente Finanzas]: Procesando registro -> '{peticion}'"

def subagente_agenda(peticion):
    # Aquí irá la conexión a Google Calendar
    return f"📅 [Subagente Agenda]: Procesando evento/tarea -> '{peticion}'"

def subagente_publicador(peticion):
    # Aquí irá la conexión para publicar en redes/canales
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
    
    # Clasificación de la intención usando Gemini
    response = client.models.generate_content(
        model='gemini-2.5-flash',
        contents=prompt_router,
    )
    
    categoria = response.text.strip().upper()
    
    # Delegación al subagente correspondiente
    if "FINANZAS" in categoria:
        return subagente_finanzas(mensaje_usuario)
    elif "AGENDA" in categoria:
        return subagente_agenda(mensaje_usuario)
    elif "PUBLICAR" in categoria:
        return subagente_publicador(mensaje_usuario)
    else:
        # Respuesta conversacional general
        resp_general = client.models.generate_content(
            model='gemini-2.5-flash',
            contents=mensaje_usuario,
        )
        return resp_general.text

# -------------------------------------------------------------
# MANEJADORES DE TELEGRAM
# -------------------------------------------------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("👋 ¡Hola! Soy tu asistente personal. Envíame tus gastos, tareas o publicaciones y las procesaré automáticamente.")

async def responder(update: Update, context: ContextTypes.DEFAULT_TYPE):
    texto_usuario = update.message.text
    respuesta = orquestador_router(texto_usuario)
    await update.message.reply_text(respuesta)

if __name__ == '__main__':
    TOKEN = os.environ.get("TELEGRAM_TOKEN")
    app = Application.builder().token(TOKEN).build()
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, responder))
    
    print("Bot activo y listo...")
    app.run_polling()
