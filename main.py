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

    Esta es la base de datos actual de transacciones:
    Encabezados: {headers}
    Filas: {filas_datos}

    Analiza la intención del usuario y responde ÚNICAMENTE con un JSON plano sin bloques markdown:

    1. Si es REGISTRAR un nuevo movimiento:
    {{
        "accion": "registrar",
        "tipo": "Ingreso / Egreso / Préstamo",
        "monto": 0.00,
        "categoria": "Categoría",
        "descripcion": "detalle",
        "fecha_compromiso": "YYYY-MM-DD" (o "N/A"),
        "fecha_pago": "YYYY-MM-DD" (o "N/A" si está pendiente),
        "estado": "Pendiente / Pagado"
    }}

    2. Si el usuario quiere EDITAR o MARCAR COMO PAGADO algo existente:
    {{
        "accion": "editar",
        "id_transaccion": "ID_ENCONTRADO" (ej. TX-101),
        "campo_a_cambiar": "estado / fecha_compromiso / fecha_pago / monto / descripcion",
        "nuevo_valor": "Pagado / nueva fecha / etc."
    }}

    3. Si es CONSULTAR:
    {{
        "accion": "consultar"
    }}
    """

    try:
        response = client.models.generate_content(
            model='gemini-flash-latest',
            contents=prompt_orquestador,
        )
        texto_limpio = response.text.replace("```json", "").replace("```", "").strip()
        datos = json.loads(texto_limpio)
        accion = datos.get("accion")

        # OPCIÓN 1: EDITAR REGISTRO
        if accion == "editar":
            id_buscar = datos.get("id_transaccion", "").upper()
            
            fila_index = None
            for idx, fila in enumerate(registros, start=1):
                if len(fila) > 0 and fila[0].upper() == id_buscar:
                    fila_index = idx
                    break

            if not fila_index:
                return f"🔍 No se encontró el registro en la hoja."

            # Mapeo a las 9 columnas (A=1, B=2, C=3, D=4, E=5, F=6, G=7, H=8, I=9)
            mapa_columnas = {
                "monto": 4,
                "categoria": 5,
                "descripcion": 6,
                "fecha_compromiso": 7,
                "fecha_pago": 8,
                "estado": 9
            }
            
            campo = datos.get("campo_a_cambiar", "estado")
            nuevo_valor = datos.get("nuevo_valor", "Pagado")
            col_index = mapa_columnas.get(campo, 9)

            # Si el usuario marca algo como Pagado, también actualizamos la Fecha de Pago a hoy
            if campo == "estado" and "pagado" in str(nuevo_valor).lower():
                sheet.update_cell(fila_index, 8, fecha_hoy) # Columna H (Fecha Pago)

            sheet.update_cell(fila_index, col_index, nuevo_valor)
            return f"✏️ **Registro `{id_buscar}` actualizado**\nSe cambió `{campo}` a: **{nuevo_valor}**."

        # OPCIÓN 2: REGISTRAR NUEVO
        elif accion == "registrar":
            import random
            tx_id = f"TX-{random.randint(100, 999)}"
            tipo = datos.get("tipo", "Egreso")
            monto = datos.get("monto", 0.0)
            categoria = datos.get("categoria", "Otros")
            descripcion = datos.get("descripcion", peticion)
            fecha_compromiso = datos.get("fecha_compromiso") or "N/A"
            estado = datos.get("estado", "Pagado")
            
            # Asignar fecha de pago automática si no está pendiente
            fecha_pago = datos.get("fecha_pago")
            if not fecha_pago or fecha_pago == "N/A":
                fecha_pago = fecha_hoy if estado == "Pagado" else "N/A"
                
            fecha_registro = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            # Insertar exactamente las 9 columnas
            sheet.append_row([
                tx_id, fecha_registro, tipo, monto, categoria, 
                descripcion, fecha_compromiso, fecha_pago, estado
            ])
            
            return f"✅ **{tipo} registrado**\n🆔 ID: `{tx_id}` | 💰 Monto: ${monto}\n📝 {descripcion}\n📌 Compromiso: {fecha_compromiso} | 🗓️ Pago: {fecha_pago}\n📊 Estado: {estado}"

        # OPCIÓN 3: CONSULTAR
        else:
            prompt_respuesta = f"""
            El usuario pregunta: "{peticion}"
            Datos en la hoja (9 columnas): {filas_datos}
            Responde la consulta con formato claro en Markdown.
            """
            resp = client.models.generate_content(model='gemini-flash-latest', contents=prompt_respuesta)
            return resp.text

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
