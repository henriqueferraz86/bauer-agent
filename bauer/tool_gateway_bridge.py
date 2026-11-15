"""Bridge a verified paused AgentOS read call to the governed Tool Gateway.

The caller owns the Kernel run and supplies the authenticated Telegram user.
No step in this bridge retries a submission or continuation.
"""
from __future__ import annotations

import secrets
import ipaddress
from typing import Any, Callable
from urllib.parse import urlsplit

import httpx

from .agentos_http_client import AgentOSHttpClient
from .tool_gateway_authorization import ToolGatewayDenied
from .tool_gateway_issuer import ToolGatewayIssuer
from .tool_gateway_protocol import call_digest, continuation_proof
from .http_shared import shared_ssl_context


def secure_service_url(base_url: str) -> bool:
    if not isinstance(base_url, str):
        return False
    try:
        parsed = urlsplit(base_url)
        hostname = parsed.hostname
        _ = parsed.port
    except ValueError:
        return False
    if not hostname or parsed.username is not None or parsed.password is not None or parsed.query or parsed.fragment:
        return False
    if parsed.scheme == "https":
        return True
    try:
        return parsed.scheme == "http" and ipaddress.ip_address(hostname).is_loopback
    except ValueError:
        return False


class ToolGatewayHTTPClient:
    """Call a local or TLS-protected gateway once, without redirects/retries."""

    def __init__(self, *, base_url: str, service_key: str):
        if (not secure_service_url(base_url)
                or not isinstance(service_key, str) or len(service_key.encode("utf-8")) < 32):
            raise ValueError("invalid Tool Gateway transport")
        self._client = httpx.Client(base_url=base_url.rstrip("/") + "/", follow_redirects=False,
                                    timeout=httpx.Timeout(30, connect=5), verify=shared_ssl_context(),
                                    headers={"X-Bauer-Service-Key": service_key}, trust_env=False)

    def close(self) -> None:
        self._client.close()

    def invoke(self, capability: str, action: str, args: dict[str, Any]) -> str:
        try:
            with self._client.stream("POST", "/bauer/tool-gateway/call",
                                     headers={"Authorization": f"Bearer {capability}"},
                                     json={"action": action, "args": args}) as response:
                if response.status_code != 200:
                    raise ToolGatewayDenied("tool request denied")
                chunks: list[bytes] = []
                size = 0
                for chunk in response.iter_bytes():
                    size += len(chunk)
                    if size > 160 * 1024:
                        raise ToolGatewayDenied("tool response too large")
                    chunks.append(chunk)
            import json

            value = json.loads(b"".join(chunks))
            result = value.get("result") if isinstance(value, dict) else None
            if not isinstance(result, str) or len(result.encode("utf-8")) > 64 * 1024:
                raise ToolGatewayDenied("tool response invalid")
            return result
        except (httpx.HTTPError, ValueError, UnicodeError) as exc:
            raise ToolGatewayDenied("tool request failed or uncertain") from exc


class ToolGatewayBridge:
    def __init__(self, *, client: AgentOSHttpClient, issuer: ToolGatewayIssuer,
                 invoke: Callable[[str, str, dict[str, Any]], str], continuation_key: str):
        if not isinstance(continuation_key, str) or len(continuation_key.encode("utf-8")) < 32:
            raise ValueError("invalid continuation key")
        self.client, self.issuer, self.invoke = client, issuer, invoke
        self._key = continuation_key

    def continue_paused(self, *, authenticated_user_id: str, gateway_run_id: str,
                        agent_id: str, remote_run_id: str, remote_session_id: str) -> Any:
        challenge = secrets.token_hex(32)
        attested = self.client.tool_requirement(agent_id=agent_id, run_id=remote_run_id,
                                                session_id=remote_session_id, challenge=challenge)
        if not isinstance(attested, dict):
            raise ToolGatewayDenied("tool request denied")
        statement, proof = attested.get("statement"), attested.get("proof")
        capability = self.issuer.issue_for_requirement(authenticated_user_id=authenticated_user_id,
            gateway_run_id=gateway_run_id, challenge=challenge, statement=statement, proof=proof)
        # Issuer verifies the signed statement and the local ownership mapping.
        if not isinstance(statement, dict) or statement.get("action") != "read_file":
            raise ToolGatewayDenied("tool request denied")
        digest = call_digest(statement["action"], statement["args"])
        result = self.invoke(capability, statement["action"], statement["args"])
        signature = continuation_proof(self._key, subject=statement["subject"], agent_id=agent_id,
            run_id=remote_run_id, session_id=remote_session_id,
            tool_call_id=statement["tool_call_id"], call_digest=digest, result=result)
        return self.client.tool_continue(agent_id=agent_id, run_id=remote_run_id,
                                         session_id=remote_session_id, tool_call_id=statement["tool_call_id"],
                                         call_digest=digest, result=result, proof=signature)


