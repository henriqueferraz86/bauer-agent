"""Canonical identity of a single tool call (no execution or authorization)."""
from __future__ import annotations

import hashlib
import hmac
import json
import re
from typing import Any


TOOL_CONTINUATION_VERSION = 2


def call_digest(action: str, args: dict[str, Any]) -> str:
    if not isinstance(action, str) or not action or not isinstance(args, dict):
        raise ValueError("invalid tool call")
    encoded = json.dumps({"action": action, "args": args}, sort_keys=True,
                         separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode("ascii")
    if len(encoded) > 16 * 1024:
        raise ValueError("tool call too large")
    return hashlib.sha256(encoded).hexdigest()


_REQUIREMENT_FIELDS = {"version", "subject", "entity_type", "entity_id", "remote_run_id",
                       "remote_session_id", "status", "tool_call_id", "action", "args"}


def requirement_proof(key: str, challenge: str, statement: dict[str, Any]) -> str:
    """Bind a server-derived paused requirement to a one-request challenge."""
    if not isinstance(key, str) or len(key.encode("utf-8")) < 32:
        raise ValueError("invalid requirement key")
    if not isinstance(challenge, str) or not re.fullmatch(r"[0-9a-f]{64}", challenge):
        raise ValueError("invalid requirement challenge")
    if not isinstance(statement, dict) or set(statement) != _REQUIREMENT_FIELDS:
        raise ValueError("invalid requirement statement")
    if type(statement["version"]) is not int or statement["version"] != 1 or statement["entity_type"] != "agent" or statement["status"] != "PAUSED":
        raise ValueError("unsupported requirement")
    for field in ("subject", "entity_id", "remote_run_id", "remote_session_id", "tool_call_id", "action"):
        value = statement[field]
        if not isinstance(value, str) or not value.strip() or value != value.strip() or len(value) > 256:
            raise ValueError("invalid requirement identity")
    call_digest(statement["action"], statement["args"])
    encoded = json.dumps(statement, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
                         allow_nan=False).encode("ascii")
    if len(encoded) > 16 * 1024:
        raise ValueError("requirement too large")
    message = b"bauer-tool-requirement-v1\0" + challenge.encode("ascii") + b"\0" + encoded
    return hmac.new(key.encode("utf-8"), message, hashlib.sha256).hexdigest()


def verify_requirement_proof(key: str, challenge: str, statement: dict[str, Any], proof: str) -> bool:
    try:
        return isinstance(proof, str) and hmac.compare_digest(requirement_proof(key, challenge, statement), proof)
    except (TypeError, ValueError):
        return False


def continuation_proof(key: str, *, subject: str, agent_id: str, run_id: str,
                       session_id: str, tool_call_id: str, call_digest: str, result: str) -> str:
    """Authenticate a result and the exact owner/run/call it belongs to."""
    if not isinstance(key, str) or len(key.encode("utf-8")) < 32:
        raise ValueError("invalid continuation key")
    identifiers = (subject, agent_id, run_id, session_id, tool_call_id)
    if any(not isinstance(v, str) or not v.strip() or v != v.strip() or len(v) > 256 for v in identifiers):
        raise ValueError("invalid continuation identity")
    if not isinstance(call_digest, str) or not re.fullmatch(r"[0-9a-f]{64}", call_digest):
        raise ValueError("invalid continuation call digest")
    if not isinstance(result, str) or len(result.encode("utf-8")) > 64 * 1024:
        raise ValueError("invalid continuation result")
    payload = json.dumps([*identifiers, call_digest, result], ensure_ascii=True, separators=(",", ":")).encode("ascii")
    domain = f"bauer-tool-result-v{TOOL_CONTINUATION_VERSION}\0".encode("ascii")
    return hmac.new(key.encode("utf-8"), domain + payload, hashlib.sha256).hexdigest()
