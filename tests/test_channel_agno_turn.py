"""Sprint 40 (plano 062): caminho Telegram via Agno (`runtime.telegram_default_adapter`).

Reexecuta os critérios de aceite do sprint 34/plano 061
(`tests/test_channel_execution.py`) contra o branch `_turno_agno` novo, e
prova um turno simples completando pelo canal com o SDK Agno real (sem
provider), mesmo padrão de `tests/test_agno_runtime_adapter.py`.

Achado documentado (ver `specs/sprint-40-telegram/BACKLOG.md`): o
cancelamento cooperativo em si propaga corretamente — o Run termina
``cancelled``, a sessão desbloqueia, nenhum run fica órfão — mas
``AgnoRuntimeAdapter`` (sprint 35/36, fora do escopo desta sprint) só
reporta custo ao ``cost_sink`` no evento final ``RunCompleted``/
``TeamRunCompleted``. Um turno cancelado NUNCA emite esse evento, então
qualquer custo de LLM já incorrido por chamadas completadas ANTES do
cancelamento (ex.: a 1ª chamada de um loop de tool-calling) nunca chega ao
``cost_sink`` — ao contrário do motor nativo, que reporta cada chamada
assim que ela completa, antes do próximo checkpoint. `test_
agno_cancellation_during_tool_loop_loses_partial_llm_cost_confirmed_gap`
reproduz isso com o SDK real e documenta o comportamento atual (não é uma
correção; `AgnoRuntimeAdapter`/`execution_control.py` não foram tocados).
"""

from __future__ import annotations

import threading

import pytest

from bauer.channel_base import AgentBackend, ChannelMessage
from bauer.channel_projects import operator_key
from bauer.core.runtime.run_manager import RunManager
from bauer.cost_meter import cost_sink
from bauer.execution_control import current_control
from bauer.sqlite_session_store import SqliteSessionStore
from bauer.tool_router import ToolRouter

pytest.importorskip("agno")
pytest.importorskip("sqlalchemy")


def message(text="work", user="42", chat="42", private=True):
    return ChannelMessage(
        channel="telegram", user_id=user, chat_id=chat, text=text,
        raw={"message": {"chat": {"type": "private" if private else "group"}}},
    )


def backend_at(tmp_path, *, model_name="fake"):
    """Mesmo template de `tests/test_channel_execution.py::backend_at`, com
    `runtime.telegram_default_adapter: agno` (sprint 40).

    `model.provider` fica `ollama` (único valor aceito pelo schema estrito de
    `ModelSection` entre os candidatos sem rede) — o próprio
    `AgnoRuntimeAdapter._build_model` decide por `OfflineAgnoModel` quando o
    nome do modelo começa com `offline` (ver `model_name`), não pelo
    provider; `config_loader` não aceita `"local"`/`"offline"` como
    provider.
    """
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    config = tmp_path / "config.yaml"
    config.write_text(
        f"agent:\n  workspace: {workspace.as_posix()}\n"
        "model:\n  provider: ollama\n  name: fake\n"
        "kernel:\n  enabled: true\n"
        "runtime:\n  telegram_default_adapter: agno\n",
        encoding="utf-8",
    )
    backend = AgentBackend(config, tmp_path / "memory" / "sessions")
    backend._client = object()
    backend._model_name = model_name
    backend._applied_context = 4096
    backend._system_prompt = "Controlled worker"
    backend._router = ToolRouter(workspace=workspace)
    backend._store = SqliteSessionStore(backend.sessions_dir, semantic_indexing_enabled=False)
    backend._maybe_reload = lambda: None
    return backend


# ─── Mirror de test_cancel_waits_for_real_worker_and_preserves_all_cost ────
# Monkeypatcha `AgnoRuntimeAdapter.run_agent` (o ponto de entrada que
# `_turno_agno` de fato chama) em vez de `run_one_turn_with_fallback` — mesma
# ideia de `threading.Event`, adaptada ao Agno (ver ARCHITECTURE.md).


