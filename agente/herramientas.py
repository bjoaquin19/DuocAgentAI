import json
import re
from datetime import timedelta
from typing import Annotated, Literal, Optional

from langchain_core.messages import ToolMessage
from langchain_core.tools import InjectedToolCallId, tool
from langgraph.prebuilt import InjectedState
from langgraph.types import Command
from pydantic import BaseModel, Field

from agente import calendario, fechas

HERRAMIENTAS_CRITICAS = {"guardar_plan_inicio", "crear_ticket_derivacion"}

FuenteDoc = Literal["reglamento_academico", "calendario_academico", "practicas"]
Periodo = Literal["TAV", "2026-1", "2026-2"]


class ItemPlan(BaseModel):
    tarea: str = Field(description="Acción concreta que debe hacer el estudiante")
    fecha: Optional[str] = Field(default=None, description="Fecha límite o de inicio en formato DD-MM-AAAA, si aplica")
    fuente: Optional[str] = Field(default=None, description="Documento de donde sale la fecha o la tarea")


def formatear_documentos(docs: list[dict]) -> str:
    if not docs:
        return "SIN_RESULTADOS: no se encontraron fragmentos relevantes."
    partes = []
    for i, d in enumerate(docs, 1):
        partes.append(f"[{i}] Fuente: {d['fuente']} ({d['tipo']})\n{d['contenido']}")
    return "\n\n---\n\n".join(partes)


def generar_ics(titulo: str, items: list[dict]) -> str:
    lineas = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//DuocAIAgent//EV2//ES"]
    for i, item in enumerate(items):
        if not item.get("fecha"):
            continue
        try:
            d = fechas.parsear(item["fecha"])
        except ValueError:
            continue
        lineas += [
            "BEGIN:VEVENT",
            f"UID:duocaiagent-{d.strftime('%Y%m%d')}-{i}@duoc",
            f"DTSTART;VALUE=DATE:{d.strftime('%Y%m%d')}",
            f"DTEND;VALUE=DATE:{(d + timedelta(days=1)).strftime('%Y%m%d')}",
            f"SUMMARY:{item['tarea'][:120]}",
            f"DESCRIPTION:{titulo}",
            "END:VEVENT",
        ]
    lineas.append("END:VCALENDAR")
    return "\r\n".join(lineas)


def generar_markdown(titulo: str, perfil: dict, items: list[dict]) -> str:
    lineas = [f"# {titulo}", ""]
    if perfil:
        lineas.append("**Estudiante:** " + ", ".join(f"{k}: {v}" for k, v in perfil.items()))
        lineas.append("")
    lineas.append(f"Generado el {fechas.hoy_texto()} por DuocAIAgent.")
    lineas.append("")
    for item in items:
        fecha = f" — **{item['fecha']}**" if item.get("fecha") else ""
        fuente = f" _(fuente: {item['fuente']})_" if item.get("fuente") else ""
        lineas.append(f"- [ ] {item['tarea']}{fecha}{fuente}")
    return "\n".join(lineas) + "\n"


def _nombre_archivo(texto: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_-]+", "_", texto).strip("_")[:40] or "plan"


