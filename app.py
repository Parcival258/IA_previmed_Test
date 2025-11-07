from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from openai import OpenAI
from dotenv import load_dotenv
from fastapi.middleware.cors import CORSMiddleware
import os, httpx, json, re
from datetime import datetime
from contexto import contexto_prevemed

# Asegurar que los logs aparezcan en tiempo real en Render
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
# 🔓 CORS (modo desarrollo y prod)
# ===============================
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # durante desarrollo, libre
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ===============================
# 🧠 Memorias en servidor
# ===============================
conversaciones: dict[str, list] = {}
estado_usuario: dict[str, dict] = {}

# ===============================
# 📥 Modelo de entrada
# ===============================
class MensajeEntrada(BaseModel):
    texto: str
    documento: str | None = None
    historial: list | None = None

# ===============================
# 🔍 Utilidades básicas para datos
# ===============================
tel_regex = re.compile(r"(?<!\d)(\+?57)?\s*(3\d{9}|\d{7,10})(?!\d)")
addr_regex = re.compile(r"\b(cra|cr|cra\.|carrera|cll|calle|av|avenida)\b|\b#\b", re.IGNORECASE)
motivo_palabras = [
    "dolor", "fiebre", "tos", "mareo", "náusea", "nausea", "vomito", "vómito",
    "cansancio", "fractura", "golpe", "resfriado", "migraña", "diarrea", "presión",
    "asma", "alergia", "infección"
]

def posible_telefono(texto):
    m = tel_regex.search(texto.replace(" ", ""))
    if m:
        return m.group(0).replace("+57", "")
    return None

def posible_direccion(texto):
    if addr_regex.search(texto) and any(c.isdigit() for c in texto):
        return texto.strip()
    return None

def posible_motivo(texto):
    t = texto.lower()
    if any(p in t for p in motivo_palabras) or "me duele" in t or "consulta" in t:
        return texto.strip()
    return None

def posible_nombre(texto):
    limpio = re.sub(r"[^a-zA-ZáéíóúÁÉÍÓÚñÑ\s]", "", texto).strip()
    if len(limpio.split()) >= 2 and len(limpio) <= 60:
        return limpio
    return None

# ===============================
# 🌐 Endpoints reales de backend
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
            print("📤 POST /visitas payload:", payload)
            resp = await cliente.post(f"{BACKEND_URL}/visitas", json=payload)
            resp.raise_for_status()
            return {"ok": True, "data": resp.json()}
    except Exception as e:
        return {"ok": False, "mensaje": f"Error creando visita: {e}"}

