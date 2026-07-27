"""Testes da descoberta MCP (mcp_list_servers / mcp_list_tools).

Sem elas o `mcp_call` era porta opaca: o modelo via uma tool pedindo `server` e
`tool` e nada dizia quais valores existiam, entao servidor configurado so era
usado se o usuario digitasse o nome na mensagem.
"""
from __future__ import annotations

import pytest

from bauer.tools.base import ToolError
from bauer.tools.mcp import McpToolsMixin


class _Router(McpToolsMixin):
    """Mixin isolado — a descoberta nao depende do resto do ToolRouter."""

    def __init__(self, mcp_config=None):
        self._mcp_config = mcp_config


class _Cfg:
    def __init__(self, servers):
        self.servers = servers


# ─── mcp_list_servers ────────────────────────────────────────────────────────

def test_lista_servidores_do_config(monkeypatch):
    monkeypatch.delenv("MCP_SERVER_GRAPHIFY", raising=False)
    router = _Router(_Cfg({
        "graphify": {"command": ["graphify", "serve"], "timeout": 30},
        "fs": {"command": "npx -y server-filesystem /tmp", "timeout": 15},
    }))

    out = router._mcp_list_servers({})

    assert "graphify" in out and "fs" in out
    assert "graphify serve" in out
    assert "npx -y server-filesystem /tmp" in out
    assert "2 servidor" in out


def test_sem_servidores_orienta_como_configurar(monkeypatch):
    import os

    monkeypatch.setattr(
        os, "environ",
        {k: v for k, v in os.environ.items() if not k.startswith("MCP_SERVER_")},
    )
    router = _Router(None)

    out = router._mcp_list_servers({})

    assert "Nenhum servidor MCP configurado" in out
    assert "mcp.servers" in out


def test_env_var_aparece_e_vence_o_config(monkeypatch):
    monkeypatch.setenv("MCP_SERVER_GRAPHIFY", "python -m outro_servidor")
    router = _Router(_Cfg({"graphify": {"command": ["graphify", "serve"]}}))

    out = router._mcp_list_servers({})

    assert "python -m outro_servidor" in out
    assert "graphify serve" not in out, "env var deve vencer o config, como em _resolve_mcp_server"


def test_valores_de_env_nunca_vazam(monkeypatch):
    """env de servidor MCP e onde moram api keys, e o retorno vai p/ o provider."""
    monkeypatch.delenv("MCP_SERVER_SECRETO", raising=False)
    router = _Router(_Cfg({
        "secreto": {"command": ["srv"], "env": {"API_KEY": "sk-nao-pode-vazar"}},
    }))

    out = router._mcp_list_servers({})

    assert "API_KEY" in out
    assert "sk-nao-pode-vazar" not in out
    assert "valores omitidos" in out


# ─── mcp_list_tools ──────────────────────────────────────────────────────────

def test_list_tools_exige_server():
    with pytest.raises(ToolError, match="requer 'server'"):
        _Router()._mcp_list_tools({})


def test_list_tools_formata_nome_descricao_e_args(monkeypatch):
    class _FakeClient:
        def __init__(self, cfg):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def list_tools(self):
            return [{
                "name": "query_graph",
                "description": "Consulta o grafo",
                "inputSchema": {
                    "type": "object",
                    "properties": {"query": {"type": "string"}, "limit": {"type": "integer"}},
                    "required": ["query"],
                },
            }]

    monkeypatch.setattr("bauer.mcp_client.McpClient", _FakeClient)
    router = _Router(_Cfg({"graphify": {"command": ["graphify", "serve"]}}))

    out = router._mcp_list_tools({"server": "graphify"})

    assert "query_graph" in out and "Consulta o grafo" in out
    assert "query:string" in out, "argumento obrigatorio sem '?'"
    assert "limit?:integer" in out, "argumento opcional marcado com '?'"
    assert 'mcp_call(server="graphify"' in out


def test_list_tools_em_servidor_inexistente_orienta(monkeypatch):
    monkeypatch.delenv("MCP_SERVER_FANTASMA", raising=False)
    with pytest.raises(ToolError, match="nao configurado"):
        _Router(_Cfg({}))._mcp_list_tools({"server": "fantasma"})
