from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from openai import OpenAI
from dotenv import load_dotenv
from fastapi.middleware.cors import CORSMiddleware
import os, httpx, json, re
from datetime import datetime
from contexto import contexto_prevemed

# Asegurar logs inmediatos en Render
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
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ===============================
# 🧠 Memorias en servidor
# ===============================
# - conversaciones: historial breve de mensajes para el modelo
# - estado_usuario: datos estructurados confirmados (lo que “ya tenemos”)
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
# 🛠️ Utilidades de extracción de datos
# ===============================
tel_regex = re.compile(r"(?<!\d)(\+?57)?\s*(3\d{9}|\d{7,10})(?!\d)")
addr_regex = re.compile(r"\b(cra|cr|cra\.|carrera|cll|calle|av|avenida)\b|\b#\b", re.IGNORECASE)
motivo_palabras = [
    "dolor", "fiebre", "tos", "gripe", "mareo", "náusea", "nausea", "vomito", "vómito",
    "cansancio", "fractura", "golpe", "resfriado", "migraña", "diarrea", "presión",
    "hipertensión", "hipotensión", "asma", "alergia", "infección"
]

def posible_telefono(texto: str) -> str | None:
    m = tel_regex.search(texto.replace(" ", ""))
    if m:
        num = m.group(0)
        # Normaliza ejemplos como +57XXXXXXXXXX
        num = num.replace("+57", "")
        return num
    return None

def posible_direccion(texto: str) -> str | None:
    if addr_regex.search(texto) and any(c.isdigit() for c in texto):
        # Heurística simple: si parece dirección, devolvemos el texto tal cual
        return texto.strip()
    return None

def posible_motivo(texto: str) -> str | None:
    t = texto.lower()
    if any(p in t for p in motivo_palabras):
        return texto.strip()
    # frases comunes
    if "me duele" in t or "consulta" in t or "revisión" in t or "revision" in t or "visita" in t:
        return texto.strip()
    return None

def posible_nombre(texto: str) -> str | None:
    # Si el usuario escribe "nombre apellido" sin otros símbolos
    limpio = re.sub(r"[^a-zA-ZáéíóúÁÉÍÓÚñÑ\s]", "", texto).strip()
    if len(limpio.split()) >= 2 and len(limpio) <= 60:
        return limpio
    return None


