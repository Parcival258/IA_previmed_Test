import httpx
import os
from datetime import datetime

# 🧠 URL base del backend Adonis en Render
BACKEND_URL = os.getenv("BACKEND_URL", "https://previmedbackend-q73n.onrender.com")

async def verificar_membresia_activa(numero_documento: str):
    """
    Verifica si un paciente tiene una membresía activa consultando
    el endpoint /membresias/activa/:numeroDocumento en Adonis.
    """
    try:
        async with httpx.AsyncClient(timeout=10.0) as cliente:
            url = f"{BACKEND_URL}/membresias/activa/{numero_documento}"
            respuesta = await cliente.get(url)
            respuesta.raise_for_status()

            data = respuesta.json()

            if data.get("ok"):
                print("✅ Membresía activa encontrada:", data)
                return {
                    "ok": True,
                    "paciente_id": data["paciente"]["id_paciente"],
                    "membresia_id": data["membresia"]["id_membresia"],
                    "nombre": data["paciente"]["nombre"],
                    "numero_contrato": data["membresia"]["numero_contrato"],
                }
            else:
                print("⚠️ Sin membresía activa:", data)
                return {"ok": False, "mensaje": data.get("mensaje", "Sin membresía activa")}

    except httpx.HTTPStatusError as e:
        print("❌ Error HTTP al verificar membresía:", e)
        return {"ok": False, "mensaje": f"Error HTTP: {e.response.status_code}"}
    except Exception as e:
        print("💥 Error inesperado al verificar membresía:", e)
        return {"ok": False, "mensaje": str(e)}


async def crear_visita(
    paciente_id: int,
    medico_id: int,
    descripcion: str,
    direccion: str,
    telefono: str,
    barrio_id: int,
):
    """
    Crea una nueva visita médica en el backend de Prevemed.
    """
    try:
        fecha_actual = datetime.now().isoformat()

        async with httpx.AsyncClient(timeout=10.0) as cliente:
            respuesta = await cliente.post(
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
            data = respuesta.json()
            print("🩺 Respuesta de crear visita:", data)
            return data

    except Exception as e:
        print("❌ Error al crear la visita:", e)
        return {"ok": False, "mensaje": str(e)}