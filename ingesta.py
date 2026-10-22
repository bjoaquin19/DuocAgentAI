import argparse

from agente.db import BaseDatos
from agente.embeddings import EmbeddingClient
from agente.ingesta import Ingestor

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Carga los documentos a MongoDB Atlas")
    parser.add_argument("--reset", action="store_true", help="borra y vuelve a cargar todo")
    parser.add_argument("--sin-externas", action="store_true", help="no descarga las páginas web")
    args = parser.parse_args()
    Ingestor(BaseDatos(), EmbeddingClient()).ingestar_todo(reset=args.reset, externas=not args.sin_externas)
