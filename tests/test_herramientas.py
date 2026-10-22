import json

from fakes import crear_servicios_falsos

from agente import calendario
from agente.herramientas import crear_herramientas, generar_ics


def _tools(tmp_path):
    return {t.name: t for t in crear_herramientas(crear_servicios_falsos(tmp_path))}


def test_calendario_se_parsea_completo():
    eventos = calendario.cargar_eventos()
    assert len(eventos) >= 70
    assert {e.periodo for e in eventos} == {"TAV", "2026-1", "2026-2"}


def test_consultar_calendario_inscripcion_segundo_semestre(tmp_path):
    r = json.loads(_tools(tmp_path)["consultar_calendario"].invoke({"evento": "inscripción de asignaturas", "periodo": "2026-2"}))
    ordinaria = next(e for e in r["eventos"] if "Ordinario" in e["evento"])
    assert ordinaria["inicio"] == "27-07-2026" and ordinaria["termino"] == "30-07-2026"
    assert ordinaria["estado"] == "próximo" and ordinaria["dias_para_inicio"] == 7


def test_consultar_calendario_evento_vigente(tmp_path):
    r = json.loads(_tools(tmp_path)["consultar_calendario"].invoke({"evento": "período de exámenes", "periodo": "2026-1"}))
    assert r["eventos"][0]["estado"] == "vigente"


def test_consultar_calendario_sin_resultados(tmp_path):
    r = _tools(tmp_path)["consultar_calendario"].invoke({"evento": "ceremonia de titulación"})
    assert r.startswith("SIN_RESULTADOS")


def test_calcular_dias(tmp_path):
    t = _tools(tmp_path)["calcular_dias"]
    assert "Faltan 21 días" in t.invoke({"fecha": "10-08-2026"})
    assert "ya pasó" in t.invoke({"fecha": "01-07-2026"})
    assert t.invoke({"fecha": "mañana"}).startswith("ERROR")


def test_verificar_etapa_practica(tmp_path):
    t = _tools(tmp_path)["verificar_etapa_practica"]
    primero = json.loads(t.invoke({"semestre_actual": 1, "tipo_carrera": "profesional"}))
    assert "faltan 3" in primero["practica_laboral"]
    cuarto = json.loads(t.invoke({"semestre_actual": 4, "tipo_carrera": "profesional"}))
    assert "etapa en que generalmente" in cuarto["practica_laboral"]
    assert t.invoke({"semestre_actual": 0, "tipo_carrera": "tecnica"}).startswith("ERROR")


def test_generar_ics_omite_items_sin_fecha():
    ics = generar_ics("Plan", [{"tarea": "Inscribir ramos", "fecha": "27-07-2026"}, {"tarea": "Hablar con DC"}])
    assert ics.count("BEGIN:VEVENT") == 1
    assert "DTSTART;VALUE=DATE:20260727" in ics
