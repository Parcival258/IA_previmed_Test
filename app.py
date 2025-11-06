# app.py
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from openai import OpenAI
from dotenv import load_dotenv
from fastapi.middleware.cors import CORSMiddleware
import os, httpx, unicodedata
from datetime import datetime
from contexto import contexto_prevemed

# ===============================
# 📦 Entorno
# ===============================
load_dotenv()
CLAVE_OPENAI = os.getenv("OPENAI_API_KEY")
BACKEND_URL = os.getenv("BACKEND_URL", "https://previmedbackend-q73n.onrender.com")
ALLOWED_ORIGINS = os.getenv("ALLOWED_ORIGINS", "http://localhost:5173,https://previmed.onrender.com")

# ===============================
# 🚀 App
# ===============================
app = FastAPI(title="Asistente IA Previmed")
cliente_openai = OpenAI(api_key=CLAVE_OPENAI)

# ===============================
# 🔓 CORS
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
# 🧠 Memoria (servidor)
# ===============================
conversaciones = {}

# ===============================
# 📥 Modelo
# ===============================
class MensajeEntrada(BaseModel):
    texto: str
    documento: str | None = None
    historial: list | None = None

# ===============================
# 🔧 Utilidades
# ===============================
def normalize(s: str) -> str:
    if not s:
        return ""
    s = s.lower().strip()
    s = "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")
    return s

def tiene_keyword_visita(texto: str) -> bool:
    t = normalize(texto)
    keywords = ["visita", "cita", "medico", "médico", "doctor", "agendar", "domicilio", "domiciliaria"]
    return any(k in t for k in keywords)

def reconstruir_contexto(historial: list | None) -> dict:
    contexto = {}
    if not historial:
        return contexto
    for msg in historial:
        if not isinstance(msg, dict):  # por si viene sucio
            continue
        role = msg.get("role")
        text = normalize(msg.get("text") or "")
        if role == "assistant":
            if "membresia esta activa" in text or "membresia está activa" in text:
                contexto["membresia_verificada"] = True
            if "motivo" in text:
                contexto["fase"] = "motivo"
            if "direccion" in text:
                contexto["fase"] = "direccion"
            if "telefono" in text:
                contexto["fase"] = "telefono"
    return contexto

# ===============================
# 🧩 Endpoints auxiliares (backend médico)
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
    try:
        async with httpx.AsyncClient(timeout=10.0) as cliente:
            resp = await cliente.get(f"{BACKEND_URL}/barrios")
            resp.raise_for_status()
            data = resp.json()
            barrios = [b for b in data.get("msj", []) if b.get("estado")]
            print(f"🏙️ Barrios activos: {len(barrios)}")
            return barrios
    except Exception as e:
        print(f"❌ Error obteniendo barrios: {e}")
        return []

async def crear_visita(paciente_id: int, medico_id: int, descripcion: str,
                       direccion: str, telefono: str, barrio_id: int):
    try:
        fecha_actual = datetime.now().isoformat()
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
        async with httpx.AsyncClient(timeout=10.0) as cliente:
            resp = await cliente.post(f"{BACKEND_URL}/visitas", json=payload)
            resp.raise_for_status()
            return resp.json()
    except Exception as e:
        print(f"❌ Error creando visita: {e}")
        return {"ok": False, "mensaje": str(e)}

# ===============================
# 🧠 Detección de intención (híbrida)
# ===============================
async def detectar_intencion(texto: str) -> str:
    # Regla directa primero (robusta a acentos)
    if tiene_keyword_visita(texto):
        return "visita"
    # Si no cae por reglas, pedimos a GPT como apoyo
    try:
        completion = cliente_openai.chat.completions.create(
            model="gpt-4.1-mini",
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Eres un clasificador de intención. "
                        "Responde SOLO con una palabra: visita, informacion, cancelar u otro."
                    ),
                },
                {"role": "user", "content": texto},
            ],
        )
        intent = normalize(completion.choices[0].message.content)
        if "visita" in intent:
            return "visita"
        if "informacion" in intent:
            return "informacion"
        if "cancelar" in intent:
            return "cancelar"
        return "otro"
    except Exception as e:
        print(f"❌ Error detectando intención (GPT): {e}")
        # Fallback final por reglas:
        return "visita" if tiene_keyword_visita(texto) else "otro"

