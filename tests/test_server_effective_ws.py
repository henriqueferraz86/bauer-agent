"""Regressão: router mockado não deve gerar Path-lixo no prefetch de memória.

Antes do fix, os endpoints do serve faziam ``Path(active_router.workspace)``
direto no call site. Com um router mockado (comum nos testes), isso produzia
``Path("MagicMock/mock.workspace/<id>")`` — um Path válido que passava pelo
guard ``isinstance(x, Path)`` a jusante e fazia o prefetch criar
``decisions.db``/``sessions`` na CWD (poluição do repo). ``_effective_ws``
fecha isso na origem: só converte quando o workspace é um caminho real.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from bauer.server import _effective_ws


def test_mock_router_yields_none():
    # MagicMock().workspace é outro MagicMock — não é caminho de verdade.
    assert _effective_ws(MagicMock()) is None


def test_router_without_workspace_attr_yields_none():
    class _Bare:
        pass

    assert _effective_ws(_Bare()) is None


def test_str_workspace_becomes_path(tmp_path):
    router = MagicMock()
    router.workspace = str(tmp_path)
    assert _effective_ws(router) == Path(tmp_path)


def test_path_workspace_preserved(tmp_path):
    router = MagicMock()
    router.workspace = tmp_path
    assert _effective_ws(router) == tmp_path


def test_bytes_workspace_yields_none(tmp_path):
    # Path() não aceita bytes; tratamos como "não é caminho de verdade".
    router = MagicMock()
    router.workspace = str(tmp_path).encode()
    assert _effective_ws(router) is None