def start_blocked_agno_turn(backend, monkeypatch, *, cost_before=0, cost_after=0):
    entered, release = threading.Event(), threading.Event()
    seen = []
    output = {}

    def fake_run_agent(self, request):
        seen.append(current_control())
        if cost_before:
            cost_sink.get()("openai", "controlled", {"prompt_tokens": 1}, cost_before)
        entered.set()
        assert release.wait(timeout=5)
        control = current_control()
        cancelled = control is not None and control.cancel_event.is_set()
        if cost_after:
            cost_sink.get()("openai", "controlled", {"prompt_tokens": 1}, cost_after)
        run_id = request.get("run_id") or "agno-run"
        if cancelled:
            return {
                "status": "cancelled", "event": "run.cancelled", "run_id": run_id,
                "runtime_adapter": "agno",
                "error": "Provider retry interrupted: cancelled",
                "metadata": {"tools": [{"tool_name": "read_file", "result": "completed"}]},
            }
        return {
            "status": "completed", "event": "run.completed", "run_id": run_id,
            "runtime_adapter": "agno", "output": "partial result",
            "metadata": {"tools": [{"tool_name": "read_file", "result": "completed"}]},
        }

    monkeypatch.setattr(
        "bauer.core.runtime.adapters.agno_adapter.AgnoRuntimeAdapter.run_agent",
        fake_run_agent,
    )
    caller = threading.Thread(target=lambda: output.update(reply=backend.process(message())))
    caller.start()
    assert entered.wait(timeout=5)
    return caller, release, seen, output


def test_cancel_waits_for_real_agno_worker_and_preserves_all_cost(tmp_path, monkeypatch):
    """Mesmas garantias de `test_cancel_waits_for_real_worker_and_preserves_all_cost`
    (plano 061/sprint 34), agora contra o branch `_turno_agno`: o cancelamento
    cooperativo interrompe um turno Agno em andamento sem matar a sessão nem
    deixar run órfão, e a custódia de custo (reportada via `cost_sink`, igual
    ao motor nativo) chega ao Kernel mesmo após o cancelamento.
    """
    backend = backend_at(tmp_path)
    caller, release, seen, output = start_blocked_agno_turn(
        backend, monkeypatch, cost_before=0.1, cost_after=0.15,
    )
    try:
        job = backend._live_session_execution("tg:42")
        manager = RunManager(root=backend.sessions_dir.parent / "runtime")
        run_id = job.control.run_id
        reply = backend.cancel_channel_execution(message())
        assert "solicitado" in reply
        assert seen[0].cancel_event.is_set()
        assert manager.get_run(run_id).status == "running"
        assert backend._sessions["tg:42"][1].locked()
        assert not job.done.is_set()
    finally:
        release.set()
        caller.join(timeout=5)
    assert not caller.is_alive()
    assert "cancelada" in output["reply"].lower()
    run = manager.get_run(run_id)
    assert run.status == "cancelled"
    assert run.cost_estimate == pytest.approx(0.25)
    assert not backend._sessions["tg:42"][1].locked()
    # A sessão não fica travada: um novo turno nela funciona depois.
    monkeypatch.setattr(
        "bauer.core.runtime.adapters.agno_adapter.AgnoRuntimeAdapter.run_agent",
        lambda self, request: {
            "status": "completed", "event": "run.completed",
            "run_id": request.get("run_id") or "agno-run-2",
            "runtime_adapter": "agno", "output": "ok again", "metadata": {},
        },
    )
    assert backend.process(message("another")) == "ok again"


def test_other_operator_cannot_cancel_agno_turn(tmp_path, monkeypatch):
    """Isolamento entre sessões concorrentes preservado no branch Agno."""
    backend = backend_at(tmp_path)
    caller, release, seen, _ = start_blocked_agno_turn(backend, monkeypatch)
    try:
        other = message(user="99", chat="99")
        backend.cancel_channel_execution(other)
        assert not seen[0].cancel_event.is_set()
    finally:
        release.set()
        caller.join(timeout=5)


# ─── Caminho feliz: SDK Agno real, sem provider (mesmo padrão de
# tests/test_agno_runtime_adapter.py — modelo local/offline, sem rede). ────


