"""Sprint 44 (plano 061, Etapa 5): `/loop` autônomo no Telegram.

Hermético: nenhum teste aqui bate em rede, Telegram ou provider LLM real.
`AgentBackend` é real (sessões/ContextManager/router são puros em memória),
mas o Kernel é um fake mínimo (admit/continue_run/runs) e o turno do modelo
(`run_one_turn_with_fallback`) é monkeypatchado — igual ao padrão usado em
`tests/test_background_delivery.py`.
"""

from __future__ import annotations

import os
import time
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from bauer import channel_execution, telegram_loop
from bauer.channel_base import AgentBackend, ChannelMessage
from bauer.shell_runner import ShellRunner
from bauer.sqlite_session_store import SqliteSessionStore
from bauer.tool_router import ToolRouter


@pytest.fixture(autouse=True)
def _isolated_delivery_registry():
    """Mesmo isolamento de `tests/test_background_delivery.py`: o registro de
    entrega/idempotência é global ao processo — sem isto, outros arquivos de
    teste (que registram "tg:") poderiam vazar estado para aqui."""
    saved_channels = dict(channel_execution._delivery_channels)
    saved_keys = set(channel_execution._delivered_keys)
    channel_execution._delivery_channels.clear()
    channel_execution._delivered_keys.clear()
    yield
    channel_execution._delivery_channels.clear()
    channel_execution._delivery_channels.update(saved_channels)
    channel_execution._delivered_keys.clear()
    channel_execution._delivered_keys.update(saved_keys)


# ── Kernel fake ──────────────────────────────────────────────────────────────

class FakeRun:
    def __init__(self, run_id: str, status: str = "queued"):
        self.id = run_id
        self.status = status
        self.output: dict | None = None
        self.error: str | None = None
        self.finished_at: str | None = None
        self.tool_calls_count = 0


class FakeRunManager:
    def __init__(self) -> None:
        self._runs: dict[str, FakeRun] = {}
        self._seq = 0

    def create(self, status: str = "queued") -> FakeRun:
        self._seq += 1
        run = FakeRun(f"run-{self._seq}", status)
        self._runs[run.id] = run
        return run

    def get_run(self, run_id: str) -> FakeRun | None:
        return self._runs.get(run_id)

    def cancel_run(self, run_id: str) -> FakeRun:
        run = self._runs[run_id]
        run.status = "cancelled"
        return run

    def fail_run_se_nao_terminal(self, run_id: str, error: str) -> FakeRun:
        run = self._runs[run_id]
        if run.status not in ("completed", "failed", "cancelled"):
            run.status = "failed"
            run.error = error
            run.finished_at = datetime.now(UTC).isoformat()
        return run


class FakeKernel:
    """Implementa só o que `telegram_loop.py` toca: admit/continue_run/runs."""

    def __init__(self, *, admit_fn=None, control=None) -> None:
        self.runs = FakeRunManager()
        self.control = control
        self.bus = None
        self._admit_fn = admit_fn
        self.admit_calls: list = []
        self.continue_calls: list = []

    def admit(self, request):
        self.admit_calls.append(request)
        if self._admit_fn is not None:
            return self._admit_fn(self, request)
        run = self.runs.create(status="queued")
        return run, None

    def continue_run(self, run_id, *, extra_input=None, executor):
        self.continue_calls.append(run_id)
        result = executor({}) or {}
        run = self.runs.get_run(run_id)
        status = str(result.get("status") or "completed")
        run.status = status
        run.output = {"output": result.get("output")}
        run.error = result.get("error")
        run.tool_calls_count = result.get("tool_calls_count", 0)
        run.finished_at = datetime.now(UTC).isoformat()
        return SimpleNamespace(run_id=run_id, status=status,
                               output=result.get("output"), error=run.error)


# ── fixtures ─────────────────────────────────────────────────────────────────

def message(text="/loop faça algo", chat="42", user="42", private=True):
    return ChannelMessage(
        channel="telegram", user_id=user, chat_id=chat, text=text,
        raw={"message": {"chat": {"type": "private" if private else "group"}}},
    )


