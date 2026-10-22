from langgraph.checkpoint.memory import MemorySaver

from fakes import FakeLLM, crear_servicios_falsos, tool_call

from agente import config, prompts
from agente.grafo import HechosNuevos, Paso, Plan, Validacion
from agente.orquestador import Orquestador

DOCS = [
    ("La Práctica Laboral dura 180 horas y se realiza cerca del cuarto semestre.", "practicas"),
    ("Artículo N°30. La asistencia mínima es de 70% en cada asignatura.", "reglamento_academico"),
]


def plan(tipo, *herramientas, motivo="test"):
    return Plan(tipo=tipo, motivo=motivo, consulta_reescrita="consulta",
                pasos=[Paso(descripcion=h, herramienta=h, prioridad=i + 1) for i, h in enumerate(herramientas)])


def crear(tmp_path, llm):
    servicios = crear_servicios_falsos(tmp_path, DOCS)
    return Orquestador(llm=llm, servicios=servicios, checkpointer=MemorySaver()), servicios


def test_consulta_simple_usa_herramienta_y_valida(tmp_path):
    llm = FakeLLM(
        respuestas_agente=[[tool_call("consultar_calendario", {"evento": "inscripción de asignaturas", "periodo": "2026-2"})],
                           "La inscripción ordinaria es del 27-07-2026 al 30-07-2026 [Fuente: calendario_academico]"],
        estructurados={"Plan": plan("consulta_simple", "consultar_calendario"), "Validacion": Validacion(respaldada=True)},
    )
    agente, servicios = crear(tmp_path, llm)
    r = agente.preguntar("u1", "t1", "¿Cuándo es la toma de ramos del segundo semestre?")
    assert "27-07-2026" in r["respuesta"]
    assert r["herramientas"] == ["consultar_calendario"]
    assert any("Validador: respuesta respaldada" in d for d in r["decisiones"])
    assert servicios.db.trazas[-1]["herramientas"] == ["consultar_calendario"]


def test_saludo_no_usa_herramientas(tmp_path):
    llm = FakeLLM(estructurados={"Plan": plan("saludo")})
    agente, _ = crear(tmp_path, llm)
    r = agente.preguntar("u1", "t1", "hola!")
    assert r["respuesta"] == llm.texto
    assert r["herramientas"] == [] and llm.llamadas_agente == []


def test_fuera_de_alcance(tmp_path):
    agente, _ = crear(tmp_path, FakeLLM(estructurados={"Plan": plan("fuera_de_alcance")}))
    assert agente.preguntar("u1", "t1", "¿quién ganó el mundial?")["respuesta"] == prompts.MENSAJE_FUERA_DE_DOMINIO


def test_escritura_pide_aprobacion_y_guarda_si_acepta(tmp_path):
    items = [{"tarea": "Inscribir asignaturas", "fecha": "27-07-2026", "fuente": "calendario_academico"}]
    llm = FakeLLM(
        respuestas_agente=[[tool_call("guardar_plan_inicio", {"titulo": "Plan 2026-2", "items": items})],
                           "Listo, guardé tu plan."],
        estructurados={"Plan": plan("multi_etapa", "consultar_calendario", "guardar_plan_inicio")},
    )
    agente, servicios = crear(tmp_path, llm)
    r = agente.preguntar("u1", "t1", "Ármame un plan y guárdalo")
    assert r["pendiente_aprobacion"]["acciones"][0]["herramienta"] == "guardar_plan_inicio"
    assert servicios.db.planes == []
    r = agente.responder_aprobacion("t1", True)
    assert r["respuesta"] == "Listo, guardé tu plan."
    assert len(servicios.db.planes) == 1
    assert len(list((tmp_path / "salidas").glob("*.ics"))) == 1


def test_escritura_rechazada_no_guarda(tmp_path):
    llm = FakeLLM(
        respuestas_agente=[[tool_call("crear_ticket_derivacion", {"motivo": "beca", "resumen": "consulta beca"})],
                           "Ok, no creé el ticket."],
        estructurados={"Plan": plan("consulta_simple", "crear_ticket_derivacion")},
    )
    agente, servicios = crear(tmp_path, llm)
    agente.preguntar("u1", "t1", "deriva mi caso")
    r = agente.responder_aprobacion("t1", False)
    assert servicios.db.tickets == []
    assert any("rechazó" in d for d in r["decisiones"])


def test_cambio_de_perfil_dispara_replanificacion(tmp_path):
    llm = FakeLLM(
        respuestas_agente=[[tool_call("actualizar_perfil", {"semestre": 4})],
                           [tool_call("verificar_etapa_practica", {"semestre_actual": 4, "tipo_carrera": "profesional"})],
                           "Como estás en 4° semestre te corresponde la práctica laboral (180 horas) [Fuente: practicas]"],
        estructurados={"Plan": [plan("multi_etapa", "actualizar_perfil", "verificar_etapa_practica"),
                                plan("multi_etapa", "verificar_etapa_practica")],
                       "Validacion": Validacion(respaldada=True)},
    )
    agente, servicios = crear(tmp_path, llm)
    servicios.db.actualizar_perfil("u1", {"semestre": 2, "tipo_carrera": "profesional"})
    r = agente.preguntar("u1", "t1", "Me equivoqué, estoy en cuarto semestre. ¿Me toca práctica?")
    assert llm.contar("Plan") == 2
    assert any("Re-planificación" in d for d in r["decisiones"])
    assert servicios.db.leer_perfil("u1")["semestre"] == 4
    assert r["perfil"]["semestre"] == 4


