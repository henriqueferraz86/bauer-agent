"""Operator settings for Agno capabilities (``agno_capabilities.yaml``).

Kept in its own file under ``$BAUER_HOME`` on purpose:

* ``config.yaml`` sections are strict and shared by every Bauer subsystem; a
  new capability field there would break boot on machines whose Bauer code
  is older than their config (see AGENTS.md "Seções de config são estritas").
* Absence of the file means "nothing configured": every capability that needs
  scope/connection settings reports ``needs_configuration`` and the rest of
  Bauer behaves exactly as before. Install/update never creates or rewrites
  it — the loader only reads.

Values here are *scope* (repositories, connections, regions, domains). Secrets
are never stored here: settings reference credential *names* resolved through
``bauer credential``/environment at call time.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, ValidationError

from ....config_loader import ConfigError

SETTINGS_ENV = "BAUER_AGNO_CAPABILITIES"
SETTINGS_FILENAME = "agno_capabilities.yaml"


class CapabilitySettingsError(ConfigError):
    """Invalid ``agno_capabilities.yaml`` (fail-closed: nothing is enabled)."""


class StrictSettings(BaseModel):
    """Base for per-capability settings: unknown keys are rejected."""

    model_config = ConfigDict(extra="forbid", frozen=True)


_SETTINGS_MODELS: dict[str, type[StrictSettings]] = {}


def register_settings_model(capability_id: str, model: type[StrictSettings]) -> None:
    if capability_id in _SETTINGS_MODELS and _SETTINGS_MODELS[capability_id] is not model:
        raise ValueError(f"settings model already registered for {capability_id}")
    _SETTINGS_MODELS[capability_id] = model


def settings_model_for(capability_id: str) -> type[StrictSettings] | None:
    from .manifest import reviewed_policies

    reviewed_policies()  # make sure family modules registered their models
    return _SETTINGS_MODELS.get(capability_id)


class CapabilitySettings:
    """Validated, read-only view of the operator settings."""

    def __init__(self, sections: dict[str, StrictSettings] | None = None, *, source: str | None = None) -> None:
        self._sections = dict(sections or {})
        self.source = source

    def section(self, capability_id: str) -> StrictSettings | None:
        return self._sections.get(capability_id)

    def configured_ids(self) -> list[str]:
        return sorted(self._sections)

    def __contains__(self, capability_id: object) -> bool:
        return capability_id in self._sections


def settings_path() -> Path:
    explicit = os.environ.get(SETTINGS_ENV, "").strip()
    if explicit:
        return Path(explicit).expanduser()
    from ....paths import get_bauer_home

    return get_bauer_home() / SETTINGS_FILENAME


def parse_settings(raw: Any, *, source: str | None = None) -> CapabilitySettings:
    if raw is None:
        return CapabilitySettings(source=source)
    if not isinstance(raw, dict):
        raise CapabilitySettingsError("agno_capabilities: o documento deve ser um mapeamento YAML")
    unknown_top = set(raw) - {"version", "capabilities"}
    if unknown_top:
        raise CapabilitySettingsError(
            f"agno_capabilities: chaves desconhecidas no topo: {', '.join(sorted(map(str, unknown_top)))}"
        )
    version = raw.get("version", 1)
    if version != 1:
        raise CapabilitySettingsError(f"agno_capabilities: versão não suportada: {version!r}")
    capabilities = raw.get("capabilities") or {}
    if not isinstance(capabilities, dict):
        raise CapabilitySettingsError("agno_capabilities.capabilities deve ser um mapeamento")
    sections: dict[str, StrictSettings] = {}
    for capability_id, payload in capabilities.items():
        key = str(capability_id).strip()
        model = settings_model_for(key)
        if model is None:
            raise CapabilitySettingsError(
                f"agno_capabilities: '{key}' não é uma capability configurável conhecida"
            )
        try:
            sections[key] = model.model_validate(payload or {})
        except ValidationError as exc:
            # Pydantic messages may echo the offending *value*; report only
            # field locations so a misplaced secret never reaches logs/UI.
            fields = sorted({".".join(str(part) for part in err.get("loc", ())) or "<raiz>" for err in exc.errors()})
            raise CapabilitySettingsError(
                f"agno_capabilities: configuração inválida para '{key}' (campos: {', '.join(fields)})"
            ) from None
    return CapabilitySettings(sections, source=source)


def load_capability_settings(path: str | Path | None = None) -> CapabilitySettings:
    """Read and validate the settings file. Never writes or migrates it."""
    target = Path(path) if path is not None else settings_path()
    if not target.is_file():
        return CapabilitySettings(source=None)
    try:
        raw = yaml.safe_load(target.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise CapabilitySettingsError(
            f"agno_capabilities: não foi possível ler {target.name}: {type(exc).__name__}"
        ) from None
    return parse_settings(raw, source=str(target))
