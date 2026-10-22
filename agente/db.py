import time
from datetime import datetime, timezone

from pymongo import DESCENDING, MongoClient
from pymongo.operations import SearchIndexModel

from agente import config


def _ahora():
    return datetime.now(timezone.utc)


def _fecha(d: datetime, con_hora: bool = False) -> str:
    return d.strftime("%d-%m-%Y %H:%M" if con_hora else "%d-%m-%Y") if isinstance(d, datetime) else str(d)


def crear_cliente(uri: str | None = None) -> MongoClient:
    uri = uri or config.MONGODB_URI
    if not uri:
        raise ValueError("Falta MONGODB_URI en el .env")
    opciones = {}
    if uri.startswith("mongodb+srv"):
        import certifi
        opciones["tlsCAFile"] = certifi.where()
    return MongoClient(uri, **opciones)


class BaseDatos:
    def __init__(self, uri: str | None = None, db_name: str = config.MONGODB_DB, cliente: MongoClient | None = None,
                 crear_indices: bool = True):
        self.client = cliente or crear_cliente(uri)
        self.db = self.client[db_name]
        self.documentos = self.db["documentos"]
        self.memorias = self.db["memorias"]
        self.perfiles = self.db["perfiles"]
        self.planes = self.db["planes"]
        self.tickets = self.db["tickets"]
        self.trazas = self.db["trazas"]
        if crear_indices:
            self.crear_indices()

    def crear_indices(self, esperar: bool = True):
        self._crear_indice_vectorial(self.documentos, config.VECTOR_INDEX_DOCUMENTOS, ["fuente", "tipo"], esperar)
        self._crear_indice_vectorial(self.memorias, config.VECTOR_INDEX_MEMORIAS, ["user_id"], esperar)
        self.perfiles.create_index("user_id", unique=True)
        self.planes.create_index([("user_id", 1), ("creado_en", DESCENDING)])
        self.tickets.create_index([("estado", 1), ("creado_en", DESCENDING)])

    def _crear_indice_vectorial(self, coleccion, nombre: str, filtros: list[str], esperar: bool):
        if coleccion.name not in self.db.list_collection_names():
            self.db.create_collection(coleccion.name)
        if nombre in [i["name"] for i in coleccion.list_search_indexes()]:
            return
        print(f"Creando índice vectorial '{nombre}'...")
        coleccion.create_search_index(SearchIndexModel(
            name=nombre,
            type="vectorSearch",
            definition={"fields": [
                {"type": "vector", "path": "embedding", "numDimensions": config.EMBEDDING_DIM, "similarity": "cosine"},
                *[{"type": "filter", "path": f} for f in filtros],
            ]},
        ))
        if not esperar:
            return
        for _ in range(60):
            idx = list(coleccion.list_search_indexes(nombre))
            if idx and idx[0].get("queryable"):
                print(f"Índice '{nombre}' listo.")
                return
            time.sleep(2)
        print(f"[WARN] el índice '{nombre}' todavía no está listo, revisar en Atlas -> Search")

    def _vector_search(self, coleccion, indice: str, embedding: list[float], top_k: int, filtro: dict | None, campos: dict):
        etapa = {"index": indice, "path": "embedding", "queryVector": embedding,
                 "numCandidates": max(top_k * 10, 50), "limit": top_k}
        if filtro:
            etapa["filter"] = filtro
        return list(coleccion.aggregate([
            {"$vectorSearch": etapa},
            {"$project": {"_id": 0, **campos, "score": {"$meta": "vectorSearchScore"}}},
        ]))

    def origen_ya_ingestado(self, origen: str) -> bool:
        return self.documentos.count_documents({"origen": origen}) > 0

    def borrar_origen(self, origen: str):
        self.documentos.delete_many({"origen": origen})

    def insertar_chunks(self, chunks: list[dict]):
        ahora = _ahora()
        self.documentos.insert_many([{**c, "creado_en": ahora} for c in chunks])

    def buscar_documentos(self, embedding: list[float], top_k: int = 5, fuente: str | None = None) -> list[dict]:
        return self._vector_search(self.documentos, config.VECTOR_INDEX_DOCUMENTOS, embedding, top_k,
                                   {"fuente": fuente} if fuente else None,
                                   {"contenido": 1, "fuente": 1, "tipo": 1, "origen": 1})

    def contar_por_fuente(self) -> dict:
        filas = self.documentos.aggregate([{"$group": {"_id": "$fuente", "n": {"$sum": 1}}}, {"$sort": {"_id": 1}}])
        return {f["_id"]: f["n"] for f in filas}

    def leer_perfil(self, user_id: str) -> dict:
        fila = self.perfiles.find_one({"user_id": user_id})
        return dict(fila.get("datos", {})) if fila else {}

    def actualizar_perfil(self, user_id: str, cambios: dict) -> dict:
        perfil = {**self.leer_perfil(user_id), **cambios}
        self.perfiles.update_one({"user_id": user_id}, {"$set": {"datos": perfil, "actualizado_en": _ahora()}}, upsert=True)
        return perfil

    def guardar_memoria(self, user_id: str, texto: str, embedding: list[float]):
        self.memorias.insert_one({"user_id": user_id, "texto": texto, "embedding": embedding, "creado_en": _ahora()})

    def buscar_memorias(self, user_id: str, embedding: list[float], top_k: int = 4) -> list[dict]:
        filas = self._vector_search(self.memorias, config.VECTOR_INDEX_MEMORIAS, embedding, top_k,
                                    {"user_id": user_id}, {"texto": 1, "creado_en": 1})
        return [{"texto": f["texto"], "fecha": _fecha(f.get("creado_en")), "score": float(f["score"])} for f in filas]

    def listar_memorias(self, user_id: str) -> list[dict]:
        filas = self.memorias.find({"user_id": user_id}, {"_id": 0, "texto": 1, "creado_en": 1}).sort("creado_en", DESCENDING)
        return [{"texto": f["texto"], "fecha": _fecha(f["creado_en"], True)} for f in filas]

    def _siguiente_numero(self, coleccion) -> int:
        ultimo = coleccion.find_one(sort=[("numero", DESCENDING)])
        return (ultimo["numero"] if ultimo else 0) + 1

    def guardar_plan(self, user_id: str, titulo: str, items: list[dict], archivo: str | None) -> int:
        numero = self._siguiente_numero(self.planes)
        self.planes.insert_one({"numero": numero, "user_id": user_id, "titulo": titulo, "items": items,
                                "archivo": archivo, "creado_en": _ahora()})
        return numero

    def listar_planes(self, user_id: str, limite: int = 3) -> list[dict]:
        filas = self.planes.find({"user_id": user_id}, {"_id": 0}).sort("numero", DESCENDING).limit(limite)
        return [{**f, "creado_en": _fecha(f["creado_en"], True)} for f in filas]

    def crear_ticket(self, user_id: str, motivo: str, resumen: str, prioridad: str) -> int:
        numero = self._siguiente_numero(self.tickets)
        self.tickets.insert_one({"numero": numero, "user_id": user_id, "motivo": motivo, "resumen": resumen,
                                 "prioridad": prioridad, "estado": "abierto", "creado_en": _ahora()})
        return numero

    def listar_tickets(self, estado: str | None = None) -> list[dict]:
        filas = self.tickets.find({"estado": estado} if estado else {}, {"_id": 0}).sort("numero", DESCENDING)
        return [{**f, "creado_en": _fecha(f["creado_en"], True)} for f in filas]

    def listar_tickets_por_notificar(self) -> list[dict]:
        filas = self.tickets.find({"estado": "abierto", "notificado": {"$ne": True}}, {"_id": 0}).sort("numero", 1)
        return [{**f, "creado_en": _fecha(f["creado_en"], True)} for f in filas]

    def marcar_ticket_notificado(self, numero: int) -> bool:
        return self.tickets.update_one({"numero": numero}, {"$set": {"notificado": True, "notificado_en": _ahora()}}).matched_count > 0

    def cerrar_ticket(self, numero: int):
        self.tickets.update_one({"numero": numero}, {"$set": {"estado": "cerrado"}})

    def guardar_traza(self, datos: dict):
        self.trazas.insert_one({**datos, "creado_en": _ahora()})

    def listar_trazas(self, user_id: str, limite: int = 20) -> list[dict]:
        filas = self.trazas.find({"user_id": user_id}, {"_id": 0}).sort("creado_en", DESCENDING).limit(limite)
        return [{**f, "creado_en": _fecha(f["creado_en"], True)} for f in filas]

    def borrar_usuario(self, user_id: str):
        for coleccion in (self.perfiles, self.memorias, self.planes, self.tickets, self.trazas):
            coleccion.delete_many({"user_id": user_id})
