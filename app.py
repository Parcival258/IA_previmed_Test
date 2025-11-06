from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from openai import OpenAI
from dotenv import load_dotenv
from fastapi.middleware.cors import CORSMiddleware
import os, httpx
from datetime import datetime
from contexto import contexto_prevemed

# ===============================
# 📦 Variables de entorno
# ===============================
load_dotenv()

CLAVE_OPENAI = os.getenv("OPENAI_API_KEY")
BACKEND_URL = os.getenv("BACKEND_URL", "https://previmedbackend-q73n.onrender.com")
ALLOWED_ORIGINS = os.getenv("ALLOWED_ORIGINS", "http://localhost:5173,https://previmed.onrender.com")

# ===============================
# 🚀 Inicializar aplicación
# ===============================
app = FastAPI(title="Asistente IA Previmed")
cliente_openai = OpenAI(api_key=CLAVE_OPENAI)

# ===============================
# 🔓 Configurar CORS
# ===============================
origins = [o.strip() for o in ALLOWED_ORIGINS.split(",") if o.strip()]

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
# 🔧 Funciones auxiliares
# ===============================
async def verificar_membresia_activa(numero_documento: str):
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
    try:
        async with httpx.AsyncClient(timeout=10.0) as cliente:
            resp = await cliente.get(f"{BACKEND_URL}/medicos")
            resp.raise_for_status()
            data = resp.json()
            return [
                m for m in data.get("data", [])
                if m.get("estado") and m.get("disponibilidad")
            ]
    except Exception as e:
        print(f"❌ Error obteniendo médicos: {e}")
        return []

async def get_barrios_activos():
    try:
        async with httpx.AsyncClient(timeout=10.0) as cliente:
            resp = await cliente.get(f"{BACKEND_URL}/barrios")
            resp.raise_for_status()
            data = resp.json()
            return [b for b in data.get("msj", []) if b.get("estado")]
    except Exception as e:
        print(f"❌ Error obteniendo barrios: {e}")
        return []

async def crear_visita(paciente_id: int, medico_id: int, descripcion: str, direccion: str, telefono: str, barrio_id: int):
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
            resp.raise_for_status()
            return resp.json()
    except Exception as e:
        print(f"❌ Error creando visita: {e}")
        return {"ok": False, "mensaje": str(e)}

def reconstruir_contexto(historial):
    contexto = {}
    if not historial:
        return contexto
    for msg in historial:
        texto = (msg.get("text") or "").lower()
        if "membresía está activa" in texto or "membresia está activa" in texto:
            contexto["membresia_verificada"] = True
        if "motivo" in texto:
            contexto["fase"] = "motivo"
    return contexto


# ===============================
# 🧠 Detección de intención
# ===============================
async def detectar_intencion(texto: str):
    try:
        completion = cliente_openai.chat.completions.create(
            model="gpt-4.1-mini",
            messages=[
                {"role": "system", "content": "Clasifica intención: 'visita', 'informacion', 'cancelar' o 'otro'."},
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
    hist = mensaje.historial or []
    contexto = reconstruir_contexto(hist)

    if not texto:
        raise HTTPException(status_code=400, detail="El campo 'texto' no puede estar vacío.")

    intencion = await detectar_intencion(texto)
    print(f"🧭 Intención detectada: {intencion}")

    if intencion == "cancelar":
        conversaciones.pop(doc or "default", None)
        return {"ok": True, "accion": "cancelar", "respuesta": "He cancelado la solicitud."}

    if intencion == "informacion" and not any(p in texto.lower() for p in ["visita","doctor","médico","cita"]):
        completion = cliente_openai.chat.completions.create(
            model="gpt-4.1-mini",
            messages=[
                {"role": "system", "content": f"Eres asistente institucional de Previmed:\n{contexto_prevemed}"},
                {"role": "user", "content": texto},
            ],
        )
        return {"ok": True, "accion": "informacion", "respuesta": completion.choices[0].message.content}

    if intencion == "visita":
        if not doc:
            return {"ok": False, "accion": "solicitar_documento", "respuesta": "¿Podrías darme tu número de cédula?"}

        if "membresia_verificada" not in contexto:
            data = await verificar_membresia_activa(doc)
            if not data.get("ok"):
                return {"ok": False, "accion": "sin_membresia", "respuesta": "No hay membresía activa registrada."}

            contexto["membresia_verificada"] = True
            contexto["paciente_id"] = data["paciente"]["id_paciente"]
            return {"ok": True, "accion": "pedir_motivo", "respuesta": "Tu membresía está activa. ¿Cuál es el motivo de la visita?"}

        if "motivo" not in contexto:
            contexto["motivo"] = texto
            return {"ok": True, "accion": "pedir_direccion", "respuesta": "¿En qué dirección deseas recibir la visita?"}

        if "direccion" not in contexto:
            contexto["direccion"] = texto
            return {"ok": True, "accion": "pedir_telefono", "respuesta": "Por favor, indícame tu número de contacto."}

        if "telefono" not in contexto:
            contexto["telefono"] = texto
            medicos = await get_medicos_disponibles()
            if not medicos:
                return {"ok": False, "accion": "sin_medicos", "respuesta": "No hay médicos disponibles."}
            nombres = ", ".join([f"{m['usuario']['nombre']} {m['usuario']['apellido']}" for m in medicos])
            contexto["medicos_disponibles"] = medicos
            return {"ok": True, "accion": "elegir_medico", "respuesta": f"Disponibles: {nombres}. ¿Con cuál deseas agendar?"}

        if "medico_id" not in contexto:
            elegido = next((m for m in contexto["medicos_disponibles"] if m["usuario"]["nombre"].lower() in texto.lower()), None)
            if not elegido:
                return {"ok": False, "accion": "repetir_medico", "respuesta": "No encontré ese médico. Dime solo el nombre."}
            contexto["medico_id"] = elegido["id_medico"]
            barrios = await get_barrios_activos()
            if not barrios:
                return {"ok": False, "accion": "sin_barrios", "respuesta": "No hay barrios activos disponibles."}
            contexto["barrios_activos"] = barrios
            nombres_barrios = ", ".join([b["nombreBarrio"] for b in barrios])
            return {"ok": True, "accion": "elegir_barrio", "respuesta": f"¿En qué barrio te encuentras? {nombres_barrios}"}

        if "barrio_id" not in contexto:
            elegido = next((b for b in contexto["barrios_activos"] if b["nombreBarrio"].lower() in texto.lower()), None)
            if not elegido:
                return {"ok": False, "accion": "repetir_barrio", "respuesta": "No reconocí ese barrio."}
            contexto["barrio_id"] = elegido["idBarrio"]
            return {"ok": True, "accion": "confirmar", "respuesta": f"Confirmo: visita por '{contexto['motivo']}' en '{contexto['direccion']}', barrio {elegido['nombreBarrio']}. ¿Confirmas?"}

        if "sí" in texto.lower() or "si" in texto.lower():
            visita = await crear_visita(
                contexto["paciente_id"], contexto["medico_id"], contexto["motivo"],
                contexto["direccion"], contexto["telefono"], contexto["barrio_id"]
            )
            conversaciones.pop(doc, None)
            return {"ok": True, "accion": "visita_creada", "respuesta": "✅ Tu visita fue creada exitosamente."}

        return {"ok": True, "accion": "esperando_confirmacion", "respuesta": "¿Deseas confirmar la visita?"}

    return {"ok": True, "accion": "otro", "respuesta": "¿Podrías aclararme qué necesitas?"}


@app.get("/")
def inicio():
    return {"mensaje": "🤖 Asistente IA Previmed operativo"}
