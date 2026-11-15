"""Fail-closed authorization primitive for a future Gateway-hosted tool service.

This module does not execute tools or expose a network endpoint. The caller
must authenticate transport separately, enforce a server-side tool policy,
bind each tool-call digest to a single-use approval, and revalidate under the
same execution/cancellation barrier immediately before any side effect.
"""
from __future__ import annotations

import hashlib
import hmac
import re
import time
from dataclasses import dataclass
from pathlib import Path
import jwt

from . import projects_registry
from .channel_projects import ChannelProjectStore
from .core.runtime.run_manager import RunManager
from .remote_run_store import RemoteRunStore


class ToolGatewayDenied(PermissionError):
    """Authentication or context validation failed (no detail is exposed)."""


@dataclass(frozen=True)
class AuthorizedToolContext:
    owner_subject: str
    operator: str
    gateway_run_id: str
    gateway_session_id: str
    project_id: str
    workspace: Path
    remote_run_id: str
    remote_session_id: str
    agent_id: str
    token_id: str
    tool_call_id: str
    call_digest: str


class ToolGatewayAuthorizer:
    """Verify a Gateway-issued asymmetric token against authoritative local state.

    The public verification key can be given to the AgentOS-facing service;
    only the Gateway holds the separate private signing key. The identity
    derivation key matches the Gateway's AgentOS subject derivation and never
    comes from the request. Tokens alone are insufficient: the current Kernel
    run, remote-run mapping and local project binding must all agree.
    """

    def __init__(self, *, public_key: str, identity_key: str, issuer: str, audience: str,
                 runs: RunManager, remote_runs: RemoteRunStore, bindings: ChannelProjectStore,
                 registry_path: Path):
        if any(not isinstance(v, str) or not v.strip() for v in (public_key, identity_key, issuer, audience)):
            raise ValueError("tool gateway authentication configuration missing")
        if len(identity_key.encode("utf-8")) < 32 or not isinstance(registry_path, Path):
            raise ValueError("invalid tool gateway configuration")
        self._public_key, self._identity_key = public_key, identity_key
        self._issuer, self._audience = issuer, audience
        self._runs, self._remote_runs, self._bindings, self._registry_path = runs, remote_runs, bindings, registry_path
        root = getattr(getattr(runs, "store", None), "root", None)
        if not isinstance(root, Path):
            raise ValueError("authoritative run store must have a filesystem root")
        self.policy_root = root.resolve()

    def activate_effect_guard(self, ledger_path: Path) -> None:
        """Persist guard activation in the same store that validates runs."""
        from .core.runtime.state_store import SqliteStateStore

        if not isinstance(self._runs.store, SqliteStateStore):
            raise ValueError("Tool Gateway requires an authoritative SQLite run store")
        self._runs.store.activate_tool_gateway_guard(ledger_path)

    def authorize(self, bearer: str) -> AuthorizedToolContext:
        """Read current authorities and return context only; never execute a tool."""
        try:
            return self._authorize(bearer)
        except ToolGatewayDenied:
            raise
        except Exception as exc:  # noqa: BLE001 - no internal data disclosed at boundary
            raise ToolGatewayDenied("tool request denied") from exc

    def _authorize(self, bearer: str) -> AuthorizedToolContext:
        if not isinstance(bearer, str):
            raise ToolGatewayDenied("tool request denied")
        parts = bearer.split(" ")
        if len(parts) != 2 or parts[0].lower() != "bearer" or not parts[1]:
            raise ToolGatewayDenied("tool request denied")
        claims = jwt.decode(parts[1], self._public_key, algorithms=["RS256"],
                            audience=self._audience, issuer=self._issuer,
                            options={"require": ["sub", "iss", "aud", "exp", "iat", "jti"]})
        required = ("sub", "jti", "gateway_run_id", "gateway_session_id", "project_id",
                    "remote_run_id", "remote_session_id", "agent_id", "tool_call_id", "call_digest")
        allowed_claims = set(required) | {"iss", "aud", "exp", "iat", "nbf", "scopes", "channel"}
        if set(claims) - allowed_claims:
            raise ToolGatewayDenied("tool request denied")
        if any(not isinstance(claims.get(name), str) or not claims[name].strip()
               or claims[name] != claims[name].strip() or len(claims[name]) > 256 for name in required):
            raise ToolGatewayDenied("tool request denied")
        if (not re.fullmatch(r"[0-9a-f]{64}", claims["call_digest"])
                or claims.get("scopes") != ["tool_gateway:invoke"] or claims.get("channel") != "telegram"
                or isinstance(claims.get("iat"), bool) or not isinstance(claims.get("iat"), int)
                or isinstance(claims.get("exp"), bool) or not isinstance(claims.get("exp"), int)
                or claims["exp"] <= claims["iat"] or claims["exp"] - claims["iat"] > 60
                or claims["iat"] > int(time.time()) + 5):
            raise ToolGatewayDenied("tool request denied")
        run = self._runs.get_run(claims["gateway_run_id"])
        if (run is None or run.status != "running" or run.agent_id != "channel.telegram"
                or run.runtime_adapter != "agentos_http" or run.session_id != claims["gateway_session_id"]
                or run.input.get("endpoint") != "canal:telegram"
                or run.input.get("project_id") != claims["project_id"]):
            raise ToolGatewayDenied("tool request denied")
        operator = run.input.get("operator")
        operator_parts = operator.split(":") if isinstance(operator, str) else []
        if (len(operator_parts) != 3 or operator_parts[0] != "telegram"
                or not operator_parts[1].isascii() or not operator_parts[1].isdecimal()
                or operator_parts[1] != operator_parts[2] or int(operator_parts[1]) <= 0
                or str(int(operator_parts[1])) != operator_parts[1]):
            raise ToolGatewayDenied("tool request denied")
        digest = hmac.new(self._identity_key.encode("utf-8"),
                          b"bauer-agentos-telegram-subject-v1\0" + operator_parts[2].encode("ascii"),
                          hashlib.sha256).hexdigest()
        if not hmac.compare_digest(claims["sub"], f"tg-{digest}"):
            raise ToolGatewayDenied("tool request denied")
        remote = self._remote_runs.get(run.id, owner_id=claims["sub"])
        if (remote.channel != "telegram" or remote.state not in {"RUNNING", "PAUSED"} or remote.entity_type != "agent"
                or remote.remote_run_id != claims["remote_run_id"] or remote.session_id != claims["remote_session_id"]
                or remote.entity_id != claims["agent_id"]):
            raise ToolGatewayDenied("tool request denied")
        binding = self._bindings.binding(operator)
        project = projects_registry.get_project(claims["project_id"], registry_path=self._registry_path)
        if binding is None or not isinstance(project, dict) or not isinstance(project.get("path"), str):
            raise ToolGatewayDenied("tool request denied")
        workspace = Path(project["path"]).expanduser().resolve()
        run_workspace = run.input.get("workspace")
        if (binding != (claims["project_id"], str(workspace)) or not isinstance(run_workspace, str)
                or Path(run_workspace).expanduser().resolve() != workspace
                or projects_registry.project_id(workspace) != claims["project_id"]
                or projects_registry.is_sensitive_dir(workspace) or not workspace.is_dir()):
            raise ToolGatewayDenied("tool request denied")
        return AuthorizedToolContext(
            owner_subject=claims["sub"], operator=operator, gateway_run_id=run.id,
            gateway_session_id=run.session_id, project_id=claims["project_id"], workspace=workspace,
            remote_run_id=remote.remote_run_id, remote_session_id=remote.session_id,
            agent_id=remote.entity_id, token_id=claims["jti"], tool_call_id=claims["tool_call_id"],
            call_digest=claims["call_digest"],
        )
