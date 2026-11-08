"""Telegram tool-approval cards: isolated, project-bound and fail-closed."""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest

from bauer.channel_base import AgentBackend, ChannelMessage
from bauer.channel_execution import ChannelExecution, SessionWorker
from bauer.channel_projects import ChannelProjectContext, operator_key
from bauer.core.events import EventBus
from bauer.core.kernel import BauerKernel
from bauer.core.policy import ApprovalManager
from bauer.core.runtime.run_manager import RunManager
from bauer.telegram_bridge import TelegramBridge


def _message(user="42", chat="42"):
    return ChannelMessage(
        "telegram", str(user), str(chat), "",
        raw={"message": {"chat": {"id": int(chat), "type": "private"}}},
    )


class _ApprovalFixture:
    def __init__(self, tmp_path: Path, handler=None):
        self.workspace = tmp_path / "project"
        self.workspace.mkdir()
        self.session = "tg-project-ownerhash-project-1"
        self.owner = operator_key(_message())
        self.root = tmp_path / "runtime"
        self.bus = EventBus(root=self.root)
        self.runs = RunManager(root=self.root, event_bus=self.bus)
        self.approvals = ApprovalManager(root=self.root, event_bus=self.bus)
        self.kernel = BauerKernel(runs=self.runs, bus=self.bus, approvals=self.approvals,
                                  heartbeat_interval_s=0)
        self.run = self.runs.create_run(
            session_id=self.session, agent_id="channel.telegram",
            input={"endpoint": "canal:telegram", "operator": self.owner,
                   "project_id": "project-1", "workspace": str(self.workspace),
                   "task": "Deploy safely"},
            status="running",
        )
        self.job = ChannelExecution(
            owner=self.owner, channel="telegram", chat_id="42", project_id="project-1",
            session_key=self.session, workspace=self.workspace, batch=SessionWorker(),
            private_chat=True,
        )
        self.job.control.run_id = self.run.id
        self.context = ChannelProjectContext(
            "project-1", "Example Project", self.workspace, self.session,
        )
        self.backend = AgentBackend(tmp_path / "missing-config.yaml", tmp_path / "sessions")
        self.backend._executions.add(self.job)
        self.backend.channel_approval_source = lambda msg: (self.context, self.kernel)
        self.bridge = TelegramBridge(
            "123:FAKE", self.backend, allowed_users=[42], state_dir=tmp_path / "gateway",
        )
        self.requests: list[tuple[str, dict]] = []

        def transport(request):
            method = request.url.path.rsplit("/", 1)[-1]
            body = json.loads(request.content or b"{}")
            self.requests.append((method, body))
            if handler is not None:
                return handler(request)
            result = {"message_id": 777} if method == "sendMessage" else True
            return httpx.Response(200, json={"ok": True, "result": result})

        self.bridge._http.close()
        self.bridge._http = httpx.Client(transport=httpx.MockTransport(transport))

    def request(self, *, expires_in_seconds=600, command="git status", before_publish=None):
        def register(record):
            assert self.job.control.begin_approval_wait(
                record.id, record.payload_digest, record.expires_at,
            )
            if before_publish:
                before_publish(record)

        # This is the same bridge hook AgentBackend invokes before run_governed
        # starts and before ApprovalManager synchronously publishes the event.
        self.backend._telegram_approval_subscriber(self.bus)
        return self.approvals.request(
            operation="shell.execute", tool_name="run_command", reason="Needs approval",
            risk_level="high", payload={"command": command}, run_id=self.run.id,
            session_id=self.session, suspend_run=True,
            expires_in_seconds=expires_in_seconds, before_publish=register,
        )

    def wait_for_card(self, timeout=2):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            cards = [body for method, body in self.requests if method == "sendMessage"]
            if cards:
                return cards[-1]
            time.sleep(0.01)
        return None

    def callback(self, token, *, user="42", chat="42", message_id=777,
                 private=True, choice="yes"):
        self.bridge._approval_callback({
            "id": "query-1", "data": f"apr:{token}:{choice}",
            "from": {"id": int(user)},
            "message": {"message_id": message_id,
                        "chat": {"id": int(chat), "type": "private" if private else "group"}},
        })

    def token(self, card):
        data = card["reply_markup"]["inline_keyboard"][0][0]["callback_data"]
        return data.split(":")[1]

    def close(self):
        self.bridge.stop()


@pytest.fixture
def kit(tmp_path):
    value = _ApprovalFixture(tmp_path)
    yield value
    value.close()


def test_subscriber_and_exact_waiter_are_registered_before_request_event(kit):
    seen = []
    original = kit.bus.publish

    def publish(event_type, **kwargs):
        if event_type == "approval.requested":
            seen.append((kit.job.control.approval_wait_id,
                         kit.job.control.approval_wait_digest,
                         id(kit.bus) in kit.bridge._approval_buses))
        return original(event_type, **kwargs)

    kit.bus.publish = publish
    record = kit.request()
    assert seen == [(record.id, record.payload_digest, True)]
    card = kit.wait_for_card()
    assert card and "Example Project" in card["text"]
    assert "Deploy safely" in card["text"]
    assert "git status" not in card["text"]
    assert "Aprovar uma vez" in card["reply_markup"]["inline_keyboard"][0][0]["text"]


