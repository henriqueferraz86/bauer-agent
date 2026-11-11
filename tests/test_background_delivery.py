"""Sprint 43 (plano 063): entrega de resultado de tarefas em background no
Telegram — registro de canais de entrega, captura de origem, idempotência,
retry e um teste de ponta a ponta mínimo.

Hermético: nenhum teste aqui bate em rede real ou na API do Telegram — o
canal de entrega usado em todos os testes é um `deliver_fn` fake registrado
sob um prefixo de teste (ou, no teste de ponta a ponta, sob o prefixo real
"tg:" que `TelegramBridge` usaria, mas com um fake no lugar do bridge).
"""

from __future__ import annotations

import re
import threading
import uuid

import pytest

from bauer import channel_execution
from bauer.channel_base import AgentBackend, ChannelMessage
from bauer.channel_execution import (
    claim_delivery,
    deliver_background_completion,
    deliver_to_session,
    deliver_with_retry,
    format_background_result,
    register_delivery_channel,
)
from bauer.runtime_ids import reset_runtime_ids, set_runtime_ids
from bauer.shell_runner import ShellRunner
from bauer.sqlite_session_store import SqliteSessionStore
from bauer.tool_router import ToolRouter
from tests._deadlines import ANTI_HANG  # noqa: E402


@pytest.fixture(autouse=True)
def _isolated_delivery_registry():
    """Dá a cada teste um registro de entrega/idempotência limpo.

    `TelegramBridge` real (exercitado em outros arquivos de teste, ex.
    `tests/test_telegram_bridge.py`) registra o prefixo "tg:" global ao ser
    construído — sem isto, esses testes poderiam contaminar uns aos outros
    dependendo da ordem de execução.
    """
    saved_channels = dict(channel_execution._delivery_channels)
    saved_keys = set(channel_execution._delivered_keys)
    channel_execution._delivery_channels.clear()
    channel_execution._delivered_keys.clear()
    yield
    channel_execution._delivery_channels.clear()
    channel_execution._delivery_channels.update(saved_channels)
    channel_execution._delivered_keys.clear()
    channel_execution._delivered_keys.update(saved_keys)


def message(text="work", user="42", chat="42", private=True):
    return ChannelMessage(
        channel="telegram", user_id=user, chat_id=chat, text=text,
        raw={"message": {"chat": {"type": "private" if private else "group"}}},
    )


def backend_at(tmp_path, *, governed=True):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    config = tmp_path / "config.yaml"
    if governed:
        config.write_text(f"agent:\n  workspace: {workspace.as_posix()}\n"
                          "model:\n  provider: ollama\n  name: fake\n"
                          "kernel:\n  enabled: true\n", encoding="utf-8")
    backend = AgentBackend(config, tmp_path / "memory" / "sessions")
    backend._client = object()
    backend._model_name = "fake"
    backend._applied_context = 4096
    backend._system_prompt = "Controlled worker"
    backend._router = ToolRouter(workspace=workspace, shell_runner=ShellRunner(workspace=workspace))
    backend._store = SqliteSessionStore(backend.sessions_dir, semantic_indexing_enabled=False)
    backend._maybe_reload = lambda: None
    return backend


# ── 1. Registro de canais de entrega ────────────────────────────────────────

def test_deliver_to_session_resolves_by_prefix():
    received = []

    def fake(session_id, text):
        received.append((session_id, text))
        return True

    register_delivery_channel("fake:", fake)
    assert deliver_to_session("fake:123", "hello") is True
    assert received == [("fake:123", "hello")]


def test_deliver_to_session_without_registered_channel_is_noop():
    assert deliver_to_session("unknown:1", "hi") is False


def test_register_delivery_channel_replaces_previous_handler():
    calls = []
    register_delivery_channel("fake:", lambda sid, text: calls.append("first") or True)
    register_delivery_channel("fake:", lambda sid, text: calls.append("second") or True)
    assert deliver_to_session("fake:1", "x") is True
    assert calls == ["second"]


# ── 2. Captura de origin_session_id + watcher em execution.py ──────────────

@pytest.fixture
def router_shell(tmp_path):
    sr = ShellRunner(workspace=tmp_path)
    return ToolRouter(workspace=tmp_path, shell_runner=sr)


def test_background_run_captures_origin_session_id(router_shell):
    # Prefixo único por teste: um watcher daemon de um teste anterior (não
    # joinado — ele sobrevive deliberadamente ao teste que o criou) não deve
    # conseguir casar com o canal "fake:" registrado por outro teste quando
    # a suíte inteira roda e os dois acabam no mesmo worker do xdist.
    session_id = f"fake-origin-{uuid.uuid4().hex[:8]}:777"
    token = set_runtime_ids(session_id, "run-1")
    try:
        out = router_shell.execute({
            "action": "run_command",
            "args": {"command": 'python -c "print(1)"', "background": True},
        })
    finally:
        reset_runtime_ids(token)

    pid = re.search(r"PID (\d+)", out).group(1)
    assert router_shell._processes[pid]["origin_session_id"] == session_id


