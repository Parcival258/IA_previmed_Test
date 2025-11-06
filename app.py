from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from openai import OpenAI
from dotenv import load_dotenv
from fastapi.middleware.cors import CORSMiddleware
import os, httpx, json
from datetime import datetime
from contexto import contexto_prevemed

# Para que los print() aparezcan en Render
import sys
sys.stdout.reconfigure(line_buffering=True)

load_dotenv()

# ===============================
# 🔧 Variables de entorno
# ===============================
CLAVE_OPENAI = os.getenv("OPENAI_API_KEY")
BACKEND_URL = os.getenv("BACKEND_URL", "https://previmedbackend-q73n.onrender.com")

# ===============================
# 🚀 Inicializar app
# ===============================
app = FastAPI(title="Asistente IA Previmed")
cliente_openai = OpenAI(api_key=CLAVE_OPENAI)

# ===============================
# 🔓 CORS
# ===============================
origins = [
    "http://localhost:5173",
    "https://previmed.onrender.com"
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ===============================
# 🧠 Memoria simple
# ===============================
conversaciones = {}

# ===============================
# 📥 Modelo de entrada
# ===============================
class MensajeEntrada(BaseModel):
    texto: str
    documento: str | None = None
    historial: list | None = None


# ===============================
# 🔧 Funciones auxiliares
# ===============================
async def verificar_membresia_activa(documento: str):
    try:
        async with httpx.AsyncClient(timeout=10) as cliente:
            url = f"{BACKEND_URL}/membresias/activa/{documento}"
            resp = await cliente.get(url)
            resp.raise_for_status()
            return resp.json()
    except Exception as e:
        return {"ok": False, "mensaje": f"Error verificando membresía: {e}"}

async def get_medicos():
    try:
        async with httpx.AsyncClient(timeout=10) as cliente:
            resp = await cliente.get(f"{BACKEND_URL}/medicos/")
            resp.raise_for_status()
            data = resp.json()
            return [m for m in data.get("data", []) if m.get("estado") and m.get("disponibilidad")]
    except Exception as e:
        return {"ok": False, "mensaje": f"Error obteniendo médicos: {e}"}

async def get_barrios():
    try:
        async with httpx.AsyncClient(timeout=10) as cliente:
            resp = await cliente.get(f"{BACKEND_URL}/barrios")
            resp.raise_for_status()
            data = resp.json()
            return [b for b in data.get("msj", []) if b.get("estado")]
    except Exception as e:
        return {"ok": False, "mensaje": f"Error obteniendo barrios: {e}"}

async def crear_visita(paciente_id, medico_id, descripcion, direccion, telefono, barrio_id):
    try:
        async with httpx.AsyncClient(timeout=10) as cliente:
            payload = {
                "fecha_visita": datetime.now().isoformat(),
                "descripcion": descripcion,
                "direccion": direccion,
                "telefono": telefono,
                "estado": True,
                "paciente_id": paciente_id,
                "medico_id": medico_id,
                "barrio_id": barrio_id,
            }
            resp = await cliente.post(f"{BACKEND_URL}/visitas", json=payload)
            resp.raise_for_status()
            return {"ok": True, "data": resp.json()}
    except Exception as e:
        return {"ok": False, "mensaje": f"Error creando visita: {e}"}


# ===============================
# 🤖 Orquestador Inteligente
# ===============================
@app.post("/chat")
async def chat(mensaje: MensajeEntrada):
    texto = mensaje.texto.strip()
    doc = mensaje.documento or "default"

    if not texto:
        raise HTTPException(400, "Texto vacío")

    # Recuperar contexto previo
    contexto = conversaciones.get(doc, [])

    # Mensaje del sistema (instrucciones)
    system_prompt = {
        "role": "system",
        "content": (
            "Eres el asistente institucional y médico de Previmed. "
            "Debes responder con empatía y claridad, pero también indicar acciones cuando se requiera. "
            "Responde SIEMPRE en formato JSON válido con las claves: "
            "'accion', 'respuesta', y opcionalmente 'detalle'.\n\n"
            "Posibles acciones:\n"
            "- 'info': responder información general usando el contexto institucional.\n"
            "- 'verificar_membresia': cuando necesites revisar una membresía activa.\n"
            "- 'listar_medicos': cuando necesites mostrar médicos disponibles.\n"
            "- 'listar_barrios': cuando necesites mostrar barrios activos.\n"
            "- 'crear_visita': cuando tengas todos los datos para crear una visita.\n"
            "- 'pedir_dato': cuando falte información como dirección, teléfono o motivo.\n\n"
            "El contexto institucional es:\n"
            f"{contexto_prevemed}"
        ),
    }

    # Construir el historial completo para el modelo
    mensajes = [system_prompt, *contexto, {"role": "user", "content": texto}]

    # 1️⃣ El modelo decide qué hacer
    try:
        completion = cliente_openai.chat.completions.create(
            model="gpt-4.1-mini",
            messages=mensajes,
            temperature=0.5,
            max_tokens=400,
        )
        contenido = completion.choices[0].message.content
        print("🤖 Respuesta IA:", contenido)
        data = json.loads(contenido)
    except Exception as e:
        print("⚠️ Error interpretando salida IA:", e)
        data = {"accion": "info", "respuesta": texto, "detalle": {}}

    accion = data.get("accion", "info")
    respuesta_texto = data.get("respuesta", "Lo siento, no entendí bien tu solicitud.")
    detalle = data.get("detalle", {})

    # 2️⃣ Ejecutar la acción si aplica
    resultado = {}
    try:
        if accion == "verificar_membresia":
            if not mensaje.documento:
                respuesta_texto = "Por favor indícame tu número de cédula para verificar tu membresía."
            else:
                resultado = await verificar_membresia_activa(mensaje.documento)
                detalle["membresia"] = resultado
                if resultado.get("ok"):
                    respuesta_texto = f"Tu membresía está activa. ¿Deseas agendar una visita?"
                else:
                    respuesta_texto = "No encontré una membresía activa. ¿Deseas renovarla?"

        elif accion == "listar_medicos":
            medicos = await get_medicos()
            if isinstance(medicos, list) and medicos:
                nombres = [f"{m['usuario']['nombre']} {m['usuario']['apellido']}" for m in medicos]
                detalle["medicos"] = nombres
                respuesta_texto = "Los médicos disponibles son: " + ", ".join(nombres)
            else:
                respuesta_texto = "No hay médicos disponibles en este momento."

        elif accion == "listar_barrios":
            barrios = await get_barrios()
            if isinstance(barrios, list) and barrios:
                nombres = [b["nombreBarrio"] for b in barrios]
                detalle["barrios"] = nombres
                respuesta_texto = "Barrios disponibles: " + ", ".join(nombres)
            else:
                respuesta_texto = "No hay barrios activos en este momento."

        elif accion == "crear_visita":
            datos = detalle or {}
            paciente_id = datos.get("paciente_id")
            medico_id = datos.get("medico_id")
            barrio_id = datos.get("barrio_id")
            descripcion = datos.get("descripcion", "Visita médica domiciliaria")
            direccion = datos.get("direccion", "")
            telefono = datos.get("telefono", "")

            if all([paciente_id, medico_id, barrio_id, direccion, telefono]):
                visita = await crear_visita(paciente_id, medico_id, descripcion, direccion, telefono, barrio_id)
                detalle["visita"] = visita
                respuesta_texto = "✅ Tu visita fue creada exitosamente. Gracias por confiar en Previmed."
            else:
                respuesta_texto = "Faltan algunos datos para crear la visita. ¿Podrías confirmarlos?"

    except Exception as e:
        print("❌ Error ejecutando acción:", e)
        respuesta_texto = f"Ocurrió un error ejecutando la acción {accion}."

    # 3️⃣ Guardar contexto de la conversación
    contexto.append({"role": "user", "content": texto})
    contexto.append({"role": "assistant", "content": respuesta_texto})
    conversaciones[doc] = contexto[-10:]  # mantener solo últimos 10 turnos

    # 4️⃣ Devolver respuesta al frontend
    return {
        "ok": True,
        "accion": accion,
        "respuesta": respuesta_texto,
        "detalle": detalle,
    }


# ===============================
# 🩺 Rutas básicas
# ===============================
@app.get("/")
def inicio():
    return {"status": "ok", "mensaje": "Asistente IA operativo"}

@app.get("/health")
def health():
    return {"ok": True, "status": "running"}
