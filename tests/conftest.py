import pytest

from agente import config


@pytest.fixture(autouse=True)
def fecha_fija(monkeypatch):
    monkeypatch.setattr(config, "FECHA_HOY", "2026-07-20")