def build_telegram_tool_bridge(*, client: AgentOSHttpClient, kernel: Any, state_dir: Any,
                               remote_runs: Any) -> tuple[ToolGatewayBridge, ToolGatewayHTTPClient]:
    """Build from operator configuration, failing closed on missing context."""
    import os
    from pathlib import Path

    from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey
    from cryptography.hazmat.primitives.serialization import load_pem_private_key, load_pem_public_key

    from .channel_projects import ChannelProjectStore
    from .paths import get_bauer_home
    from .tool_gateway_authorization import ToolGatewayAuthorizer

    names = ("BAUER_TOOL_GATEWAY_PRIVATE_KEY_FILE", "BAUER_TOOL_GATEWAY_PUBLIC_KEY_FILE",
             "BAUER_AGENTOS_JWT_KEY", "BAUER_TOOL_GATEWAY_REQUIREMENT_KEY",
             "BAUER_TOOL_GATEWAY_CONTINUATION_KEY", "BAUER_TOOL_GATEWAY_SERVICE_KEY",
             "BAUER_TOOL_GATEWAY_ISSUER", "BAUER_TOOL_GATEWAY_AUDIENCE", "BAUER_TOOL_GATEWAY_URL")
    values = {name: os.environ.get(name, "").strip() for name in names}
    if (kernel is None or getattr(kernel, "runs", None) is None or not isinstance(state_dir, Path)
            or any(not value for value in values.values())):
        raise ValueError("missing Tool Gateway configuration")
    identity_key = values["BAUER_AGENTOS_JWT_KEY"]
    requirement_key = values["BAUER_TOOL_GATEWAY_REQUIREMENT_KEY"]
    continuation_key = values["BAUER_TOOL_GATEWAY_CONTINUATION_KEY"]
    service_key = values["BAUER_TOOL_GATEWAY_SERVICE_KEY"]
    if (any(len(key.encode("utf-8")) < 32 for key in (identity_key, requirement_key, continuation_key, service_key))
            or len({identity_key, requirement_key, continuation_key, service_key}) != 4):
        raise ValueError("Tool Gateway keys must be independent")
    private_pem = Path(values["BAUER_TOOL_GATEWAY_PRIVATE_KEY_FILE"]).read_text(encoding="utf-8")
    public_pem = Path(values["BAUER_TOOL_GATEWAY_PUBLIC_KEY_FILE"]).read_text(encoding="utf-8")
    private = load_pem_private_key(private_pem.encode(), password=None)
    public = load_pem_public_key(public_pem.encode())
    if (not isinstance(private, RSAPrivateKey) or private.key_size < 2048
            or private.public_key().public_numbers() != getattr(public, "public_numbers", lambda: None)()):
        raise ValueError("Tool Gateway key pair invalid")
    bindings_path = state_dir / "channel_projects.sqlite3"
    registry_path = get_bauer_home() / "projects.json"
    if not bindings_path.is_file() or not registry_path.is_file():
        raise ValueError("project stores unavailable")
    authorizer = ToolGatewayAuthorizer(public_key=public_pem, identity_key=identity_key,
        issuer=values["BAUER_TOOL_GATEWAY_ISSUER"], audience=values["BAUER_TOOL_GATEWAY_AUDIENCE"],
        runs=kernel.runs, remote_runs=remote_runs, bindings=ChannelProjectStore(bindings_path), registry_path=registry_path)
    issuer = ToolGatewayIssuer(private_key=private_pem, identity_key=identity_key,
        requirement_key=requirement_key, issuer=values["BAUER_TOOL_GATEWAY_ISSUER"],
        audience=values["BAUER_TOOL_GATEWAY_AUDIENCE"], runs=kernel.runs, remote_runs=remote_runs,
        authorizer=authorizer)
    http = ToolGatewayHTTPClient(base_url=values["BAUER_TOOL_GATEWAY_URL"], service_key=service_key)
    return ToolGatewayBridge(client=client, issuer=issuer, invoke=http.invoke, continuation_key=continuation_key), http
