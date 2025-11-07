from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from openai import OpenAI
from dotenv import load_dotenv
from fastapi.middleware.cors import CORSMiddleware
import os, httpx, json, re
from datetime import datetime
from contexto import contexto_prevemed

load_dotenv()

# ===============================
# 🔧 Configuración
# ===============================
CLAVE_OPENAI = os.getenv("OPENAI_API_KEY")
BACKEND_URL = os.getenv("BACKEND_URL", "https://previmedbackend-q73n.onrender.com")

app = FastAPI(title="Asistente IA Previmed")
cliente_openai = OpenAI(api_key=CLAVE_OPENAI)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ===============================
# 🧠 Memoria de conversación
# ===============================
conversaciones = {}
estado_usuario = {}

# ===============================
# 📥 Modelos
# ===============================
class MensajeEntrada(BaseModel):
    texto: str
    documento: str | None = None
    historial: list | None = None

# ===============================
# 🔎 Detección automática
# ===============================
tel_regex = re.compile(r"(?<!\d)(\+?57)?\s*(3\d{9}|\d{7,10})(?!\d)")
addr_regex = re.compile(r"\b(cra|cr|carrera|cll|calle|av|avenida)\b|\b#\b", re.IGNORECASE)

def detectar_telefono(texto):
    m = tel_regex.search(texto.replace(" ", ""))
    return m.group(0).replace("+57", "") if m else None

def detectar_direccion(texto):
    if addr_regex.search(texto) and any(c.isdigit() for c in texto):
        return texto.strip()
    return None

def detectar_motivo(texto):
    if "me duele" in texto.lower() or any(p in texto.lower() for p in ["dolor", "fiebre", "tos", "mareo", "vómito"]):
        return texto.strip()
    return None

def detectar_nombre(texto):
    limpio = re.sub(r"[^a-zA-ZáéíóúÁÉÍÓÚñÑ\s]", "", texto).strip()
    if len(limpio.split()) >= 2:
        return limpio
    return None

# ===============================
# 🌐 Funciones de API externa
# ===============================
async def verificar_membresia(documento: str):
    try:
        async with httpx.AsyncClient(timeout=10) as cliente:
            r = await cliente.get(f"{BACKEND_URL}/membresias/activa/{documento}")
            return r.json()
    except Exception as e:
        print("❌ Error en membresía:", e)
        return {"ok": False}

async def get_medicos():
    try:
        async with httpx.AsyncClient(timeout=10) as cliente:
            r = await cliente.get(f"{BACKEND_URL}/medicos/")
            data = r.json()
            return [m for m in data.get("data", []) if m.get("estado") and m.get("disponibilidad")]
    except Exception:
        return []

async def get_barrios():
    try:
        async with httpx.AsyncClient(timeout=10) as cliente:
            r = await cliente.get(f"{BACKEND_URL}/barrios")
            data = r.json()
            return [b for b in data.get("msj", []) if b.get("estado")]
    except Exception:
        return []

async def crear_visita(paciente_id, medico_id, descripcion, direccion, telefono, barrio_id):
    try:
        async with httpx.AsyncClient(timeout=40) as cliente:
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
            print("📤 Enviando visita:", payload)
            r = await cliente.post(f"{BACKEND_URL}/visitas", json=payload)
            data = r.json()
            print("📥 Respuesta backend:", data)
            return {"ok": 200 <= r.status_code < 300, "status": r.status_code, "data": data}
    except Exception as e:
        print("❌ Error creando visita:", e)
        return {"ok": False, "mensaje": str(e)}