# ===============================
# 🌐 Clientes a endpoints reales
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
# 🤖 Orquestador con estado
# ===============================
@app.post("/chat")
async def chat(mensaje: MensajeEntrada):
    texto = mensaje.texto.strip()
    doc = mensaje.documento or "default"

    if not texto:
        raise HTTPException(400, "Texto vacío")

    # Inicializa historial conversacional
    historial = conversaciones.get(doc, [])
    # Inicializa estado estructurado
    if doc not in estado_usuario:
        estado_usuario[doc] = {
            "nombre": None,
            "telefono": None,
            "direccion": None,
            "motivo": None,
            "barrio_nombre": None,
            "barrio_id": None,
            "medico_nombre": None,
            "medico_id": None,
            "paciente_id": None,
            "medicos_cache": [],  # lista de médicos disponibles
            "barrios_cache": []   # lista de barrios activos
        }
    estado = estado_usuario[doc]

    # 0) Pre-extracción automática desde el mensaje del usuario
    #    (así el modelo ya recibe el estado actualizado y no repite preguntas)
    t_low = texto.lower()

    # teléfono
    tel = posible_telefono(texto)
    if tel and not estado["telefono"]:
        estado["telefono"] = tel

    # dirección
    dire = posible_direccion(texto)
    if dire and not estado["direccion"]:
        estado["direccion"] = dire

    # motivo
    mot = posible_motivo(texto)
    if mot and not estado["motivo"]:
        estado["motivo"] = mot

    # nombre
    nom = posible_nombre(texto)
    if nom and not estado["nombre"]:
        estado["nombre"] = nom

    # selección de médico por nombre existente en cache
    if estado["medicos_cache"] and not estado["medico_id"]:
        elegido = None
        for m in estado["medicos_cache"]:
            nombre_completo = f"{m['usuario']['nombre']} {m['usuario']['apellido']}".lower()
            if m["usuario"]["nombre"].lower() in t_low or nombre_completo in t_low:
                elegido = m
                break
        if elegido:
            estado["medico_id"] = elegido["id_medico"]
            estado["medico_nombre"] = f"{elegido['usuario']['nombre']} {elegido['usuario']['apellido']}"

    # selección de barrio por nombre existente en cache
    if estado["barrios_cache"] and not estado["barrio_id"]:
        elegido_b = None
        for b in estado["barrios_cache"]:
            if b["nombreBarrio"].lower() in t_low:
                elegido_b = b
                break
        if elegido_b:
            estado["barrio_id"] = elegido_b["idBarrio"]
            estado["barrio_nombre"] = elegido_b["nombreBarrio"]

    # 1) Construimos prompt del sistema con CONTEXTO + ESTADO
    system_prompt = {
        "role": "system",
        "content": (
            "Eres el asistente institucional y médico de Previmed. "
            "Siempre responde en JSON válido con las claves: 'accion', 'respuesta' y opcionalmente 'detalle'.\n\n"
            "Acciones posibles:\n"
            "- 'info': responder información general usando el contexto institucional.\n"
            "- 'verificar_membresia': cuando necesites revisar una membresía activa.\n"
            "- 'listar_medicos': cuando necesites mostrar médicos disponibles.\n"
            "- 'listar_barrios': cuando necesites mostrar barrios activos.\n"
            "- 'pedir_dato': si falta algún dato (nombre, teléfono, dirección, motivo, médico, barrio).\n"
            "- 'confirmar_datos': cuando ya tengas casi todo, pide confirmación para crear la visita.\n"
            "- 'crear_visita': cuando tengas TODOS los campos para crear la visita (paciente_id, medico_id, barrio_id, telefono, direccion, motivo).\n\n"
            "Estado actual del usuario (lo que el sistema ya tiene guardado):\n"
            f"{json.dumps(estado, ensure_ascii=False)}\n\n"
            "Contexto institucional (para 'info'):\n"
            f"{contexto_prevemed}\n\n"
            "IMPORTANTE:\n"
            "- No repitas datos ya confirmados en 'pedir_dato'. Pide solo lo que falta.\n"
            "- Si no hay 'paciente_id', solicita 'verificar_membresia'.\n"
            "- Para 'crear_visita' deben estar presentes: paciente_id, medico_id, barrio_id, telefono, direccion, motivo.\n"
            "- 'detalle' puede incluir sugerencias como { 'faltan': ['telefono', 'barrio'] } o listas para el usuario.\n"
        ),
    }

    mensajes = [system_prompt, *historial, {"role": "user", "content": texto}]

    # 2) El modelo decide qué hacer
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
        data = {"accion": "info", "respuesta": "¿En qué puedo ayudarte con Previmed?", "detalle": {}}

    accion = data.get("accion", "info")
    respuesta_texto = data.get("respuesta", "¿En qué puedo ayudarte con Previmed?")
    detalle = data.get("detalle", {}) if isinstance(data.get("detalle", {}), dict) else {}

    # 3) Ejecutar acciones solicitadas por el modelo usando ENDPOINTS REALES
    try:
        # Verificar membresía
        if accion == "verificar_membresia":
            if not mensaje.documento:
                respuesta_texto = "Por favor indícame tu número de cédula para verificar tu membresía."
            else:
                res = await verificar_membresia_activa(mensaje.documento)
                print("📄 Membresía:", res)
                detalle["membresia"] = res
                if res.get("ok"):
                    estado["paciente_id"] = res["paciente"]["id_paciente"]
                    respuesta_texto = "Tu membresía está activa ✅. ¿Continuamos con tu solicitud de visita?"
                else:
                    respuesta_texto = "No encontré una membresía activa. ¿Deseas renovarla?"

        # Listar médicos disponibles
        elif accion == "listar_medicos":
            medicos = await get_medicos()
            if isinstance(medicos, list) and medicos:
                estado["medicos_cache"] = medicos
                nombres = [f"{m['usuario']['nombre']} {m['usuario']['apellido']}" for m in medicos]
                detalle["medicos"] = nombres
                if not estado["medico_id"]:
                    respuesta_texto = "Selecciona un médico disponible: " + ", ".join(nombres)
            else:
                respuesta_texto = "No hay médicos disponibles en este momento."

        # Listar barrios activos
        elif accion == "listar_barrios":
            barrios = await get_barrios()
            if isinstance(barrios, list) and barrios:
                estado["barrios_cache"] = barrios
                nombres = [b["nombreBarrio"] for b in barrios]
                detalle["barrios"] = nombres
                if not estado["barrio_id"]:
                    respuesta_texto = "¿En qué barrio estás? Barrios disponibles: " + ", ".join(nombres)
            else:
                respuesta_texto = "No hay barrios activos en este momento."

        # Confirmar datos antes de crear
        elif accion == "confirmar_datos":
            faltan = []
            if not estado["paciente_id"]:
                faltan.append("paciente_id (verificar membresía)")
            if not estado["medico_id"]:
                faltan.append("medico")
            if not estado["barrio_id"]:
                faltan.append("barrio")
            if not estado["telefono"]:
                faltan.append("telefono")
            if not estado["direccion"]:
                faltan.append("direccion")
            if not estado["motivo"]:
                faltan.append("motivo")

            if faltan:
                detalle["faltan"] = faltan
                respuesta_texto = "Aún faltan datos: " + ", ".join(faltan)
            else:
                resumen = (
                    f"Motivo: {estado['motivo']} | Dirección: {estado['direccion']} | "
                    f"Barrio: {estado['barrio_nombre']} | Teléfono: {estado['telefono']} | "
                    f"Médico: {estado['medico_nombre']}"
                )
                respuesta_texto = f"Perfecto. Voy a crear la visita con estos datos: {resumen}. ¿Confirmas?"

        # Crear visita
        elif accion == "crear_visita":
            # Validación de campos obligatorios
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
                    # Reset suave del estado para nueva conversación
                    conversaciones.pop(doc, None)
                    estado_usuario.pop(doc, None)
                else:
                    respuesta_texto = "Ocurrió un problema creando la visita. Intenta de nuevo en unos minutos."

        # Acción 'info' no ejecuta endpoints; el modelo responde usando el contexto
        # Acción 'pedir_dato' también queda solo como respuesta del modelo

    except Exception as e:
        print("❌ Error ejecutando acción:", e)
        respuesta_texto = f"Ocurrió un error ejecutando la acción '{accion}'. Intenta nuevamente."

    # 4) Guardar historial conversacional (máximo 10 turnos)
    historial.append({"role": "user", "content": texto})
    historial.append({"role": "assistant", "content": respuesta_texto})
    conversaciones[doc] = historial[-10:]

    # 5) Respuesta normalizada al frontend
    return {
        "ok": True,
        "accion": accion,
        "respuesta": respuesta_texto,
        "detalle": detalle
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
