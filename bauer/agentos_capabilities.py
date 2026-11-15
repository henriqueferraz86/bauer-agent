"""Challenge-bound AgentOS channel capability statements.

Possession of the service JWT key authenticates a statement to the gateway;
it does not provide transport confidentiality or prove tool governance.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import re
from typing import Any


def capability_proof(key: str, challenge: str, capabilities: dict[str, Any]) -> str:
    if not isinstance(key, str) or len(key.encode("utf-8")) < 32:
        raise ValueError("invalid capability key")
    if not isinstance(challenge, str) or not re.fullmatch(r"[0-9a-f]{64}", challenge):
        raise ValueError("invalid capability challenge")
    encoded = json.dumps(capabilities, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("ascii")
    message = b"bauer-agentos-capabilities-v1\0" + challenge.encode("ascii") + b"\0" + encoded
    return hmac.new(key.encode("utf-8"), message, hashlib.sha256).hexdigest()


def verify_capability_proof(key: str, challenge: str, capabilities: dict[str, Any], proof: str) -> bool:
    try:
        expected = capability_proof(key, challenge, capabilities)
        return isinstance(proof, str) and hmac.compare_digest(expected, proof)
    except (TypeError, ValueError):
        return False