def test_agno_happy_path_completes_simple_turn_through_channel(tmp_path):
    """Turno simples completa via `_turno_agno` pelo canal, SDK Agno real.

    `model.name: offline-echo` força `AgnoRuntimeAdapter._build_model` a usar
    o `OfflineAgnoModel` (eco local, sem rede) — SDK real, modelo fake, sem
    provider, igual aos sprints 38/39.
    """
    backend = backend_at(tmp_path, model_name="offline-echo")
    reply = backend.process(message("hello from telegram"))
    assert "user:hello from telegram" in reply
    stored = backend._store.load("tg:42")
    assistant_msgs = [m for m in stored if m.get("role") == "assistant"]
    # ctx.add_assistant não duplica no branch Agno (sprints 38/39): só o
    # append feito por `_turno_agno`, nunca o nativo (que já grava via
    # run_one_turn_with_fallback) — um turno = uma mensagem de assistant.
    assert len(assistant_msgs) == 1
    assert assistant_msgs[0]["content"] == reply
    user_msgs = [m for m in stored if m.get("role") == "user"]
    assert len(user_msgs) == 1


# ─── Achado documentado (STOP): custo de um turno Agno cancelado durante um
# loop de tool-calling não chega ao cost_sink — ver docstring do módulo. ───


def test_agno_cancellation_during_tool_loop_loses_partial_llm_cost_confirmed_gap(tmp_path):
    """Reproduz, com o SDK Agno real (sem monkeypatch de `run_agent`), que um
    turno cancelado NO MEIO de um tool call não reporta nenhum custo ao
    `cost_sink` — mesmo tendo uma chamada de LLM completa (com usage real)
    antes do cancelamento. `AgnoRuntimeAdapter._report_run_cost` só roda no
    evento final `RunCompleted`, que nunca é emitido numa execução
    cancelada. Isto é uma lacuna de paridade com o motor nativo (que reporta
    cada chamada de LLM assim que ela completa, antes do próximo
    checkpoint) — confirmada aqui, não corrigida (fora do escopo desta
    sprint: `AgnoRuntimeAdapter` não foi tocado). Ver BACKLOG.md.
    """
    from agno.models.base import Model
    from agno.models.response import ModelResponse

    from bauer.core.runtime.adapters.agno_adapter import AgnoRuntimeAdapter
    from bauer.execution_control import ExecutionControl, checkpoint_execution

    class CancellingRouter:
        def __init__(self):
            self.approval_started = threading.Event()
            self.approval_released = threading.Event()
            self.calls = 0

        def get_tool_schemas(self):
            return [{"function": {
                "name": "datetime_now",
                "parameters": {"type": "object", "properties": {}},
            }}]

        def execute_native_call(self, name, arguments):
            self.calls += 1
            self.approval_started.set()
            assert self.approval_released.wait(timeout=5)
            checkpoint_execution()
            raise AssertionError("checkpoint deveria interromper antes do tool rodar")

    class CountingToolModel(Model):
        calls = 0

        def invoke(self, *args, **kwargs):
            messages = kwargs.get("messages") or []
            self.calls += 1
            if messages and messages[-1].role == "tool":
                return ModelResponse(role="assistant", content="done")
            return ModelResponse(
                role="assistant",
                tool_calls=[{
                    "id": "call_1", "type": "function",
                    "function": {"name": "datetime_now", "arguments": "{}"},
                }],
            )

        async def ainvoke(self, *args, **kwargs):
            return self.invoke(*args, **kwargs)

        def invoke_stream(self, *args, **kwargs):
            yield self.invoke(*args, **kwargs)

        async def ainvoke_stream(self, *args, **kwargs):
            yield self.invoke(*args, **kwargs)

        def _parse_provider_response(self, response, **kwargs):
            return response

        def _parse_provider_response_delta(self, response):
            return response

    model = CountingToolModel(id="cancel-cost-model")
    control = ExecutionControl(run_id="cancel-cost", session_id="cancel-cost")
    router = CancellingRouter()
    reported: list[tuple] = []
    token = cost_sink.set(lambda *args: reported.append(args))

    def cancel_after_approval_wait_starts():
        assert router.approval_started.wait(timeout=5)
        control.request_cancel()
        router.approval_released.set()

    canceller = threading.Thread(target=cancel_after_approval_wait_starts)
    canceller.start()
    adapter = AgnoRuntimeAdapter(
        adapter_config={"db_file": str(tmp_path / "cancel-cost.db")},
        tool_router=router,
    )
    try:
        with control.activate():
            result = adapter.run_agent({
                "run_id": "cancel-cost", "session_id": "cancel-cost", "task": "call datetime",
                "agent_spec": {"id": "cancel-cost-agent", "tools": ["datetime_now"], "agno_model": model},
            })
    finally:
        cost_sink.reset(token)
    canceller.join(timeout=5)

    assert not canceller.is_alive()
    assert result["status"] == "cancelled"
    assert model.calls == 1  # a 1ª chamada de LLM completou de verdade
    assert router.calls == 1
    # Achado confirmado: nenhum custo chega ao sink, apesar da chamada
    # completada. Se uma sprint futura corrigir isso em
    # `AgnoRuntimeAdapter`, este teste vai falhar — é o sinal para
    # atualizá-lo junto da correção.
    assert reported == []


