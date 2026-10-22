import os

from dotenv import load_dotenv

load_dotenv()

GROQ_API_KEY = os.getenv("GROQ_API_KEY")
GROQ_MODEL = os.getenv("GROQ_MODEL", "openai/gpt-oss-20b")
GROQ_MODEL_JUEZ = os.getenv("GROQ_MODEL_JUEZ", "openai/gpt-oss-120b")
TEMPERATURA = float(os.getenv("TEMPERATURA", "0.1"))

MONGODB_URI = os.getenv("MONGODB_URI")
MONGODB_DB = os.getenv("MONGODB_DB", "duoc-ai-agent-ev2")
VECTOR_INDEX_DOCUMENTOS = os.getenv("VECTOR_INDEX_DOCUMENTOS", "vector_index_documentos")
VECTOR_INDEX_MEMORIAS = os.getenv("VECTOR_INDEX_MEMORIAS", "vector_index_memorias")

EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "paraphrase-multilingual-MiniLM-L12-v2")
EMBEDDING_DIM = 384
RERANKER_MODEL = os.getenv("RERANKER_MODEL", "cross-encoder/mmarco-mMiniLMv2-L12-H384-v1")
USAR_RERANKER = os.getenv("USAR_RERANKER", "true").lower() == "true"
TOP_K_CANDIDATOS = int(os.getenv("TOP_K_CANDIDATOS", "20"))
TOP_K_FINAL = int(os.getenv("TOP_K_FINAL", "5"))

VENTANA_MENSAJES = int(os.getenv("VENTANA_MENSAJES", "10"))
MAX_MENSAJES_ANTES_DE_RESUMIR = int(os.getenv("MAX_MENSAJES_ANTES_DE_RESUMIR", "16"))
TOP_K_MEMORIAS = int(os.getenv("TOP_K_MEMORIAS", "4"))
MAX_REINTENTOS_VALIDADOR = int(os.getenv("MAX_REINTENTOS_VALIDADOR", "1"))
MAX_REPLANIFICACIONES = int(os.getenv("MAX_REPLANIFICACIONES", "1"))
RECURSION_LIMIT = int(os.getenv("RECURSION_LIMIT", "30"))
MAX_REINTENTOS_LLM = int(os.getenv("MAX_REINTENTOS_LLM", "3"))
ESPERA_REINTENTO_S = float(os.getenv("ESPERA_REINTENTO_S", "2"))

CARPETA_INTERNOS = os.getenv("CARPETA_INTERNOS", "data/internos")
ARCHIVO_EXTERNOS = os.getenv("ARCHIVO_EXTERNOS", "data/externos/fuentes.txt")
ARCHIVO_CALENDARIO = os.getenv("ARCHIVO_CALENDARIO", "data/internos/calendario DuocUC.pdf")
CARPETA_SALIDAS = os.getenv("CARPETA_SALIDAS", "salidas")
FECHA_HOY = os.getenv("FECHA_HOY")

API_KEY = os.getenv("API_KEY")
