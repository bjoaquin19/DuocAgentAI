from functools import lru_cache

from agente import config


@lru_cache(maxsize=1)
def _modelo():
    from sentence_transformers import SentenceTransformer
    return SentenceTransformer(config.EMBEDDING_MODEL)


class EmbeddingClient:
    def get_embedding(self, texto: str) -> list[float]:
        return _modelo().encode(texto).tolist()

    def get_embeddings(self, textos: list[str]) -> list[list[float]]:
        return _modelo().encode(textos, batch_size=32, show_progress_bar=len(textos) > 50).tolist()
