from langchain_core.messages import AIMessage, HumanMessage
from langgraph.types import Command

from agente import config
from agente.grafo import construir_grafo, crear_checkpointer


class Orquestador:
    def __init__(self, llm=None, servicios=None, checkpointer=None, llm_validador=None):
        if servicios is None:
            from agente.servicios import crear_servicios
            servicios = crear_servicios()
        if llm is None:
            from agente.llm import crear_llm
            llm = crear_llm()
        if checkpointer is None:
            checkpointer = crear_checkpointer(getattr(servicios.db, "client", None))
        self.servicios = servicios
        self.grafo = construir_grafo(llm, servicios, checkpointer, llm_validador)

    def _config(self, thread_id: str) -> dict:
        return {"configurable": {"thread_id": thread_id}, "recursion_limit": config.RECURSION_LIMIT}

    def _resultado(self, salida: dict, thread_id: str) -> dict:
        estado = self.grafo.get_state(self._config(thread_id))
        valores = estado.values
        pendiente = None
        interrupciones = salida.get("__interrupt__") if isinstance(salida, dict) else None
        if interrupciones:
            pendiente = interrupciones[0].value
        mensajes = valores.get("messages", [])
        respuesta = mensajes[-1].content if mensajes and isinstance(mensajes[-1], AIMessage) and not pendiente else ""
        return {
            "respuesta": respuesta,
            "pendiente_aprobacion": pendiente,
            "tipo": valores.get("tipo"),
            "motivo": valores.get("motivo"),
            "consulta": valores.get("consulta"),
            "plan": valores.get("plan", []),
            "decisiones": valores.get("decisiones", []),
            "herramientas": valores.get("herramientas_usadas", []),
            "documentos": valores.get("documentos", []),
            "perfil": valores.get("perfil", {}),
            "memorias": valores.get("memorias", []),
            "resumen": valores.get("resumen", ""),
        }

    def preguntar(self, user_id: str, thread_id: str, texto: str) -> dict:
        salida = self.grafo.invoke({"messages": [HumanMessage(texto)], "user_id": user_id}, self._config(thread_id))
        return self._resultado(salida, thread_id)

    def responder_aprobacion(self, thread_id: str, aprobado: bool) -> dict:
        salida = self.grafo.invoke(Command(resume=aprobado), self._config(thread_id))
        return self._resultado(salida, thread_id)

    def historial(self, thread_id: str) -> list[dict]:
        valores = self.grafo.get_state(self._config(thread_id)).values
        filas = []
        for m in valores.get("messages", []):
            if isinstance(m, HumanMessage):
                filas.append({"rol": "user", "contenido": m.content})
            elif isinstance(m, AIMessage) and m.content and not m.tool_calls:
                filas.append({"rol": "assistant", "contenido": m.content})
        return filas
