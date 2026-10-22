import uuid

from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

from agente import config


class Pregunta(BaseModel):
    usuario: str = Field(min_length=1, max_length=80)
    mensaje: str = Field(min_length=1, max_length=2000)
    conversacion: str | None = None


class Aprobacion(BaseModel):
    conversacion: str
    aprobado: bool


def crear_app(orquestador=None) -> FastAPI:
    app = FastAPI(title="DuocAIAgent API", version="2.0")
    estado = {"agente": orquestador}

    def agente():
        if estado["agente"] is None:
            from agente.orquestador import Orquestador
            estado["agente"] = Orquestador()
        return estado["agente"]

    def verificar_clave(x_api_key: str | None = Header(default=None)):
        if config.API_KEY and x_api_key != config.API_KEY:
            raise HTTPException(status_code=401, detail="API key inválida")

    def salida(r: dict, conversacion: str) -> dict:
        return {
            "conversacion": conversacion,
            "respuesta": r["respuesta"],
            "pendiente_aprobacion": r["pendiente_aprobacion"],
            "tipo": r["tipo"],
            "plan": r["plan"],
            "herramientas": r["herramientas"],
            "decisiones": r["decisiones"],
            "fuentes": sorted({d["fuente"] for d in r["documentos"]}),
        }

    @app.get("/salud")
    def salud():
        return {"estado": "ok", "modelo": config.GROQ_MODEL}

    @app.post("/preguntar", dependencies=[Depends(verificar_clave)])
    def preguntar(p: Pregunta):
        conversacion = p.conversacion or f"{p.usuario}-{uuid.uuid4().hex[:8]}"
        return salida(agente().preguntar(p.usuario, conversacion, p.mensaje), conversacion)

    @app.post("/aprobar", dependencies=[Depends(verificar_clave)])
    def aprobar(a: Aprobacion):
        estado_grafo = agente().grafo.get_state(agente()._config(a.conversacion))
        if not estado_grafo.next:
            raise HTTPException(status_code=409, detail="La conversación no tiene acciones pendientes de aprobación")
        return salida(agente().responder_aprobacion(a.conversacion, a.aprobado), a.conversacion)

    @app.get("/tickets/por-notificar", dependencies=[Depends(verificar_clave)])
    def tickets_por_notificar():
        return agente().servicios.db.listar_tickets_por_notificar()

    @app.post("/tickets/{numero}/notificado", dependencies=[Depends(verificar_clave)])
    def ticket_notificado(numero: int):
        if not agente().servicios.db.marcar_ticket_notificado(numero):
            raise HTTPException(status_code=404, detail="Ticket no encontrado")
        return {"numero": numero, "notificado": True}

    return app


app = crear_app()
