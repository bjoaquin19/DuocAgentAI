import json
import time
from typing import Literal

from langchain_core.messages import AIMessage, HumanMessage, RemoveMessage, SystemMessage, ToolMessage
from langgraph.graph import END, START, StateGraph
from langgraph.prebuilt import ToolNode
from langgraph.types import Command, interrupt
from pydantic import BaseModel, Field

from agente import config, fechas, prompts
from agente.estado import AgentState
from agente.herramientas import HERRAMIENTAS_CRITICAS, crear_herramientas

RESET = "__reset__"


class Paso(BaseModel):
    descripcion: str = Field(description="Qué se hace en este paso")
    herramienta: str = Field(description="Nombre de la herramienta a usar, o 'preguntar_al_estudiante' o 'responder'")
    prioridad: int = Field(description="1 es lo primero que se hace", ge=1, le=10)


class Plan(BaseModel):
    tipo: Literal["saludo", "fuera_de_alcance", "consulta_simple", "multi_etapa", "dato_personal"]
    motivo: str = Field(description="Por qué se eligió este tipo y este plan, en una frase")
    consulta_reescrita: str = Field(description="La pregunta del estudiante reescrita de forma autocontenida")
    pasos: list[Paso] = Field(default_factory=list)


class Validacion(BaseModel):
    respaldada: bool
    problema: str = Field(default="", description="Qué dato no está en la evidencia, si aplica")


class HechosNuevos(BaseModel):
    hechos: list[str] = Field(default_factory=list)


def _json(obj) -> str:
    return json.dumps(obj, ensure_ascii=False) if obj else "(sin datos)"


def _texto_memorias(memorias: list[dict]) -> str:
    return "; ".join(f"{m['texto']} ({m['fecha']})" for m in memorias) if memorias else "(sin memorias)"


def _texto_plan(plan: list[dict]) -> str:
    if not plan:
        return "(sin plan: responde directamente)"
    return "\n".join(f"{p['prioridad']}. [{p.get('estado', 'pendiente')}] {p['herramienta']}: {p['descripcion']}"
                     for p in sorted(plan, key=lambda p: p["prioridad"]))


def marcar_pasos(plan: list[dict], usadas: list[str]) -> tuple[list[dict], list[str]]:
    nuevo = [dict(p) for p in plan]
    marcados = []
    for herramienta in usadas:
        for paso in sorted(nuevo, key=lambda p: p["prioridad"]):
            if paso["herramienta"] == herramienta and paso.get("estado") != "hecho":
                paso["estado"] = "hecho"
                marcados.append(f"{paso['prioridad']}. {herramienta}")
                break
    return nuevo, marcados


def mensajes_del_turno(messages: list) -> list:
    for i in range(len(messages) - 1, -1, -1):
        if isinstance(messages[i], HumanMessage):
            return messages[i:]
    return messages


def ultima_pregunta(messages: list) -> str:
    for m in reversed(messages):
        if isinstance(m, HumanMessage):
            return m.content
    return ""


def ventana(messages: list, n: int) -> list:
    recorte = messages[-n:]
    while recorte and not isinstance(recorte[0], HumanMessage):
        recorte = recorte[1:]
    return recorte or mensajes_del_turno(messages)


def conversacion_texto(messages: list) -> str:
    lineas = []
    for m in messages:
        if isinstance(m, HumanMessage):
            lineas.append(f"ESTUDIANTE: {m.content}")
        elif isinstance(m, AIMessage) and m.content:
            lineas.append(f"AGENTE: {m.content}")
    return "\n".join(lineas)


