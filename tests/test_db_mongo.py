import mongomock

from agente.db import BaseDatos


def _db():
    return BaseDatos(cliente=mongomock.MongoClient(), db_name="test", crear_indices=False)


def test_perfil_se_actualiza_sin_perder_datos():
    db = _db()
    db.actualizar_perfil("u1", {"carrera": "Informática", "semestre": 1})
    perfil = db.actualizar_perfil("u1", {"semestre": 4})
    assert perfil == {"carrera": "Informática", "semestre": 4}
    assert db.leer_perfil("u2") == {}


def test_planes_y_tickets_numerados():
    db = _db()
    assert db.guardar_plan("u1", "Plan", [{"tarea": "x"}], None) == 1
    assert db.guardar_plan("u1", "Plan 2", [{"tarea": "y"}], None) == 2
    assert db.listar_planes("u1")[0]["titulo"] == "Plan 2"
    n = db.crear_ticket("u1", "motivo", "resumen", "alta")
    db.cerrar_ticket(n)
    assert db.listar_tickets("abierto") == []
    assert db.listar_tickets()[0]["estado"] == "cerrado"


def test_borrar_usuario():
    db = _db()
    db.actualizar_perfil("u1", {"a": 1})
    db.guardar_memoria("u1", "hecho", [0.1] * 3)
    db.borrar_usuario("u1")
    assert db.leer_perfil("u1") == {} and db.listar_memorias("u1") == []


def test_tickets_por_notificar():
    db = _db()
    n = db.crear_ticket("u1", "m", "r", "media")
    assert [t["numero"] for t in db.listar_tickets_por_notificar()] == [n]
    assert db.marcar_ticket_notificado(n)
    assert db.listar_tickets_por_notificar() == []
    assert not db.marcar_ticket_notificado(99)
