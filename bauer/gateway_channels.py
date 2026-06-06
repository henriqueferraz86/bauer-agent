"""Durable registry for outbound gateway channels."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .gateway_adapters import SUPPORTED_GATEWAY_CHANNELS
from .secret_policy import sanitize_mapping, sanitize_text


@dataclass(frozen=True)
class GatewayChannelConfig:
    name: str
    platform: str
    target: str
    enabled: bool = True
    created_at: str = ""
    updated_at: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "platform": self.platform,
            "target": self.target,
            "enabled": self.enabled,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "metadata": sanitize_mapping(self.metadata),
        }


@dataclass(frozen=True)
class ResolvedGatewayTarget:
    channel: str
    target: str
    metadata: dict[str, Any] = field(default_factory=dict)
    source: str = "direct"


class GatewayChannelRegistry:
    """JSON-backed channel registry scoped to one workspace."""

    def __init__(self, workspace: str | Path = "workspace"):
        self.workspace = Path(workspace).resolve()
        self.store_dir = self.workspace / ".bauer_gateway"
        self.path = self.store_dir / "channels.json"

    def list_channels(self, *, include_disabled: bool = False) -> list[GatewayChannelConfig]:
        channels = list(self._read().values())
        if not include_disabled:
            channels = [channel for channel in channels if channel.enabled]
        return sorted(channels, key=lambda channel: channel.name)

    def get(self, name: str) -> GatewayChannelConfig | None:
        return self._read().get(_clean_name(name))

    def upsert(
        self,
        *,
        name: str,
        platform: str,
        target: str,
        enabled: bool = True,
        metadata: dict[str, Any] | None = None,
    ) -> GatewayChannelConfig:
        name = _clean_name(name)
        platform = platform.strip().lower()
        target = target.strip()
        metadata = dict(metadata or {})
        if not name:
            raise ValueError("gateway channel name is required")
        if platform not in SUPPORTED_GATEWAY_CHANNELS:
            allowed = ", ".join(sorted(SUPPORTED_GATEWAY_CHANNELS))
            raise ValueError(f"gateway platform must be one of: {allowed}")
        if not target:
            raise ValueError("gateway target is required")
        validate_gateway_metadata(metadata)
        now = _now_iso()
        channels = self._read()
        current = channels.get(name)
        config = GatewayChannelConfig(
            name=name,
            platform=platform,
            target=target,
            enabled=bool(enabled),
            created_at=current.created_at if current else now,
            updated_at=now,
            metadata=metadata,
        )
        channels[name] = config
        self._write(channels)
        return config

    def delete(self, name: str) -> bool:
        name = _clean_name(name)
        channels = self._read()
        if name not in channels:
            return False
        channels.pop(name)
        self._write(channels)
        return True

    def _read(self) -> dict[str, GatewayChannelConfig]:
        if not self.path.exists():
            return {}
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        if not isinstance(raw, dict):
            return {}
        raw_channels = raw.get("channels", [])
        if not isinstance(raw_channels, list):
            return {}
        channels: dict[str, GatewayChannelConfig] = {}
        for item in raw_channels:
            if not isinstance(item, dict):
                continue
            config = GatewayChannelConfig(
                name=_clean_name(str(item.get("name") or "")),
                platform=str(item.get("platform") or "").strip().lower(),
                target=str(item.get("target") or "").strip(),
                enabled=bool(item.get("enabled", True)),
                created_at=str(item.get("created_at") or ""),
                updated_at=str(item.get("updated_at") or ""),
                metadata=item.get("metadata") if isinstance(item.get("metadata"), dict) else {},
            )
            if config.name and config.platform and config.target:
                channels[config.name] = config
        return channels

    def _write(self, channels: dict[str, GatewayChannelConfig]) -> None:
        self.store_dir.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": 1,
            "channels": [_channel_to_storage_dict(channel) for channel in sorted(channels.values(), key=lambda item: item.name)],
        }
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
        tmp.replace(self.path)


def resolve_delivery_spec(delivery: str, registry: GatewayChannelRegistry) -> ResolvedGatewayTarget:
    prefix, sep, target = str(delivery or "").strip().partition(":")
    prefix = prefix.strip().lower()
    target = target.strip()
    if not sep or not prefix or not target:
        raise ValueError(_delivery_help())
    if prefix in {"channel", "gateway"}:
        config = registry.get(target)
        if config is None:
            raise ValueError(f"gateway channel not found: {target}")
        if not config.enabled:
            raise ValueError(f"gateway channel is disabled: {target}")
        metadata = {"gateway_channel": config.name}
        metadata.update(config.metadata)
        return ResolvedGatewayTarget(config.platform, config.target, metadata=metadata, source=f"channel:{config.name}")
    if prefix in SUPPORTED_GATEWAY_CHANNELS:
        if prefix == "file" and target.startswith("//"):
            target = "file:" + target
        return ResolvedGatewayTarget(prefix, target, metadata={}, source="direct")
    raise ValueError(_delivery_help())


def _delivery_help() -> str:
    allowed = ", ".join(sorted(SUPPORTED_GATEWAY_CHANNELS))
    return f"delivery must be channel:<name> or one of {allowed}:<target>"


def validate_gateway_metadata(metadata: dict[str, Any]) -> None:
    _reject_secret_values(metadata)


def _channel_to_storage_dict(channel: GatewayChannelConfig) -> dict[str, Any]:
    return {
        "name": channel.name,
        "platform": channel.platform,
        "target": channel.target,
        "enabled": channel.enabled,
        "created_at": channel.created_at,
        "updated_at": channel.updated_at,
        "metadata": channel.metadata,
    }


def _clean_name(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch in {"-", "_", "."} else "-" for ch in value.strip().lower()).strip("-")


def _reject_secret_values(metadata: dict[str, Any], *, path: str = "metadata") -> None:
    for key, value in metadata.items():
        key_l = str(key).lower()
        if any(marker in key_l for marker in ("token", "secret", "password", "api_key", "authorization")) and not key_l.endswith("_env"):
            raise ValueError(f"gateway metadata must not store secrets at {path}.{key}; use *_env keys instead")
        if isinstance(value, dict):
            _reject_secret_values(value, path=f"{path}.{key}")
        elif isinstance(value, list):
            for index, item in enumerate(value):
                if isinstance(item, dict):
                    _reject_secret_values(item, path=f"{path}.{key}[{index}]")
                elif isinstance(item, str) and sanitize_text(item) != item:
                    raise ValueError(f"gateway metadata must not store secret-like values at {path}.{key}[{index}]")
        elif isinstance(value, str) and sanitize_text(value) != value:
            raise ValueError(f"gateway metadata must not store secret-like values at {path}.{key}")


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()