def test_background_run_without_runtime_ids_has_no_origin(router_shell):
    # CLI/sem canal — get_runtime_ids() não instalado — não deve registrar
    # origem nem criar um watcher sem ter pra onde entregar.
    out = router_shell.execute({
        "action": "run_command",
        "args": {"command": 'python -c "print(1)"', "background": True},
    })
    pid = re.search(r"PID (\d+)", out).group(1)
    assert router_shell._processes[pid]["origin_session_id"] is None


def test_background_run_watcher_delivers_on_completion(router_shell):
    # Prefixo único por teste (ver comentário no teste anterior) — evita que
    # um watcher de outro teste, ainda vivo, entregue usando ESTE canal.
    prefix = f"fake-watch-{uuid.uuid4().hex[:8]}:"
    delivered = threading.Event()
    calls = []

    def fake_deliver(session_id, text):
        calls.append((session_id, text))
        delivered.set()
        return True

    register_delivery_channel(prefix, fake_deliver)

    session_id = f"{prefix}777"
    token = set_runtime_ids(session_id, "run-1")
    try:
        out = router_shell.execute({
            "action": "run_command",
            "args": {"command": 'python -c "print(1)"', "background": True},
        })
    finally:
        reset_runtime_ids(token)

    pid = re.search(r"PID (\d+)", out).group(1)
    assert delivered.wait(timeout=ANTI_HANG), "watcher não entregou a tempo"
    got_session_id, text = calls[0]
    assert got_session_id == session_id
    assert f"PID {pid}" in text
    assert "exit: 0" in text
    assert "1" in text  # stdout do `print(1)`


# ── 3. Idempotência ──────────────────────────────────────────────────────────

def test_claim_delivery_only_succeeds_once():
    assert claim_delivery("pid:1") is True
    assert claim_delivery("pid:1") is False
    assert claim_delivery("pid:2") is True


def test_deliver_background_completion_delivers_only_once():
    calls = []
    register_delivery_channel("fake:", lambda sid, text: calls.append((sid, text)) or True)

    kwargs = dict(
        pid="123", command="echo hi", returncode=0,
        stdout_buf=["hi\n"], stderr_buf=[],
        session_id="fake:1", started_at=100.0,
    )
    assert deliver_background_completion(**kwargs) is True
    assert deliver_background_completion(**kwargs) is False
    assert len(calls) == 1


def test_format_background_result_has_explicit_header_and_exit_code():
    text = format_background_result(
        "999", "echo hi", 0, ["hi\n"], [],
    )
    assert text.startswith("Resultado da tarefa em background (PID 999)")
    assert "$ echo hi" in text
    assert "exit: 0" in text
    assert "hi" in text


# ── 4. Retry curto, transitório vs. permanente ──────────────────────────────

def test_retry_succeeds_after_transient_failures():
    attempts = []

    def flaky(session_id, text):
        attempts.append(1)
        if len(attempts) < 3:
            raise TimeoutError("network blip")
        return True

    register_delivery_channel("fake:", flaky)
    sleeps = []
    assert deliver_with_retry("fake:1", "hi", sleep=sleeps.append) is True
    assert len(attempts) == 3
    assert sleeps == [2.0, 5.0]


def test_retry_gives_up_immediately_on_permanent_error():
    attempts = []

    def blocked(session_id, text):
        attempts.append(1)
        raise RuntimeError(
            "Telegram API sendMessage: Forbidden: bot was blocked by the user"
        )

    register_delivery_channel("fake:", blocked)
    sleeps = []
    assert deliver_with_retry("fake:1", "hi", sleep=sleeps.append) is False
    assert len(attempts) == 1
    assert sleeps == []


def test_retry_exhausts_schedule_on_persistent_transient_error():
    attempts = []

    def always_fails(session_id, text):
        attempts.append(1)
        raise TimeoutError("still down")

    register_delivery_channel("fake:", always_fails)
    sleeps = []
    assert deliver_with_retry("fake:1", "hi", sleep=sleeps.append) is False
    assert len(attempts) == 4  # 1 tentativa inicial + 3 retries
    assert sleeps == [2.0, 5.0, 10.0]


def test_retry_gives_up_when_no_channel_registered_without_sleeping():
    sleeps = []
    assert deliver_with_retry("unknown:1", "hi", sleep=sleeps.append) is False
    assert sleeps == []


# ── 5. Ponta a ponta mínimo ──────────────────────────────────────────────────

def test_end_to_end_background_task_delivers_without_asking_again(tmp_path, monkeypatch):
    backend = backend_at(tmp_path)

    delivered = threading.Event()
    received = []

    def fake_telegram_deliver(session_id, text):
        received.append((session_id, text))
        delivered.set()
        return True

    register_delivery_channel("tg:", fake_telegram_deliver)

    def turn(ctx, router, *_args, **_kwargs):
        out = router.execute({
            "action": "run_command",
            "args": {"command": 'python -c "print(1)"', "background": True},
        })
        ctx.add_assistant(out)
        return out, [{"tool": "run_command"}]

    monkeypatch.setattr("bauer.agent.run_one_turn_with_fallback", turn)

    reply = backend.process(message("roda isso em background"))
    assert "PID" in reply
    assert "process(" in reply  # instrução original de poll ainda presente

    assert delivered.wait(timeout=ANTI_HANG), "entrega de background não chegou a tempo"
    session_id, text = received[0]
    assert session_id == "tg:42"
    assert "Resultado da tarefa em background" in text
    assert "exit: 0" in text
