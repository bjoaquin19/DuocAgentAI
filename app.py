import uuid
from pathlib import Path

import pandas as pd
import streamlit as st

from agente import config, fechas
from agente.orquestador import Orquestador

st.set_page_config(page_title="Orientador Duoc · EV2", page_icon="🎓", layout="wide")

NOMBRES_HERRAMIENTAS = {
    "guardar_plan_inicio": "guardar tu plan de inicio",
    "crear_ticket_derivacion": "crear un ticket de derivación a coordinación",
}


@st.cache_resource(show_spinner="Cargando agente, base de datos y modelos...")
def cargar_agente() -> Orquestador:
    return Orquestador()


agente = cargar_agente()
db = agente.servicios.db

if "usuario" not in st.session_state:
    st.session_state.usuario = "camila"
if "thread_id" not in st.session_state:
    st.session_state.thread_id = f"{st.session_state.usuario}-{uuid.uuid4().hex[:6]}"
if "detalles" not in st.session_state:
    st.session_state.detalles = {}
if "pendiente" not in st.session_state:
    st.session_state.pendiente = None


def nueva_conversacion():
    st.session_state.thread_id = f"{st.session_state.usuario}-{uuid.uuid4().hex[:6]}"
    st.session_state.detalles = {}
    st.session_state.pendiente = None


with st.sidebar:
    st.title("🎓 Orientador Duoc")
    st.caption(f"Hoy: {fechas.hoy_texto()} · Modelo: {config.GROQ_MODEL}")
    usuario = st.text_input("Usuario (id del estudiante)", st.session_state.usuario)
    if usuario != st.session_state.usuario:
        st.session_state.usuario = usuario
        nueva_conversacion()
    st.button("Nueva conversación", on_click=nueva_conversacion, width="stretch")
    st.caption(f"Conversación: `{st.session_state.thread_id}`")

    st.subheader("Perfil (memoria largo plazo)")
    perfil = db.leer_perfil(st.session_state.usuario)
    if perfil:
        st.json(perfil)
    else:
        st.caption("Sin datos todavía.")

    st.subheader("Memorias guardadas")
    memorias = db.listar_memorias(st.session_state.usuario)
    if memorias:
        for m in memorias[:8]:
            st.caption(f"• {m['texto']} ({m['fecha']})")
    else:
        st.caption("Sin memorias todavía.")

    if st.button("Borrar datos de este usuario", width="stretch"):
        db.borrar_usuario(st.session_state.usuario)
        nueva_conversacion()
        st.rerun()


def mostrar_detalle(detalle: dict):
    with st.expander("🧠 Razonamiento del agente"):
        st.markdown(f"**Tipo de tarea:** `{detalle.get('tipo')}` — {detalle.get('motivo') or ''}")
        if detalle.get("consulta"):
            st.markdown(f"**Consulta reescrita:** {detalle['consulta']}")
        if detalle.get("plan"):
            st.markdown("**Plan (por prioridad):**")
            st.table(pd.DataFrame(detalle["plan"]))
        st.markdown("**Decisiones:**")
        for d in detalle.get("decisiones", []):
            st.markdown(f"- {d}")
        if detalle.get("herramientas"):
            st.markdown("**Herramientas usadas:** " + ", ".join(f"`{h}`" for h in detalle["herramientas"]))
        if detalle.get("documentos"):
            st.markdown("**Fragmentos recuperados:**")
            for d in detalle["documentos"]:
                st.caption(f"[{d['fuente']} · score {d['score']}] {d['contenido'][:300]}...")


def procesar(resultado: dict):
    st.session_state.pendiente = resultado["pendiente_aprobacion"]
    if not resultado["pendiente_aprobacion"]:
        n = len(agente.historial(st.session_state.thread_id))
        st.session_state.detalles[n - 1] = resultado


tab_chat, tab_tickets, tab_planes, tab_trazas, tab_eval = st.tabs(
    ["💬 Chat", "📋 Panel coordinador", "🗓️ Planes", "🔎 Trazas", "📊 Evaluación"]
)

