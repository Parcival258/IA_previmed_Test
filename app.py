from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from openai import OpenAI
from dotenv import load_dotenv
import os
import httpx
from datetime import datetime
from fastapi.middleware.cors import CORSMiddleware
from contexto import contexto_prevemed  # 🧩 Importamos el contexto institucional

# 📦 Cargar variables del entorno
load_dotenv()

CLAVE_OPENAI = os.getenv("OPENAI_API_KEY")
BACKEND_URL = os.getenv("BACKEND_URL", "https://previmedbackend-q73n.onrender.com")

cliente_openai = OpenAI(api_key=CLAVE_OPENAI)
app = FastAPI(title="Asistente IA Previmed")

# 🌐 CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 🧠 Memoria conversacional
conversaciones = {}

# -------------------------------
# 📥 MODELO DE ENTRADA
# -------------------------------
class MensajeEntrada(BaseModel):
    texto: str
    documento: str | None = None


# -------------------------------
# ⚙️ FUNCIONES AUXILIARES
# -------------------------------
async def verificar_membresia_activa(numero_documento: str):
    """Consulta si el paciente tiene una membresía activa."""
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
    """Obtiene médicos que están activos y disponibles."""
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
    """Obtiene los barrios activos desde el backend."""
    try:
        async with httpx.AsyncClient(timeout=10.0) as cliente:
            resp = await cliente.get(f"{BACKEND_URL}/barrios")
            resp.raise_for_status()
            data = resp.json()
            return [b for b in data.get("msj", []) if b.get("estado")]
    except Exception as e:
        print(f"❌ Error obteniendo barrios: {e}")
        return []

async def crear_visita(paciente_id: int, medico_id: int, descripcion: str,
                       direccion: str, telefono: str, barrio_id: int):
    """Crea la visita en el backend."""
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
            return resp.json()
    except Exception as e:
        print(f"❌ Error creando visita: {e}")
        return {"ok": False, "mensaje": str(e)}

# -------------------------------
# 🤖 FUNCIÓN GPT: detectar intención
# -------------------------------
async def detectar_intencion(texto: str):
    try:
        completion = cliente_openai.chat.completions.create(
            model="gpt-4.1-mini",
            messages=[
                {
                    "role": "system",
                    "content": "Analiza la intención del usuario. Responde solo con una palabra: 'visita', 'informacion', 'cancelar' o 'otro'.",
                },
                {"role": "user", "content": texto},
            ],
        )
        return completion.choices[0].message.content.strip().lower()
    except Exception as e:
        print("❌ Error detectando intención:", e)
        return "otro"