def crear_herramientas(servicios) -> list:
    db = servicios.db

    @tool(description=(
        "Busca en los documentos oficiales de Duoc UC (reglamento académico, calendario académico 2026 y "
        "prácticas) usando búsqueda semántica. Úsala para cualquier pregunta sobre normas, notas, asistencia, "
        "prácticas o procesos. 'fuente' es opcional y filtra por documento."
    ))
    def buscar_documentos(
        consulta: str,
        tool_call_id: Annotated[str, InjectedToolCallId],
        fuente: Optional[FuenteDoc] = None,
    ) -> Command:
        docs = servicios.retriever.buscar(consulta, fuente=fuente)
        if not docs and fuente:
            docs = servicios.retriever.buscar(consulta)
        limpios = [{k: d[k] for k in ("contenido", "fuente", "tipo", "origen")} | {"score": round(d.get("rerank_score", d["score"]), 3)} for d in docs]
        return Command(update={
            "documentos": limpios,
            "messages": [ToolMessage(formatear_documentos(limpios), tool_call_id=tool_call_id)],
        })

    @tool(description=(
        "Consulta fechas exactas del Calendario Académico Duoc UC 2026 (inscripción de asignaturas, clases, "
        "exámenes, retiro de asignaturas, feriados, matrículas, etc.). Devuelve inicio, término, si está vigente, "
        "terminado o próximo, y cuántos días faltan. 'periodo' es opcional: TAV, 2026-1 o 2026-2."
    ))
    def consultar_calendario(evento: str, periodo: Optional[Periodo] = None) -> str:
        hoy = fechas.hoy()
        encontrados = calendario.buscar_eventos(evento, periodo)
        if not encontrados:
            return f"SIN_RESULTADOS: el Calendario Académico 2026 no tiene un evento parecido a '{evento}'."
        return json.dumps({"fecha_hoy": fechas.formatear(hoy), "eventos": [e.como_dict(hoy) for e in encontrados]}, ensure_ascii=False)

    @tool(description="Lista los eventos del calendario académico que empiezan en los próximos N días o que están vigentes hoy.")
    def proximos_eventos(dias: int = 30) -> str:
        hoy = fechas.hoy()
        eventos = calendario.proximos_eventos(hoy, dias)
        if not eventos:
            return f"SIN_RESULTADOS: no hay eventos en los próximos {dias} días."
        return json.dumps({"fecha_hoy": fechas.formatear(hoy), "eventos": [e.como_dict(hoy) for e in eventos]}, ensure_ascii=False)

    @tool(description="Calcula cuántos días faltan (o pasaron) desde hoy hasta una fecha en formato DD-MM-AAAA.")
    def calcular_dias(fecha: str) -> str:
        try:
            objetivo = fechas.parsear(fecha)
        except ValueError as e:
            return f"ERROR: {e}"
        diferencia = (objetivo - fechas.hoy()).days
        if diferencia > 0:
            return f"Faltan {diferencia} días para el {fechas.formatear(objetivo)} (hoy es {fechas.formatear(fechas.hoy())})."
        if diferencia == 0:
            return f"El {fechas.formatear(objetivo)} es hoy."
        return f"El {fechas.formatear(objetivo)} ya pasó hace {-diferencia} días (hoy es {fechas.formatear(fechas.hoy())})."

    @tool(description=(
        "Razona sobre en qué etapa está el estudiante respecto a sus prácticas, según el documento oficial de "
        "prácticas: la Práctica Laboral (180 horas) se hace a mitad de malla, cerca del 4° semestre en carreras "
        "profesionales; la Práctica Profesional al final de la malla. Necesita el semestre actual y el tipo de carrera."
    ))
    def verificar_etapa_practica(semestre_actual: int, tipo_carrera: Literal["profesional", "tecnica"], semestres_carrera: Optional[int] = None) -> str:
        if semestre_actual < 1 or semestre_actual > 12:
            return "ERROR: el semestre debe estar entre 1 y 12."
        total = semestres_carrera or (8 if tipo_carrera == "profesional" else 5)
        resultado = {"semestre_actual": semestre_actual, "tipo_carrera": tipo_carrera, "semestres_carrera_estimados": total}
        if tipo_carrera == "profesional":
            if semestre_actual < 4:
                resultado["practica_laboral"] = f"Todavía no. Generalmente se hace cerca del 4° semestre; te faltan {4 - semestre_actual} semestre(s). Dura 180 horas."
            elif semestre_actual == 4:
                resultado["practica_laboral"] = "Estás en la etapa en que generalmente se realiza (cerca del 4° semestre). Dura 180 horas."
            else:
                resultado["practica_laboral"] = "Ya pasaste la etapa en que normalmente se realiza (4° semestre); si no la has hecho, confírmalo con tu Director/a de Carrera."
        else:
            resultado["practica_laboral"] = "El documento de prácticas solo indica la etapa de la Práctica Laboral para carreras profesionales; para carreras técnicas hay que confirmarlo con la escuela."
        if semestre_actual >= total:
            resultado["practica_profesional"] = "Estás en el final de la malla, que es cuando generalmente se realiza la Práctica Profesional."
        else:
            resultado["practica_profesional"] = f"Se realiza al final de la malla; te faltan aproximadamente {total - semestre_actual} semestre(s). El documento no indica su duración en horas."
        resultado["fuente"] = "practicas (Horas de practica duocUc.pdf)"
        resultado["advertencia"] = "Son etapas referenciales ('generalmente'); la malla de cada carrera manda."
        return json.dumps(resultado, ensure_ascii=False)

    @tool(description="Lee el perfil guardado del estudiante (carrera, tipo de carrera, jornada, sede, semestre, nombre).")
    def consultar_perfil(state: Annotated[dict, InjectedState]) -> str:
        perfil = db.leer_perfil(state["user_id"])
        return json.dumps(perfil, ensure_ascii=False) if perfil else "PERFIL_VACIO: todavía no sé nada del estudiante."

    @tool(description=(
        "Guarda o corrige datos del perfil del estudiante en la memoria de largo plazo. Úsala apenas el "
        "estudiante cuente o corrija su nombre, carrera, tipo de carrera, jornada, sede o semestre."
    ))
    def actualizar_perfil(
        tool_call_id: Annotated[str, InjectedToolCallId],
        state: Annotated[dict, InjectedState],
        nombre: Optional[str] = None,
        carrera: Optional[str] = None,
        tipo_carrera: Optional[Literal["profesional", "tecnica"]] = None,
        jornada: Optional[Literal["diurna", "vespertina"]] = None,
        sede: Optional[str] = None,
        semestre: Optional[int] = None,
    ) -> Command:
        cambios = {k: v for k, v in {"nombre": nombre, "carrera": carrera, "tipo_carrera": tipo_carrera,
                                     "jornada": jornada, "sede": sede, "semestre": semestre}.items() if v is not None}
        anterior = state.get("perfil") or {}
        if not cambios:
            return Command(update={"messages": [ToolMessage("ERROR: no se indicó ningún dato para actualizar.", tool_call_id=tool_call_id)]})
        perfil = db.actualizar_perfil(state["user_id"], cambios)
        corregidos = {k: v for k, v in cambios.items() if k in anterior and anterior[k] != v}
        texto = f"Perfil actualizado: {json.dumps(perfil, ensure_ascii=False)}"
        if corregidos:
            texto += f". Datos corregidos: {json.dumps(corregidos, ensure_ascii=False)}"
        return Command(update={
            "perfil": perfil,
            "perfil_actualizado": bool(corregidos) or not anterior,
            "messages": [ToolMessage(texto, tool_call_id=tool_call_id)],
        })

    @tool(description="Recupera los planes de inicio que el agente ya le guardó antes a este estudiante (memoria episódica).")
    def consultar_planes_previos(state: Annotated[dict, InjectedState]) -> str:
        planes = db.listar_planes(state["user_id"])
        if not planes:
            return "SIN_RESULTADOS: este estudiante no tiene planes guardados."
        return json.dumps(planes, ensure_ascii=False, default=str)

    @tool(description=(
        "ESCRITURA (requiere confirmación del estudiante). Guarda un plan personal de inicio con tareas y fechas, "
        "y genera un archivo .md y un .ics para agregar al calendario. Las fechas deben venir de consultar_calendario."
    ))
    def guardar_plan_inicio(titulo: str, items: list[ItemPlan], state: Annotated[dict, InjectedState]) -> str:
        user_id = state["user_id"]
        filas = [i.model_dump() if isinstance(i, BaseModel) else dict(i) for i in items]
        if not filas:
            return "ERROR: el plan no tiene tareas."
        filas.sort(key=lambda i: fechas.parsear(i["fecha"]) if i.get("fecha") and re.match(r"\d{2}-\d{2}-\d{4}$", i["fecha"]) else fechas.hoy() + timedelta(days=3650))
        carpeta = servicios.carpeta_salidas
        carpeta.mkdir(parents=True, exist_ok=True)
        base = f"{_nombre_archivo(user_id)}_{fechas.hoy().strftime('%Y%m%d')}_{_nombre_archivo(titulo)}"
        ruta_md = carpeta / f"{base}.md"
        ruta_md.write_text(generar_markdown(titulo, state.get("perfil") or {}, filas), encoding="utf-8")
        (carpeta / f"{base}.ics").write_text(generar_ics(titulo, filas), encoding="utf-8")
        plan_id = db.guardar_plan(user_id, titulo, filas, str(ruta_md))
        return f"Plan #{plan_id} guardado con {len(filas)} tareas. Archivos: {ruta_md.name} y {base}.ics en la carpeta {carpeta}."

    @tool(description=(
        "ESCRITURA (requiere confirmación del estudiante). Crea un ticket de derivación para que coordinación / "
        "Punto Estudiantil atienda el caso. Úsala cuando la información no está en los documentos, cuando el caso "
        "es personal o sensible (situación económica, salud, apelaciones) o cuando el estudiante lo pide."
    ))
    def crear_ticket_derivacion(motivo: str, resumen: str, state: Annotated[dict, InjectedState], prioridad: Literal["baja", "media", "alta"] = "media") -> str:
        ticket_id = db.crear_ticket(state["user_id"], motivo, resumen, prioridad)
        return f"Ticket #{ticket_id} creado con prioridad {prioridad}. Coordinación lo revisará en el panel de tickets."

    return [buscar_documentos, consultar_calendario, proximos_eventos, calcular_dias, verificar_etapa_practica,
            consultar_perfil, actualizar_perfil, consultar_planes_previos, guardar_plan_inicio, crear_ticket_derivacion]