def construir_grafo(llm, servicios, checkpointer=None, llm_validador=None):
    herramientas = crear_herramientas(servicios)
    llm_con_tools = llm.bind_tools(herramientas)
    llm_validador = llm_validador or llm
    db = servicios.db

    def inicio(state: AgentState) -> dict:
        user_id = state["user_id"]
        pregunta = ultima_pregunta(state["messages"])
        perfil = db.leer_perfil(user_id)
        try:
            memorias = db.buscar_memorias(user_id, servicios.embedder.get_embedding(pregunta), config.TOP_K_MEMORIAS)
        except Exception as e:
            memorias = []
            print(f"[WARN] no se pudieron leer las memorias: {e}")
        decision = f"Memoria cargada: perfil con {len(perfil)} dato(s) y {len(memorias)} memoria(s) relevante(s)."
        return {
            "perfil": perfil, "memorias": memorias, "documentos": [RESET], "plan": [], "tipo": "", "motivo": "",
            "consulta": pregunta, "perfil_actualizado": False, "replanificaciones": 0, "intentos_validador": 0,
            "feedback_validador": "", "respuesta_rechazada": False, "inicio_ts": time.time(),
            "herramientas_usadas": [RESET], "decisiones": [RESET, decision],
        }

    def planificador(state: AgentState) -> dict:
        replan = state.get("replanificaciones", 0) > 0
        nota = ("ATENCIÓN: el estudiante corrigió datos de su perfil a mitad de la tarea. Vuelve a planificar los pasos "
                "que dependían de esos datos.\n") if replan else ""
        system = prompts.PLANIFICADOR.format(
            fecha=fechas.hoy_texto(), perfil=_json(state.get("perfil")), memorias=_texto_memorias(state.get("memorias", [])),
            resumen=state.get("resumen") or "(sin resumen)", nota_replan=nota,
        )
        try:
            plan: Plan = llm.with_structured_output(Plan).invoke(
                [SystemMessage(system)] + ventana(state["messages"], config.VENTANA_MENSAJES)
            )
        except Exception as e:
            print(f"[WARN] el planificador falló: {e}")
            plan = Plan(tipo="consulta_simple", motivo=f"El planificador falló ({type(e).__name__}); se usa un plan por defecto.",
                        consulta_reescrita=ultima_pregunta(state["messages"]),
                        pasos=[Paso(descripcion="Buscar en los documentos", herramienta="buscar_documentos", prioridad=1)])
        pasos = [p.model_dump() | {"estado": "pendiente"} for p in sorted(plan.pasos, key=lambda p: p.prioridad)]
        etiqueta = "Re-planificación" if replan else "Planificación"
        return {
            "tipo": plan.tipo, "motivo": plan.motivo, "consulta": plan.consulta_reescrita, "plan": pasos,
            "decisiones": [f"{etiqueta}: tipo={plan.tipo}. {plan.motivo} Pasos: "
                           + (" → ".join(p["herramienta"] for p in pasos) or "ninguno")],
        }

    def ruta_planificador(state: AgentState) -> str:
        return "responder_directo" if state["tipo"] in ("saludo", "fuera_de_alcance") else "agente"

    def responder_directo(state: AgentState) -> dict:
        if state["tipo"] == "fuera_de_alcance":
            return {"messages": [AIMessage(prompts.MENSAJE_FUERA_DE_DOMINIO)],
                    "decisiones": ["Fuera de alcance: se responde con el mensaje fijo sin usar herramientas."]}
        system = prompts.SALUDO.format(perfil=_json(state.get("perfil")), memorias=_texto_memorias(state.get("memorias", [])))
        respuesta = llm.invoke([SystemMessage(system)] + ventana(state["messages"], 4))
        return {"messages": [AIMessage(respuesta.content)], "decisiones": ["Saludo: respuesta directa sin herramientas."]}

    def agente(state: AgentState) -> dict:
        feedback = ""
        if state.get("feedback_validador"):
            feedback = ("\n# CORRECCIÓN DEL VALIDADOR\nTu respuesta anterior fue rechazada: "
                        f"{state['feedback_validador']}\nVuelve a buscar o responde solo con lo que está en la evidencia.")
        system = prompts.AGENTE.format(
            fecha=fechas.hoy_texto(), perfil=_json(state.get("perfil")), memorias=_texto_memorias(state.get("memorias", [])),
            resumen=state.get("resumen") or "(sin resumen)", consulta=state.get("consulta", ""),
            plan=_texto_plan(state.get("plan", [])), fuera_dominio=prompts.MENSAJE_FUERA_DE_DOMINIO, feedback=feedback,
        )
        mensajes = [SystemMessage(system)] + ventana(state["messages"], config.VENTANA_MENSAJES)
        error = None
        for intento in range(1, config.MAX_REINTENTOS_LLM + 1):
            try:
                respuesta = llm_con_tools.invoke(mensajes)
                if intento > 1:
                    return {"messages": [respuesta], "decisiones": [f"El modelo falló ({error}); funcionó en el intento {intento}."]}
                return {"messages": [respuesta]}
            except Exception as e:
                error = type(e).__name__
                print(f"[WARN] el modelo falló (intento {intento}): {e}")
                time.sleep(config.ESPERA_REINTENTO_S * intento)
        return {"messages": [AIMessage(prompts.MENSAJE_ERROR_TECNICO)],
                "decisiones": [f"El modelo falló {config.MAX_REINTENTOS_LLM} veces ({error}) → se responde con un mensaje de error controlado."]}

    def ruta_agente(state: AgentState) -> str:
        ultimo = state["messages"][-1]
        if isinstance(ultimo, AIMessage) and ultimo.tool_calls:
            if any(tc["name"] in HERRAMIENTAS_CRITICAS for tc in ultimo.tool_calls):
                return "aprobacion"
            return "herramientas"
        usadas = {m.name for m in mensajes_del_turno(state["messages"]) if isinstance(m, ToolMessage)}
        if usadas & {"buscar_documentos", "consultar_calendario", "proximos_eventos", "verificar_etapa_practica"}:
            return "validador"
        return "memoria"

    def aprobacion(state: AgentState) -> Command:
        ultimo = state["messages"][-1]
        criticas = [tc for tc in ultimo.tool_calls if tc["name"] in HERRAMIENTAS_CRITICAS]
        respuesta = interrupt({
            "tipo": "aprobacion",
            "acciones": [{"herramienta": tc["name"], "argumentos": tc["args"]} for tc in criticas],
        })
        aprobado = respuesta is True or str(respuesta).strip().lower() in ("si", "sí", "s", "true", "ok", "aprobar")
        if aprobado:
            return Command(goto="herramientas",
                           update={"decisiones": [f"Human-in-the-loop: el estudiante aprobó {', '.join(tc['name'] for tc in criticas)}."]})
        rechazos = [ToolMessage("RECHAZADO: el estudiante no autorizó esta acción. No la vuelvas a intentar en este turno.",
                                tool_call_id=tc["id"], name=tc["name"]) for tc in ultimo.tool_calls]
        return Command(goto="agente", update={
            "messages": rechazos,
            "decisiones": [f"Human-in-the-loop: el estudiante rechazó {', '.join(tc['name'] for tc in criticas)}; el agente sigue sin escribir."],
        })

    def revisar(state: AgentState) -> Command:
        ultimo_ai = next(m for m in reversed(state["messages"]) if isinstance(m, AIMessage))
        ids = {tc["id"] for tc in ultimo_ai.tool_calls}
        resultados = [m for m in state["messages"] if isinstance(m, ToolMessage) and m.tool_call_id in ids]
        usadas = [m.name for m in resultados]
        decisiones = []
        for m in resultados:
            contenido = str(m.content)
            if contenido.startswith("SIN_RESULTADOS"):
                decisiones.append(f"{m.name} no encontró resultados → el agente debe reformular o derivar.")
            elif contenido.startswith("ERROR"):
                decisiones.append(f"{m.name} devolvió un error → el agente debe corregir el argumento.")
            else:
                decisiones.append(f"Observación de {m.name}: OK.")
        plan, marcados = marcar_pasos(state.get("plan", []), usadas)
        if marcados:
            pendientes = [p for p in plan if p.get("estado") != "hecho" and p["herramienta"] not in ("responder", "preguntar_al_estudiante")]
            decisiones.append(f"Progreso del plan: completado(s) {', '.join(marcados)}; quedan {len(pendientes)} paso(s) con herramienta.")
        update = {"herramientas_usadas": usadas, "decisiones": decisiones, "plan": plan}
        if (state.get("perfil_actualizado") and state.get("tipo") in ("multi_etapa", "consulta_simple")
                and state.get("replanificaciones", 0) < config.MAX_REPLANIFICACIONES):
            update.update({"perfil_actualizado": False, "replanificaciones": state.get("replanificaciones", 0) + 1})
            update["decisiones"] = decisiones + ["Condición cambiante: el perfil cambió durante la tarea → se vuelve a planificar."]
            return Command(goto="planificador", update=update)
        update["perfil_actualizado"] = False
        return Command(goto="agente", update=update)

    def validador(state: AgentState) -> Command:
        turno = mensajes_del_turno(state["messages"])
        respuesta = turno[-1]
        evidencia = "\n\n".join(f"[{m.name}] {m.content}" for m in turno if isinstance(m, ToolMessage))
        try:
            resultado: Validacion = llm_validador.with_structured_output(Validacion).invoke(
                prompts.VALIDADOR.format(evidencia=evidencia[:12000], respuesta=respuesta.content)
            )
        except Exception as e:
            return Command(goto="memoria", update={"decisiones": [f"Validador no disponible ({type(e).__name__}); se entrega la respuesta."]})
        if resultado.respaldada:
            return Command(goto="memoria", update={"decisiones": ["Validador: respuesta respaldada por la evidencia."]})
        intentos = state.get("intentos_validador", 0)
        if intentos < config.MAX_REINTENTOS_VALIDADOR:
            return Command(goto="agente", update={
                "messages": [RemoveMessage(id=respuesta.id)],
                "intentos_validador": intentos + 1,
                "feedback_validador": resultado.problema,
                "decisiones": [f"Validador rechazó la respuesta ({resultado.problema}) → reintento {intentos + 1}."],
            })
        return Command(goto="memoria", update={
            "messages": [RemoveMessage(id=respuesta.id), AIMessage(prompts.MENSAJE_SIN_INFO)],
            "respuesta_rechazada": True,
            "decisiones": [f"Validador rechazó otra vez ({resultado.problema}) → respuesta segura y se ofrece ticket."],
        })

    def memoria(state: AgentState) -> dict:
        user_id = state["user_id"]
        messages = state["messages"]
        pregunta = ultima_pregunta(messages)
        respuesta = messages[-1].content if messages and isinstance(messages[-1], AIMessage) else ""
        update = {"decisiones": []}

        if state.get("tipo") not in ("fuera_de_alcance",):
            try:
                existentes = db.listar_memorias(user_id)[:15]
                extraidos: HechosNuevos = llm.with_structured_output(HechosNuevos).invoke(prompts.EXTRACTOR_MEMORIA.format(
                    perfil=_json(state.get("perfil")), memorias=_texto_memorias(existentes) if existentes else "(ninguna)",
                    pregunta=pregunta, respuesta=respuesta,
                ))
                textos_existentes = {m["texto"].strip().lower() for m in existentes}
                nuevos = [h.strip() for h in extraidos.hechos if h.strip() and h.strip().lower() not in textos_existentes]
                for hecho in nuevos:
                    db.guardar_memoria(user_id, hecho, servicios.embedder.get_embedding(hecho))
                if nuevos:
                    update["decisiones"].append(f"Memoria de largo plazo: se guardaron {len(nuevos)} hecho(s) nuevo(s): {'; '.join(nuevos)}")
            except Exception as e:
                update["decisiones"].append(f"No se pudo actualizar la memoria de largo plazo ({type(e).__name__}).")

        if len(messages) > config.MAX_MENSAJES_ANTES_DE_RESUMIR:
            conservar = ventana(messages, config.VENTANA_MENSAJES)
            ids_conservar = {m.id for m in conservar}
            antiguos = [m for m in messages if m.id not in ids_conservar]
            if antiguos:
                try:
                    resumen = llm.invoke(prompts.RESUMIDOR.format(resumen=state.get("resumen") or "(ninguno)",
                                                                   conversacion=conversacion_texto(antiguos))).content
                    update["resumen"] = resumen
                    update["messages"] = [RemoveMessage(id=m.id) for m in antiguos]
                    update["decisiones"].append(f"Memoria de corto plazo: se resumieron {len(antiguos)} mensajes antiguos.")
                except Exception as e:
                    update["decisiones"].append(f"No se pudo resumir la conversación ({type(e).__name__}).")

        try:
            db.guardar_traza({
                "user_id": user_id, "pregunta": pregunta, "respuesta": respuesta, "tipo": state.get("tipo"),
                "plan": state.get("plan", []), "decisiones": state.get("decisiones", []) + update["decisiones"],
                "herramientas": state.get("herramientas_usadas", []),
                "latencia_ms": int((time.time() - state.get("inicio_ts", time.time())) * 1000),
            })
        except Exception as e:
            print(f"[WARN] no se pudo guardar la traza: {e}")
        return update

    grafo = StateGraph(AgentState)
    grafo.add_node("inicio", inicio)
    grafo.add_node("planificador", planificador)
    grafo.add_node("responder_directo", responder_directo)
    grafo.add_node("agente", agente)
    grafo.add_node("aprobacion", aprobacion, destinations=("herramientas", "agente"))
    grafo.add_node("herramientas", ToolNode(herramientas))
    grafo.add_node("revisar", revisar, destinations=("agente", "planificador"))
    grafo.add_node("validador", validador, destinations=("agente", "memoria"))
    grafo.add_node("memoria", memoria)

    grafo.add_edge(START, "inicio")
    grafo.add_edge("inicio", "planificador")
    grafo.add_conditional_edges("planificador", ruta_planificador, ["responder_directo", "agente"])
    grafo.add_edge("responder_directo", "memoria")
    grafo.add_conditional_edges("agente", ruta_agente, ["aprobacion", "herramientas", "validador", "memoria"])
    grafo.add_edge("herramientas", "revisar")
    grafo.add_edge("memoria", END)
    return grafo.compile(checkpointer=checkpointer)


def crear_checkpointer(cliente=None):
    if cliente is None:
        from langgraph.checkpoint.memory import MemorySaver
        return MemorySaver()
    from langgraph.checkpoint.mongodb import MongoDBSaver
    return MongoDBSaver(cliente, db_name=config.MONGODB_DB, checkpoint_collection_name="checkpoints",
                        writes_collection_name="checkpoint_writes")
