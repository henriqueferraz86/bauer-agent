"""Cenários da suíte. O import é o que registra — ver `runner.cenario`."""

from __future__ import annotations


def carregar_tudo() -> None:
    from . import governanca, resiliencia  # noqa: F401
