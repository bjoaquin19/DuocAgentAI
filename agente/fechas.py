from datetime import date, datetime

from agente import config

MESES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto",
         "septiembre", "octubre", "noviembre", "diciembre"]


def hoy() -> date:
    if config.FECHA_HOY:
        return datetime.strptime(config.FECHA_HOY, "%Y-%m-%d").date()
    return date.today()


def hoy_texto() -> str:
    d = hoy()
    return f"{d.day} de {MESES[d.month - 1]} de {d.year}"


def parsear(fecha: str) -> date:
    fecha = fecha.strip()
    for formato in ("%d-%m-%Y", "%Y-%m-%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(fecha, formato).date()
        except ValueError:
            continue
    raise ValueError(f"Formato de fecha no reconocido: {fecha}. Usa DD-MM-AAAA.")


def formatear(d: date) -> str:
    return d.strftime("%d-%m-%Y")
