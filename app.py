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
# 📦 Cargar variables de entorno
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
# 🔓 CORS — configuración segura
# ===============================
origins = [
    "http://localhost:5173",          # Front local (React)
    "https://previmed.onrender.com",  # Dominio de producción (futuro)
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ===============================
# 🧠 Memoria conversacional temporal
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
            return [b for b in data.get("msj", []) if b.get("estado")]
    except Exception as e:
        print(f"❌ Error obteniendo barrios: {e}")
        return []


async def crear_visita(paciente_id: int, medico_id: int, descripcion: str,
                       direccion: str, telefono: str, barrio_id: int):
    """Crea una visita médica en el backend."""
    try:
        fecha_actual = datetime.now().isoformat()
        async with httpx.AsyncClient(timeout=10.0) as cliente:
            resp = await cliente.post(
                f"{BACKEND_URL}/visitas",
                json={
                    "fecha_visita": fecha_actual,
                    "descripcion": descripcion,
                    "direccion": direccion,
                    "estado": True,
                    "telefono": telefono,
                    "paciente_id": paciente_id,
                    "medico_id": medico_id,
                    "barrio_id": barrio_id,
                },
            )
            return resp.json()
    except Exception as e:
        print(f"❌ Error creando visita: {e}")
        return {"ok": False, "mensaje": str(e)}


async def detectar_intencion(texto: str):
    """Clasifica la intención del usuario."""
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
        return completion.choices[0].message.content.strip().lower()
    except Exception as e:
        print(f"❌ Error detectando intención: {e}")
        return "otro"


# ===============================
# 💬 Endpoint principal del chat
# ===============================
@app.post("/chat")
async def responder(mensaje: MensajeEntrada):
    texto = mensaje.texto.strip()
    doc = mensaje.documento
    contexto = conversaciones.get(doc or "default", {})

    if not texto:
        raise HTTPException(status_code=400, detail="El campo 'texto' no puede estar vacío.")

    # 🧩 reconstruir historial si viene del frontend
    historial = []
    if mensaje.historial:
        try:
            historial = [
                {"role": m.get("role", "user"), "content": m.get("content", "")}
                for m in mensaje.historial
            ]
        except Exception as e:
            print(f"⚠️ Error procesando historial: {e}")

    # 🧭 Detectar intención
    intencion = await detectar_intencion(texto)
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
                 "content": f"Eres el asistente institucional de Previmed. Usa este contexto:\n\n{contexto_prevemed}"}
            ] + historial + [{"role": "user", "content": texto}],
        )
        respuesta = completion.choices[0].message.content
        return {"ok": True, "accion": "informacion", "respuesta": respuesta}

    # 🚀 Si menciona visita
    if any(p in texto.lower() for p in ["visita", "médico", "doctor", "cita"]):
        intencion = "visita"

    # 💬 Conversación natural
    if intencion == "otro":
        completion = cliente_openai.chat.completions.create(
            model="gpt-4.1-mini",
            messages=[
                {"role": "system",
                 "content": f"Eres un asistente empático de Previmed. Usa este contexto:\n\n{contexto_prevemed}"}
            ] + historial + [{"role": "user", "content": texto}],
        )
        respuesta = completion.choices[0].message.content
        return {"ok": True, "accion": "otro", "respuesta": respuesta}

    # 🏥 Flujo completo de visita
    if intencion == "visita":
        if not doc:
            return {
                "ok": False,
                "accion": "solicitar_documento",
                "respuesta": "¿Podrías darme tu número de cédula para verificar tu membresía activa?"
            }

        if "membresia_verificada" not in contexto:
            data = await verificar_membresia_activa(doc)
            if not data.get("ok"):
                return {
                    "ok": False,
                    "accion": "sin_membresia",
                    "respuesta": "No encuentro una membresía activa con ese documento. ¿Deseas renovarla?"
                }

            contexto["membresia_verificada"] = True
            contexto["paciente_id"] = data["paciente"]["id_paciente"]
            conversaciones[doc] = contexto
            return {"ok": True, "accion": "pedir_motivo", "respuesta": "Perfecto, tu membresía está activa. ¿Cuál es el motivo de la visita?"}

        if "motivo" not in contexto:
            contexto["motivo"] = texto
            conversaciones[doc] = contexto
            return {"ok": True, "accion": "pedir_direccion", "respuesta": "¿En qué dirección deseas recibir la visita?"}

        if "direccion" not in contexto:
            contexto["direccion"] = texto
            conversaciones[doc] = contexto
            return {"ok": True, "accion": "pedir_telefono", "respuesta": "Por favor, indícame un número de contacto."}

        if "telefono" not in contexto:
            contexto["telefono"] = texto
            conversaciones[doc] = contexto
            medicos = await get_medicos_disponibles()
            if not medicos:
                return {"ok": False, "accion": "sin_medicos", "respuesta": "No hay médicos disponibles en este momento 😔."}

            contexto["medicos_disponibles"] = medicos
            conversaciones[doc] = contexto
            nombres = ", ".join([f"{m['usuario']['nombre']} {m['usuario']['apellido']}" for m in medicos])
            return {"ok": True, "accion": "elegir_medico", "respuesta": f"Tengo disponibles a los siguientes médicos: {nombres}. ¿Con cuál deseas agendar?"}

        if "medico_id" not in contexto:
            medicos = contexto.get("medicos_disponibles", [])
            elegido = next((m for m in medicos if m["usuario"]["nombre"].lower() in texto.lower()), None)
            if not elegido:
                return {"ok": False, "accion": "repetir_medico", "respuesta": "No logré identificar el médico que mencionas. Dime solo su nombre."}

            contexto["medico_id"] = elegido["id_medico"]
            conversaciones[doc] = contexto

            barrios = await get_barrios_activos()
            if not barrios:
                return {"ok": False, "accion": "sin_barrios", "respuesta": "No hay barrios activos disponibles."}

            contexto["barrios_activos"] = barrios
            conversaciones[doc] = contexto
            nombres_barrios = ", ".join([b["nombreBarrio"] for b in barrios])
            return {"ok": True, "accion": "elegir_barrio", "respuesta": f"¿En qué barrio te encuentras? Barrios disponibles: {nombres_barrios}."}

        if "barrio_id" not in contexto:
            barrios = contexto.get("barrios_activos", [])
            elegido = next((b for b in barrios if b["nombreBarrio"].lower() in texto.lower()), None)
            if not elegido:
                return {"ok": False, "accion": "repetir_barrio", "respuesta": "No logré identificar el barrio. Escribe solo el nombre."}

            contexto["barrio_id"] = elegido["idBarrio"]
            conversaciones[doc] = contexto
            return {"ok": True, "accion": "confirmar", "respuesta": f"Confirmo: visita por '{contexto['motivo']}' en '{contexto['direccion']}', barrio {elegido['nombreBarrio']}. ¿Deseas agendarla?"}

        if "sí" in texto.lower() or "si" in texto.lower():
            visita = await crear_visita(
                paciente_id=contexto["paciente_id"],
                medico_id=contexto["medico_id"],
                descripcion=contexto["motivo"],
                direccion=contexto["direccion"],
                telefono=contexto["telefono"],
                barrio_id=contexto["barrio_id"],
            )
            conversaciones.pop(doc, None)
            return {"ok": True, "accion": "visita_creada", "respuesta": "✅ Tu visita fue creada exitosamente. Recibirás confirmación en tu correo."}

        return {"ok": True, "accion": "esperando_confirmacion", "respuesta": "¿Deseas que cree la visita con esos datos?"}


# ===============================
# 🌐 Endpoint raíz
# ===============================
@app.get("/")
def inicio():
    return {"mensaje": "🤖 Asistente IA Previmed operativo"}
