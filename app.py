from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from openai import OpenAI
from dotenv import load_dotenv
from fastapi.middleware.cors import CORSMiddleware
import os, httpx, json, re
from datetime import datetime
from contexto import contexto_prevemed

import sys
sys.stdout.reconfigure(line_buffering=True)

load_dotenv()

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
# Memorias
# ===============================
conversaciones: dict[str, list] = {}
estado_usuario: dict[str, dict] = {}

# ===============================
# Modelos
# ===============================
class MensajeEntrada(BaseModel):
    texto: str
    documento: str | None = None
    historial: list | None = None

# ===============================
# Utilidades
# ===============================
tel_regex = re.compile(r"(?<!\d)(\+?57)?\s*(3\d{9}|\d{7,10})(?!\d)")
addr_regex = re.compile(r"\b(cra|cr|cra\.|carrera|cll|calle|av|avenida)\b|\b#\b", re.IGNORECASE)
motivo_palabras = [
    "dolor", "fiebre", "tos", "mareo", "náusea", "vomito", "vómito", "fractura",
    "golpe", "resfriado", "migraña", "diarrea", "presión", "asma", "alergia",
    "infección", "consulta", "revisión"
]

def posible_telefono(texto):
    m = tel_regex.search(texto.replace(" ", ""))
    return m.group(0).replace("+57", "") if m else None

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
# Endpoints reales
# ===============================
async def verificar_membresia_activa(documento: str):
    try:
        async with httpx.AsyncClient(timeout=10) as cliente:
            resp = await cliente.get(f"{BACKEND_URL}/membresias/activa/{documento}")
            resp.raise_for_status()
            return resp.json()
    except Exception as e:
        print("❌ Error verificando membresía:", e)
        return {"ok": False, "mensaje": str(e)}

async def get_medicos():
    try:
        async with httpx.AsyncClient(timeout=10) as cliente:
            resp = await cliente.get(f"{BACKEND_URL}/medicos/")
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

# ✅ Crear visita (snake_case + validación real)
async def crear_visita(paciente_id, medico_id, descripcion, direccion, telefono, barrio_id):
    try:
        async with httpx.AsyncClient(timeout=15) as cliente:
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
            resp = await cliente.post(f"{BACKEND_URL}/visitas", json=payload, headers={"Content-Type": "application/json"})

            status = resp.status_code
            body_str = (await resp.aread()).decode(errors="ignore")
            print(f"📥 /visitas status={status} body={body_str}")

            try:
                body = json.loads(body_str)
            except Exception:
                body = {"raw": body_str}

            ok = 200 <= status < 300
            return {"ok": ok, "status": status, "data": body}
    except Exception as e:
        import traceback
        print("❌ Excepción creando visita:", repr(e))
        traceback.print_exc()
        return {"ok": False, "mensaje": f"Error creando visita: {repr(e)}"}

# ===============================
# Chat principal
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
    if not estado["telefono"]: estado["telefono"] = posible_telefono(texto)
    if not estado["direccion"]: estado["direccion"] = posible_direccion(texto)
    if not estado["motivo"]: estado["motivo"] = posible_motivo(texto)
    if not estado["nombre"]: estado["nombre"] = posible_nombre(texto)

    # Asociación médico/barrio
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

    # Campos faltantes
    faltan = []
    for campo, desc in [
        ("paciente_id", "membresía"),
        ("nombre", "nombre completo"),
        ("telefono", "teléfono de contacto"),
        ("direccion", "dirección"),
        ("motivo", "motivo de la visita"),
        ("barrio_id", "barrio"),
        ("medico_id", "médico"),
    ]:
        if not estado.get(campo): faltan.append(desc)

    estado_json = json.dumps(estado, ensure_ascii=False)
    faltan_texto = ", ".join(faltan) if faltan else "ninguno"

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
            "1️⃣ No repitas información ya conocida.\n"
            "2️⃣ Si faltan datos, pídelos de uno en uno, con empatía y claridad.\n"
            "3️⃣ Si ya tienes todo, pasa a crear_visita.\n"
            "4️⃣ Si falta membresía, usa verificar_membresia.\n"
            "5️⃣ Si ocurre un error, pide confirmación en lenguaje natural.\n"
            "6️⃣ Mantén coherencia y evita frases genéricas o repetitivas.\n"
            "7️⃣ Devuelve solo JSON puro.\n\n"
            f"Contexto institucional:\n{contexto_prevemed}\n"
        ),
    }

    mensajes = [system_prompt, *historial, {"role": "user", "content": texto}]
    print("🧾 ESTADO ANTES:", estado_json)

    # Llamada a OpenAI
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
        data = {"accion": "pedir_dato", "respuesta": "¿Podrías repetir eso por favor?", "detalle": {}}

    accion = data.get("accion", "info")
    respuesta_texto = data.get("respuesta", "¿En qué puedo ayudarte?")
    detalle = data.get("detalle", {}) if isinstance(data.get("detalle", {}), dict) else {}

    try:
        # === Verificar membresía ===
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

        # === Listar médicos ===
        elif accion == "listar_medicos":
            medicos = await get_medicos()
            if medicos:
                estado["medicos_cache"] = medicos
                nombres = [f"{m['usuario']['nombre']} {m['usuario']['apellido']}" for m in medicos]
                respuesta_texto = "Estos son los médicos disponibles: " + ", ".join(nombres)
            else:
                respuesta_texto = "Actualmente no hay médicos disponibles."

        # === Listar barrios ===
        elif accion == "listar_barrios":
            barrios = await get_barrios()
            if barrios:
                estado["barrios_cache"] = barrios
                nombres = [b["nombreBarrio"] for b in barrios]
                respuesta_texto = "¿En qué barrio estás? " + ", ".join(nombres)
            else:
                respuesta_texto = "No hay barrios activos registrados."

        # === Crear visita ===
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

                if visita.get("ok") and visita.get("status") in [200, 201]:
                    data = visita.get("data") or {}
                    msj = data.get("msj")
                    id_visita = None
                    if isinstance(msj, list) and len(msj) > 0:
                        id_visita = msj[0].get("idVisita")
                    elif isinstance(msj, dict):
                        id_visita = msj.get("idVisita")

                    if id_visita:
                        nombre_medico = estado.get("medico_nombre", "")
                        respuesta_texto = f"✅ Tu visita fue creada exitosamente con {nombre_medico}. ID de visita: {id_visita}"
                        conversaciones.pop(doc, None)
                        estado_usuario.pop(doc, None)
                    else:
                        respuesta_texto = "⚠️ El backend respondió correctamente, pero no devolvió ID de visita."
                else:
                    status = visita.get("status")
                    err_raw = (visita.get("data") or {}).get("raw")
                    respuesta_texto = f"⚠️ No pude confirmar la creación de la visita (status {status})."
                    if err_raw:
                        respuesta_texto += " Detalle técnico en logs."

    except Exception as e:
        print("❌ Error ejecutando acción:", e)
        respuesta_texto = f"Error al procesar '{accion}'."

    # === Actualizar conversación ===
    historial.append({"role": "user", "content": texto})
    historial.append({"role": "assistant", "content": respuesta_texto})
    conversaciones[doc] = historial[-10:]

    print("🎯 Acción:", accion, "| Faltan:", detalle.get("faltan"))
    return {"ok": True, "accion": accion, "respuesta": respuesta_texto, "detalle": detalle}

# ===============================
# Endpoints de control
# ===============================
@app.get("/")
def root():
    return {"status": "ok", "mensaje": "Asistente IA operativo"}

@app.get("/health")
def health():
    return {"ok": True, "status": "running"}