# ===============================
# 💬 Chat inteligente con memoria de estado
# ===============================
@app.post("/chat")
async def chat(mensaje: MensajeEntrada):
    texto = mensaje.texto.strip()
    doc = mensaje.documento or "default"

    if not texto:
        raise HTTPException(400, "Texto vacío")

    historial = conversaciones.get(doc, [])
    if doc not in estado_usuario:
        estado_usuario[doc] = {
            "nombre": None, "telefono": None, "direccion": None, "motivo": None,
            "barrio_nombre": None, "barrio_id": None,
            "medico_nombre": None, "medico_id": None,
            "paciente_id": None, "medicos_cache": [], "barrios_cache": []
        }
    estado = estado_usuario[doc]

    # Detección automática previa
    t_low = texto.lower()
    if not estado["telefono"]:
        tel = posible_telefono(texto)
        if tel: estado["telefono"] = tel
    if not estado["direccion"]:
        dire = posible_direccion(texto)
        if dire: estado["direccion"] = dire
    if not estado["motivo"]:
        mot = posible_motivo(texto)
        if mot: estado["motivo"] = mot
    if not estado["nombre"]:
        nom = posible_nombre(texto)
        if nom: estado["nombre"] = nom

    # Selección de médico/barrio
    if estado["medicos_cache"] and not estado["medico_id"]:
        for m in estado["medicos_cache"]:
            if m["usuario"]["nombre"].lower() in t_low:
                estado["medico_id"] = m["id_medico"]
                estado["medico_nombre"] = f"{m['usuario']['nombre']} {m['usuario']['apellido']}"
    if estado["barrios_cache"] and not estado["barrio_id"]:
        for b in estado["barrios_cache"]:
            if b["nombreBarrio"].lower() in t_low:
                estado["barrio_id"] = b["idBarrio"]
                estado["barrio_nombre"] = b["nombreBarrio"]

    # Prompt reforzado
    estado_json = json.dumps(estado, ensure_ascii=False)
    system_prompt = {
        "role": "system",
        "content": (
            "Eres el asistente institucional y médico de Previmed.\n"
            "SIEMPRE responde en JSON **válido** con las claves: 'accion', 'respuesta' y opcionalmente 'detalle'.\n\n"
            "Acciones permitidas:\n"
            "- 'info': información general (usa el contexto institucional).\n"
            "- 'verificar_membresia': validar membresía.\n"
            "- 'listar_medicos': mostrar médicos activos.\n"
            "- 'listar_barrios': mostrar barrios activos.\n"
            "- 'pedir_dato': solicitar campos faltantes.\n"
            "- 'confirmar_datos': confirmar antes de crear la visita.\n"
            "- 'crear_visita': si ya están todos los campos (paciente_id, medico_id, barrio_id, telefono, direccion, motivo).\n\n"
            "ESTADO ACTUAL:\n"
            f"{estado_json}\n\n"
            "REGLAS:\n"
            "1) Tratar el ESTADO ACTUAL como verdad absoluta. Si un campo no está vacío, no lo pidas.\n"
            "2) Si falta paciente_id, primero 'verificar_membresia'.\n"
            "3) Si faltan algunos datos, usa 'pedir_dato' y en 'detalle' pon 'faltan': ['campo1','campo2'].\n"
            "4) No reinicies conversación ni pidas datos repetidos.\n"
            "5) 'crear_visita' solo si todos los campos requeridos están presentes.\n"
            "6) Responde solo JSON puro, sin texto adicional.\n\n"
            "Contexto institucional:\n"
            f"{contexto_prevemed}\n"
        )
    }

    mensajes = [system_prompt, *historial, {"role": "user", "content": texto}]
    print("🧾 ESTADO ANTES:", estado_json)

    try:
        completion = cliente_openai.chat.completions.create(
            model="gpt-4.1-mini",
            messages=mensajes,
            temperature=0.3,
            max_tokens=500,
        )
        contenido = completion.choices[0].message.content
        print("🤖 Respuesta IA:", contenido)
        data = json.loads(contenido)
    except Exception as e:
        print("⚠️ Error interpretando salida IA:", e)
        data = {"accion": "pedir_dato", "respuesta": "¿Qué te gustaría hacer con Previmed?", "detalle": {}}

    accion = data.get("accion", "info")
    respuesta_texto = data.get("respuesta", "¿En qué puedo ayudarte?")
    detalle = data.get("detalle", {}) if isinstance(data.get("detalle", {}), dict) else {}

    # Ejecutar acciones solicitadas
    try:
        if accion == "verificar_membresia":
            if not mensaje.documento:
                respuesta_texto = "Por favor indícame tu número de cédula."
            else:
                res = await verificar_membresia_activa(mensaje.documento)
                print("📄 Membresía:", res)
                detalle["membresia"] = res
                if res.get("ok"):
                    estado["paciente_id"] = res["paciente"]["id_paciente"]
                    respuesta_texto = "Tu membresía está activa ✅. ¿Deseas agendar la visita?"
                else:
                    respuesta_texto = "No encontré membresía activa. ¿Quieres renovarla?"

        elif accion == "listar_medicos":
            medicos = await get_medicos()
            if isinstance(medicos, list) and medicos:
                estado["medicos_cache"] = medicos
                nombres = [f"{m['usuario']['nombre']} {m['usuario']['apellido']}" for m in medicos]
                detalle["medicos"] = nombres
                respuesta_texto = "Selecciona un médico disponible: " + ", ".join(nombres)
            else:
                respuesta_texto = "No hay médicos disponibles ahora."

        elif accion == "listar_barrios":
            barrios = await get_barrios()
            if isinstance(barrios, list) and barrios:
                estado["barrios_cache"] = barrios
                nombres = [b["nombreBarrio"] for b in barrios]
                detalle["barrios"] = nombres
                respuesta_texto = "¿En qué barrio estás? " + ", ".join(nombres)
            else:
                respuesta_texto = "No hay barrios activos en este momento."

        elif accion == "crear_visita":
            oblig = ["paciente_id", "medico_id", "barrio_id", "telefono", "direccion", "motivo"]
            faltan = [c for c in oblig if not estado.get(c)]
            if faltan:
                detalle["faltan"] = faltan
                respuesta_texto = "Faltan datos para crear la visita: " + ", ".join(faltan)
            else:
                visita = await crear_visita(
                    paciente_id=estado["paciente_id"],
                    medico_id=estado["medico_id"],
                    descripcion=estado["motivo"],
                    direccion=estado["direccion"],
                    telefono=estado["telefono"],
                    barrio_id=estado["barrio_id"]
                )
                detalle["visita"] = visita
                if visita.get("ok"):
                    respuesta_texto = "✅ Tu visita fue creada exitosamente. Gracias por confiar en Previmed."
                    conversaciones.pop(doc, None)
                    estado_usuario.pop(doc, None)
                else:
                    respuesta_texto = "Ocurrió un problema creando la visita."

    except Exception as e:
        print("❌ Error ejecutando acción:", e)
        respuesta_texto = f"Error en acción '{accion}'."

    # Guardar historial
    historial.append({"role": "user", "content": texto})
    historial.append({"role": "assistant", "content": respuesta_texto})
    conversaciones[doc] = historial[-10:]

    print("🎯 IA ACCION:", accion, "| FALTAN:", detalle.get("faltan"))
    return {"ok": True, "accion": accion, "respuesta": respuesta_texto, "detalle": detalle}

# ===============================
# 🩺 Rutas de control
# ===============================
@app.get("/")
def root():
    return {"status": "ok", "mensaje": "Asistente IA operativo"}

@app.get("/health")
def health():
    return {"ok": True, "status": "running"}
