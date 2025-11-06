from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from openai import OpenAI
from dotenv import load_dotenv
from fastapi.middleware.cors import CORSMiddleware
import os, httpx
from datetime import datetime
from contexto import contexto_prevemed

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
    except Exception:
        return {"ok": False, "mensaje": "No se pudo verificar la membresía en este momento."}

async def get_medicos():
    try:
        async with httpx.AsyncClient(timeout=10) as cliente:
            resp = await cliente.get(f"{BACKEND_URL}/medicos/")
            resp.raise_for_status()
            data = resp.json()
            return [m for m in data.get("data", []) if m.get("estado") and m.get("disponibilidad")]
    except Exception:
        return []

async def get_barrios():
    try:
        async with httpx.AsyncClient(timeout=10) as cliente:
            resp = await cliente.get(f"{BACKEND_URL}/barrios")
            resp.raise_for_status()
            data = resp.json()
            return [b for b in data.get("msj", []) if b.get("estado")]
    except Exception:
        return []

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
            return resp.json()
    except Exception:
        return {"ok": False, "mensaje": "No se pudo crear la visita en este momento."}


# ===============================
# 🧠 Chat principal
# ===============================
@app.post("/chat")
async def chat(mensaje: MensajeEntrada):
    texto = mensaje.texto.lower().strip()
    doc = mensaje.documento or "default"

    if not texto:
        raise HTTPException(400, "Texto vacío")

    contexto = conversaciones.get(doc, {})
    palabras_visita = ["visita", "cita", "médico", "medico", "doctor"]

    intencion = "visita" if any(p in texto for p in palabras_visita) else "info"

    # ========= FLUJO VISITA =========
    if intencion == "visita":
        # Documento
        if not mensaje.documento:
            return {"ok": False, "accion": "pedir_documento", "respuesta": "¿Podrías darme tu número de cédula para verificar tu membresía?", "detalle": {}}

        # Membresía
        if "membresia" not in contexto:
            data = await verificar_membresia_activa(mensaje.documento)
            if not data.get("ok"):
                return {"ok": False, "accion": "sin_membresia", "respuesta": "No se encontró membresía activa. ¿Deseas renovarla?", "detalle": {}}
            contexto["membresia"] = data
            conversaciones[doc] = contexto
            return {"ok": True, "accion": "pedir_motivo", "respuesta": "Perfecto, tu membresía está activa. ¿Cuál es el motivo de la visita?", "detalle": {}}

        # Motivo
        if "motivo" not in contexto:
            contexto["motivo"] = texto
            conversaciones[doc] = contexto
            return {"ok": True, "accion": "pedir_direccion", "respuesta": "¿En qué dirección deseas recibir la visita?", "detalle": {}}

        # Dirección
        if "direccion" not in contexto:
            contexto["direccion"] = texto
            conversaciones[doc] = contexto
            return {"ok": True, "accion": "pedir_telefono", "respuesta": "Por favor indícame un número de contacto.", "detalle": {}}

        # Teléfono
        if "telefono" not in contexto:
            contexto["telefono"] = texto
            conversaciones[doc] = contexto
            medicos = await get_medicos()
            if not medicos:
                return {"ok": False, "accion": "sin_medicos", "respuesta": "No hay médicos disponibles en este momento 😔.", "detalle": {}}
            contexto["medicos"] = medicos
            conversaciones[doc] = contexto
            lista = [f"{m['usuario']['nombre']} {m['usuario']['apellido']}" for m in medicos]
            return {"ok": True, "accion": "elegir_medico", "respuesta": "Selecciona un médico disponible:", "detalle": {"medicos": lista}}

        # Médico
        if "medico" not in contexto:
            elegido = next((m for m in contexto["medicos"] if m["usuario"]["nombre"].lower() in texto), None)
            if not elegido:
                return {"ok": False, "accion": "repetir_medico", "respuesta": "No logré identificar el médico. Dime solo su nombre.", "detalle": {}}
            contexto["medico"] = elegido
            barrios = await get_barrios()
            contexto["barrios"] = barrios
            conversaciones[doc] = contexto
            lista = [b["nombreBarrio"] for b in barrios]
            return {"ok": True, "accion": "elegir_barrio", "respuesta": "Selecciona tu barrio:", "detalle": {"barrios": lista}}

        # Barrio
        if "barrio" not in contexto:
            elegido = next((b for b in contexto["barrios"] if b["nombreBarrio"].lower() in texto), None)
            if not elegido:
                return {"ok": False, "accion": "repetir_barrio", "respuesta": "No logré identificar el barrio. Dime solo el nombre.", "detalle": {}}
            contexto["barrio"] = elegido
            conversaciones[doc] = contexto
            return {"ok": True, "accion": "confirmar", "respuesta": f"Confirmo visita por '{contexto['motivo']}' en '{contexto['direccion']}', barrio {elegido['nombreBarrio']}. ¿Deseas agendarla?", "detalle": {}}

        # Confirmar
        if texto in ["sí", "si", "confirmar", "claro"]:
            paciente_id = contexto["membresia"]["paciente"]["id_paciente"]
            medico_id = contexto["medico"]["id_medico"]
            barrio_id = contexto["barrio"]["idBarrio"]
            visita = await crear_visita(
                paciente_id, medico_id,
                contexto["motivo"], contexto["direccion"],
                contexto["telefono"], barrio_id
            )
            conversaciones.pop(doc, None)
            return {"ok": True, "accion": "visita_creada", "respuesta": "✅ Tu visita fue creada exitosamente. Gracias por confiar en Previmed.", "detalle": {"visita": visita}}

        return {"ok": True, "accion": "esperando_confirmacion", "respuesta": "¿Deseas que cree la visita con esos datos?", "detalle": {}}

    # ========= INFORMACIÓN =========
    completion = cliente_openai.chat.completions.create(
        model="gpt-4.1-mini",
        messages=[
            {"role": "system", "content": f"Eres el asistente institucional de Previmed. Usa este contexto:\n{contexto_prevemed}"},
            {"role": "user", "content": mensaje.texto}
        ]
    )
    return {"ok": True, "accion": "info", "respuesta": completion.choices[0].message.content, "detalle": {}}


@app.get("/")
def inicio():
    return {"status": "ok", "mensaje": "Asistente IA operativo"}

@app.get("/health")
def health():
    return {"ok": True, "status": "running"}
