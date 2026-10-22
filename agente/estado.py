from typing import Annotated

from langgraph.graph import MessagesState


def _sumar_o_reiniciar(actual: list | None, nuevo: list | None) -> list:
    if nuevo is None:
        return actual or []
    if nuevo and nuevo[0] == "__reset__":
        return list(nuevo[1:])
    return (actual or []) + nuevo


class AgentState(MessagesState):
    user_id: str
    perfil: dict
    memorias: list[dict]
    resumen: str
    tipo: str
    motivo: str
    consulta: str
    plan: list[dict]
    documentos: Annotated[list[dict], _sumar_o_reiniciar]
    herramientas_usadas: Annotated[list[str], _sumar_o_reiniciar]
    decisiones: Annotated[list[str], _sumar_o_reiniciar]
    perfil_actualizado: bool
    replanificaciones: int
    intentos_validador: int
    feedback_validador: str
    respuesta_rechazada: bool
    inicio_ts: float