def test_agno_cancellation_with_lost_cost_logs_a_visible_warning(tmp_path, caplog):
    """Mitigação (não correção — ver teste acima) para o gap confirmado:
    `AgnoRuntimeAdapter.stream_agent` loga um warning quando um run
    cancelado/com falha teve pelo menos um evento real do SDK (prova de
    que o turno começou a rodar, não falhou antes de qualquer chamada de
    LLM) mas não reportou custo nenhum. Não recupera o valor perdido, só
    torna a lacuna visível em vez de silenciosa — decisão registrada em
    `plans/062-agno-executor-padrao.md`."""
    import logging

    from agno.models.base import Model
    from agno.models.response import ModelResponse

    from bauer.core.runtime.adapters.agno_adapter import AgnoRuntimeAdapter
    from bauer.execution_control import ExecutionControl, checkpoint_execution

    class CancellingRouter:
        def __init__(self):
            self.approval_started = threading.Event()
            self.approval_released = threading.Event()

        def get_tool_schemas(self):
            return [{"function": {
                "name": "datetime_now",
                "parameters": {"type": "object", "properties": {}},
            }}]

        def execute_native_call(self, name, arguments):
            self.approval_started.set()
            assert self.approval_released.wait(timeout=5)
            checkpoint_execution()
            raise AssertionError("checkpoint deveria interromper antes do tool rodar")

    class CountingToolModel(Model):
        def invoke(self, *args, **kwargs):
            messages = kwargs.get("messages") or []
            if messages and messages[-1].role == "tool":
                return ModelResponse(role="assistant", content="done")
            return ModelResponse(
                role="assistant",
                tool_calls=[{
                    "id": "call_1", "type": "function",
                    "function": {"name": "datetime_now", "arguments": "{}"},
                }],
            )

        async def ainvoke(self, *args, **kwargs):
            return self.invoke(*args, **kwargs)

        def invoke_stream(self, *args, **kwargs):
            yield self.invoke(*args, **kwargs)

        async def ainvoke_stream(self, *args, **kwargs):
            yield self.invoke(*args, **kwargs)

        def _parse_provider_response(self, response, **kwargs):
            return response

        def _parse_provider_response_delta(self, response):
            return response

    model = CountingToolModel(id="cancel-warning-model")
    control = ExecutionControl(run_id="cancel-warning", session_id="cancel-warning")
    router = CancellingRouter()

    def cancel_after_approval_wait_starts():
        assert router.approval_started.wait(timeout=5)
        control.request_cancel()
        router.approval_released.set()

    canceller = threading.Thread(target=cancel_after_approval_wait_starts)
    canceller.start()
    adapter = AgnoRuntimeAdapter(
        adapter_config={"db_file": str(tmp_path / "cancel-warning.db")},
        tool_router=router,
    )
    with caplog.at_level(logging.WARNING, logger="bauer.agno_adapter"):
        with control.activate():
            result = adapter.run_agent({
                "run_id": "cancel-warning", "session_id": "cancel-warning", "task": "call datetime",
                "agent_spec": {"id": "cancel-warning-agent", "tools": ["datetime_now"], "agno_model": model},
            })
    canceller.join(timeout=5)

    assert not canceller.is_alive()
    assert result["status"] == "cancelled"
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert "cancel-warning" in warnings[0].message
    assert "nenhum custo de LLM foi reportado" in warnings[0].message


