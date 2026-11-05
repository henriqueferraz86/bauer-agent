"""Cenários da suíte. O import é o que registra — ver `runner.cenario`."""

from __future__ import annotations


def carregar_tudo() -> None:
    from . import agno_evals, governanca, resiliencia  # noqa: F401