# -------------------------------
# 💬 ENDPOINT PRINCIPAL DE CHAT
# -------------------------------
@app.post("/chat")
async def responder(mensaje: MensajeEntrada):
    texto = mensaje.texto.strip()
    doc = mensaje.documento

    if not texto:
        raise HTTPException(status_code=400, detail="El campo 'texto' no puede estar vacío.")

    # Analizar intención con GPT
    intencion = await detectar_intencion(texto)
    print(f"🧭 Intención detectada: {intencion}")

    # 🟡 Cancelar flujo
    if intencion == "cancelar":
        conversaciones.pop(doc, None)
        return {"ok": True, "accion": "cancelar", "respuesta": "Está bien 😊. He cancelado la solicitud. ¿Deseas que te ayude con otra cosa?"}

    # 🟢 Información general (usa contexto.py)
    if intencion == "informacion":
        respuesta = cliente_openai.chat.completions.create(
            model="gpt-4.1-mini",
            messages=[
                {
                    "role": "system",
                    "content": f"Eres el asistente virtual de Previmed. Usa la siguiente información institucional para responder con empatía y precisión:\n\n{contexto_prevemed}"
                },
                {"role": "user", "content": texto},
            ],
        )
        return {"ok": True, "accion": "informacion", "respuesta": respuesta.choices[0].message.content}

    # ⚪ Conversaciones generales sin intención específica
    if intencion == "otro":
        respuesta = cliente_openai.chat.completions.create(
            model="gpt-4.1-mini",
            messages=[
                {
                    "role": "system",
                    "content": f"Eres un asistente empático y útil de Previmed. Usa esta información para orientar tus respuestas:\n\n{contexto_prevemed}"
                },
                {"role": "user", "content": texto},
            ],
        )
        return {"ok": True, "accion": "otro", "respuesta": respuesta.choices[0].message.content}

    # 🏥 Flujo de visita médica
    contexto = conversaciones.get(doc or "default", {})

    if not doc:
        return {
            "ok": False,
            "accion": "solicitar_documento",
            "respuesta": "Perfecto 😊. ¿Podrías darme tu número de cédula o documento para verificar tu membresía activa?"
        }

    if "membresia_verificada" not in contexto:
        data = await verificar_membresia_activa(doc)
        if not data.get("ok"):
            return {
                "ok": False,
                "accion": "sin_membresia",
                "respuesta": "No encuentro una membresía activa con ese documento. Si lo deseas puedo ayudarte a crear o renovar una membresía."
            }

        contexto["membresia_verificada"] = True
        contexto["paciente_id"] = data["paciente"]["id_paciente"]
        conversaciones[doc] = contexto
        return {
            "ok": True,
            "accion": "pedir_motivo",
            "respuesta": "Excelente 😄. Ya confirmé tu membresía activa. ¿Podrías contarme brevemente el motivo de tu visita?"
        }

    if "motivo" not in contexto:
        contexto["motivo"] = mensaje.texto
        conversaciones[doc] = contexto
        return {"ok": True, "accion": "pedir_direccion", "respuesta": "Entendido 👍. ¿En qué dirección deseas recibir la visita?"}

    if "direccion" not in contexto:
        contexto["direccion"] = mensaje.texto
        conversaciones[doc] = contexto
        return {"ok": True, "accion": "pedir_telefono", "respuesta": "Perfecto 🏠. Ahora necesito un número de contacto, por favor."}

    if "telefono" not in contexto:
        contexto["telefono"] = mensaje.texto
        conversaciones[doc] = contexto

        medicos = await get_medicos_disponibles()
        if not medicos:
            return {"ok": False, "accion": "sin_medicos", "respuesta": "Lamentablemente no hay médicos disponibles en este momento 😔. ¿Deseas que te notifique cuando haya uno libre?"}

        contexto["medicos_disponibles"] = medicos
        conversaciones[doc] = contexto

        nombres = ", ".join([f"{m['usuario']['nombre']} {m['usuario']['apellido']}" for m in medicos])
        return {"ok": True, "accion": "elegir_medico", "respuesta": f"Perfecto. Tengo disponibles a los siguientes médicos: {nombres}. ¿Con cuál deseas agendar la visita?"}

    if "medico_id" not in contexto:
        medicos = contexto.get("medicos_disponibles", [])
        elegido = next((m for m in medicos if m["usuario"]["nombre"].lower() in texto.lower()), None)
        if not elegido:
            return {"ok": False, "accion": "repetir_medico", "respuesta": "No logré identificar el médico que mencionas 😅. Por favor dime solo el nombre, por ejemplo: *Samanta*."}

        contexto["medico_id"] = elegido["id_medico"]
        conversaciones[doc] = contexto

        barrios = await get_barrios_activos()
        if not barrios:
            return {"ok": False, "accion": "sin_barrios", "respuesta": "Parece que no hay barrios activos disponibles. Por favor contacta soporte para registrar la dirección."}

        contexto["barrios_activos"] = barrios
        conversaciones[doc] = contexto

        nombres_barrios = ", ".join([b["nombreBarrio"] for b in barrios])
        return {"ok": True, "accion": "elegir_barrio", "respuesta": f"Perfecto 👩‍⚕️. Ahora dime en qué barrio te encuentras. Barrios disponibles: {nombres_barrios}."}

    if "barrio_id" not in contexto:
        barrios = contexto.get("barrios_activos", [])
        elegido = next((b for b in barrios if b["nombreBarrio"].lower() in texto.lower()), None)
        if not elegido:
            return {"ok": False, "accion": "repetir_barrio", "respuesta": "No logré identificar ese barrio 😅. Intenta escribir solo el nombre, por ejemplo: *Modelo*."}

        contexto["barrio_id"] = elegido["idBarrio"]
        conversaciones[doc] = contexto
        return {"ok": True, "accion": "confirmar", "respuesta": f"Perfecto. Agendaremos una visita por '{contexto['motivo']}' en '{contexto['direccion']}', barrio {elegido['nombreBarrio']}. ¿Confirmas?"}

    if "sí" in texto.lower() or "si" in texto.lower():
        visita = await crear_visita(
            paciente_id=contexto["paciente_id"],
            medico_id=contexto["medico_id"],
            descripcion=contexto["motivo"],
            direccion=contexto["direccion"],
            telefono=contexto["telefono"],
            barrio_id=contexto["barrio_id"],
        )
        conversaciones.pop(doc, None)
        return {"ok": True, "accion": "visita_creada", "respuesta": "✅ ¡Listo! Tu visita fue creada exitosamente. En unos minutos recibirás la confirmación en tu correo. ¿Deseas que te recuerde los detalles?"}

    return {"ok": True, "accion": "esperando_confirmacion", "respuesta": "¿Deseas que cree la visita con esos datos?"}


# -------------------------------
# 🌐 ENDPOINT RAÍZ
# -------------------------------
@app.get("/")
def inicio():
    return {"mensaje": "🤖 Asistente IA Previmed operativo"}

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 10000))
    uvicorn.run("app:app", host="0.0.0.0", port=port, reload=False)
