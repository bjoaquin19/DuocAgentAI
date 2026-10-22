from pathlib import Path

from fakes import FakeDB, FakeEmbedder

from agente.ingesta import CONFIG_FUENTES, Ingestor, dividir_en_chunks, extraer_texto_pdf, limpiar_texto

INTERNOS = Path("data/internos")


def test_limpiar_texto_une_palabras_sueltas():
    assert limpiar_texto("FUENTE\n \nDE\n \nCONOCIMIENTO") == "FUENTE DE CONOCIMIENTO"


def test_chunks_de_los_pdfs_reales():
    practicas = dividir_en_chunks(extraer_texto_pdf(INTERNOS / "Horas de practica duocUc.pdf"), CONFIG_FUENTES["practica"])
    calendario = dividir_en_chunks(extraer_texto_pdf(INTERNOS / "calendario DuocUC.pdf"), CONFIG_FUENTES["calendario"])
    assert len(practicas) == 2
    assert sum(c.startswith("[") for c in calendario) >= len(calendario) - 1
    assert not any("REGLAS DE INTERPRETACIÓN" in c for c in calendario)


def test_ingesta_no_duplica(monkeypatch):
    from agente import config
    monkeypatch.setattr(config, "CARPETA_INTERNOS", str(INTERNOS))
    db = FakeDB()
    ingestor = Ingestor(db, FakeEmbedder())
    ingestor.ingestar_pdf(INTERNOS / "Horas de practica duocUc.pdf")
    ingestor.ingestar_pdf(INTERNOS / "Horas de practica duocUc.pdf")
    assert db.contar_por_fuente() == {"practicas": 2}