def test_agno_normal_completion_never_logs_the_cost_gap_warning(tmp_path, caplog):
    """O warning é específico de cancelamento/falha com custo perdido — um
    turno que completa normalmente (reporta custo via RunCompleted como de
    costume) nunca deve disparar o aviso."""
    import logging

    from bauer.core.runtime.adapters.agno_adapter import AgnoRuntimeAdapter

    adapter = AgnoRuntimeAdapter(adapter_config={"db_file": str(tmp_path / "normal.db")})
    with caplog.at_level(logging.WARNING, logger="bauer.agno_adapter"):
        result = adapter.run_agent({
            "session_id": "normal-completion", "user_id": "user-1", "task": "hello",
        })

    assert result["status"] == "completed"
    assert not [r for r in caplog.records if "nenhum custo de LLM foi reportado" in r.message]


def test_agno_cancellation_during_bare_llm_call_still_logs_the_warning(tmp_path, caplog):
    """Fecha a lacuna teórica apontada na revisão: `_checkpointed_events`
    chama `checkpoint_execution()` ANTES de entregar cada evento ao loop de
    `stream_agent` — em teoria, um cancelamento detectado bem no meio dessa
    janela poderia descartar o próprio evento que provaria custo incorrido,
    zerando `sdk_events_seen` mesmo após uma chamada de LLM real. Isso não
    se materializa na prática porque o SDK Agno sempre emite `RunStarted`
    (evento barato, sem chamada de modelo) ANTES de invocar o modelo pela
    primeira vez — então pelo menos um evento já foi contado antes de
    qualquer custo real poder existir. Este teste cancela durante uma
    chamada de LLM NUA (sem tool call, caminho de interrupção diferente do
    teste de cancelamento-durante-tool acima — aqui não passa por
    `_guard_bauer_tool`) e confirma que o warning ainda dispara."""
    import logging
    import threading

    from agno.models.base import Model
    from agno.models.response import ModelResponse

    from bauer.core.runtime.adapters.agno_adapter import AgnoRuntimeAdapter
    from bauer.execution_control import ExecutionControl, checkpoint_execution

    class BlockingModel(Model):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.invoke_started = threading.Event()
            self.release = threading.Event()

        def invoke(self, *args, **kwargs):
            self.invoke_started.set()
            assert self.release.wait(timeout=5)
            checkpoint_execution()
            raise AssertionError("checkpoint deveria interromper antes do invoke retornar")

        async def ainvoke(self, *args, **kwargs):
            return self.invoke(*args, **kwargs)

        def invoke_stream(self, *args, **kwargs):
            yield self.invoke(*args, **kwargs)

        async def ainvoke_stream(self, *args, **kwargs):
            yield self.invoke(*args, **kwargs)

        def _parse_provider_response(self, response, **kwargs):
            return response

        def _parse_provider_response_delta(self, response):
            return response

    model = BlockingModel(id="bare-call-cancel-model")
    control = ExecutionControl(run_id="bare-call-cancel", session_id="bare-call-cancel")

    def cancel_once_invoke_starts():
        assert model.invoke_started.wait(timeout=5)
        control.request_cancel()
        model.release.set()

    canceller = threading.Thread(target=cancel_once_invoke_starts)
    canceller.start()
    adapter = AgnoRuntimeAdapter(adapter_config={"db_file": str(tmp_path / "bare-call-cancel.db")})
    with caplog.at_level(logging.WARNING, logger="bauer.agno_adapter"):
        with control.activate():
            result = adapter.run_agent({
                "run_id": "bare-call-cancel", "session_id": "bare-call-cancel", "task": "hello",
                "agent_spec": {"id": "bare-call-cancel-agent", "agno_model": model},
            })
    canceller.join(timeout=5)

    assert not canceller.is_alive()
    assert result["status"] == "cancelled"
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert "nenhum custo de LLM foi reportado" in warnings[0].message
