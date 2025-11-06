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
    except Exception as e:
        print("❌ Error verificando membresía:", e)
        return {"ok": False, "mensaje": str(e)}

async def get_medicos():
    try:
        async with httpx.AsyncClient(timeout=10) as cliente:
            resp = await cliente.get(f"{BACKEND_URL}/medicos")
            resp.raise_for_status()
            data = resp.json()
            return [m for m in data.get("data", []) if m.get("estado") and m.get("disponibilidad")]
    except Exception as e:
        print("❌ Error obteniendo médicos:", e)
        return []

async def get_barrios():
    try:
        async with httpx.AsyncClient(timeout=10) as cliente:
            resp = await cliente.get(f"{BACKEND_URL}/barrios")
            resp.raise_for_status()
            data = resp.json()
            return [b for b in data.get("msj", []) if b.get("estado")]
    except Exception as e:
        print("❌ Error obteniendo barrios:", e)
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
            print("📤 Enviando creación de visita:", payload)
            resp = await cliente.post(f"{BACKEND_URL}/visitas", json=payload)
            resp.raise_for_status()
            return resp.json()
    except Exception as e:
        print("❌ Error creando visita:", e)
        return {"ok": False, "mensaje": str(e)}


# ===============================
# 🧠 Chat principal
# ===============================
@app.post("/chat")
async def chat(mensaje: MensajeEntrada):
    print(f"\n📩 Texto: {mensaje.texto} | Documento: {mensaje.documento}")
    texto = mensaje.texto.lower().strip()
    doc = mensaje.documento or "default"

    if not texto:
        raise HTTPException(400, "Texto vacío")

    contexto = conversaciones.get(doc, {})

    # Detectar intención
    palabras_visita = ["visita", "cita", "médico", "medico", "doctor"]
    if any(p in texto for p in palabras_visita):
        intencion = "visita"
    else:
        intencion = "info"

    print("🧭 Intención:", intencion)

    # ========= FLUJO VISITA =========
    if intencion == "visita":
        # Documento
        if not mensaje.documento:
            return {"ok": False, "accion": "pedir_documento", "respuesta": "¿Podrías darme tu número de cédula para verificar tu membresía?"}

        # Membresía
        if "membresia" not in contexto:
            data = await verificar_membresia_activa(mensaje.documento)
            print("📄 Respuesta membresía:", data)
            if not data.get("ok"):
                return {"ok": False, "accion": "sin_membresia", "respuesta": "No se encontró membresía activa. ¿Deseas renovarla?"}
            contexto["membresia"] = data
            conversaciones[doc] = contexto
            return {"ok": True, "accion": "pedir_motivo", "respuesta": "Perfecto, tu membresía está activa. ¿Cuál es el motivo de la visita?"}

        # Motivo
        if "motivo" not in contexto:
            contexto["motivo"] = texto
            conversaciones[doc] = contexto
            return {"ok": True, "accion": "pedir_direccion", "respuesta": "¿En qué dirección deseas recibir la visita?"}

        # Dirección
        if "direccion" not in contexto:
            contexto["direccion"] = texto
            conversaciones[doc] = contexto
            return {"ok": True, "accion": "pedir_telefono", "respuesta": "Por favor indícame un número de contacto."}

        # Teléfono
        if "telefono" not in contexto:
            contexto["telefono"] = texto
            conversaciones[doc] = contexto
            medicos = await get_medicos()
            if not medicos:
                return {"ok": False, "accion": "sin_medicos", "respuesta": "No hay médicos disponibles en este momento 😔."}
            contexto["medicos"] = medicos
            conversaciones[doc] = contexto
            lista = ", ".join([f"{m['usuario']['nombre']} {m['usuario']['apellido']}" for m in medicos])
            return {"ok": True, "accion": "elegir_medico", "respuesta": f"Los médicos disponibles son: {lista}. ¿Con cuál deseas agendar?"}

        # Médico
        if "medico" not in contexto:
            elegido = next((m for m in contexto["medicos"] if m["usuario"]["nombre"].lower() in texto), None)
            if not elegido:
                return {"ok": False, "accion": "repetir_medico", "respuesta": "No logré identificar el médico. Dime solo su nombre."}
            contexto["medico"] = elegido
            barrios = await get_barrios()
            contexto["barrios"] = barrios
            conversaciones[doc] = contexto
            lista = ", ".join([b["nombreBarrio"] for b in barrios])
            return {"ok": True, "accion": "elegir_barrio", "respuesta": f"¿En qué barrio estás? Barrios disponibles: {lista}."}

        # Barrio
        if "barrio" not in contexto:
            elegido = next((b for b in contexto["barrios"] if b["nombreBarrio"].lower() in texto), None)
            if not elegido:
                return {"ok": False, "accion": "repetir_barrio", "respuesta": "No logré identificar el barrio. Dime solo el nombre."}
            contexto["barrio"] = elegido
            conversaciones[doc] = contexto
            return {"ok": True, "accion": "confirmar", "respuesta": f"Confirmo visita por '{contexto['motivo']}' en '{contexto['direccion']}', barrio {elegido['nombreBarrio']}. ¿Deseas agendarla?"}

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
            return {"ok": True, "accion": "visita_creada", "respuesta": "✅ Tu visita fue creada exitosamente."}

        return {"ok": True, "accion": "esperando_confirmacion", "respuesta": "¿Deseas que cree la visita con esos datos?"}

    # ========= INFORMACIÓN =========
    completion = cliente_openai.chat.completions.create(
        model="gpt-4.1-mini",
        messages=[
            {"role": "system", "content": f"Eres el asistente institucional de Previmed. Usa este contexto:\n{contexto_prevemed}"},
            {"role": "user", "content": mensaje.texto}
        ]
    )
    return {"ok": True, "accion": "info", "respuesta": completion.choices[0].message.content}


@app.get("/")
def inicio():
    return {"status": "ok", "mensaje": "Asistente IA operativo"}
