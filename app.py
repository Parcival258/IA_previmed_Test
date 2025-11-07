from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from openai import OpenAI
from dotenv import load_dotenv
from fastapi.middleware.cors import CORSMiddleware
import os, httpx, json, re
from datetime import datetime
from contexto import contexto_prevemed

# Mostrar logs en tiempo real (Render)
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
# 🔓 CORS (libre para desarrollo)
# ===============================
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
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
# 🔍 Utilidades para extraer datos
# ===============================
tel_regex = re.compile(r"(?<!\d)(\+?57)?\s*(3\d{9}|\d{7,10})(?!\d)")
addr_regex = re.compile(r"\b(cra|cr|cra\.|carrera|cll|calle|av|avenida)\b|\b#\b", re.IGNORECASE)
motivo_palabras = [
    "dolor", "fiebre", "tos", "mareo", "náusea", "vomito", "vómito", "fractura", "golpe",
    "resfriado", "migraña", "diarrea", "presión", "asma", "alergia", "infección", "consulta", "revisión"
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
    if any(p in t for p in motivo_palabras) or "me duele" in t:
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
# 💬 Chat inteligente
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

    # Extracción automática
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

    # Detectar campos faltantes
    faltan = []
    if not estado.get("paciente_id"): faltan.append("membresía")
    if not estado.get("nombre"): faltan.append("nombre completo")
    if not estado.get("telefono"): faltan.append("teléfono de contacto")
    if not estado.get("direccion"): faltan.append("dirección")
    if not estado.get("motivo"): faltan.append("motivo de la visita")
    if not estado.get("barrio_id"): faltan.append("barrio")
    if not estado.get("medico_id"): faltan.append("médico")

    faltan_texto = ", ".join(faltan) if faltan else "ninguno"
    estado_json = json.dumps(estado, ensure_ascii=False)

    # Prompt reforzado
    system_prompt = {
        "role": "system",
        "content": (
            "Eres el asistente institucional y médico de Previmed.\n"
            "Responde SIEMPRE en JSON con las claves: 'accion', 'respuesta' y 'detalle'.\n\n"
            "Acciones posibles: info, verificar_membresia, listar_medicos, listar_barrios, pedir_dato, confirmar_datos, crear_visita.\n\n"
            f"ESTADO ACTUAL:\n{estado_json}\n\n"
            f"Campos faltantes detectados: {faltan_texto}\n\n"
            "REGLAS:\n"
            "1️⃣ Usa el estado actual como verdad: no repitas datos ya completos.\n"
            "2️⃣ Si faltan datos, responde con 'accion':'pedir_dato' e indica en 'detalle':{'faltan':[...]}.\n"
            "3️⃣ Si hay varios campos faltantes, pregunta uno a la vez en orden lógico (teléfono → dirección → motivo → barrio → médico).\n"
            "4️⃣ Si ya tienes todos los datos, responde con 'accion':'crear_visita'.\n"
            "5️⃣ Si falta membresía, responde con 'accion':'verificar_membresia'.\n"
            "6️⃣ Nunca digas solo 'proporcione los datos'; especifica siempre cuáles faltan.\n"
            "7️⃣ Mantén un tono empático y conversacional.\n"
            "8️⃣ Devuelve únicamente JSON puro (sin texto adicional).\n\n"
            f"Contexto institucional:\n{contexto_prevemed}\n"
        ),
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

    # Ejecutar acciones reales
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
                respuesta_texto = "No hay barrios activos."

        elif accion == "crear_visita":
            oblig = ["paciente_id", "medico_id", "barrio_id", "telefono", "direccion", "motivo"]
            faltantes = [c for c in oblig if not estado.get(c)]
            if faltantes:
                detalle["faltan"] = faltantes
                respuesta_texto = "Faltan datos: " + ", ".join(faltantes)
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

    historial.append({"role": "user", "content": texto})
    historial.append({"role": "assistant", "content": respuesta_texto})
    conversaciones[doc] = historial[-10:]

    print("🎯 Acción:", accion, "| Faltan:", detalle.get("faltan"))
    return {"ok": True, "accion": accion, "respuesta": respuesta_texto, "detalle": detalle}

# ===============================
# 🩺 Rutas básicas
# ===============================
@app.get("/")
def root():
    return {"status": "ok", "mensaje": "Asistente IA operativo"}

@app.get("/health")
def health():
    return {"ok": True, "status": "running"}
