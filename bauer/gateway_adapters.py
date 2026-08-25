"""Outbound gateway adapters for durable outbox delivery.

The outbox owns persistence and retry. This module owns platform-specific
payload mapping and HTTP/file delivery.
"""

from __future__ import annotations

import json
import logging
import os
import urllib.request
from pathlib import Path
from typing import Any

from .url_safety import UrlSafetyConfig, is_safe_url


SUPPORTED_GATEWAY_CHANNELS = frozenset({"file", "webhook", "telegram", "discord", "slack", "whatsapp"})
logger = logging.getLogger(__name__)


class GatewayDeliveryAdapter:
    """Deliver sanitized outbox payloads to supported outbound platforms.

    User-configured webhook destinations reject private and internal addresses
    by default.  Trusted deployments that intentionally use an internal
    receiver can opt in with ``allow_internal_webhooks=True`` or
    ``gateway.allow_internal_webhooks: true`` in the Bauer config.
    """

    def __init__(
        self,
        workspace: str | Path = "workspace",
        *,
        allow_internal_webhooks: bool | None = None,
    ):
        self.workspace = Path(workspace).resolve()
        self._allow_internal_webhooks = (
            _allow_internal_webhooks_from_config()
            if allow_internal_webhooks is None
            else bool(allow_internal_webhooks)
        )

    def deliver(
        self,
        *,
        channel: str,
        target: str,
        payload: dict[str, Any],
        metadata: dict[str, Any] | None = None,
        message_id: str = "",
    ) -> None:
        channel = channel.strip().lower()
        metadata = dict(metadata or {})
        if channel == "file":
            self._deliver_file(target, payload, message_id=message_id)
            return
        if channel == "webhook":
            self._deliver_webhook(target, payload, metadata=metadata)
            return
        if channel == "telegram":
            self._deliver_telegram(target, payload, metadata=metadata)
            return
        if channel == "discord":
            self._deliver_discord(target, payload, metadata=metadata)
            return
        if channel == "slack":
            self._deliver_slack(target, payload, metadata=metadata)
            return
        if channel == "whatsapp":
            self._deliver_whatsapp(target, payload, metadata=metadata)
            return
        raise ValueError(f"unsupported gateway channel: {channel}")

    def _deliver_file(self, target: str, payload: dict[str, Any], *, message_id: str) -> None:
        path = self._target_path(target)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({"message_id": message_id, "payload": payload}, ensure_ascii=False) + "\n")

    def _deliver_webhook(self, target: str, payload: dict[str, Any], *, metadata: dict[str, Any]) -> None:
        headers = _metadata_headers(metadata)
        self._post_webhook(target, payload, headers=headers, timeout=_metadata_timeout(metadata))

    def _deliver_telegram(self, target: str, payload: dict[str, Any], *, metadata: dict[str, Any]) -> None:
        token = _required_env(str(metadata.get("token_env") or "TELEGRAM_BOT_TOKEN"))
        body: dict[str, Any] = {
            "chat_id": target,
            "text": _payload_text(payload),
        }
        parse_mode = str(metadata.get("parse_mode") or "").strip()
        if parse_mode:
            body["parse_mode"] = parse_mode
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        _post_json(url, body, timeout=_metadata_timeout(metadata))

    def _deliver_discord(self, target: str, payload: dict[str, Any], *, metadata: dict[str, Any]) -> None:
        body: dict[str, Any] = {"content": _payload_text(payload)}
        username = str(metadata.get("username") or "").strip()
        if username:
            body["username"] = username
        self._post_webhook(target, body, timeout=_metadata_timeout(metadata))

    def _deliver_slack(self, target: str, payload: dict[str, Any], *, metadata: dict[str, Any]) -> None:
        body: dict[str, Any] = {"text": _payload_text(payload)}
        self._post_webhook(target, body, timeout=_metadata_timeout(metadata))

    def _deliver_whatsapp(self, target: str, payload: dict[str, Any], *, metadata: dict[str, Any]) -> None:
        token = _required_env(str(metadata.get("token_env") or "WHATSAPP_ACCESS_TOKEN"))
        phone_number_id = str(metadata.get("phone_number_id") or "").strip()
        recipient = target.strip()
        if "/" in recipient and not phone_number_id:
            phone_number_id, recipient = recipient.split("/", 1)
        if not phone_number_id:
            raise ValueError("whatsapp delivery requires metadata.phone_number_id or target '<phone_number_id>/<to>'")
        if not recipient:
            raise ValueError("whatsapp recipient is required")
        api_base = str(metadata.get("api_base") or "https://graph.facebook.com").rstrip("/")
        graph_version = str(metadata.get("graph_version") or os.environ.get("WHATSAPP_GRAPH_VERSION") or "v20.0")
        url = f"{api_base}/{graph_version}/{phone_number_id}/messages"
        body = {
            "messaging_product": "whatsapp",
            "to": recipient,
            "type": "text",
            "text": {"body": _payload_text(payload)},
        }
        self._post_webhook(
            url,
            body,
            headers={"Authorization": f"Bearer {token}"},
            timeout=_metadata_timeout(metadata),
        )

    def _target_path(self, target: str) -> Path:
        raw = target[7:] if target.startswith("file://") else target
        path = Path(raw)
        if not path.is_absolute():
            path = self.workspace / path
        return path.resolve()

    def _post_webhook(
        self,
        url: str,
        payload: dict[str, Any],
        *,
        headers: dict[str, str] | None = None,
        timeout: int = 15,
    ) -> None:
        _validate_webhook_url(url, allow_internal=self._allow_internal_webhooks)
        _post_json(url, payload, headers=headers, timeout=timeout)


