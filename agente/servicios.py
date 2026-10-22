from dataclasses import dataclass, field
from pathlib import Path

from agente import config


@dataclass
class Servicios:
    db: object
    retriever: object
    embedder: object
    carpeta_salidas: Path = field(default_factory=lambda: Path(config.CARPETA_SALIDAS))


def crear_servicios() -> Servicios:
    from agente.db import BaseDatos
    from agente.embeddings import EmbeddingClient
    from agente.retrieval import Retriever

    db = BaseDatos()
    embedder = EmbeddingClient()
    return Servicios(db=db, retriever=Retriever(db, embedder), embedder=embedder)
