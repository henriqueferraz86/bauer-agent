"""Sprint 46: exact Telegram approval end to end, native and Agno executors.

Drives a real ``AgentBackend`` turn (real Kernel, RunManager, EventBus,
ApprovalManager and ToolRouter policy) bound to a selected project, with a
``TelegramBridge`` talking to ``httpx.MockTransport``. The tool body is a
counter installed after the ToolRouter's policy seam, so the test proves the
worker suspended in the router resumes exactly once after the card decision,
both for ``bauer_native`` and for ``runtime.telegram_default_adapter: agno``.
"""

from __future__ import annotations

import json
import threading
import time

import httpx
import pytest

from bauer import projects_registry as registry
from bauer.channel_base import AgentBackend, ChannelMessage
from bauer.shell_runner import ShellRunner
from bauer.sqlite_session_store import SqliteSessionStore
from bauer.telegram_bridge import TelegramBridge
from bauer.tool_router import ToolRouter

COMMAND = "git status"


def _message(text="run it"):
    return ChannelMessage(
        "telegram", "42", "42", text,
        raw={"message": {"chat": {"id": 42, "type": "private"}}},
    )


class _E2E:
    def __init__(self, tmp_path, monkeypatch, adapter: str):
        monkeypatch.setattr(registry, "_DEFAULT_REGISTRY", tmp_path / "projects.json")
        monkeypatch.setattr("bauer.shell_runner.shutil.which", lambda executable: executable)
        self.project = tmp_path / "alpha"
        self.project.mkdir()
        entry = registry.add_project(self.project)
        default = tmp_path / "default"
        default.mkdir()
        runtime = f"runtime:\n  telegram_default_adapter: {adapter}\n" if adapter == "agno" else ""
        config = tmp_path / "config.yaml"
        config.write_text(
            f"agent:\n  workspace: {default.as_posix()}\n"
            "model:\n  provider: ollama\n  name: fake\n"
            "kernel:\n  enabled: true\n" + runtime,
            encoding="utf-8",
        )
        self.executed: list[dict] = []
        self.routers: list[ToolRouter] = []

        def build_router(path):
            router = ToolRouter(path, shell_runner=ShellRunner(workspace=path, safe_mode=False))
            # Replace only the tool body: ToolRouter.execute still runs the
            # HARDLINE, runtime policy and approval wait before dispatching.
            router._tools["run_command"]["fn"] = lambda args: (
                self.executed.append(dict(args)) or "command ran"
            )
            self.routers.append(router)
            return router

        backend = AgentBackend(config, tmp_path / "memory" / "sessions")
        backend._client = object()
        backend._model_name = "fake"
        backend._provider = "ollama"
        backend._applied_context = 4096
        backend._system_prompt = "Controlled worker"
        backend._router = ToolRouter(default)
        backend._store = SqliteSessionStore(backend.sessions_dir, semantic_indexing_enabled=False)
        backend._maybe_reload = lambda: None
        backend._project_router_builder = build_router
        backend.select_channel_project(_message(), entry["id"])
        self.backend = backend
        self.requests: list[tuple[str, dict]] = []

        def transport(request):
            method = request.url.path.rsplit("/", 1)[-1]
            body = json.loads(request.content or b"{}")
            self.requests.append((method, body))
            result = {"message_id": 777} if method == "sendMessage" else True
            return httpx.Response(200, json={"ok": True, "result": result})

        self.bridge = TelegramBridge(
            "123:FAKE", backend, allowed_users=[42], state_dir=tmp_path / "gateway",
        )
        self.bridge._http.close()
        self.bridge._http = httpx.Client(transport=httpx.MockTransport(transport))

    def card(self, timeout=10.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            for method, body in list(self.requests):
                markup = body.get("reply_markup") if method == "sendMessage" else None
                if isinstance(markup, dict) and markup.get("inline_keyboard"):
                    data = markup["inline_keyboard"][0][0].get("callback_data", "")
                    if data.startswith("apr:"):
                        return body
            time.sleep(0.02)
        return None

    def click(self, card, choice):
        token = card["reply_markup"]["inline_keyboard"][0][0]["callback_data"].split(":")[1]
        self.bridge._approval_callback({
            "id": "query-1", "data": f"apr:{token}:{choice}", "from": {"id": 42},
            "message": {"message_id": 777, "chat": {"id": 42, "type": "private"}},
        })

    def kernel(self):
        return self.backend._resolver_kernel(workspace=self.project.resolve())


def _native_turn(monkeypatch):
    def turn(ctx, router, *_args, **_kwargs):
        try:
            result = router.execute({"action": "run_command", "args": {"command": COMMAND}})
        except Exception as exc:  # the ToolError is what the model would see
            result = f"tool error: {exc}"
        ctx.add_assistant(result)
        return result, [{"tool": "run_command", "result": result}]

    monkeypatch.setattr("bauer.agent.run_one_turn_with_fallback", turn)


def _agno_turn(monkeypatch):
    pytest.importorskip("agno")
    pytest.importorskip("sqlalchemy")
    from agno.models.base import Model
    from agno.models.response import ModelResponse

    class ToolCallingModel(Model):
        def invoke(self, *args, **kwargs):
            messages = kwargs.get("messages") or []
            if messages and messages[-1].role == "tool":
                return ModelResponse(role="assistant", content=f"tool said: {messages[-1].content}")
            return ModelResponse(role="assistant", tool_calls=[{
                "id": "call_1", "type": "function",
                "function": {"name": "run_command", "arguments": json.dumps({"command": COMMAND})},
            }])

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

    monkeypatch.setattr(
        "bauer.core.runtime.adapters.agno_adapter.AgnoRuntimeAdapter._build_model",
        lambda self, spec: ToolCallingModel(id="approval-e2e"),
    )


@pytest.mark.parametrize("adapter", ["bauer_native", "agno"])
@pytest.mark.parametrize("choice", ["yes", "no"])
def test_card_decision_resumes_or_stops_the_suspended_worker_exactly_once(
    tmp_path, monkeypatch, adapter, choice,
):
    (_agno_turn if adapter == "agno" else _native_turn)(monkeypatch)
    kit = _E2E(tmp_path, monkeypatch, adapter)
    output: dict = {}
    worker = threading.Thread(
        target=lambda: output.update(reply=kit.backend.process(_message())), daemon=True,
    )
    worker.start()
    try:
        card = kit.card()
        assert card is not None, kit.requests
        assert COMMAND not in card["text"]
        assert str(kit.project) not in card["text"]
        kernel = kit.kernel()
        (pending,) = kernel.approvals.list("pending")
        assert pending.expires_at is not None
        assert kit.executed == []
        kit.click(card, choice)
        worker.join(timeout=20)
        assert not worker.is_alive()
    finally:
        kit.bridge.stop()
        worker.join(timeout=5)

    record = kernel.approvals.get(pending.id)
    if choice == "yes":
        assert kit.executed == [{"command": COMMAND, "confirm": False, "background": False}] \
            if adapter == "agno" else [{"command": COMMAND}]
        assert record.status == "approved" and record.consumed_at
        assert "command ran" in output["reply"]
    else:
        assert kit.executed == []
        assert record.status == "denied"
        assert "command ran" not in output["reply"]
    # A second click on the same card never produces a second execution.
    kit.click(card, "yes")
    assert len(kit.executed) == (1 if choice == "yes" else 0)
