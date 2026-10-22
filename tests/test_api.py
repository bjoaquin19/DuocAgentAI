from fastapi.testclient import TestClient
from langgraph.checkpoint.memory import MemorySaver

from fakes import FakeLLM, crear_servicios_falsos, tool_call

from agente import config
from agente.grafo import Paso, Plan
from agente.orquestador import Orquestador
from api import crear_app


def cliente(tmp_path, llm):
    servicios = crear_servicios_falsos(tmp_path)
    orq = Orquestador(llm=llm, servicios=servicios, checkpointer=MemorySaver())
    return TestClient(crear_app(orq)), servicios


def plan(tipo, *h):
    return Plan(tipo=tipo, motivo="m", consulta_reescrita="c",
                pasos=[Paso(descripcion=x, herramienta=x, prioridad=i + 1) for i, x in enumerate(h)])


def test_preguntar_y_salud(tmp_path):
    c, _ = cliente(tmp_path, FakeLLM(estructurados={"Plan": plan("saludo")}))
    assert c.get("/salud").json()["estado"] == "ok"
    r = c.post("/preguntar", json={"usuario": "u1", "mensaje": "hola"}).json()
    assert r["respuesta"] and r["conversacion"].startswith("u1-") and r["pendiente_aprobacion"] is None


def test_flujo_de_aprobacion_y_aviso_de_tickets(tmp_path):
    llm = FakeLLM(respuestas_agente=[[tool_call("crear_ticket_derivacion", {"motivo": "beca", "resumen": "no le responden"})], "Ticket creado."],
                  estructurados={"Plan": plan("consulta_simple", "crear_ticket_derivacion")})
    c, servicios = cliente(tmp_path, llm)
    r = c.post("/preguntar", json={"usuario": "u1", "mensaje": "deriva mi caso", "conversacion": "t1"}).json()
    assert r["pendiente_aprobacion"]["acciones"][0]["herramienta"] == "crear_ticket_derivacion"
    r = c.post("/aprobar", json={"conversacion": "t1", "aprobado": True}).json()
    assert r["respuesta"] == "Ticket creado."
    pendientes = c.get("/tickets/por-notificar").json()
    assert [t["numero"] for t in pendientes] == [1]
    assert c.post("/tickets/1/notificado").status_code == 200
    assert c.get("/tickets/por-notificar").json() == []
    assert c.post("/tickets/99/notificado").status_code == 404


def test_aprobar_sin_pendientes_da_409(tmp_path):
    c, _ = cliente(tmp_path, FakeLLM(estructurados={"Plan": plan("saludo")}))
    c.post("/preguntar", json={"usuario": "u1", "mensaje": "hola", "conversacion": "t1"})
    assert c.post("/aprobar", json={"conversacion": "t1", "aprobado": True}).status_code == 409


def test_api_key(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "API_KEY", "secreta")
    c, _ = cliente(tmp_path, FakeLLM(estructurados={"Plan": plan("saludo")}))
    assert c.post("/preguntar", json={"usuario": "u1", "mensaje": "hola"}).status_code == 401
    assert c.post("/preguntar", json={"usuario": "u1", "mensaje": "hola"}, headers={"X-API-Key": "secreta"}).status_code == 200
