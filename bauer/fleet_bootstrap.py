"""Bootstrap seguro da configuração do Fleet Autopilot.

Este módulo só prepara configuração e diretórios. Não cria credenciais, não
promove tarefas e não inicia processos; o comando ``runtime fleet up`` chama o
supervisor explicitamente depois desta etapa.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from .paths import models_path, workspace_dir

DEFAULT_MISSION = (
    "Continuously inspect the discovered projects for bugs, improvements, "
    "maintenance, and quality issues. Propose small, reviewable changes "
    "through the governed dispatcher; never make destructive changes silently."
)
DEFAULT_MODEL = {"provider": "ollama", "name": "qwen2.5:7b"}


class FleetBootstrapError(ValueError):
    """Configuração existente não pode ser preparada com segurança."""


@dataclass(frozen=True)
class FleetBootstrapResult:
    config_path: Path
    models_path: Path
    root: Path
    created: bool
    changed: bool
    fields_added: tuple[str, ...]


def prepare_fleet_config(
    config: str | Path,
    *,
    root: str | Path | None = None,
    mission: str | None = None,
) -> FleetBootstrapResult:
    """Cria/atualiza somente defaults ausentes do config do Fleet.

    Um caminho explícito é respeitado. O comando passa o caminho canônico por
    padrão, portanto nunca há criação acidental de ``./config.yaml``.
    Valores já presentes, inclusive ``false``/vazio, não são substituídos;
    ``--mission`` é a única alteração deliberada solicitada pelo operador.
    """

    config_path = Path(config).expanduser().resolve()
    created = not config_path.exists()
    if created:
        data: dict[str, Any] = {}
    else:
        try:
            raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        except yaml.YAMLError as exc:
            raise FleetBootstrapError(f"YAML inválido em {config_path}: {exc}") from exc
        if raw is None:
            data = {}
        elif isinstance(raw, dict):
            data = raw
        else:
            raise FleetBootstrapError(
                f"Conteúdo de {config_path} precisa ser um mapeamento YAML no topo."
            )

    fields: list[str] = []

    if "model" not in data:
        data["model"] = dict(DEFAULT_MODEL)
        fields.append("model")

    autopilot = data.get("autopilot")
    if autopilot is None:
        autopilot = {}
        data["autopilot"] = autopilot
        fields.append("autopilot")
    elif not isinstance(autopilot, dict):
        raise FleetBootstrapError("a seção autopilot precisa ser um mapeamento YAML")

    _setdefault(autopilot, "enabled", True, "autopilot.enabled", fields)
    _setdefault(autopilot, "mission", DEFAULT_MISSION, "autopilot.mission", fields)
    _setdefault(autopilot, "approval_mode", "threshold", "autopilot.approval_mode", fields)
    if mission is not None:
        mission = mission.strip()
        if not mission:
            raise FleetBootstrapError("--mission não pode ser vazio")
        if autopilot.get("mission") != mission:
            autopilot["mission"] = mission
            fields.append("autopilot.mission")

    fleet = data.get("fleet")
    if fleet is None:
        fleet = {}
        data["fleet"] = fleet
        fields.append("fleet")
    elif not isinstance(fleet, dict):
        raise FleetBootstrapError("a seção fleet precisa ser um mapeamento YAML")

    _setdefault(fleet, "enabled", True, "fleet.enabled", fields)
    if "root" not in fleet:
        effective_root = _effective_root(root)
        fleet["root"] = str(effective_root)
        fields.append("fleet.root")
    effective_root = Path(str(fleet.get("root") or _effective_root(root))).expanduser().resolve()
    effective_root.mkdir(parents=True, exist_ok=True)

    changed = bool(fields)
    if changed:
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text(
            yaml.safe_dump(data, sort_keys=False, allow_unicode=True),
            encoding="utf-8",
        )

    return FleetBootstrapResult(
        config_path=config_path,
        models_path=models_path().resolve(),
        root=effective_root,
        created=created,
        changed=changed,
        fields_added=tuple(fields),
    )


def _effective_root(root: str | Path | None) -> Path:
    return Path(root).expanduser().resolve() if root is not None else workspace_dir().resolve()


def _setdefault(
    mapping: dict[str, Any], key: str, value: Any, label: str, fields: list[str]
) -> None:
    if key not in mapping:
        mapping[key] = value
        fields.append(label)


__all__ = [
    "DEFAULT_MISSION",
    "FleetBootstrapError",
    "FleetBootstrapResult",
    "prepare_fleet_config",
]