@pytest.mark.parametrize(("choice", "approval_status", "run_status", "copy"), [
    ("yes", "approved", "queued", "Aprovação concedida"),
    ("no", "denied", "failed", "Solicitação negada"),
])
def test_private_card_decision_is_bound_and_one_time(kit, choice, approval_status, run_status, copy):
    record = kit.request()
    card = kit.wait_for_card()
    assert card
    kit.callback(kit.token(card), choice=choice)
    assert kit.approvals.get(record.id).status == approval_status
    assert kit.runs.get_run(kit.run.id).status == run_status
    edits = [body for method, body in kit.requests if method == "editMessageText"]
    assert edits and copy in edits[-1]["text"]
    kit.callback(kit.token(card), choice=choice)
    assert kit.approvals.get(record.id).status == approval_status


@pytest.mark.parametrize("mismatch", ["user", "chat", "message", "group", "project", "digest"])
def test_callback_rejects_identity_project_or_digest_mismatch(kit, mismatch):
    record = kit.request()
    card = kit.wait_for_card()
    assert card
    token = kit.token(card)
    kwargs = {}
    if mismatch == "user":
        kwargs["user"] = "99"
    elif mismatch == "chat":
        kwargs["chat"] = "99"
    elif mismatch == "message":
        kwargs["message_id"] = 778
    elif mismatch == "group":
        kwargs["private"] = False
    elif mismatch == "project":
        kit.context = ChannelProjectContext(
            "other-project", "Other", kit.workspace, kit.session,
        )
    elif mismatch == "digest":
        kit.job.control._approval_wait_digest = "different-digest"
    kit.callback(token, **kwargs)
    assert kit.approvals.get(record.id).status == "pending"
    assert kit.runs.get_run(kit.run.id).status == "waiting_approval"


def test_deadline_expiry_is_persisted_and_callback_is_not_actionable(kit):
    record = kit.request(expires_in_seconds=600)
    card = kit.wait_for_card()
    assert card
    state = kit.bridge._approval_callbacks[kit.token(card)]
    state["callback_expires_at"] = time.monotonic() - 1
    kit.callback(state["token"])
    assert kit.approvals.get(record.id).status == "pending"
    assert kit.runs.get_run(kit.run.id).status == "waiting_approval"


def test_expiry_winning_inside_sqlite_decision_is_reported_from_persisted_record(kit):
    record = kit.request()
    card = kit.wait_for_card()
    assert card
    approve = kit.kernel.approve

    def expire_at_decision(approval_id, **kwargs):
        current = kit.approvals.store.latest("approvals", approval_id)
        kit.approvals.store.upsert("approvals", {
            **current,
            "expires_at": (datetime.now(UTC) - timedelta(seconds=1)).isoformat(),
        })
        return approve(approval_id, **kwargs)

    kit.kernel.approve = expire_at_decision
    kit.callback(kit.token(card))
    assert kit.approvals.get(record.id).status == "expired"
    assert kit.runs.get_run(kit.run.id).status == "failed"
    edits = [body for method, body in kit.requests if method == "editMessageText"]
    assert edits and "expirou" in edits[-1]["text"]


@pytest.mark.parametrize("path", [
    "/home/alice/private/project", r"C:\Users\Alice\private\project",
    r"\\server\share\private\project",
])
def test_card_redacts_posix_drive_and_unc_paths(tmp_path, path):
    kit = _ApprovalFixture(tmp_path)
    try:
        kit.run = kit.runs.update_run(
            kit.run.id, input={**kit.run.input, "task": f"Please inspect {path}"},
        )
        # The mutable run input is the persisted task source used by the card.
        record = kit.request(command=f"git -C {path} status")
        card = kit.wait_for_card()
        assert record
        assert card
        assert path not in card["text"]
        assert "[caminho removido]" in card["text"]
    finally:
        kit.close()


def test_telegram_send_failure_does_not_resolve_or_break_approval(tmp_path):
    kit = _ApprovalFixture(tmp_path)
    try:
        class InlineExecutor:
            @staticmethod
            def submit(fn, *args):
                fn(*args)

            @staticmethod
            def shutdown(wait=False):
                pass

        def broken(method, **_kwargs):
            if method == "sendMessage":
                raise httpx.ConnectError("offline")
            return {"message_id": 777}

        kit.bridge._approval_executor = InlineExecutor()
        kit.bridge._api = broken
        record = kit.request()
        assert kit.approvals.get(record.id).status == "pending"
        assert kit.runs.get_run(kit.run.id).status == "waiting_approval"
        assert not kit.bridge._approval_callbacks
    finally:
        kit.close()


def test_stop_reset_clears_callbacks_and_only_requests_cooperative_cancel(kit):
    record = kit.request()
    card = kit.wait_for_card()
    assert card
    token = kit.token(card)
    assert token in kit.bridge._approval_callbacks
    kit.bridge.stop()
    assert kit.job.control.cancel_event.is_set()
    assert kit.runs.get_run(kit.run.id).status == "waiting_approval"
    assert not kit.bridge._approval_callbacks
    kit.bridge.reset()
    kit.bridge._http.close()
    kit.bridge._http = httpx.Client(transport=httpx.MockTransport(
        lambda request: httpx.Response(200, json={"ok": True, "result": True})
    ))
    kit.callback(token)
    assert kit.approvals.get(record.id).status == "pending"
    assert kit.runs.get_run(kit.run.id).status == "waiting_approval"


def test_non_private_telegram_execution_never_gets_an_approval_card(tmp_path):
    kit = _ApprovalFixture(tmp_path)
    try:
        # Group/topic executions never enable the Telegram wait; even if a
        # waiter registration exists, the card must not be rendered.
        kit.job.private_chat = False
        record = kit.request()
        assert kit.wait_for_card(timeout=0.5) is None
        assert not kit.bridge._approval_callbacks
        assert kit.approvals.get(record.id).status == "pending"
    finally:
        kit.close()
