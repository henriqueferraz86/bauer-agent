"""Issue short-lived Tool Gateway tokens from already-authenticated turns.

Not wired into Telegram or AgentOS. Private signing key stays at the Gateway.
"""
from __future__ import annotations

import hashlib
import hmac
import time
import uuid

import jwt

from .core.runtime.run_manager import RunManager
from .remote_run_store import RemoteRunStore
from .tool_gateway_authorization import ToolGatewayAuthorizer, ToolGatewayDenied
from .tool_gateway_protocol import call_digest, verify_requirement_proof


class ToolGatewayIssuer:
    def __init__(self, *, private_key: str, identity_key: str, requirement_key: str, issuer: str, audience: str,
                 runs: RunManager, remote_runs: RemoteRunStore, authorizer: ToolGatewayAuthorizer):
        if any(not isinstance(v, str) or not v.strip() for v in (private_key, identity_key, requirement_key, issuer, audience)):
            raise ValueError("missing Tool Gateway signing configuration")
        if len(identity_key.encode("utf-8")) < 32 or len(requirement_key.encode("utf-8")) < 32:
            raise ValueError("identity and requirement keys must contain at least 32 bytes")
        self._private_key, self._identity_key, self._requirement_key = private_key, identity_key, requirement_key
        self._issuer, self._audience = issuer, audience
        self._runs, self._remote_runs, self._authorizer = runs, remote_runs, authorizer

    def issue_for_requirement(self, *, authenticated_user_id: str, gateway_run_id: str,
                              challenge: str, statement: dict, proof: str) -> str:
        """Mint only from a signed, server-derived requirement for this run.

        The trusted caller must authenticate the user, generate an unpredictable
        fresh challenge and obtain the statement from the authenticated AgentOS.
        """
        if (not isinstance(authenticated_user_id, str) or not authenticated_user_id.isascii()
                or not authenticated_user_id.isdecimal() or len(authenticated_user_id) > 32
                or int(authenticated_user_id) <= 0 or str(int(authenticated_user_id)) != authenticated_user_id
                or not isinstance(gateway_run_id, str) or not gateway_run_id.strip()
                or not verify_requirement_proof(self._requirement_key, challenge, statement, proof)):
            raise ToolGatewayDenied("tool request denied")
        subject = "tg-" + hmac.new(
            self._identity_key.encode(), b"bauer-agentos-telegram-subject-v1\0" + authenticated_user_id.encode(),
            hashlib.sha256,
        ).hexdigest()
        try:
            run = self._runs.get_run(gateway_run_id)
            remote = self._remote_runs.get(gateway_run_id, owner_id=subject)
            if (run is None or statement["subject"] != subject or statement["entity_type"] != "agent"
                    or statement["entity_id"] != remote.entity_id or statement["remote_run_id"] != remote.remote_run_id
                    or statement["remote_session_id"] != remote.session_id):
                raise ValueError("run unavailable")
            now = int(time.time())
            claims = {"sub": subject, "iss": self._issuer, "aud": self._audience,
                      "iat": now, "exp": now + 60, "jti": uuid.uuid4().hex,
                      "scopes": ["tool_gateway:invoke"], "channel": "telegram",
                      "gateway_run_id": run.id, "gateway_session_id": run.session_id,
                      "project_id": run.input.get("project_id"), "remote_run_id": remote.remote_run_id,
                      "remote_session_id": remote.session_id, "agent_id": remote.entity_id,
                      "tool_call_id": statement["tool_call_id"],
                      "call_digest": call_digest(statement["action"], statement["args"])}
            token = jwt.encode(claims, self._private_key, algorithm="RS256")
            # Do not mint a useful capability for a stale or foreign turn.
            self._authorizer.authorize(f"Bearer {token}")
            return token
        except Exception as exc:  # noqa: BLE001 - no context or secret leaks
            raise ToolGatewayDenied("tool request denied") from exc