with tab_chat:
    historial = agente.historial(st.session_state.thread_id)
    for i, m in enumerate(historial):
        with st.chat_message(m["rol"]):
            st.markdown(m["contenido"])
            if m["rol"] == "assistant" and i in st.session_state.detalles:
                mostrar_detalle(st.session_state.detalles[i])

    if st.session_state.pendiente:
        with st.chat_message("assistant"):
            st.warning("Necesito tu autorización antes de escribir:")
            for accion in st.session_state.pendiente["acciones"]:
                st.markdown(f"**Acción:** {NOMBRES_HERRAMIENTAS.get(accion['herramienta'], accion['herramienta'])}")
                st.json(accion["argumentos"])
            c1, c2 = st.columns(2)
            if c1.button("✅ Autorizar", width="stretch"):
                with st.spinner("Ejecutando..."):
                    procesar(agente.responder_aprobacion(st.session_state.thread_id, True))
                st.rerun()
            if c2.button("❌ Rechazar", width="stretch"):
                with st.spinner("Continuando sin escribir..."):
                    procesar(agente.responder_aprobacion(st.session_state.thread_id, False))
                st.rerun()

    pregunta = st.chat_input("Pregúntame sobre prácticas, calendario o reglamento...", disabled=bool(st.session_state.pendiente))
    if pregunta:
        with st.chat_message("user"):
            st.markdown(pregunta)
        with st.spinner("Planificando y consultando..."):
            try:
                procesar(agente.preguntar(st.session_state.usuario, st.session_state.thread_id, pregunta))
            except Exception as e:
                st.error(f"Error del agente: {type(e).__name__}: {e}")
        st.rerun()

with tab_tickets:
    st.subheader("Tickets de derivación")
    estado = st.radio("Estado", ["abierto", "cerrado", "todos"], horizontal=True)
    tickets = db.listar_tickets(None if estado == "todos" else estado)
    if tickets:
        st.dataframe(pd.DataFrame(tickets)[["numero", "creado_en", "user_id", "prioridad", "estado", "motivo", "resumen"]],
                     width="stretch", hide_index=True)
        abiertos = [t["numero"] for t in tickets if t["estado"] == "abierto"]
        if abiertos:
            numero = st.selectbox("Cerrar ticket", abiertos)
            if st.button("Marcar como atendido"):
                db.cerrar_ticket(numero)
                st.rerun()
    else:
        st.caption("No hay tickets.")

with tab_planes:
    st.subheader(f"Planes guardados de {st.session_state.usuario}")
    planes = db.listar_planes(st.session_state.usuario, limite=10)
    if not planes:
        st.caption("Todavía no hay planes. Pídele al agente que te arme uno.")
    for p in planes:
        with st.expander(f"#{p['numero']} · {p['titulo']} · {p['creado_en']}"):
            st.table(pd.DataFrame(p["items"]))
            if p.get("archivo") and Path(p["archivo"]).exists():
                md = Path(p["archivo"])
                ics = md.with_suffix(".ics")
                c1, c2 = st.columns(2)
                c1.download_button("Descargar .md", md.read_bytes(), file_name=md.name, key=f"md{p['numero']}")
                if ics.exists():
                    c2.download_button("Descargar .ics (calendario)", ics.read_bytes(), file_name=ics.name, key=f"ics{p['numero']}")

with tab_trazas:
    st.subheader("Últimas interacciones registradas")
    trazas = db.listar_trazas(st.session_state.usuario)
    if trazas:
        for t in trazas:
            with st.expander(f"{t['creado_en']} · {t.get('tipo')} · {t.get('latencia_ms')} ms · {t['pregunta'][:60]}"):
                st.markdown(f"**Respuesta:** {t.get('respuesta')}")
                st.markdown("**Herramientas:** " + (", ".join(t.get("herramientas") or []) or "ninguna"))
                for d in t.get("decisiones", []):
                    st.caption(f"• {d}")
    else:
        st.caption("Sin trazas todavía.")

with tab_eval:
    carpeta = Path("eval/resultados")
    evaluaciones = sorted(carpeta.glob("resultados_*.md"), reverse=True)
    escenarios = sorted(carpeta.glob("escenarios_*.md"), reverse=True)
    if not evaluaciones and not escenarios:
        st.caption("Todavía no hay resultados. Corre `python -m eval.evaluar` y `python -m eval.escenarios`.")
    if evaluaciones:
        elegido = st.selectbox("Corrida de evaluación", evaluaciones, format_func=lambda p: p.stem)
        st.markdown(elegido.read_text(encoding="utf-8"))
    if escenarios:
        with st.expander("Evidencia de escenarios (decisiones adaptativas)"):
            st.markdown(escenarios[0].read_text(encoding="utf-8"))
