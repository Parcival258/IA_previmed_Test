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

conversaciones = {}
estado_usuario = {}

class MensajeEntrada(BaseModel):
    texto: str
    documento: str | None = None
    historial: list | None = None

# ------------------------- #
#   UTILIDADES DE DETECCIÓN #
# ------------------------- #
tel_regex = re.compile(r"(?<!\d)(\+?57)?\s*(3\d{9}|\d{7,10})(?!\d)")
addr_regex = re.compile(r"\b(cra|cr|cra\.|carrera|cll|calle|av|avenida)\b|\b#\b", re.IGNORECASE)
motivo_palabras = ["dolor", "fiebre", "tos", "mareo", "náusea", "vomito", "vómito", "fractura", "golpe", "resfriado", "migraña", "diarrea", "presión", "asma", "alergia", "infección", "consulta", "revisión"]

def posible_telefono(t):
    m = tel_regex.search(t.replace(" ", ""))
    return m.group(0).replace("+57", "") if m else None

def posible_direccion(t):
    if addr_regex.search(t) and any(c.isdigit() for c in t):
        return t.strip()
    return None

def posible_motivo(t):
    tl = t.lower()
    if any(p in tl for p in motivo_palabras) or "me duele" in tl:
        return t.strip()
    return None

def posible_nombre(t):
    limpio = re.sub(r"[^a-zA-ZáéíóúÁÉÍÓÚñÑ\s]", "", t).strip()
    if len(limpio.split()) >= 2 and len(limpio) <= 60:
        return limpio
    return None

# ------------------------- #
#   LLAMADAS AL BACKEND     #
# ------------------------- #
async def verificar_membresia_activa(documento: str):
    try:
        async with httpx.AsyncClient(timeout=10) as cliente:
            r = await cliente.get(f"{BACKEND_URL}/membresias/activa/{documento}")
            r.raise_for_status()
            return r.json()
    except Exception as e:
        return {"ok": False, "mensaje": str(e)}

async def get_medicos():
    try:
        async with httpx.AsyncClient(timeout=10) as cliente:
            r = await cliente.get(f"{BACKEND_URL}/medicos/")
            r.raise_for_status()
            data = r.json()
            return [m for m in data.get("data", []) if m.get("estado") and m.get("disponibilidad")]
    except Exception:
        return []

async def get_barrios():
    try:
        async with httpx.AsyncClient(timeout=10) as cliente:
            r = await cliente.get(f"{BACKEND_URL}/barrios")
            r.raise_for_status()
            data = r.json()
            return [b for b in data.get("msj", []) if b.get("estado")]
    except Exception:
        return []

async def crear_visita(paciente_id, medico_id, descripcion, direccion, telefono, barrio_id):
    try:
        async with httpx.AsyncClient(timeout=40) as cliente:  # timeout aumentado
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

