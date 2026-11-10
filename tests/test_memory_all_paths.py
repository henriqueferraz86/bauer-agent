"""Sprint 70 (plano 066): memória de workspace em todos os caminhos de execução.

Cobre ``serve``/web (``/chat``, ``/stream``, ``/loop``), OpenAI-compat
(``/v1``) e canais (Telegram com projeto vinculado, Discord sem vínculo):

* proveniência de workspace nas sessões novas de cada caminho;
* diretivas aprovadas e fatos consolidados no prompt, só do workspace certo;
* revogação valendo no turno seguinte, dentro da mesma sessão aberta;
* sessões históricas sem proveniência continuam inelegíveis.

Tudo hermético: cliente LLM fake, nenhum provider ou rede real.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from bauer import projects_registry as pr
from bauer.channel_base import AgentBackend, ChannelMessage
from bauer.memory_consolidation import ConsolidatedRecord, write_records
from bauer.memory_directives import DirectiveStore
from bauer.memory_feedback import FeedbackStore
from bauer.memory_injection import (
    DIRECTIVES_HEADER,
    attach_workspace_memory,
    strip_persisted_directives,
    workspace_memory_block,
)
from bauer.memory_scope import resolve_workspace_scope, workspace_id_for
from bauer.server import create_app
from bauer.sqlite_session_store import SqliteSessionStore
from bauer.tool_router import ToolRouter


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def approve_directive(workspace: Path, marker: str) -> str:
    """Cria uma diretiva APROVADA (via feedback real + approve) com ``marker``."""
    feedback = FeedbackStore.for_workspace(workspace)
    assert feedback is not None
    for sid in ("s1", "s2", "s3"):
        feedback.record(rating="negative", session_id=sid, reason=f"respostas {marker}")
    store = DirectiveStore.for_workspace(workspace)
    assert store is not None
    draft = store.propose()[0]
    store.approve(draft.directive_id, ttl_days=30, text=f"Evite: regra {marker}")
    return draft.directive_id


def revoke(workspace: Path, directive_id: str) -> None:
    store = DirectiveStore.for_workspace(workspace)
    assert store is not None
    store.revoke(directive_id)


def write_fact(workspace: Path, marker: str) -> None:
    scope = resolve_workspace_scope(workspace)
    assert scope is not None
    now = datetime.now(timezone.utc)
    record = ConsolidatedRecord(
        record_id=f"cr-{marker}", session_id="s0", kind="fact",
        text=f"fato consolidado {marker}", confidence=0.9,
        valid_until="2999-01-01", consolidated_on=now.strftime("%Y-%m-%d"),
    )
    write_records(scope, [record], now=now)


def payload_text(ctx) -> str:
    return "\n".join(str(item.get("content") or "") for item in ctx.get_payload())


def provenance(sessions_dir: Path, sid: str) -> dict | None:
    return SqliteSessionStore(sessions_dir, semantic_indexing_enabled=False).get_provenance(sid)


def eligible(sessions_dir: Path, workspace: Path) -> set[str]:
    store = SqliteSessionStore(sessions_dir, semantic_indexing_enabled=False)
    return {row["session_id"] for row in store.list_sessions_for_workspace(workspace_id_for(workspace))}


# ---------------------------------------------------------------------------
# Núcleo: bloco dinâmico, escopo e histórico
# ---------------------------------------------------------------------------

def test_memory_block_is_fresh_per_payload_and_never_persisted(tmp_path):
    from bauer.context_manager import ContextManager

    ws = tmp_path / "ws"
    ws.mkdir()
    directive = approve_directive(ws, "alfa")
    ctx = ContextManager(applied_context=8192, system_prompt="base")
    assert attach_workspace_memory(ctx, ws)
    ctx.add_user("oi")
    assert "regra alfa" in payload_text(ctx)
    assert all("regra alfa" not in str(m.get("content")) for m in ctx.messages)
    revoke(ws, directive)
    # Mesma sessão aberta, próximo payload: a revogação já vale.
    assert "regra alfa" not in payload_text(ctx)


def test_memory_block_fails_closed_without_real_workspace(tmp_path):
    from bauer.context_manager import ContextManager

    ctx = ContextManager(applied_context=8192, system_prompt="base")
    for value in (None, MagicMock(), "", 42):
        assert attach_workspace_memory(ctx, value) is False
        assert ctx.memory_block is None
    assert workspace_memory_block(MagicMock()) == ""


def test_copied_consolidated_and_directives_do_not_leak(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir()
    b.mkdir()
    approve_directive(a, "alfa")
    write_fact(a, "alfa")
    # Copia a memória inteira de A para B: nada de A pode aparecer em B.
    (b / "memory" / "consolidation").mkdir(parents=True)
    (b / "memory" / "CONSOLIDATED.md").write_bytes((a / "memory" / "CONSOLIDATED.md").read_bytes())
    (b / "memory" / "consolidation" / "directives.json").write_bytes(
        (a / "memory" / "consolidation" / "directives.json").read_bytes())
    assert "fato consolidado alfa" in workspace_memory_block(a)
    assert "regra alfa" in workspace_memory_block(a)
    assert workspace_memory_block(b) == ""


def test_strip_persisted_directives_from_legacy_history():
    legacy = [
        {"role": "system", "content": f"## Memória do Projeto\n\nfato util\n\n{DIRECTIVES_HEADER}\n- Evite: x"},
        {"role": "system", "content": f"{DIRECTIVES_HEADER}\n- Evite: y"},
        {"role": "user", "content": f"cito {DIRECTIVES_HEADER} no texto"},
    ]
    cleaned = strip_persisted_directives(legacy)
    assert cleaned[0]["content"] == "## Memória do Projeto\n\nfato util"
    assert len(cleaned) == 2
    assert cleaned[1]["role"] == "user"


def test_local_memory_provider_no_longer_freezes_directives(tmp_path):
    from bauer.memory_provider import LocalMemoryProvider

    ws = tmp_path / "ws"
    ws.mkdir()
    approve_directive(ws, "alfa")
    provider = LocalMemoryProvider()
    provider.initialize(ws)
    provider.prefetch()
    assert "regra alfa" not in provider.system_prompt_block()


def test_sqlite_store_per_save_workspace_override(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir()
    b.mkdir()
    sessions = tmp_path / "sessions"
    store = SqliteSessionStore(sessions, semantic_indexing_enabled=False)
    store.save("legacy", [{"role": "user", "content": "x"}])
    store.save("sa", [{"role": "user", "content": "x"}], workspace=a)
    store.save("sb", [{"role": "user", "content": "x"}], workspace=b)
    store.save("none", [{"role": "user", "content": "x"}], workspace=None)
    # Histórica nunca ganha proveniência; trocar de workspace marca conflito.
    store.save("legacy", [{"role": "user", "content": "x"}], workspace=a)
    store.save("sb", [{"role": "user", "content": "x"}], workspace=a)
    assert eligible(sessions, a) == {"sa"}
    assert eligible(sessions, b) == set()
    assert provenance(sessions, "legacy")["workspace_id"] is None
    assert provenance(sessions, "none")["workspace_id"] is None


# ---------------------------------------------------------------------------
# serve / web: /chat, /stream, /loop
# ---------------------------------------------------------------------------

@pytest.fixture
def web(tmp_path, monkeypatch):
    monkeypatch.setattr(pr, "_DEFAULT_REGISTRY", tmp_path / "projects.json")
    monkeypatch.setattr("bauer.server._MEMORY_PREFETCH_ENABLED", False)
    monkeypatch.setattr("bauer.commands._runtime._build_router",
                        lambda cfg, path, **kwargs: ToolRouter(workspace=path))
    default = tmp_path / "default"
    default.mkdir()
    projects = []
    for name in ("A", "B"):
        path = tmp_path / name
        path.mkdir()
        projects.append(pr.add_project(path))
    config = tmp_path / "config.yaml"
    config.write_text("model:\n  provider: ollama\n  name: controlled\nkernel:\n  enabled: false\n")
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    (runtime / "serve-runtime-mode.json").write_text(json.dumps({"mode": "bauer_native"}))
    seen: list[str] = []

    def turn(ctx, router, *args, **kwargs):
        seen.append(payload_text(ctx))
        ctx.add_assistant("ok")
        return "ok", []

    monkeypatch.setattr("bauer.agent.run_one_turn", turn)
    app = create_app(
        model_name="controlled", applied_context=4096, client=object(),
        router=ToolRouter(workspace=default), system_prompt="boot",
        sessions_dir=tmp_path / "sessions", config_path=config,
        api_key="", web_auth_enabled=False, rate_limit_requests=0,
    )
    return app, [Path(p["path"]) for p in projects], [p["id"] for p in projects], default, seen


def _send(client, transport, message, *, project_id=None, session_id=None):
    payload = {"message": message}
    if project_id:
        payload["project_id"] = project_id
    if session_id:
        payload["session_id"] = session_id
    if transport == "stream":
        response = client.get("/stream", params=payload)
        assert response.status_code == 200, response.text
        return response.headers["X-Session-ID"]
    response = client.post("/chat", json=payload)
    assert response.status_code == 200, response.text
    return response.json()["session_id"]


@pytest.mark.parametrize("transport", ["chat", "stream"])
def test_web_two_workspaces_provenance_injection_and_revoke(web, tmp_path, transport):
    app, (a, b), (pid_a, pid_b), default, seen = web
    dir_a = approve_directive(a, "alfa")
    approve_directive(b, "beta")
    write_fact(a, "alfa")
    approve_directive(default, "padrao")
    sessions = tmp_path / "sessions"
    with TestClient(app) as client:
        sid_a = _send(client, transport, "oi A", project_id=pid_a)
        prompt_a = seen[-1]
        sid_b = _send(client, transport, "oi B", project_id=pid_b)
        prompt_b = seen[-1]
        # Sem leitura cruzada.
        assert "regra alfa" in prompt_a and "fato consolidado alfa" in prompt_a
        assert "regra beta" not in prompt_a and "regra padrao" not in prompt_a
        assert "regra beta" in prompt_b
        assert "regra alfa" not in prompt_b and "fato consolidado alfa" not in prompt_b
        # Sem escrita cruzada: cada sessão só é elegível no próprio workspace.
        assert eligible(sessions, a) == {sid_a}
        assert eligible(sessions, b) == {sid_b}
        assert eligible(sessions, default) == set()
        # Revogar vale no próximo turno da MESMA sessão.
        revoke(a, dir_a)
        _send(client, transport, "de novo", session_id=sid_a)
        assert "regra alfa" not in seen[-1]
        assert "fato consolidado alfa" in seen[-1]
    # A memória injetada nunca vai para o histórico gravado.
    history = (sessions / f"{sid_a}.jsonl").read_text(encoding="utf-8")
    assert "regra alfa" not in history and "fato consolidado" not in history


def test_web_historic_session_without_provenance_stays_excluded(web, tmp_path):
    app, (a, _), (pid_a, _), _, _ = web
    sessions = tmp_path / "sessions"
    sessions.mkdir(exist_ok=True)
    # Sessão JSONL anterior à sprint 70 (sem proveniência).
    (sessions / "oldweb01.jsonl").write_text(
        json.dumps({"role": "user", "content": "antiga"}) + "\n", encoding="utf-8")
    with TestClient(app) as client:
        _send(client, "chat", "continua", project_id=pid_a, session_id="oldweb01")
    assert provenance(sessions, "oldweb01")["workspace_id"] is None
    assert "oldweb01" not in eligible(sessions, a)


def test_web_session_delete_removes_mirror(web, tmp_path):
    app, (a, _), (pid_a, _), _, _ = web
    sessions = tmp_path / "sessions"
    with TestClient(app) as client:
        sid = _send(client, "chat", "oi", project_id=pid_a)
        assert eligible(sessions, a) == {sid}
        assert client.delete(f"/sessions/{sid}").status_code in (200, 204)
    assert eligible(sessions, a) == set()


def _wait_loop(client, run_id, timeout_s=30.0):
    deadline = time.monotonic() + timeout_s
    body: dict = {}
    while time.monotonic() < deadline:
        body = client.get(f"/loop/{run_id}").json()
        if body["state"] != "running":
            return body
        time.sleep(0.1)
    raise AssertionError(f"loop não terminou: {body}")


def test_loop_records_provenance_and_injects_only_its_workspace(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    other = tmp_path / "other"
    other.mkdir()
    approve_directive(ws, "laco")
    approve_directive(other, "intrusa")
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        "model:\n  provider: openrouter\n  name: deepseek/deepseek-v4-flash\n"
        "kernel:\n  enabled: false\nloop:\n  max_minutes: 5\n  max_tool_calls: 50\n"
        "  max_cost_usd: 1.0\n", encoding="utf-8")
    prompts: list[str] = []
    responses = iter(["terminei", "confirmo"])

    def chat_stream(model, msgs, *a, **k):
        prompts.append("\n".join(str(m.get("content") or "") for m in msgs))
        return iter([next(responses)])

    client = MagicMock()
    client.chat_stream.side_effect = chat_stream
    client._provider = "openrouter"
    with patch("bauer.projects_registry._DEFAULT_REGISTRY", tmp_path / "p.json"), \
         patch("bauer.agent._try_parse_tool", return_value=None):
        app = create_app(
            model_name="deepseek/deepseek-v4-flash", applied_context=4096,
            router=ToolRouter(workspace=ws), client=client, system_prompt="s",
            sessions_dir=tmp_path / "sessions", api_key="", rate_limit_requests=0,
            config_path=cfg,
        )
    with TestClient(app) as http:
        started = http.post("/loop", json={"message": "faça"}).json()
        final = _wait_loop(http, started["run_id"])
    assert final["state"] == "completed"
    assert prompts and all("regra laco" in p and "regra intrusa" not in p for p in prompts)
    assert eligible(tmp_path / "sessions", ws) == {started["session_id"]}
    assert eligible(tmp_path / "sessions", other) == set()


# ---------------------------------------------------------------------------
# OpenAI-compat (/v1)
# ---------------------------------------------------------------------------

def test_v1_provenance_injection_and_revoke(tmp_path, monkeypatch):
    from bauer.openai_client import OpenAIClient

    ws = tmp_path / "ws"
    ws.mkdir()
    other = tmp_path / "other"
    other.mkdir()
    directive = approve_directive(ws, "openai")
    approve_directive(other, "intrusa")

    class Fake(OpenAIClient):
        def __init__(self):
            super().__init__(host="http://unused.invalid", model="gpt-4o")
            self._provider = "openai"
            self.prompts: list[str] = []

        def chat_with_tools(self, model, messages, tools, **_kw):
            self.prompts.append("\n".join(str(m.get("content") or "") for m in messages))
            self.last_usage = {"prompt_tokens": 1, "completion_tokens": 1}
            return {"content": "OK", "tool_calls": []}

    fake = Fake()
    config = tmp_path / "config.yaml"
    config.write_text("model:\n  name: gpt-4o\n  provider: openai\nkernel:\n  enabled: false\n",
                      encoding="utf-8")
    app = create_app(
        model_name="gpt-4o", applied_context=4096, router=ToolRouter(workspace=ws),
        client=fake, system_prompt="SERVER", sessions_dir=tmp_path / "sessions",
        config_path=config, rate_limit_requests=0,
    )
    body = {"messages": [{"role": "user", "content": "oi"}], "session_id": "v1sess01",
            "history_mode": "incremental"}
    with TestClient(app) as http:
        assert http.post("/v1/chat/completions", json=body).status_code == 200
        assert "regra openai" in fake.prompts[-1] and "regra intrusa" not in fake.prompts[-1]
        revoke(ws, directive)
        assert http.post("/v1/chat/completions", json=body).status_code == 200
        assert "regra openai" not in fake.prompts[-1]
    sessions = tmp_path / "sessions"
    assert eligible(sessions, ws) == {"v1sess01"}
    assert eligible(sessions, other) == set()


# ---------------------------------------------------------------------------
# Canais: Telegram (projeto vinculado) e Discord (sem vínculo)
# ---------------------------------------------------------------------------

def tg(text="oi", *, user="42", chat="42"):
    return ChannelMessage("telegram", user, chat, text,
                          raw={"message": {"chat": {"type": "private"}}})


@pytest.fixture
def channel(tmp_path, monkeypatch):
    monkeypatch.setattr(pr, "_DEFAULT_REGISTRY", tmp_path / "projects.json")
    paths = []
    entries = []
    for name in ("alpha", "beta"):
        path = tmp_path / name
        path.mkdir()
        paths.append(path)
        entries.append(pr.add_project(path))
    default = tmp_path / "default"
    default.mkdir()
    sessions = tmp_path / "sessions"
    backend = AgentBackend(tmp_path / "absent.yaml", sessions)
    backend._client = object()
    backend._model_name = "default"
    backend._provider = "ollama"
    backend._applied_context = 8192
    backend._router = ToolRouter(default)
    backend._store = SqliteSessionStore(sessions, semantic_indexing_enabled=False)
    backend._system_prompt = "legacy prompt"
    backend._project_router_builder = lambda path: ToolRouter(path)
    backend._resolver_kernel = lambda *a, **k: None
    seen: list[str] = []

    def turn(ctx, router, client, model, fallbacks):
        seen.append(payload_text(ctx))
        ctx.add_assistant("ok")
        return "ok", []

    monkeypatch.setattr("bauer.agent.run_one_turn_with_fallback", turn)
    return backend, entries, paths, default, sessions, seen


def test_telegram_two_projects_isolated_and_mid_session_revoke(channel):
    backend, entries, (alpha, beta), default, sessions, seen = channel
    dir_alpha = approve_directive(alpha, "alfa")
    approve_directive(beta, "beta")
    approve_directive(default, "padrao")
    write_fact(alpha, "alfa")

    backend.select_channel_project(tg(), entries[0]["id"])
    assert backend.process(tg("pergunta alfa")) == "ok"
    key_alpha = backend.session_key(tg())
    assert "regra alfa" in seen[-1] and "fato consolidado alfa" in seen[-1]
    assert "regra beta" not in seen[-1] and "regra padrao" not in seen[-1]

    backend.select_channel_project(tg(), entries[1]["id"])
    backend.process(tg("pergunta beta"))
    key_beta = backend.session_key(tg())
    assert "regra beta" in seen[-1]
    assert "regra alfa" not in seen[-1] and "fato consolidado alfa" not in seen[-1]

    assert eligible(sessions, alpha) == {key_alpha}
    assert eligible(sessions, beta) == {key_beta}
    assert eligible(sessions, default) == set()

    # Volta para alfa: o ContextManager da sessão continua em cache (aberta).
    backend.select_channel_project(tg(), entries[0]["id"])
    with backend._sessions_lock:
        assert key_alpha in backend._sessions
    revoke(alpha, dir_alpha)
    backend.process(tg("depois da revogação"))
    assert "regra alfa" not in seen[-1]
    assert "fato consolidado alfa" in seen[-1]
    stored = " ".join(m["content"] for m in backend._store.load(key_alpha))
    assert "regra alfa" not in stored


def test_telegram_without_binding_records_nothing(channel):
    backend, _, _, default, sessions, seen = channel
    approve_directive(default, "padrao")
    backend.process(tg("sem projeto"))
    assert "regra padrao" not in seen[-1]
    assert provenance(sessions, tg().session_key)["workspace_id"] is None
    assert eligible(sessions, default) == set()


def test_telegram_historic_session_stays_excluded_after_binding(channel):
    backend, entries, (alpha, _), _, sessions, _ = channel
    backend.select_channel_project(tg(), entries[0]["id"])
    key = backend.session_key(tg())
    # Linha gravada antes da sprint 70: sem proveniência.
    SqliteSessionStore(sessions, semantic_indexing_enabled=False).save(
        key, [{"role": "user", "content": "antiga"}])
    backend.process(tg("continua"))
    assert provenance(sessions, key)["workspace_id"] is None
    assert eligible(sessions, alpha) == set()


def test_discord_has_no_binding_so_no_provenance_or_memory(channel):
    from bauer.discord_bridge import DiscordBridge

    backend, _, _, default, sessions, seen = channel
    approve_directive(default, "padrao")
    bridge = DiscordBridge("token", backend, allow_all=True)
    msg = ChannelMessage("discord", "7", "99", "olá")
    assert bridge.handle_message(msg) == "ok"
    assert "regra padrao" not in seen[-1]
    assert provenance(sessions, msg.session_key)["workspace_id"] is None
    assert eligible(sessions, default) == set()


# ---------------------------------------------------------------------------
# bauer agent: revogação dentro da sessão aberta e histórico legado
# ---------------------------------------------------------------------------

def test_bauer_agent_revoke_applies_next_turn_and_strips_legacy_history(tmp_path, monkeypatch):
    from rich.console import Console

    from bauer.agent import run_agent_session

    monkeypatch.chdir(tmp_path)
    ws = tmp_path / "ws"
    ws.mkdir()
    directive = approve_directive(ws, "agente")
    sessions = tmp_path / "sessions"
    store = SqliteSessionStore(sessions, semantic_indexing_enabled=False, workspace=ws)
    # Sessão gravada antes da sprint 70, com a diretiva congelada no histórico.
    store.save("legado01", [
        {"role": "system",
         "content": f"## Memória do Projeto\n\nnota\n\n{DIRECTIVES_HEADER}\n- Evite: regra agente"},
        {"role": "user", "content": "antes"},
        {"role": "assistant", "content": "ok"},
    ])
    prompts: list[str] = []
    client = MagicMock()

    def chat_stream(model, msgs, *a, **k):
        prompts.append("\n".join(str(m.get("content") or "") for m in msgs))
        return iter(["resposta"])

    client.chat_stream.side_effect = chat_stream
    inputs = iter(["primeira", "segunda"])

    def fake_input(*_a, **_k):
        try:
            value = next(inputs)
        except StopIteration:
            raise EOFError from None
        if value == "segunda":
            revoke(ws, directive)
        return value

    with patch("builtins.input", side_effect=fake_input):
        run_agent_session(client, "test-model", 4096, Console(), ToolRouter(workspace=ws),
                          session_id="legado01", session_store=store)
    assert len(prompts) >= 2
    # 1º turno: a diretiva aprovada aparece UMA vez (a dinâmica, não a congelada).
    assert prompts[0].count("regra agente") == 1
    assert "regra agente" not in prompts[-1]
    saved = " ".join(m["content"] for m in store.load("legado01"))
    assert "regra agente" not in saved
