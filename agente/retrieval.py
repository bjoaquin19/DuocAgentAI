from functools import lru_cache

from agente import config


@lru_cache(maxsize=1)
def _reranker():
    from sentence_transformers import CrossEncoder
    return CrossEncoder(config.RERANKER_MODEL)


class Retriever:
    def __init__(self, db, embedder, usar_reranker: bool = config.USAR_RERANKER):
        self.db = db
        self.embedder = embedder
        self.usar_reranker = usar_reranker

    def buscar(self, consulta: str, fuente: str | None = None, top_k: int = config.TOP_K_FINAL) -> list[dict]:
        embedding = self.embedder.get_embedding(consulta)
        n = config.TOP_K_CANDIDATOS if self.usar_reranker else top_k
        candidatos = self.db.buscar_documentos(embedding, top_k=n, fuente=fuente)
        if not self.usar_reranker or not candidatos:
            return candidatos[:top_k]
        scores = _reranker().predict([(consulta, c["contenido"]) for c in candidatos])
        for c, s in zip(candidatos, scores):
            c["rerank_score"] = float(s)
        candidatos.sort(key=lambda c: c["rerank_score"], reverse=True)
        return candidatos[:top_k]
