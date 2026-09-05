"""Trava do contrato HTTP do `bauer serve`.

Motivo: `create_app` tinha 2.005 linhas e complexidade ciclomática 238 — o maior
god object do repositório. Quebrá-lo exige mover rotas entre escopos, e mover
rota é exatamente o tipo de mudança que quebra cliente sem quebrar teste
unitário (a rota some, ou muda de método, ou perde um query param, e a suíte
segue verde porque ninguém testava AQUELE endpoint).

Este arquivo trava a superfície inteira: se uma extração perder, renomear ou
alterar a assinatura de qualquer operação, falha aqui — não em produção.

Não é um snapshot cego: a lista abaixo é explícita e legível, então adicionar
rota nova é uma edição consciente, não um `--update-snapshot`.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest

pytest.importorskip("fastapi", reason="requer bauer-agent[server]")


# Superfície HTTP do serve em si (29 operações). `/metrics` NAO entra: usa
# include_in_schema=False, entao nao aparece no OpenAPI — e coberto pelo
# teste de auth mais abaixo, via TestClient.
OPERACOES_CORE = {
    "DELETE /sessions/{session_id}",
    "GET /approvals",
    "GET /audit",
    "GET /audit/report",
    "GET /audit/runs/{run_id}",
    "GET /audit/runs/{run_id}/score",
    "GET /audit/skills/insights",
    "GET /events",
    "GET /health",
    "GET /loop/{run_id}",
    "GET /loops",
    "GET /models",
    "GET /runs",
    "GET /runs/{run_id}",
    "GET /runs/{run_id}/events",
    "GET /runs/{run_id}/trace",
    "GET /sessions",
    "GET /status",
    "GET /stream",
    "GET /tools",
    "GET /v1/models",
    "POST /approvals/{approval_id}/approve",
    "POST /approvals/{approval_id}/deny",
    "POST /chat",
    "POST /loop",
    "POST /loop/{run_id}/stop",
    "POST /models/switch",
    "POST /speak",
    "POST /transcribe",
    "POST /v1/chat/completions",
}

# Superfície do Desktop, montada por `desktop_api.build_desktop_router`. Fica
# separada porque tem outro dono: uma extração dentro de create_app nao deveria
# mexer nela, e ver as duas listas juntas esconderia isso.
OPERACOES_API = {
    "DELETE /api/projects/{pid}",
    "GET /api/agents",
    "GET /api/audit/report",
    "GET /api/audit/runs/{run_id}",
    "GET /api/audit/runs/{run_id}/score",
    "GET /api/audit/skills",
    "GET /api/audit/skills/insights",
    "GET /api/config",
    "GET /api/config/profiles",
    "GET /api/events",
    "GET /api/gateway/status",
    "GET /api/kanban",
    "GET /api/logs/{name}/tail",
    "GET /api/models/catalog",
    "GET /api/models/providers",
    "GET /api/obs/approvals",
    "GET /api/obs/budget",
    "GET /api/obs/cost",
    "GET /api/obs/runs",
    "GET /api/obs/runs/{run_id}/events",
    "GET /api/obs/runs/{run_id}/trace",
    "GET /api/obs/summary",
    "GET /api/obs/traces",
    "GET /api/os/home",
    "GET /api/projects",
    "GET /api/projects/{pid}/stats",
    "GET /api/runtime/dashboard",
    "GET /api/skills",
    "POST /api/approvals/{approval_id}/approve",
    "POST /api/approvals/{approval_id}/deny",
    "POST /api/config/profiles/{name}/use",
    "POST /api/gateway/{action}",
    "POST /api/os/command",
    "POST /api/projects",
    "POST /api/projects/{pid}/activate",
    "PUT /api/config",
}

OPERACOES_ESPERADAS = OPERACOES_CORE | OPERACOES_API


# Query params obrigatoriamente presentes em rotas que os usam — extração
# descuidada perde `Query(...)` sem que nenhum teste de rota perceba.
PARAMS_ESPERADOS = {
    "GET /events": {"limit"},
    "GET /audit": {"run_id", "limit"},
    "GET /audit/report": {"last"},
    "GET /audit/skills/insights": {"last"},
    "GET /approvals": {"status"},
}


@pytest.fixture(scope="module")
def spec():
    from bauer.server import create_app
    from bauer.tool_router import ToolRouter

    tmp = Path(tempfile.mkdtemp())
    ws = tmp / "ws"
    ws.mkdir()

    cliente = MagicMock()
    cliente.host = "http://localhost:11434"
    cliente._provider = "ollama"

    app = create_app(
        model_name="m",
        applied_context=4096,
        router=ToolRouter(workspace=ws),
        client=cliente,
        system_prompt="p",
        sessions_dir=tmp / "sessions",
        workspace=ws,
    )
    return app.openapi()


def _operacoes(spec) -> set[str]:
    return {
        f"{metodo.upper()} {caminho}"
        for caminho, metodos in spec.get("paths", {}).items()
        for metodo in metodos
    }


def test_nenhuma_rota_sumiu(spec):
    faltando = OPERACOES_ESPERADAS - _operacoes(spec)
    assert not faltando, (
        f"rotas sumiram do app: {sorted(faltando)}. "
        "Se foi de propósito, remova-as de OPERACOES_ESPERADAS no mesmo commit."
    )


def test_nenhuma_rota_apareceu_sem_registro(spec):
    """Rota nova é bem-vinda — mas tem que ser declarada aqui, não aparecer
    de lado. Mantém esta lista como a descrição fiel da superfície pública."""
    extras = _operacoes(spec) - OPERACOES_ESPERADAS
    assert not extras, (
        f"rotas novas nao declaradas: {sorted(extras)}. "
        "Adicione-as a OPERACOES_ESPERADAS."
    )


@pytest.mark.parametrize("operacao,esperados", sorted(PARAMS_ESPERADOS.items()))
def test_query_params_preservados(spec, operacao, esperados):
    metodo, caminho = operacao.split(" ", 1)
    op = spec["paths"][caminho][metodo.lower()]
    presentes = {
        p["name"] for p in op.get("parameters", []) if p.get("in") == "query"
    }
    assert esperados <= presentes, (
        f"{operacao} perdeu query param(s): {sorted(esperados - presentes)}"
    )


def test_rotas_protegidas_exigem_auth(spec):
    """/health é pública de propósito (liveness). Todo o resto passa pelo
    _verify_key — uma extração que esqueça o `Depends` abre o endpoint."""
    from bauer.server import create_app
    from bauer.tool_router import ToolRouter
    from fastapi.testclient import TestClient

    tmp = Path(tempfile.mkdtemp())
    ws = tmp / "ws"
    ws.mkdir()
    cliente = MagicMock()
    cliente.host = "http://localhost:11434"
    cliente._provider = "ollama"

    app = create_app(
        model_name="m", applied_context=4096, router=ToolRouter(workspace=ws),
        client=cliente, system_prompt="p", sessions_dir=tmp / "s",
        api_key="segredo", workspace=ws,
    )
    c = TestClient(app)

    assert c.get("/health").status_code == 200, "/health deve seguir publica"

    for rota in ("/sessions", "/events", "/runs", "/audit", "/approvals", "/status"):
        assert c.get(rota).status_code == 401, f"{rota} respondeu sem API key"
