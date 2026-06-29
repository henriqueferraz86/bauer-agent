"""Testes para dispatch remoto entre instâncias bauer serve."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from bauer.agent_registry import AgentDef, AgentRegistry
from bauer.orchestrator import AgentOrchestrator, OrchestratorConfig, StepResult


# ─── Fixtures ────────────────────────────────────────────────────────────────


def _make_registry(tmp_path: Path) -> AgentRegistry:
    return AgentRegistry(path=str(tmp_path / "agents.yaml"))


def _make_orch(tmp_path: Path, agents_file: str = "agents.yaml") -> AgentOrchestrator:
    client = MagicMock()
    client.chat_stream.return_value = iter(["resposta local"])
    tool_router = MagicMock()
    model_router = MagicMock()
    model_router.select_model.return_value = ("phi4-mini", MagicMock())
    cfg = OrchestratorConfig(agents_file=agents_file)
    orch = AgentOrchestrator(client, tool_router, model_router, cfg)

    def _patched(task: str) -> Path:
        import hashlib
        h = hashlib.md5(task.encode()).hexdigest()[:10]
        return tmp_path / h

    orch._progress_path = _patched  # type: ignore[method-assign]
    return orch


# ─── AgentDef: novos campos url / api_key ────────────────────────────────────


def test_agentdef_url_round_trip(tmp_path: Path):
    """url e api_key devem serializar para YAML e deserializar corretamente."""
    reg = _make_registry(tmp_path)
    ag = AgentDef(
        name="worker-py",
        description="Worker Python remoto",
        system="Você é um especialista Python.",
        url="http://192.168.1.10:8000",
        api_key="secret-key-abc",
    )
    reg.save(ag)

    loaded = reg.get("worker-py")
    assert loaded is not None
    assert loaded.url == "http://192.168.1.10:8000"
    assert loaded.api_key == "secret-key-abc"


def test_agentdef_without_url_omits_key(tmp_path: Path):
    """Agente local (sem url) não deve ter 'url' no dict serializado."""
    ag = AgentDef(name="local", description="d", system="s")
    d = ag.to_dict()
    assert "url" not in d
    assert "api_key" not in d


def test_agentdef_from_dict_handles_missing_url():
    """from_dict sem url/api_key deve retornar strings vazias."""
    ag = AgentDef.from_dict({
        "name": "agente",
        "description": "d",
        "system": "s",
    })
    assert ag.url == ""
    assert ag.api_key == ""


# ─── AgentOrchestrator._remote_dispatch ──────────────────────────────────────


def test_remote_dispatch_posts_to_chat_endpoint(tmp_path: Path):
    """_remote_dispatch deve POST para {url}/chat e retornar 'response'."""
    orch = _make_orch(tmp_path)
    mock_resp = MagicMock()
    mock_resp.json.return_value = {"response": "resultado remoto", "session_id": "s1"}
    mock_resp.raise_for_status.return_value = None

    with patch("httpx.post", return_value=mock_resp) as mock_post:
        result = orch._remote_dispatch(
            url="http://192.168.1.10:8000",
            api_key="key123",
            task="analise o arquivo",
            timeout=30.0,
        )

    assert result == "resultado remoto"
    call_args = mock_post.call_args
    assert call_args[0][0] == "http://192.168.1.10:8000/chat"
    assert call_args[1]["json"] == {"message": "analise o arquivo"}
    assert call_args[1]["headers"]["X-API-Key"] == "key123"


def test_remote_dispatch_no_api_key_omits_header(tmp_path: Path):
    """Sem api_key, o header X-API-Key não deve ser enviado."""
    orch = _make_orch(tmp_path)
    mock_resp = MagicMock()
    mock_resp.json.return_value = {"response": "ok"}
    mock_resp.raise_for_status.return_value = None

    with patch("httpx.post", return_value=mock_resp) as mock_post:
        orch._remote_dispatch(url="http://localhost:8000", api_key="", task="t")

    headers = mock_post.call_args[1]["headers"]
    assert "X-API-Key" not in headers


def test_remote_dispatch_timeout_raises_runtime_error(tmp_path: Path):
    """Timeout deve levantar RuntimeError com mensagem clara."""
    import httpx
    orch = _make_orch(tmp_path)
    with patch("httpx.post", side_effect=httpx.TimeoutException("t")):
        with pytest.raises(RuntimeError, match="Timeout"):
            orch._remote_dispatch(url="http://localhost:8000", api_key="", task="t")


def test_remote_dispatch_connect_error_raises(tmp_path: Path):
    """ConnectError deve levantar RuntimeError com mensagem clara."""
    import httpx
    orch = _make_orch(tmp_path)
    with patch("httpx.post", side_effect=httpx.ConnectError("refused")):
        with pytest.raises(RuntimeError, match="conectar"):
            orch._remote_dispatch(url="http://localhost:8000", api_key="", task="t")


# ─── AgentOrchestrator.execute_step com agente remoto ────────────────────────


def test_execute_step_dispatches_remotely_when_url_set(tmp_path: Path):
    """execute_step deve usar _remote_dispatch quando o agente tem url."""
    agents_yaml = tmp_path / "agents.yaml"
    reg = AgentRegistry(path=str(agents_yaml))
    reg.save(AgentDef(
        name="worker-remoto",
        description="Worker em outra máquina",
        system="s",
        url="http://worker-host:8001",
        api_key="abc",
    ))

    orch = _make_orch(tmp_path, agents_file=str(agents_yaml))

    step = {"id": 1, "goal": "processar dados", "tools": False,
            "depends_on": [], "agent": "worker-remoto"}

    mock_resp = MagicMock()
    mock_resp.json.return_value = {"response": "dados processados"}
    mock_resp.raise_for_status.return_value = None

    with patch("httpx.post", return_value=mock_resp):
        result = orch.execute_step(step, [])

    assert result.response == "dados processados"
    assert "remote:" in result.model_used


def test_execute_step_falls_back_to_local_on_remote_failure(tmp_path: Path):
    """Falha no dispatch remoto deve fazer fallback para execução local."""
    import httpx
    agents_yaml = tmp_path / "agents.yaml"
    reg = AgentRegistry(path=str(agents_yaml))
    reg.save(AgentDef(
        name="worker-falho",
        description="Worker que vai falhar",
        system="s",
        url="http://worker-host:8001",
    ))

    orch = _make_orch(tmp_path, agents_file=str(agents_yaml))
    orch.client.chat_stream.return_value = iter(["resposta local"])

    step = {"id": 1, "goal": "tarefa", "tools": False,
            "depends_on": [], "agent": "worker-falho"}

    with patch("httpx.post", side_effect=httpx.ConnectError("refused")):
        result = orch.execute_step(step, [])

    assert "remote:" not in result.model_used


def test_execute_step_local_agent_unchanged(tmp_path: Path):
    """Agente sem url deve continuar executando localmente (sem httpx)."""
    agents_yaml = tmp_path / "agents.yaml"
    reg = AgentRegistry(path=str(agents_yaml))
    reg.save(AgentDef(
        name="local-agent",
        description="Agente local",
        system="s",
    ))

    orch = _make_orch(tmp_path, agents_file=str(agents_yaml))
    orch.client.chat_stream.return_value = iter(["resposta local"])

    step = {"id": 1, "goal": "tarefa local", "tools": False,
            "depends_on": [], "agent": "local-agent"}

    with patch("httpx.post") as mock_post:
        result = orch.execute_step(step, [])

    mock_post.assert_not_called()
    assert result.response == "resposta local"


# ─── delegate_task com agent_name remoto ─────────────────────────────────────


def test_delegate_task_dispatches_to_remote_agent(tmp_path: Path):
    """_delegate_task com agent_name remoto deve usar httpx.post."""
    from bauer.tool_router import ToolRouter
    from bauer.agent_registry import AgentRegistry, AgentDef

    agents_yaml = tmp_path / "agents.yaml"
    reg = AgentRegistry(path=str(agents_yaml))
    reg.save(AgentDef(
        name="worker-api",
        description="Worker API",
        system="s",
        url="http://worker:9000",
        api_key="key-xyz",
    ))

    router = ToolRouter(workspace=str(tmp_path), llm_client=None)
    router._bauer_home = tmp_path  # type: ignore[attr-defined]

    mock_resp = MagicMock()
    mock_resp.json.return_value = {"response": "resultado da API"}
    mock_resp.raise_for_status.return_value = None

    with patch("httpx.post", return_value=mock_resp):
        result = router.execute({
            "action": "delegate_task",
            "args": {
                "task": "fazer algo",
                "agent_name": "worker-api",
            },
        })

    assert "resultado da API" in result
    assert "worker-api" in result