# ------------------------- #
#   CHAT PRINCIPAL          #
# ------------------------- #
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

    # detección automática
    t_low = texto.lower()
    if not estado["telefono"]: estado["telefono"] = posible_telefono(texto)
    if not estado["direccion"]: estado["direccion"] = posible_direccion(texto)
    if not estado["motivo"]: estado["motivo"] = posible_motivo(texto)
    if not estado["nombre"]: estado["nombre"] = posible_nombre(texto)

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

    # campos faltantes
    faltan = []
    for k, v in [("paciente_id", "membresía"), ("nombre", "nombre completo"), ("telefono", "teléfono"),
                 ("direccion", "dirección"), ("motivo", "motivo de la visita"),
                 ("barrio_id", "barrio"), ("medico_id", "médico")]:
        if not estado.get(k):
            faltan.append(v)

    estado_json = json.dumps(estado, ensure_ascii=False)
    faltan_texto = ", ".join(faltan) if faltan else "ninguno"

    system_prompt = {
        "role": "system",
        "content": (
            "Eres el asistente médico de Previmed. "
            "Debes mantener coherencia, ser empático y preciso.\n"
            "Responde solo en JSON con 'accion', 'respuesta' y 'detalle'.\n"
            f"Estado actual: {estado_json}\nCampos faltantes: {faltan_texto}\n\n"
            "Reglas: no repitas lo que ya se sabe, pregunta con naturalidad, "
            "y si tienes todos los datos, pasa a crear_visita. "
            "Siempre mantén el contexto de la conversación.\n"
            f"Contexto institucional:\n{contexto_prevemed}\n"
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
        data = {"accion": "pedir_dato", "respuesta": "Disculpa, ¿podrías repetirlo?", "detalle": {}}

    accion = data.get("accion", "info")
    respuesta_texto = data.get("respuesta", "¿En qué puedo ayudarte?")
    detalle = data.get("detalle", {}) if isinstance(data.get("detalle", {}), dict) else {}

    try:
        if accion == "verificar_membresia":
            if not mensaje.documento:
                respuesta_texto = "Por favor, indícame tu número de cédula."
            else:
                r = await verificar_membresia_activa(mensaje.documento)
                if r.get("ok"):
                    estado["paciente_id"] = r["paciente"]["id_paciente"]
                    respuesta_texto = "Tu membresía está activa ✅. ¿Deseas agendar la visita?"
                else:
                    respuesta_texto = "No encontré membresía activa. ¿Deseas renovarla?"

        elif accion == "listar_medicos":
            medicos = await get_medicos()
            if medicos:
                estado["medicos_cache"] = medicos
                nombres = [f"{m['usuario']['nombre']} {m['usuario']['apellido']}" for m in medicos]
                respuesta_texto = "Los médicos disponibles son: " + ", ".join(nombres)
            else:
                respuesta_texto = "Por el momento no hay médicos disponibles."

        elif accion == "listar_barrios":
            barrios = await get_barrios()
            if barrios:
                estado["barrios_cache"] = barrios
                nombres = [b["nombreBarrio"] for b in barrios]
                respuesta_texto = "¿En qué barrio estás? " + ", ".join(nombres)
            else:
                respuesta_texto = "No hay barrios registrados."

        elif accion == "crear_visita":
            oblig = ["paciente_id", "medico_id", "barrio_id", "telefono", "direccion", "motivo"]
            faltantes = [c for c in oblig if not estado.get(c)]
            if faltantes:
                detalle["faltan"] = faltantes
                respuesta_texto = "Faltan datos: " + ", ".join(faltantes)
            else:
                visita = await crear_visita(
                    estado["paciente_id"], estado["medico_id"],
                    estado["motivo"], estado["direccion"],
                    estado["telefono"], estado["barrio_id"]
                )
                detalle["visita"] = visita

                if visita.get("ok") and visita.get("status") in [200, 201]:
                    data = visita.get("data") or {}
                    id_visita = None
                    # ✅ Buscar idVisita dentro de data.data según estructura real
                    if "data" in data and isinstance(data["data"], dict):
                        id_visita = data["data"].get("idVisita")

                    if id_visita:
                        nombre_medico = estado.get("medico_nombre", "")
                        respuesta_texto = f"✅ Tu visita fue registrada correctamente con {nombre_medico}. ID: {id_visita}."
                        conversaciones.pop(doc, None)
                        estado_usuario.pop(doc, None)
                    else:
                        respuesta_texto = "⚠️ El backend confirmó la visita, pero no devolvió su ID."
                else:
                    status = visita.get("status")
                    err_raw = (visita.get("data") or {}).get("raw")
                    respuesta_texto = f"⚠️ El backend tardó en responder o no confirmó (status {status}). Intenta nuevamente."
                    if err_raw:
                        respuesta_texto += " Detalle técnico en logs."

    except Exception as e:
        print("❌ Error ejecutando acción:", e)
        respuesta_texto = f"Ocurrió un error procesando '{accion}'."

    historial.append({"role": "user", "content": texto})
    historial.append({"role": "assistant", "content": respuesta_texto})
    conversaciones[doc] = historial[-10:]

    print("🎯 Acción:", accion, "| Faltan:", detalle.get("faltan"))
    return {"ok": True, "accion": accion, "respuesta": respuesta_texto, "detalle": detalle}

# ------------------------- #
#   ENDPOINTS DE CONTROL    #
# ------------------------- #
@app.get("/")
def root():
    return {"status": "ok", "mensaje": "Asistente IA operativo"}

@app.get("/health")
def health():
    return {"ok": True, "status": "running"}
