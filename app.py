from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from openai import OpenAI
from dotenv import load_dotenv
import os
import httpx
from datetime import datetime
from fastapi.middleware.cors import CORSMiddleware
from contexto import contexto_prevemed

# ===============================
# 📦 Variables de entorno
# ===============================
load_dotenv()

CLAVE_OPENAI = os.getenv("OPENAI_API_KEY")
BACKEND_URL = os.getenv("BACKEND_URL", "https://previmedbackend-q73n.onrender.com")

# ===============================
# 🚀 Inicializar aplicación
# ===============================
app = FastAPI(title="Asistente IA Previmed")
cliente_openai = OpenAI(api_key=CLAVE_OPENAI)

# ===============================
# 🔓 CORS — Configuración segura
# ===============================
origins = [
    "http://localhost:5173",
    "https://previmed.onrender.com",
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ===============================
# 🧠 Memoria conversacional
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
# ⚙️ Funciones auxiliares
# ===============================
async def verificar_membresia_activa(numero_documento: str):
    """Verifica si un paciente tiene membresía activa."""
    try:
        async with httpx.AsyncClient(timeout=10.0) as cliente:
            url = f"{BACKEND_URL}/membresias/activa/{numero_documento}"
            print(f"🔎 Consultando membresía: {url}")
            resp = await cliente.get(url)
            resp.raise_for_status()
            return resp.json()
    except Exception as e:
        print(f"❌ Error verificando membresía: {e}")
        return {"ok": False, "mensaje": str(e)}


async def get_medicos_disponibles():
    """Obtiene médicos activos y disponibles."""
    try:
        async with httpx.AsyncClient(timeout=10.0) as cliente:
            resp = await cliente.get(f"{BACKEND_URL}/medicos")
            resp.raise_for_status()
            data = resp.json()
            medicos = [
                m for m in data.get("data", [])
                if m.get("estado") and m.get("disponibilidad")
            ]
            print(f"👩‍⚕️ Médicos disponibles: {len(medicos)}")
            return medicos
    except Exception as e:
        print(f"❌ Error obteniendo médicos: {e}")
        return []


async def get_barrios_activos():
    """Obtiene los barrios activos desde el backend."""
    try:
        async with httpx.AsyncClient(timeout=10.0) as cliente:
            resp = await cliente.get(f"{BACKEND_URL}/barrios")
            resp.raise_for_status()
            data = resp.json()
            activos = [b for b in data.get("msj", []) if b.get("estado")]
            print(f"🏙️ Barrios activos: {len(activos)}")
            return activos
    except Exception as e:
        print(f"❌ Error obteniendo barrios: {e}")
        return []


async def crear_visita(paciente_id: int, medico_id: int, descripcion: str,
                       direccion: str, telefono: str, barrio_id: int):
    """Crea una visita médica en el backend."""
    try:
        fecha_actual = datetime.now().isoformat()
        async with httpx.AsyncClient(timeout=10.0) as cliente:
            payload = {
                "fecha_visita": fecha_actual,
                "descripcion": descripcion,
                "direccion": direccion,
                "estado": True,
                "telefono": telefono,
                "paciente_id": paciente_id,
                "medico_id": medico_id,
                "barrio_id": barrio_id,
            }
            print("📝 Creando visita con:", payload)
            resp = await cliente.post(f"{BACKEND_URL}/visitas", json=payload)
            resp.raise_for_status()
            return resp.json()
    except Exception as e:
        print(f"❌ Error creando visita: {e}")
        return {"ok": False, "mensaje": str(e)}


# ===============================
# 🔁 Reconstruir contexto
# ===============================
def reconstruir_contexto(historial):
    """Reconstruye el contexto de conversación con base en el historial."""
    contexto = {}
    if not historial:
        return contexto

    for msg in historial:
        if msg["role"] == "assistant":
            text = msg.get("text", "").lower()
            if "membresía está activa" in text:
                contexto["membresia_verificada"] = True
            elif "motivo" in text:
                contexto["fase"] = "motivo"
            elif "dirección" in text:
                contexto["fase"] = "direccion"
            elif "teléfono" in text:
                contexto["fase"] = "telefono"
    return contexto


# ===============================
# 💬 Endpoint principal del chat
# ===============================
@app.post("/chat")
async def responder(mensaje: MensajeEntrada):
    texto = mensaje.texto.strip()
    doc = mensaje.documento
    historial = mensaje.historial or []

    if not texto:
        raise HTTPException(status_code=400, detail="El campo 'texto' no puede estar vacío.")

    contexto = reconstruir_contexto(historial)
    print(f"📜 Contexto reconstruido: {contexto}")

    # 🧠 Detectar intención
    try:
        completion = cliente_openai.chat.completions.create(
            model="gpt-4.1-mini",
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Clasifica la intención del usuario. "
                        "Responde SOLO con una palabra: 'visita', 'informacion', 'cancelar' o 'otro'."
                    ),
                },
                {"role": "user", "content": texto},
            ],
        )
        intencion = completion.choices[0].message.content.strip().lower()
    except Exception as e:
        print(f"❌ Error detectando intención: {e}")
        intencion = "otro"

    print(f"🧭 Intención detectada: {intencion}")

    # 🚪 Cancelar conversación
    if intencion == "cancelar":
        conversaciones.pop(doc or "default", None)
        return {
            "ok": True,
            "accion": "cancelar",
            "respuesta": "He cancelado la solicitud. ¿Deseas que te ayude con otra cosa?"
        }

    # ℹ️ Información general
    if intencion == "informacion" and not any(p in texto.lower() for p in ["visita", "médico", "doctor", "cita"]):
        completion = cliente_openai.chat.completions.create(
            model="gpt-4.1-mini",
            messages=[
                {"role": "system",
                 "content": f"Eres el asistente institucional de Previmed. Usa este contexto:\n\n{contexto_prevemed}"},
                {"role": "user", "content": texto},
            ],
        )
        return {"ok": True, "accion": "informacion", "respuesta": completion.choices[0].message.content}

    # 🏥 Flujo de visita
    if any(p in texto.lower() for p in ["visita", "médico", "doctor", "cita"]):
        if not doc:
            return {"ok": False, "accion": "solicitar_documento", "respuesta": "¿Podrías indicarme tu número de cédula?"}

        if "membresia_verificada" not in contexto:
            data = await verificar_membresia_activa(doc)
            if not data.get("ok"):
                return {"ok": False, "accion": "sin_membresia", "respuesta": "No encuentro una membresía activa con ese documento."}

            contexto["membresia_verificada"] = True
            contexto["paciente_id"] = data["paciente"]["id_paciente"]
            return {"ok": True, "accion": "pedir_motivo", "respuesta": "Perfecto, tu membresía está activa. ¿Cuál es el motivo de la visita?"}

        if "motivo" not in contexto:
            contexto["motivo"] = texto
            return {"ok": True, "accion": "pedir_direccion", "respuesta": "¿En qué dirección deseas recibir la visita?"}

        if "direccion" not in contexto:
            contexto["direccion"] = texto
            return {"ok": True, "accion": "pedir_telefono", "respuesta": "Por favor, indícame un número de contacto."}

        if "telefono" not in contexto:
            contexto["telefono"] = texto
            medicos = await get_medicos_disponibles()
            if not medicos:
                return {"ok": False, "accion": "sin_medicos", "respuesta": "No hay médicos disponibles en este momento."}

            contexto["medicos_disponibles"] = medicos
            nombres = ", ".join([f"{m['usuario']['nombre']} {m['usuario']['apellido']}" for m in medicos])
            return {"ok": True, "accion": "elegir_medico", "respuesta": f"Tengo disponibles: {nombres}. ¿Con cuál deseas agendar?"}

        if "medico_id" not in contexto:
            medicos = contexto.get("medicos_disponibles", [])
            elegido = next((m for m in medicos if m["usuario"]["nombre"].lower() in texto.lower()), None)
            if not elegido:
                return {"ok": False, "accion": "repetir_medico", "respuesta": "No logré identificar el médico. Dime solo su nombre."}

            contexto["medico_id"] = elegido["id_medico"]
            barrios = await get_barrios_activos()
            contexto["barrios_activos"] = barrios
            nombres_barrios = ", ".join([b["nombreBarrio"] for b in barrios])
            return {"ok": True, "accion": "elegir_barrio", "respuesta": f"¿En qué barrio estás? Barrios: {nombres_barrios}."}

        if "barrio_id" not in contexto:
            barrios = contexto.get("barrios_activos", [])
            elegido = next((b for b in barrios if b["nombreBarrio"].lower() in texto.lower()), None)
            if not elegido:
                return {"ok": False, "accion": "repetir_barrio", "respuesta": "No logré identificar el barrio. Escribe solo el nombre."}

            contexto["barrio_id"] = elegido["idBarrio"]
            return {"ok": True, "accion": "confirmar", "respuesta": f"Confirmo: visita por '{contexto['motivo']}' en '{contexto['direccion']}', barrio {elegido['nombreBarrio']}. ¿Deseas confirmarla?"}

        if "sí" in texto.lower() or "si" in texto.lower():
            visita = await crear_visita(
                paciente_id=contexto["paciente_id"],
                medico_id=contexto["medico_id"],
                descripcion=contexto["motivo"],
                direccion=contexto["direccion"],
                telefono=contexto["telefono"],
                barrio_id=contexto["barrio_id"],
            )
            return {"ok": True, "accion": "visita_creada", "respuesta": "✅ Tu visita fue creada exitosamente."}

    # 🗣️ Conversación general
    completion = cliente_openai.chat.completions.create(
        model="gpt-4.1-mini",
        messages=[
            {"role": "system", "content": f"Eres un asistente empático de Previmed. Usa este contexto:\n\n{contexto_prevemed}"},
            {"role": "user", "content": texto},
        ],
    )
    return {"ok": True, "accion": "otro", "respuesta": completion.choices[0].message.content}


# ===============================
# 🌐 Endpoint raíz
# ===============================
@app.get("/")
def inicio():
    return {"mensaje": "🤖 Asistente IA Previmed operativo"}
