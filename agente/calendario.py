import re
import unicodedata
from dataclasses import dataclass
from datetime import date
from functools import lru_cache
from pathlib import Path

from agente import config, fechas
from agente.ingesta import extraer_texto_pdf

PERIODOS = {
    "TEMPORADA ACADÉMICA DE VERANO (TAV)": "TAV",
    "PRIMER SEMESTRE 2026 (2026-1)": "2026-1",
    "SEGUNDO SEMESTRE 2026 (2026-2)": "2026-2",
}
PATRON_RANGO = re.compile(r"([^:.]+?):\s*Inicio:\s*(\d{2}-\d{2}-\d{4})\.\s*Término:\s*(\d{2}-\d{2}-\d{4})\.")
PATRON_FECHA = re.compile(r"([^:.]+?):\s*Fecha:\s*(\d{2}-\d{2}-\d{4})\.")
PATRON_FERIADO = re.compile(r"(\d{2}-\d{2}-\d{4})(?:\s*al\s*(\d{2}-\d{2}-\d{4}))?:\s*([^.]+)\.")
TITULOS_SECCION = ("MATRÍCULAS", "PERÍODO ACADÉMICO", "INSCRIPCIÓN DE ASIGNATURAS", "DIAGNÓSTICOS",
                   "PROCESOS REGLAMENTARIOS", "EXÁMENES", "ENCUESTA DOCENTE", "ASUNTOS ESTUDIANTILES", "PASTORAL")


@dataclass
class Evento:
    periodo: str
    nombre: str
    inicio: date
    termino: date

    def estado(self, referencia: date) -> str:
        if referencia < self.inicio:
            return "próximo"
        if referencia > self.termino:
            return "terminado"
        return "vigente"

    def como_dict(self, referencia: date) -> dict:
        d = {"periodo": self.periodo, "evento": self.nombre, "inicio": fechas.formatear(self.inicio),
             "termino": fechas.formatear(self.termino), "estado": self.estado(referencia)}
        if referencia < self.inicio:
            d["dias_para_inicio"] = (self.inicio - referencia).days
        elif referencia <= self.termino:
            d["dias_para_termino"] = (self.termino - referencia).days
        return d


def normalizar(texto: str) -> str:
    texto = unicodedata.normalize("NFD", texto.lower())
    return "".join(c for c in texto if unicodedata.category(c) != "Mn")


def _limpiar_nombre(nombre: str) -> str:
    nombre = " ".join(nombre.split())
    for titulo in TITULOS_SECCION:
        if nombre.startswith(titulo + " "):
            nombre = nombre[len(titulo) + 1:]
    if nombre.startswith("FERIADOS Y FECHAS ESPECIALES"):
        nombre = ""
    return nombre.strip()


def parsear_calendario(texto: str) -> list[Evento]:
    texto = " ".join(texto.split())
    eventos = []
    secciones = re.split(r"={10,}\s*(.+?)\s*={10,}", texto)
    for i in range(1, len(secciones), 2):
        periodo = PERIODOS.get(secciones[i].strip())
        if not periodo:
            continue
        cuerpo = secciones[i + 1]
        feriados = ""
        if "FERIADOS Y FECHAS ESPECIALES" in cuerpo:
            cuerpo, feriados = cuerpo.split("FERIADOS Y FECHAS ESPECIALES", 1)
        for m in PATRON_RANGO.finditer(cuerpo):
            eventos.append(Evento(periodo, _limpiar_nombre(m.group(1)), fechas.parsear(m.group(2)), fechas.parsear(m.group(3))))
        for m in PATRON_FECHA.finditer(cuerpo):
            d = fechas.parsear(m.group(2))
            eventos.append(Evento(periodo, _limpiar_nombre(m.group(1)), d, d))
        for m in PATRON_FERIADO.finditer(feriados):
            inicio = fechas.parsear(m.group(1))
            termino = fechas.parsear(m.group(2)) if m.group(2) else inicio
            eventos.append(Evento(periodo, " ".join(m.group(3).split()), inicio, termino))
    return eventos


@lru_cache(maxsize=1)
def cargar_eventos(ruta: str = config.ARCHIVO_CALENDARIO) -> tuple[Evento, ...]:
    if not Path(ruta).exists():
        return tuple()
    return tuple(parsear_calendario(extraer_texto_pdf(Path(ruta))))


SINONIMOS = {
    "inscripcion": "inscripción de asignaturas",
    "toma de ramos": "inscripción de asignaturas",
    "ramos": "asignaturas",
    "clases": "período de clases",
    "examen": "exámenes",
    "retiro": "retiro de asignaturas",
    "botar": "retiro",
    "matricula": "matrícula",
    "vacaciones": "cierre académico",
}


def buscar_eventos(consulta: str, periodo: str | None = None, eventos=None) -> list[Evento]:
    eventos = cargar_eventos() if eventos is None else eventos
    consulta_n = normalizar(consulta)
    for k, v in SINONIMOS.items():
        if k in consulta_n:
            consulta_n += " " + normalizar(v)
    palabras = {p for p in re.findall(r"\w+", consulta_n) if len(p) > 3}
    puntajes = []
    for e in eventos:
        if periodo and e.periodo != periodo:
            continue
        nombre = normalizar(e.nombre)
        puntaje = sum(1 for p in palabras if p in nombre)
        if puntaje:
            puntajes.append((puntaje, e))
    if not puntajes:
        return []
    mejor = max(p for p, _ in puntajes)
    return [e for p, e in puntajes if p == mejor]


def proximos_eventos(referencia: date, dias: int = 60, eventos=None) -> list[Evento]:
    eventos = cargar_eventos() if eventos is None else eventos
    return sorted(
        [e for e in eventos if e.termino >= referencia and (e.inicio - referencia).days <= dias],
        key=lambda e: e.inicio,
    )
