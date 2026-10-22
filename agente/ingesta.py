import re
from pathlib import Path

import requests
from bs4 import BeautifulSoup
from langchain_text_splitters import RecursiveCharacterTextSplitter
from pypdf import PdfReader

from agente import config

CONFIG_FUENTES = {
    "reglamento": {
        "fuente": "reglamento_academico",
        "chunk_size": 1000,
        "chunk_overlap": 150,
        "separators": ["\n\nTÍTULO", "\n\nArtículo N°", "\n\n", "\n", ". ", " "],
    },
    "calendario": {
        "fuente": "calendario_academico",
        "chunk_size": 600,
        "chunk_overlap": 100,
        "separators": ["\n\n", "\n", " "],
    },
    "practica": {
        "fuente": "practicas",
        "chunk_size": 700,
        "chunk_overlap": 100,
        "separators": ["\nTIPO:", "\nTEMA:", "\n\n", "\n", " "],
    },
}
CONFIG_DEFAULT = {"fuente": "otros", "chunk_size": 1000, "chunk_overlap": 150, "separators": ["\n\n", "\n", ". ", " "]}
CONFIG_WEB = {"chunk_size": 1000, "chunk_overlap": 150, "separators": ["\n\n", "\n", ". ", " "]}

PATRON_SECCION = re.compile(r"={10,}\s*(.+?)\s*={10,}")
SECCIONES_IGNORADAS = ("REGLAS DE INTERPRETACIÓN",)


def limpiar_texto(texto: str) -> str:
    texto = texto.replace("\r", "")
    texto = re.sub(r"(\n[ \t]*){3,}", "<<PARRAFO>>", texto)
    texto = re.sub(r"\n[ \t]*\n", " ", texto)
    texto = texto.replace("<<PARRAFO>>", "\n\n")
    texto = re.sub(r"[ \t]{2,}", " ", texto)
    return texto.strip()


def preparar_reglamento(texto: str) -> str:
    texto = re.sub(r"\s*(Artículo N°\s?\d+\s?\.)", r"\n\n\1", texto)
    texto = re.sub(r"\s*(TÍTULO [IVXL]+)\s", r"\n\n\1 ", texto)
    return texto


def extraer_texto_pdf(ruta: Path) -> str:
    reader = PdfReader(str(ruta))
    return limpiar_texto("\n".join(p.extract_text() or "" for p in reader.pages))


def extraer_texto_web(url: str) -> str:
    resp = requests.get(url, timeout=20, headers={"User-Agent": "Mozilla/5.0 (DuocAIAgent - proyecto estudiantil)"})
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")
    for tag in soup(["script", "style", "nav", "footer", "header", "noscript"]):
        tag.decompose()
    return re.sub(r"\n\s*\n+", "\n\n", soup.get_text("\n")).strip()


def config_para_archivo(nombre: str) -> dict:
    nombre = nombre.lower()
    for clave, cfg in CONFIG_FUENTES.items():
        if clave in nombre:
            return cfg
    return CONFIG_DEFAULT


def separar_secciones(texto: str) -> list[tuple[str, str]]:
    partes = PATRON_SECCION.split(texto)
    if len(partes) == 1:
        return [("", texto)]
    secciones = [("", partes[0])] if partes[0].strip() else []
    for i in range(1, len(partes), 2):
        secciones.append((partes[i].strip(), partes[i + 1]))
    return secciones


def dividir_en_chunks(texto: str, cfg: dict) -> list[str]:
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=cfg["chunk_size"], chunk_overlap=cfg["chunk_overlap"], separators=cfg["separators"]
    )
    if cfg.get("fuente") == "reglamento_academico":
        texto = preparar_reglamento(texto)
    chunks = []
    for titulo, contenido in separar_secciones(texto):
        if titulo.startswith(SECCIONES_IGNORADAS):
            continue
        for c in splitter.split_text(contenido):
            c = c.strip()
            if len(c) < 80:
                continue
            chunks.append(f"[{titulo}] {c}" if titulo else c)
    return chunks


def leer_fuentes_externas(ruta: str) -> list[str]:
    path = Path(ruta)
    if not path.exists():
        return []
    lineas = [l.strip() for l in path.read_text(encoding="utf-8").splitlines()]
    return [l for l in lineas if l and not l.startswith("#")]


class Ingestor:
    def __init__(self, db, embedder):
        self.db = db
        self.embedder = embedder

    def _guardar(self, chunks: list[str], fuente: str, tipo: str, origen: str):
        embeddings = self.embedder.get_embeddings(chunks)
        self.db.insertar_chunks([
            {"contenido": c, "fuente": fuente, "tipo": tipo, "origen": origen, "chunk_id": i, "embedding": e}
            for i, (c, e) in enumerate(zip(chunks, embeddings))
        ])
        print(f"  -> {len(chunks)} chunks guardados ({fuente})")

    def ingestar_pdf(self, ruta: Path, reset: bool = False):
        if reset:
            self.db.borrar_origen(ruta.name)
        elif self.db.origen_ya_ingestado(ruta.name):
            print(f"  (ya estaba) {ruta.name}")
            return
        cfg = config_para_archivo(ruta.name)
        self._guardar(dividir_en_chunks(extraer_texto_pdf(ruta), cfg), cfg["fuente"], "interna", ruta.name)

    def ingestar_url(self, url: str, reset: bool = False):
        if reset:
            self.db.borrar_origen(url)
        elif self.db.origen_ya_ingestado(url):
            print(f"  (ya estaba) {url}")
            return
        try:
            texto = extraer_texto_web(url)
        except Exception as e:
            print(f"  [WARN] no se pudo descargar {url}: {e}")
            return
        dominio = url.split("/")[2].replace("www.", "")
        self._guardar(dividir_en_chunks(texto, CONFIG_WEB), f"web_{dominio}", "externa", url)

    def ingestar_todo(self, reset: bool = False, externas: bool = True):
        pdfs = sorted(Path(config.CARPETA_INTERNOS).glob("*.pdf"))
        print(f"Fuentes internas: {len(pdfs)} PDF(s)")
        for ruta in pdfs:
            self.ingestar_pdf(ruta, reset)
        if externas:
            urls = leer_fuentes_externas(config.ARCHIVO_EXTERNOS)
            print(f"Fuentes externas: {len(urls)} URL(s)")
            for url in urls:
                self.ingestar_url(url, reset)
        print("\nChunks en la base de datos:")
        for fuente, n in self.db.contar_por_fuente().items():
            print(f"  {fuente}: {n}")