def backend_at(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    config = tmp_path / "config.yaml"
    config.write_text(
        f"agent:\n  workspace: {workspace.as_posix()}\n"
        "model:\n  provider: ollama\n  name: fake\n"
        "kernel:\n  enabled: true\n",
        encoding="utf-8",
    )
    backend = AgentBackend(config, tmp_path / "memory" / "sessions")
    backend._client = object()
    backend._model_name = "fake"
    backend._applied_context = 4096
    backend._system_prompt = "Controlled worker"
    backend._router = ToolRouter(workspace=workspace, shell_runner=ShellRunner(workspace=workspace))
    backend._store = SqliteSessionStore(backend.sessions_dir, semantic_indexing_enabled=False)
    backend._maybe_reload = lambda: None
    return backend


def install_kernel(backend, kernel):
    backend._resolver_kernel = lambda workspace=None: kernel


def fake_turn_completes_in_two_rounds(monkeypatch, text="Trabalho concluído."):
    """`run_one_turn_with_fallback` que sinaliza conclusão natural (2 rodadas
    só-texto, mesma semântica de `serve_loop.run_loop_rounds`)."""
    def _turn(ctx, router, client, model, fallback_clients=None, **kwargs):
        ctx.add_assistant(text)
        return text, []

    monkeypatch.setattr("bauer.agent.run_one_turn_with_fallback", _turn)


def wait_for_delivery(received: list, timeout: float = 5.0) -> None:
    """Espera a thread de background do /loop entregar o relatório final."""
    start = time.monotonic()
    while not received and time.monotonic() - start < timeout:
        time.sleep(0.02)
    assert received, "o relatório final do /loop não chegou a tempo"


# ── 1. admissão feliz ────────────────────────────────────────────────────────

def test_happy_path_admits_persists_before_reply_and_delivers_report(tmp_path, monkeypatch):
    backend = backend_at(tmp_path)
    kernel = FakeKernel()
    install_kernel(backend, kernel)
    fake_turn_completes_in_two_rounds(monkeypatch)

    received: list[tuple[str, str]] = []
    channel_execution.register_delivery_channel(
        "tg:", lambda sid, text: received.append((sid, text)) or True,
    )

    msg = message()
    reply = telegram_loop.start_loop(backend, msg, update_id=1, goal="faça algo")

    assert "iniciado" in reply.lower()
    assert "run_id=" in reply

    # Persistido ANTES do caller ver a resposta "iniciado" — a chamada já
    # retornou, então o arquivo já deve existir e bater com o run admitido.
    store = telegram_loop._store_for(backend)
    record = store.get_by_update_id(1)
    assert record is not None
    assert record.run_id == kernel.runs.get_run(record.run_id).id
    assert record.state in ("admitted", "running", "completed")

    wait_for_delivery(received)
    sid, text = received[0]
    assert sid == "tg:42"
    assert "stop_reason=completed" in text
    assert "✅" in text

    final = store.get_by_update_id(1)
    assert final.state == "completed"
    assert final.delivered is True


@pytest.mark.skipif(os.name == "nt", reason="permissões POSIX não se aplicam no Windows")
def test_record_file_has_restrictive_permissions(tmp_path, monkeypatch):
    backend = backend_at(tmp_path)
    kernel = FakeKernel()
    install_kernel(backend, kernel)
    fake_turn_completes_in_two_rounds(monkeypatch)
    channel_execution.register_delivery_channel("tg:", lambda sid, text: True)

    telegram_loop.start_loop(backend, message(), update_id=10, goal="faça algo")

    store = telegram_loop._store_for(backend)
    mode = os.stat(store.path).st_mode & 0o777
    assert mode == 0o600


# ── 2. waiting_approval nunca finge "iniciado" ──────────────────────────────

def test_waiting_approval_does_not_claim_started_and_persists_received(tmp_path):
    backend = backend_at(tmp_path)

    def _admit_waiting(kernel, request):
        from bauer.core.kernel import KernelRun
        run = kernel.runs.create(status="waiting_approval")
        early = KernelRun(run_id=run.id, session_id=request.session_id,
                          status="waiting_approval", approval_id="appr-1",
                          policy_reason="custo alto")
        return run, early

    kernel = FakeKernel(admit_fn=_admit_waiting)
    install_kernel(backend, kernel)

    reply = telegram_loop.start_loop(backend, message(), update_id=2, goal="faça algo")

    assert "iniciado" not in reply.lower()
    assert "aprovação" in reply.lower()
    assert "appr-1" in reply

    store = telegram_loop._store_for(backend)
    record = store.get_by_update_id(2)
    assert record is not None
    assert record.state == "received"


# ── 3. deny não persiste nada ───────────────────────────────────────────────

def test_policy_deny_persists_nothing(tmp_path):
    backend = backend_at(tmp_path)

    def _admit_deny(kernel, request):
        from bauer.core.kernel import KernelRun
        run = kernel.runs.create(status="failed")
        early = KernelRun(run_id=run.id, session_id=request.session_id,
                          status="failed", error="política negou",
                          policy_action="deny", policy_reason="ação proibida")
        return run, early

    kernel = FakeKernel(admit_fn=_admit_deny)
    install_kernel(backend, kernel)

    reply = telegram_loop.start_loop(backend, message(), update_id=3, goal="faça algo")

    assert reply.startswith("⛔")
    store = telegram_loop._store_for(backend)
    assert store.get_by_update_id(3) is None


# ── 4. replay do mesmo update_id não duplica ────────────────────────────────

def test_replay_same_update_id_does_not_readmit(tmp_path, monkeypatch):
    backend = backend_at(tmp_path)
    kernel = FakeKernel()
    install_kernel(backend, kernel)
    fake_turn_completes_in_two_rounds(monkeypatch)
    received: list = []
    channel_execution.register_delivery_channel(
        "tg:", lambda sid, text: received.append((sid, text)) or True,
    )

    msg = message()
    first = telegram_loop.start_loop(backend, msg, update_id=4, goal="faça algo")
    wait_for_delivery(received)

    second = telegram_loop.start_loop(backend, msg, update_id=4, goal="faça algo")

    assert len(kernel.admit_calls) == 1  # não readmitiu
    assert "run_id=" in first
    assert second != first
    assert "já" in second.lower()


# ── 5. cancelamento via /cancelar <run_id> ──────────────────────────────────

def test_cancel_loop_calls_cancel_run_and_reports(tmp_path):
    backend = backend_at(tmp_path)
    kernel = FakeKernel()
    install_kernel(backend, kernel)

    run = kernel.runs.create(status="running")
    store = telegram_loop._store_for(backend)
    store.put(telegram_loop.TelegramLoopRecord(
        update_id=5, chat_id="42", session_id="tg:42", run_id=run.id,
        workspace=str(backend._router.workspace), goal="faça algo", state="running",
    ))

    response = telegram_loop.cancel_loop(backend, "42", run.id)

    assert response is not None
    assert run.id in response
    assert kernel.runs.get_run(run.id).status == "cancelled"


def test_cancel_loop_returns_none_when_no_candidate(tmp_path):
    backend = backend_at(tmp_path)
    kernel = FakeKernel()
    install_kernel(backend, kernel)
    assert telegram_loop.cancel_loop(backend, "42", None) is None


# ── 6. reconciliação: run real já terminal ──────────────────────────────────

def test_reconcile_pending_syncs_terminal_run_and_delivers_once(tmp_path):
    backend = backend_at(tmp_path)
    kernel = FakeKernel()
    install_kernel(backend, kernel)

    run = kernel.runs.create(status="completed")
    run.output = {"response": "já tinha terminado"}
    run.finished_at = datetime.now(UTC).isoformat()

    store = telegram_loop._store_for(backend)
    store.put(telegram_loop.TelegramLoopRecord(
        update_id=6, chat_id="42", session_id="tg:42", run_id=run.id,
        workspace=str(backend._router.workspace), goal="faça algo", state="running",
    ))

    received: list = []
    channel_execution.register_delivery_channel(
        "tg:", lambda sid, text: received.append((sid, text)) or True,
    )

    bridge = SimpleNamespace(backend=backend)
    telegram_loop.reconcile_pending(bridge)
    telegram_loop.reconcile_pending(bridge)  # idempotente: não duplica a entrega

    assert len(received) == 1
    record = store.get_by_update_id(6)
    assert record.state == "completed"
    assert record.delivered is True


# ── 7. reconciliação: run real NÃO terminal (thread morreu com o processo) ──

def test_reconcile_pending_marks_dead_thread_failed_and_never_resubmits(tmp_path):
    backend = backend_at(tmp_path)
    kernel = FakeKernel()
    install_kernel(backend, kernel)

    run = kernel.runs.create(status="running")

    store = telegram_loop._store_for(backend)
    store.put(telegram_loop.TelegramLoopRecord(
        update_id=7, chat_id="42", session_id="tg:42", run_id=run.id,
        workspace=str(backend._router.workspace), goal="faça algo", state="running",
    ))

    received: list = []
    channel_execution.register_delivery_channel(
        "tg:", lambda sid, text: received.append((sid, text)) or True,
    )

    bridge = SimpleNamespace(backend=backend)
    telegram_loop.reconcile_pending(bridge)

    assert kernel.runs.get_run(run.id).status == "failed"
    assert len(kernel.admit_calls) == 0  # nunca reemitiu o /loop

    record = store.get_by_update_id(7)
    assert record.state == "stopped"
    assert len(received) == 1
    assert "reiniciou" in received[0][1]


# ── 7b. reconciliação é execução única por instância de bridge ─────────────
# (reviewer fix: `GatewayRuntime._run_bridge` pode retomar `bridge.start()`
# NA MESMA instância/processo após uma exceção não tratada — sem a guarda,
# esse retry re-executaria `reconcile_pending` contra uma thread de `/loop`
# que ainda está genuinamente viva, marcando-a `failed` por engano.)

def test_reconcile_pending_runs_only_once_per_bridge_instance(tmp_path):
    backend = backend_at(tmp_path)
    kernel = FakeKernel()
    install_kernel(backend, kernel)

    run = kernel.runs.create(status="running")
    store = telegram_loop._store_for(backend)
    store.put(telegram_loop.TelegramLoopRecord(
        update_id=20, chat_id="42", session_id="tg:42", run_id=run.id,
        workspace=str(backend._router.workspace), goal="faça algo", state="running",
    ))

    received: list = []
    channel_execution.register_delivery_channel(
        "tg:", lambda sid, text: received.append((sid, text)) or True,
    )

    bridge = SimpleNamespace(backend=backend)
    telegram_loop.reconcile_pending(bridge)

    # Primeira chamada: thread "morta" (nada rodando de fato aqui) é uma
    # reconciliação legítima — marca failed e avisa.
    assert kernel.runs.get_run(run.id).status == "failed"
    assert len(received) == 1
    assert bridge._loop_reconciled is True

    # Um novo /loop é admitido enquanto o processo (e a instância do bridge)
    # continuam vivos — simula a thread genuinamente viva que o bug mataria.
    run2 = kernel.runs.create(status="running")
    store.put(telegram_loop.TelegramLoopRecord(
        update_id=21, chat_id="42", session_id="tg:42", run_id=run2.id,
        workspace=str(backend._router.workspace), goal="outra coisa", state="running",
    ))

    # Retry in-process de `start()` na MESMA instância de bridge: deve ser
    # um no-op completo, mesmo havendo um registro não-terminal no store.
    telegram_loop.reconcile_pending(bridge)

    assert kernel.runs.get_run(run2.id).status == "running"  # intocado
    assert len(received) == 1  # nenhuma notificação nova/falsa
    assert store.get_by_update_id(21).state == "running"  # não marcado failed


# ── 7c. registro terminal mas nunca entregue é re-entregue na reconciliação ─
# (reviewer fix: um crash entre marcar o estado terminal e confirmar a
# entrega perdia o relatório para sempre, já que `list_non_terminal` nunca
# revisita um registro já terminal.)

def test_reconcile_pending_redelivers_terminal_record_missing_delivery(tmp_path):
    backend = backend_at(tmp_path)
    kernel = FakeKernel()
    install_kernel(backend, kernel)

    run = kernel.runs.create(status="completed")
    run.output = {"response": "trabalho concluído antes do crash"}
    run.tool_calls_count = 3
    run.finished_at = datetime.now(UTC).isoformat()

    store = telegram_loop._store_for(backend)
    store.put(telegram_loop.TelegramLoopRecord(
        update_id=22, chat_id="42", session_id="tg:42", run_id=run.id,
        workspace=str(backend._router.workspace), goal="faça algo",
        state="completed", finished_at=run.finished_at, delivered=False,
    ))

    received: list = []
    channel_execution.register_delivery_channel(
        "tg:", lambda sid, text: received.append((sid, text)) or True,
    )

    bridge = SimpleNamespace(backend=backend)
    telegram_loop.reconcile_pending(bridge)

    assert len(received) == 1
    assert "trabalho concluído antes do crash" in received[0][1]
    record = store.get_by_update_id(22)
    assert record.delivered is True

    # Um segundo "restart de processo" (nova instância de bridge) reconcilia
    # de novo — o registro já está `delivered=True`, então não é mais
    # candidato (e a claim key já usada protegeria mesmo que estivesse).
    bridge2 = SimpleNamespace(backend=backend)
    telegram_loop.reconcile_pending(bridge2)

    assert len(received) == 1  # entregue exatamente uma vez


# ── 8. expurgo de registros antigos ─────────────────────────────────────────

def test_purge_old_removes_terminal_records_past_ttl(tmp_path):
    backend = backend_at(tmp_path)
    store = telegram_loop._store_for(backend)

    old = telegram_loop.TelegramLoopRecord(
        update_id=100, chat_id="42", session_id="tg:42", run_id="run-old",
        workspace=str(backend._router.workspace), goal="g", state="completed",
        finished_at=(datetime.now(UTC) - timedelta(days=8)).isoformat(),
    )
    recent = telegram_loop.TelegramLoopRecord(
        update_id=101, chat_id="42", session_id="tg:42", run_id="run-recent",
        workspace=str(backend._router.workspace), goal="g", state="completed",
        finished_at=(datetime.now(UTC) - timedelta(days=1)).isoformat(),
    )
    still_running = telegram_loop.TelegramLoopRecord(
        update_id=102, chat_id="42", session_id="tg:42", run_id="run-live",
        workspace=str(backend._router.workspace), goal="g", state="running",
    )
    store.put(old)
    store.put(recent)
    store.put(still_running)

    removed = store.purge_old()

    assert removed == 1
    assert store.get_by_update_id(100) is None
    assert store.get_by_update_id(101) is not None
    assert store.get_by_update_id(102) is not None


# ── 9. /loop sem objetivo e fora de conversa privada ────────────────────────

def test_loop_without_goal_returns_usage_without_admitting(tmp_path):
    backend = backend_at(tmp_path)
    kernel = FakeKernel()
    install_kernel(backend, kernel)

    reply = telegram_loop.start_loop(backend, message(text="/loop"), update_id=8, goal="")

    assert "/loop <objetivo>" in reply
    assert len(kernel.admit_calls) == 0


def test_loop_outside_private_chat_refuses_without_admitting(tmp_path):
    backend = backend_at(tmp_path)
    kernel = FakeKernel()
    install_kernel(backend, kernel)

    reply = telegram_loop.start_loop(
        backend, message(private=False), update_id=9, goal="faça algo",
    )

    assert "privada" in reply.lower()
    assert len(kernel.admit_calls) == 0


def test_provider_error_detail_reaches_the_run_error(tmp_path, monkeypatch):
    """Sprint 78: o painel mostrava só "loop parou: provider_error"."""
    backend = backend_at(tmp_path)
    kernel = FakeKernel()
    install_kernel(backend, kernel)

    def failing_turn(ctx, router, client, model, fallback_clients=None, **kwargs):
        raise RuntimeError("401 Unauthorized: token expirado")

    monkeypatch.setattr("bauer.agent.run_one_turn_with_fallback", failing_turn)
    received: list[tuple[str, str]] = []
    channel_execution.register_delivery_channel(
        "tg:", lambda sid, text: received.append((sid, text)) or True,
    )
    telegram_loop.start_loop(backend, message(), update_id=71, goal="faça algo")
    wait_for_delivery(received)
    record = telegram_loop._store_for(backend).get_by_update_id(71)
    run = kernel.runs.get_run(record.run_id)
    assert run.status == "failed"
    assert run.error.startswith("loop parou: provider_error — RuntimeError: 401")
    assert "token expirado" in received[0][1]
