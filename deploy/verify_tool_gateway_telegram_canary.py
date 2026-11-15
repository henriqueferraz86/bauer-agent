"""Verify the live canary's HTTP boundary before starting its Telegram poller."""
from __future__ import annotations

import os

import httpx

from bauer.agentos_http_client import AgentOSHttpClient
from bauer.agentos_jwt_issuer import AgentOSJWTIssuer
from bauer.config_loader import load_config
from bauer.telegram_agentos_http import _verify_profile


def main() -> None:
    cfg = load_config("/app/config.yaml")
    settings = cfg.runtime.adapters["agentos_http"]
    if (cfg.runtime.telegram_default_adapter != "agentos_http" or not cfg.telegram.enabled
            or cfg.telegram.allow_all or len(cfg.telegram.allowed_users) != 1):
        raise SystemExit("canary Telegram config is not restricted to one operator")
    agent_id = str(settings["agent_id"])
    scopes = [f"agents:{agent_id}:run", f"agents:{agent_id}:read"]
    issuer = AgentOSJWTIssuer(
        key=os.environ.get("BAUER_AGENTOS_JWT_KEY", ""),
        issuer=str(settings["jwt_issuer"]),
        audience=str(settings["jwt_audience"]),
        allowed_scopes=scopes,
    )
    token = issuer.issue(authenticated_user_id=str(cfg.telegram.allowed_users[0]), scopes=scopes)
    with AgentOSHttpClient(settings["base_url"], token, trust_env=False) as client:
        _verify_profile(client, settings, token)
    with httpx.Client(base_url="http://127.0.0.1:8000", trust_env=False, timeout=5) as client:
        health = client.get("/health")
        health.raise_for_status()
        denied = client.post("/bauer/tool-gateway/call",
                             json={"action": "read_file", "args": {"path": "gateway-canary-marker.txt"}})
        if denied.status_code != 401:
            raise SystemExit("Tool Gateway did not reject an unauthenticated call")
    print("Canary boundary verified: signed AgentOS profile, isolated user, unauthenticated gateway denied.")


if __name__ == "__main__":
    main()