# ===============================
# 💬 Chat principal
# ===============================
@app.post("/chat")
async def chat(m: MensajeEntrada):
    texto = m.texto.strip()
    doc = m.documento or "default"

    if not texto:
        raise HTTPException(400, "Texto vacío")

    if doc not in estado_usuario:
        estado_usuario[doc] = {
            "nombre": None, "telefono": None, "direccion": None, "motivo": None,
            "barrio_nombre": None, "barrio_id": None, "medico_nombre": None, "medico_id": None,
            "paciente_id": None, "barrios_cache": [], "medicos_cache": []
        }

    estado = estado_usuario[doc]
    t_low = texto.lower()

    # detección automática
    if not estado["telefono"]:
        tel = detectar_telefono(texto)
        if tel: estado["telefono"] = tel
    if not estado["direccion"]:
        d = detectar_direccion(texto)
        if d: estado["direccion"] = d
    if not estado["motivo"]:
        mot = detectar_motivo(texto)
        if mot: estado["motivo"] = mot
    if not estado["nombre"]:
        nom = detectar_nombre(texto)
        if nom: estado["nombre"] = nom

    # actualizar barrio/médico por texto
    if estado["barrios_cache"] and not estado["barrio_id"]:
        for b in estado["barrios_cache"]:
            if b["nombreBarrio"].lower() in t_low:
                estado["barrio_id"] = b["idBarrio"]
                estado["barrio_nombre"] = b["nombreBarrio"]
                break

    if estado["medicos_cache"] and not estado["medico_id"]:
        for med in estado["medicos_cache"]:
            if med["usuario"]["nombre"].lower() in t_low:
                estado["medico_id"] = med["id_medico"]
                estado["medico_nombre"] = f"{med['usuario']['nombre']} {med['usuario']['apellido']}"
                break

    # flujo principal
    if not estado["paciente_id"]:
        membresia = await verificar_membresia(m.documento)
        if not membresia.get("ok"):
            return {"ok": False, "accion": "sin_membresia", "respuesta": "No tienes una membresía activa. ¿Deseas renovarla?"}
        estado["paciente_id"] = membresia["paciente"]["id_paciente"]
        return {"ok": True, "accion": "confirmar_membresia", "respuesta": "Tu membresía está activa ✅. ¿Deseas agendar la visita?"}

    if not estado["motivo"]:
        return {"ok": True, "accion": "pedir_motivo", "respuesta": "¿Cuál es el motivo de tu consulta o visita médica?"}

    if not estado["direccion"]:
        return {"ok": True, "accion": "pedir_direccion", "respuesta": "Por favor indícame la dirección donde deseas recibir la atención médica."}

    if not estado["telefono"]:
        return {"ok": True, "accion": "pedir_telefono", "respuesta": "¿Podrías darme un número de contacto, por favor?"}

    if not estado["barrio_id"]:
        barrios = await get_barrios()
        estado["barrios_cache"] = barrios
        nombres = [b["nombreBarrio"] for b in barrios]
        return {"ok": True, "accion": "elegir_barrio", "respuesta": f"¿En qué barrio estás? {', '.join(nombres)}", "detalle": {"barrios": nombres}}

    if not estado["medico_id"]:
        medicos = await get_medicos()
        estado["medicos_cache"] = medicos
        nombres = [f"{m['usuario']['nombre']} {m['usuario']['apellido']}" for m in medicos]
        return {"ok": True, "accion": "elegir_medico", "respuesta": f"Estos son los médicos disponibles: {', '.join(nombres)}", "detalle": {"medicos": nombres}}

    # crear visita
    visita = await crear_visita(
        estado["paciente_id"], estado["medico_id"],
        estado["motivo"], estado["direccion"], estado["telefono"], estado["barrio_id"]
    )

    if visita.get("ok") and visita.get("status") in [200, 201]:
        id_visita = visita.get("data", {}).get("data", {}).get("idVisita")
        estado_usuario.pop(doc, None)
        conversaciones.pop(doc, None)
        return {
            "ok": True,
            "accion": "visita_creada",
            "respuesta": f"✅ Tu visita fue registrada correctamente con {estado['medico_nombre']}. ID: {id_visita}. ¡Gracias por confiar en Previmed!",
        }

    return {
        "ok": False,
        "accion": "error_crear",
        "respuesta": "⚠️ Ocurrió un problema al registrar la visita. Por favor intenta nuevamente.",
    }

# ===============================
# 🩺 Endpoints de control
# ===============================
@app.get("/")
def root():
    return {"status": "ok", "mensaje": "Asistente IA operativo"}

@app.get("/health")
def health():
    return {"ok": True, "status": "running"}
