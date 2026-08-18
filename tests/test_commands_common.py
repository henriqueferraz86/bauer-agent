"""Testes para bauer/commands/_common.py — primitivas compartilhadas da CLI.

Cobre `_parse_last`, extraído em 2026-08-18 de três cópias divergentes
(audit_cmd.py, perf_cmd.py, skills_cmd.py) — a cópia de skills_cmd.py usava
`datetime.now()` (naive local) em vez de UTC-aware, um bug real que a
unificação corrige.
"""
from __future__ import annotations

from datetime import timezone

import pytest
import typer

from bauer.commands._common import _parse_last


def test_parse_last_vazio_retorna_none():
    assert _parse_last("") is None


def test_parse_last_e_utc_aware():
    resultado = _parse_last("1h")
    assert resultado is not None
    assert resultado.tzinfo is not None
    assert resultado.utcoffset() == timezone.utc.utcoffset(None)


def test_parse_last_formato_invalido_levanta_bad_parameter():
    with pytest.raises(typer.BadParameter):
        _parse_last("um dia")


@pytest.mark.parametrize("unidade,segundos", [("m", 60), ("h", 3600), ("d", 86400), ("w", 604800)])
def test_parse_last_unidades(unidade, segundos):
    from datetime import datetime

    antes = datetime.now(timezone.utc)
    resultado = _parse_last(f"1{unidade}")
    depois = datetime.now(timezone.utc)
    delta = (antes - resultado).total_seconds()
    # Tolerância pelo tempo de execução do teste em si.
    assert segundos - 2 <= delta <= (depois - antes).total_seconds() + segundos + 2