def _post_json(
    url: str,
    payload: dict[str, Any],
    *,
    headers: dict[str, str] | None = None,
    timeout: int = 15,
) -> None:
    if not url.strip():
        raise ValueError("gateway target URL is required")
    request_headers = {"Content-Type": "application/json"}
    request_headers.update(headers or {})
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=request_headers, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        status = int(getattr(resp, "status", getattr(resp, "code", 200)) or 200)
        if status >= 400:
            raise RuntimeError(f"gateway returned HTTP {status}")


def _validate_webhook_url(url: str, *, allow_internal: bool) -> None:
    """Validate a user-controlled webhook destination before opening it."""
    config = UrlSafetyConfig(block_private_ips=not allow_internal)
    is_safe_url(url, config=config)


def _allow_internal_webhooks_from_config() -> bool:
    """Read the explicit opt-in, failing closed if config cannot be loaded."""
    try:
        from .config_loader import load_config

        return bool(load_config().gateway.allow_internal_webhooks)
    except Exception as exc:
        logger.debug("gateway webhook policy unavailable; denying internal URLs: %s", exc)
        return False


def _payload_text(payload: dict[str, Any]) -> str:
    for key in ("text", "message", "content", "prompt"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    if payload.get("type") == "automation.queued":
        job_name = str(payload.get("job_name") or payload.get("job_id") or "automation").strip()
        task_id = str(payload.get("task_id") or "").strip()
        due_at = str(payload.get("due_at") or "").strip()
        parts = [f"Automation queued: {job_name}"]
        if task_id:
            parts.append(f"task={task_id}")
        if due_at:
            parts.append(f"due_at={due_at}")
        return " | ".join(parts)
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def _metadata_headers(metadata: dict[str, Any]) -> dict[str, str]:
    raw = metadata.get("headers")
    if not isinstance(raw, dict):
        return {}
    headers: dict[str, str] = {}
    for key, value in raw.items():
        key_s = str(key).strip()
        value_s = str(value).strip()
        if key_s and value_s:
            headers[key_s] = value_s
    return headers


def _metadata_timeout(metadata: dict[str, Any]) -> int:
    try:
        parsed = int(str(metadata.get("timeout") or "15").strip())
        return max(1, min(parsed, 120))
    except ValueError:
        return 15


def _required_env(name: str) -> str:
    name = name.strip()
    if not name:
        raise ValueError("token env name is required")
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"missing env var: {name}")
    return value