# ===============================
# 💬 Endpoint de chat
# ===============================
@app.post("/chat")
async def responder(mensaje: MensajeEntrada):
    texto = (mensaje.texto or "").strip()
    doc = mensaje.documento
    hist = mensaje.historial or []

    if not texto:
        raise HTTPException(status_code=400, detail="El campo 'texto' no puede estar vacío.")

    print(f"📨 Ingreso: doc={doc} | texto='{texto}' | historial={len(hist)} mensajes")
    contexto = reconstruir_contexto(hist)
    print(f"📜 Contexto reconstruido: {contexto}")

    intencion = await detectar_intencion(texto)
    print(f"🧭 Intención detectada: {intencion}")

    # ─────────── Cancelar
    if intencion == "cancelar":
        conversaciones.pop(doc or "default", None)
        return {"ok": True, "accion": "cancelar", "respuesta": "He cancelado la solicitud. ¿Necesitas algo más?"}

    # ─────────── Información general (no visita)
    if intencion == "informacion" and not tiene_keyword_visita(texto):
        try:
            completion = cliente_openai.chat.completions.create(
                model="gpt-4.1-mini",
                messages=[
                    {"role": "system", "content": f"Eres el asistente institucional de Previmed. Responde con claridad usando este contexto:\n\n{contexto_prevemed}"},
                    {"role": "user", "content": texto},
                ],
            )
            return {"ok": True, "accion": "informacion", "respuesta": completion.choices[0].message.content}
        except Exception as e:
            print(f"❌ Error respondiendo informacion: {e}")
            return {"ok": True, "accion": "informacion", "respuesta": "Con gusto te doy información. ¿Qué te gustaría saber sobre Previmed?"}

    # ─────────── Flujo de visita (forzado por reglas si hay keywords)
    if intencion == "visita" or tiene_keyword_visita(texto):
        # 1) Documento
        if not doc:
            return {"ok": False, "accion": "solicitar_documento", "respuesta": "¿Podrías indicarme tu número de cédula para verificar tu membresía activa?"}

        # 2) Verificar membresía
        if "membresia_verificada" not in contexto:
            data = await verificar_membresia_activa(doc)
            print("🧾 Resultado membresía:", data)
            if not data.get("ok"):
                return {"ok": False, "accion": "sin_membresia", "respuesta": "No encuentro una membresía activa con ese documento. ¿Deseas que te ayude a renovarla?"}
            contexto["membresia_verificada"] = True
            contexto["paciente_id"] = data["paciente"]["id_paciente"]
            return {"ok": True, "accion": "pedir_motivo", "respuesta": "Perfecto, tu membresía está activa. ¿Cuál es el motivo de la visita?"}

        # 3) Motivo
        if "motivo" not in contexto:
            contexto["motivo"] = texto
            return {"ok": True, "accion": "pedir_direccion", "respuesta": "¿En qué dirección deseas recibir la visita?"}

        # 4) Dirección
        if "direccion" not in contexto:
            contexto["direccion"] = texto
            return {"ok": True, "accion": "pedir_telefono", "respuesta": "Por favor, indícame un número de contacto."}

        # 5) Teléfono → médicos
        if "telefono" not in contexto:
            contexto["telefono"] = texto
            medicos = await get_medicos_disponibles()
            if not medicos:
                return {"ok": False, "accion": "sin_medicos", "respuesta": "No hay médicos disponibles en este momento."}
            contexto["medicos_disponibles"] = medicos
            nombres = ", ".join([f"{m['usuario']['nombre']} {m['usuario']['apellido']}" for m in medicos])
            return {"ok": True, "accion": "elegir_medico", "respuesta": f"Disponibles: {nombres}. ¿Con cuál deseas agendar?"}

        # 6) Elegir médico
        if "medico_id" not in contexto:
            medicos = contexto.get("medicos_disponibles", [])
            t = normalize(texto)
            elegido = next((m for m in medicos if normalize(m["usuario"]["nombre"]) in t or normalize(f"{m['usuario']['nombre']} {m['usuario']['apellido']}") in t), None)
            if not elegido:
                return {"ok": False, "accion": "repetir_medico", "respuesta": "No logré identificar el médico. Dime solo su nombre (ej. Samanta)."}
            contexto["medico_id"] = elegido["id_medico"]
            barrios = await get_barrios_activos()
            if not barrios:
                return {"ok": False, "accion": "sin_barrios", "respuesta": "No hay barrios activos disponibles. Contacta soporte."}
            contexto["barrios_activos"] = barrios
            nombres_barrios = ", ".join([b["nombreBarrio"] for b in barrios])
            return {"ok": True, "accion": "elegir_barrio", "respuesta": f"¿En qué barrio te encuentras? Barrios disponibles: {nombres_barrios}."}

        # 7) Elegir barrio
        if "barrio_id" not in contexto:
            barrios = contexto.get("barrios_activos", [])
            t = normalize(texto)
            elegido = next((b for b in barrios if normalize(b["nombreBarrio"]) in t), None)
            if not elegido:
                return {"ok": False, "accion": "repetir_barrio", "respuesta": "No logré identificar el barrio. Escribe solo el nombre (ej. Modelo)."}
            contexto["barrio_id"] = elegido["idBarrio"]
            return {"ok": True, "accion": "confirmar", "respuesta": f"Confirmo: visita por '{contexto['motivo']}' en '{contexto['direccion']}', barrio {elegido['nombreBarrio']}. ¿Deseas confirmarla?"}

        # 8) Confirmar → crear
        if "si" in normalize(texto) or "sí" in texto.lower():
            visita = await crear_visita(
                paciente_id=contexto["paciente_id"],
                medico_id=contexto["medico_id"],
                descripcion=contexto["motivo"],
                direccion=contexto["direccion"],
                telefono=contexto["telefono"],
                barrio_id=contexto["barrio_id"],
            )
            print("✅ Resultado creación visita:", visita)
            conversaciones.pop(doc, None)
            return {"ok": True, "accion": "visita_creada", "respuesta": "Tu visita fue creada exitosamente. Te confirmaremos por correo."}

        return {"ok": True, "accion": "esperando_confirmacion", "respuesta": "¿Deseas que cree la visita con esos datos?"}

    # ─────────── Conversación genérica
    try:
        completion = cliente_openai.chat.completions.create(
            model="gpt-4.1-mini",
            messages=[
                {"role": "system", "content": f"Eres un asistente empático de Previmed. Usa este contexto:\n\n{contexto_prevemed}"},
                {"role": "user", "content": texto},
            ],
        )
        return {"ok": True, "accion": "otro", "respuesta": completion.choices[0].message.content}
    except Exception as e:
        print(f"❌ Error en respuesta genérica: {e}")
        return {"ok": True, "accion": "otro", "respuesta": "Puedo ayudarte con información o agendar una visita. ¿Qué necesitas exactamente?"}

# ===============================
# 🌐 Salud
# ===============================
@app.get("/")
def inicio():
    return {"mensaje": "🤖 Asistente IA Previmed operativo"}
