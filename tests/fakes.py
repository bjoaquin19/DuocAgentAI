import math
import re
from datetime import datetime

from langchain_core.messages import AIMessage

from agente.retrieval import Retriever
from agente.servicios import Servicios

PALABRAS = ["practica", "laboral", "profesional", "horas", "calendario", "semestre", "clases", "inscripcion",
            "reglamento", "articulo", "asistencia", "nota", "retiro", "matematicas", "reprob", "vespertin"]


class FakeEmbedder:
    def get_embedding(self, texto):
        t = texto.lower().replace("á", "a").replace("í", "i").replace("é", "e").replace("ó", "o")
        v = [float(t.count(p)) for p in PALABRAS]
        return v + [0.01]

    def get_embeddings(self, textos):
        return [self.get_embedding(t) for t in textos]


def _coseno(a, b):
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    return sum(x * y for x, y in zip(a, b)) / (na * nb) if na and nb else 0.0


class FakeDB:
    def __init__(self):
        self.docs, self.perfiles, self.mems, self.planes, self.tickets, self.trazas = [], {}, [], [], [], []

    def origen_ya_ingestado(self, origen):
        return any(d["origen"] == origen for d in self.docs)

    def borrar_origen(self, origen):
        self.docs = [d for d in self.docs if d["origen"] != origen]

    def insertar_chunks(self, chunks):
        self.docs.extend(chunks)

    def buscar_documentos(self, embedding, top_k=5, fuente=None):
        candidatos = [d for d in self.docs if not fuente or d["fuente"] == fuente]
        puntuados = [{**{k: d[k] for k in ("contenido", "fuente", "tipo", "origen")}, "score": _coseno(embedding, d["embedding"])}
                     for d in candidatos]
        puntuados = [p for p in puntuados if p["score"] > 0.2]
        return sorted(puntuados, key=lambda p: p["score"], reverse=True)[:top_k]

    def contar_por_fuente(self):
        r = {}
        for d in self.docs:
            r[d["fuente"]] = r.get(d["fuente"], 0) + 1
        return r

    def leer_perfil(self, user_id):
        return dict(self.perfiles.get(user_id, {}))

    def actualizar_perfil(self, user_id, cambios):
        self.perfiles[user_id] = {**self.perfiles.get(user_id, {}), **cambios}
        return dict(self.perfiles[user_id])

    def guardar_memoria(self, user_id, texto, embedding):
        self.mems.append({"user_id": user_id, "texto": texto, "embedding": embedding, "fecha": datetime.now().strftime("%d-%m-%Y")})

    def buscar_memorias(self, user_id, embedding, top_k=4):
        propias = [{"texto": m["texto"], "fecha": m["fecha"], "score": _coseno(embedding, m["embedding"])}
                   for m in self.mems if m["user_id"] == user_id]
        return sorted(propias, key=lambda m: m["score"], reverse=True)[:top_k]

    def listar_memorias(self, user_id):
        return [{"texto": m["texto"], "fecha": m["fecha"]} for m in self.mems if m["user_id"] == user_id]

    def guardar_plan(self, user_id, titulo, items, archivo):
        self.planes.append({"numero": len(self.planes) + 1, "user_id": user_id, "titulo": titulo, "items": items, "archivo": archivo,
                            "creado_en": datetime.now().strftime("%d-%m-%Y %H:%M")})
        return len(self.planes)

    def listar_planes(self, user_id, limite=3):
        return [p for p in self.planes if p["user_id"] == user_id][-limite:]

    def crear_ticket(self, user_id, motivo, resumen, prioridad):
        self.tickets.append({"numero": len(self.tickets) + 1, "user_id": user_id, "motivo": motivo, "resumen": resumen,
                             "prioridad": prioridad, "estado": "abierto",
                             "creado_en": datetime.now().strftime("%d-%m-%Y %H:%M")})
        return len(self.tickets)

    def listar_tickets(self, estado=None):
        return [t for t in self.tickets if not estado or t["estado"] == estado]

    def listar_tickets_por_notificar(self):
        return [t for t in self.tickets if t["estado"] == "abierto" and not t.get("notificado")]

    def marcar_ticket_notificado(self, numero):
        for t in self.tickets:
            if t["numero"] == numero:
                t["notificado"] = True
                return True
        return False

    def guardar_traza(self, datos):
        self.trazas.append(datos)


def crear_servicios_falsos(tmp_path, docs=None):
    db = FakeDB()
    embedder = FakeEmbedder()
    for i, (contenido, fuente) in enumerate(docs or []):
        db.insertar_chunks([{"contenido": contenido, "fuente": fuente, "tipo": "interna", "origen": f"{fuente}.pdf",
                             "chunk_id": i, "embedding": embedder.get_embedding(contenido)}])
    return Servicios(db=db, retriever=Retriever(db, embedder, usar_reranker=False), embedder=embedder,
                     carpeta_salidas=tmp_path / "salidas")


def tool_call(nombre, args, id_=None):
    return {"name": nombre, "args": args, "id": id_ or f"call_{nombre}_{abs(hash(str(args))) % 10000}", "type": "tool_call"}


class _Estructurado:
    def __init__(self, padre, schema):
        self.padre, self.schema = padre, schema

    def invoke(self, entrada):
        self.padre.llamadas_estructuradas.append((self.schema.__name__, entrada))
        cola = self.padre.estructurados.get(self.schema.__name__)
        if cola is None:
            return self.schema()
        valor = cola.pop(0) if isinstance(cola, list) and len(cola) > 1 else (cola[0] if isinstance(cola, list) else cola)
        return valor(entrada) if callable(valor) and not isinstance(valor, self.schema) else valor


class _ConTools:
    def __init__(self, padre):
        self.padre = padre

    def invoke(self, mensajes):
        self.padre.llamadas_agente.append(mensajes)
        if not self.padre.respuestas_agente:
            return AIMessage("Respuesta final [Fuente: calendario_academico]")
        r = self.padre.respuestas_agente.pop(0)
        if isinstance(r, Exception):
            raise r
        if isinstance(r, AIMessage):
            return r
        if isinstance(r, list):
            return AIMessage("", tool_calls=r)
        return AIMessage(r)


class FakeLLM:
    def __init__(self, respuestas_agente=None, estructurados=None, texto="Hola, ¿en qué te ayudo?"):
        self.respuestas_agente = list(respuestas_agente or [])
        self.estructurados = estructurados or {}
        self.texto = texto
        self.llamadas_agente = []
        self.llamadas_estructuradas = []
        self.tools = []

    def bind_tools(self, tools):
        self.tools = tools
        return _ConTools(self)

    def with_structured_output(self, schema):
        return _Estructurado(self, schema)

    def invoke(self, entrada):
        return AIMessage(self.texto)

    def contar(self, schema):
        return sum(1 for n, _ in self.llamadas_estructuradas if n == schema)