def test_validador_rechaza_dos_veces_y_responde_seguro(tmp_path):
    llm = FakeLLM(
        respuestas_agente=[[tool_call("buscar_documentos", {"consulta": "horas práctica profesional"})],
                           "La práctica profesional dura 360 horas.",
                           "Son 360 horas."],
        estructurados={"Plan": plan("consulta_simple", "buscar_documentos"),
                       "Validacion": Validacion(respaldada=False, problema="360 horas no aparece")},
    )
    agente, _ = crear(tmp_path, llm)
    r = agente.preguntar("u1", "t1", "¿Cuántas horas dura la práctica profesional?")
    assert r["respuesta"] == prompts.MENSAJE_SIN_INFO
    assert sum("Validador rechazó" in d for d in r["decisiones"]) == 2
    assert len(r["documentos"]) >= 1


def test_memoria_largo_plazo_entre_conversaciones(tmp_path):
    llm = FakeLLM(estructurados={"Plan": plan("saludo"),
                                 "HechosNuevos": HechosNuevos(hechos=["Reprobó Matemáticas en 2026-1"])})
    agente, servicios = crear(tmp_path, llm)
    agente.preguntar("u1", "conversacion-1", "hola, reprobé matemáticas")
    assert servicios.db.listar_memorias("u1")[0]["texto"] == "Reprobó Matemáticas en 2026-1"
    r = agente.preguntar("u1", "conversacion-2", "hola de nuevo, ¿qué pasa si repruebo matemáticas otra vez?")
    assert r["memorias"][0]["texto"] == "Reprobó Matemáticas en 2026-1"
    assert len(servicios.db.listar_memorias("u1")) == 1


def test_memoria_corto_plazo_mantiene_historial(tmp_path):
    llm = FakeLLM(estructurados={"Plan": plan("saludo")})
    agente, _ = crear(tmp_path, llm)
    agente.preguntar("u1", "t1", "hola")
    agente.preguntar("u1", "t1", "soy Camila")
    assert [m["rol"] for m in agente.historial("t1")] == ["user", "assistant", "user", "assistant"]


def test_resumen_cuando_la_conversacion_es_larga(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "MAX_MENSAJES_ANTES_DE_RESUMIR", 4)
    monkeypatch.setattr(config, "VENTANA_MENSAJES", 2)
    llm = FakeLLM(estructurados={"Plan": plan("saludo")}, texto="ok")
    agente, _ = crear(tmp_path, llm)
    for i in range(3):
        r = agente.preguntar("u1", "t1", f"mensaje {i}")
    assert r["resumen"] == "ok"
    assert len(agente.historial("t1")) <= 4


def test_plan_marca_pasos_completados(tmp_path):
    llm = FakeLLM(
        respuestas_agente=[[tool_call("consultar_calendario", {"evento": "clases", "periodo": "2026-2"})],
                           [tool_call("calcular_dias", {"fecha": "10-08-2026"})],
                           "Las clases empiezan el 10-08-2026, faltan 21 días [Fuente: calendario_academico]"],
        estructurados={"Plan": plan("multi_etapa", "consultar_calendario", "calcular_dias"), "Validacion": Validacion(respaldada=True)},
    )
    agente, _ = crear(tmp_path, llm)
    r = agente.preguntar("u1", "t1", "¿Cuánto falta para que empiecen las clases?")
    assert [p["estado"] for p in r["plan"]] == ["hecho", "hecho"]
    assert any("Progreso del plan" in d for d in r["decisiones"])
    assert "[hecho] consultar_calendario" in llm.llamadas_agente[-1][0].content


def test_reintenta_si_el_modelo_falla(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "ESPERA_REINTENTO_S", 0)
    llm = FakeLLM(respuestas_agente=[RuntimeError("tool_use_failed"), "Respuesta luego del reintento"],
                  estructurados={"Plan": plan("consulta_simple")})
    agente, _ = crear(tmp_path, llm)
    r = agente.preguntar("u1", "t1", "pregunta")
    assert r["respuesta"] == "Respuesta luego del reintento"
    assert any("intento 2" in d for d in r["decisiones"])


def test_error_controlado_si_el_modelo_falla_siempre(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "ESPERA_REINTENTO_S", 0)
    llm = FakeLLM(respuestas_agente=[RuntimeError("x")] * 3, estructurados={"Plan": plan("consulta_simple")})
    agente, _ = crear(tmp_path, llm)
    assert agente.preguntar("u1", "t1", "pregunta")["respuesta"] == prompts.MENSAJE_ERROR_TECNICO
