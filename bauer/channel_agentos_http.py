"""Staged AgentOS submission with persistent at-most-once admission.

This is not connected to Telegram: authentication, tool governance and
identity isolation must be proven before it is enabled for a channel.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .agentos_http_client import AgentOSHttpClient, AgentOSHttpError
from .remote_run_store import RemoteRun, RemoteRunConflict, RemoteRunStore


@dataclass(frozen=True)
class SubmissionResult:
    run: RemoteRun
    replay: bool
    content: Any = None


class AgentOSChannelExecutor:
    """Claim a channel update and submit at most once, even across restarts."""

    def __init__(self, client: AgentOSHttpClient, store: RemoteRunStore):
        self.client = client
        self.store = store

    def reconcile(self, gateway_run_id: str, *, owner_id: str) -> SubmissionResult:
        """Read a known remote run; never create, continue or cancel one.

        Runs without a remote ID require operator or server-side discovery;
        guessing an ID or sending the original POST again is unsafe.
        """
        record = self.store.get(gateway_run_id, owner_id=owner_id)
        if record.state in {"COMPLETED", "CANCELLED", "ERROR"}:
            return SubmissionResult(record, replay=True)
        if not record.remote_run_id:
            raise ValueError("cannot reconcile run without a remote ID")
        response = self.client.get_run(
            "agents" if record.entity_type == "agent" else "teams", record.entity_id,
            record.remote_run_id, record.session_id,
        )
        state = "UNKNOWN"
        if isinstance(response, dict):
            response_run = response.get("run_id")
            response_session = response.get("session_id")
            raw_status = response.get("status")
            status = raw_status.upper() if isinstance(raw_status, str) else ""
            valid = {"RUNNING", "PAUSED", "COMPLETED", "CANCELLED", "ERROR"}
            if (response_run == record.remote_run_id
                    and (response_session is None or response_session == record.session_id)
                    and status in valid and response.get("_http_status") != 202):
                state = status
        # The store intentionally rejects self-transitions. Preserve the
        # existing record in this case, including a still-uncertain outcome.
        updated = (record if state == record.state else
                   self.store.transition(record.gateway_run_id, owner_id=owner_id, state=state))
        content = response.get("content") if state == "COMPLETED" and isinstance(response, dict) else None
        return SubmissionResult(updated, replay=True, content=content)

    def submit(self, *, gateway_run_id: str, channel: str, update_id: str,
               generation: int, owner_id: str, session_id: str, entity_type: str,
               entity_id: str, message: str) -> SubmissionResult:
        record, created = self.store.claim(
            gateway_run_id=gateway_run_id, channel=channel, update_id=update_id,
            generation=generation, owner_id=owner_id, session_id=session_id,
            entity_type=entity_type, entity_id=entity_id,
        )
        if not created:
            return SubmissionResult(record, replay=True)

        # This transition commits before any network I/O. A crash leaves an
        # uncertain record that must be reconciled, never resubmitted blindly.
        self.store.transition(record.gateway_run_id, owner_id=owner_id, state="SUBMITTING")
        try:
            if entity_type == "agent":
                response = self.client.run_agent(entity_id, message, session_id, owner_id)
            else:
                response = self.client.run_team(entity_id, message, session_id, owner_id)
        except AgentOSHttpError:
            # An HTTP error alone cannot prove where the server rejected the
            # request; application errors can be returned after a run started.
            # Keep the session blocked until admission is reconciled.
            self.store.transition(record.gateway_run_id, owner_id=owner_id, state="UNKNOWN")
            raise
        except Exception:
            self.store.transition(record.gateway_run_id, owner_id=owner_id, state="UNKNOWN")
            raise

        if not isinstance(response, dict):
            updated = self.store.transition(record.gateway_run_id, owner_id=owner_id, state="UNKNOWN")
            return SubmissionResult(updated, replay=False)

        run_id = response.get("run_id")
        run_id = run_id if isinstance(run_id, str) and run_id.strip() else None
        response_session = response.get("session_id")
        if response_session is not None and response_session != session_id:
            updated = self.store.transition(record.gateway_run_id, owner_id=owner_id, state="UNKNOWN")
            return SubmissionResult(updated, replay=False)
        raw_status = response.get("status")
        status = raw_status.upper() if isinstance(raw_status, str) else ""
        valid = {"RUNNING", "PAUSED", "COMPLETED", "CANCELLED", "ERROR"}
        # 202 reports acceptance, not completion, even if a response body is
        # misleading. A run without an ID cannot be considered completed.
        state = status if run_id and status in valid and response.get("_http_status") != 202 else "UNKNOWN"
        updated = self.store.transition(record.gateway_run_id, owner_id=owner_id,
                                        state=state, remote_run_id=run_id)
        content = response.get("content") if state == "COMPLETED" else None
        return SubmissionResult(updated, replay=False, content=content)

    def submit_stream(self, *, gateway_run_id: str, channel: str, update_id: str,
                      generation: int, owner_id: str, session_id: str, entity_type: str,
                      entity_id: str, message: str, on_delta: Any = None,
                      on_pause: Any = None) -> SubmissionResult:
        """Submit once; persist RunStarted before accepting later events."""
        record, created = self.store.claim(
            gateway_run_id=gateway_run_id, channel=channel, update_id=update_id,
            generation=generation, owner_id=owner_id, session_id=session_id,
            entity_type=entity_type, entity_id=entity_id,
        )
        if not created:
            return SubmissionResult(record, replay=True)
        record = self.store.transition(gateway_run_id, owner_id=owner_id, state="SUBMITTING")
        parts: list[str] = []
        try:
            events = self.client.stream_run("agents" if entity_type == "agent" else "teams",
                                            entity_id, message, session_id, owner_id)
            for event in events:
                if not isinstance(event, dict):
                    continue
                remote_id = event.get("run_id")
                event_session = event.get("session_id")
                if event_session is not None and event_session != session_id:
                    raise ValueError("remote session mismatch")
                if remote_id is not None and (not isinstance(remote_id, str) or not remote_id.strip()
                                              or (record.remote_run_id and remote_id != record.remote_run_id)):
                    raise ValueError("remote run mismatch")
                name = str(event.get("event") or "")
                if name in {"RunStarted", "TeamRunStarted"} and remote_id and record.state == "SUBMITTING":
                    record = self.store.transition(gateway_run_id, owner_id=owner_id,
                                                   state="RUNNING", remote_run_id=remote_id)
                if name in {"RunContent", "TeamRunContent"} and isinstance(event.get("content"), str):
                    parts.append(event["content"])
                    if on_delta is not None:
                        try:
                            on_delta(event["content"])
                        except Exception:  # noqa: BLE001 — display is best effort
                            pass
                if name in {"RunPaused", "TeamRunPaused"} and record.remote_run_id:
                    record = self.store.transition(gateway_run_id, owner_id=owner_id, state="PAUSED")
                    if on_pause is None:
                        return SubmissionResult(record, replay=False)
                    for _ in range(16):
                        response = on_pause(record)
                        if not isinstance(response, dict):
                            raise ValueError("invalid controlled continuation")
                        state = str(response.get("status") or "").upper()
                        if state == "PAUSED":
                            continue
                        if state not in {"COMPLETED", "CANCELLED", "ERROR"}:
                            raise ValueError("uncertain controlled continuation")
                        record = self.store.transition(gateway_run_id, owner_id=owner_id, state=state)
                        return SubmissionResult(record, replay=False,
                                                content=response.get("content") if state == "COMPLETED" else None)
                    raise ValueError("controlled continuation limit exceeded")
                terminal = {"RunCompleted": "COMPLETED", "TeamRunCompleted": "COMPLETED",
                            "RunCancelled": "CANCELLED", "TeamRunCancelled": "CANCELLED",
                            "RunError": "ERROR", "TeamRunError": "ERROR"}.get(name)
                if terminal and (record.remote_run_id or remote_id):
                    try:
                        record = self.store.transition(gateway_run_id, owner_id=owner_id,
                                                       state=terminal, remote_run_id=remote_id)
                    except RemoteRunConflict:
                        record = self.store.get(gateway_run_id, owner_id=owner_id)
                    content = event.get("content") or "".join(parts)
                    return SubmissionResult(record, replay=False,
                                            content=content if record.state == "COMPLETED" else None)
        except Exception:
            record = self.store.get(gateway_run_id, owner_id=owner_id)
            if record.state not in {"COMPLETED", "CANCELLED", "ERROR", "UNKNOWN"}:
                self.store.transition(gateway_run_id, owner_id=owner_id, state="UNKNOWN")
            raise
        record = self.store.get(gateway_run_id, owner_id=owner_id)
        if record.state not in {"COMPLETED", "CANCELLED", "ERROR", "UNKNOWN"}:
            record = self.store.transition(gateway_run_id, owner_id=owner_id, state="UNKNOWN")
        return SubmissionResult(record, replay=False)
