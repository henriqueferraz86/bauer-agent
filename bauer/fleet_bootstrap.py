"""First-run defaults and canonical paths for the Fleet Autopilot."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from .config_loader import ConfigError, load_config
from .paths import config_path, get_bauer_home

DEFAULT_AUTOPILOT_MISSION = (
    "Manter todos os projetos deste workspace saudáveis: procurar bugs, "
    "falhas de testes, problemas de segurança, melhorias de qualidade e "
    "tarefas de manutenção. Priorizar uma tarefa por vez, validar as "
    "alterações com testes e registrar bloqueios."
)


def fleet_config_path(requested: str | Path = "config.yaml") -> Path:
    """Resolve o config do Fleet sem depender do diretório atual."""

    requested_path = Path(requested)
    if requested_path == Path("config.yaml"):
        return config_path().resolve()
    return requested_path.expanduser().resolve()


def fleet_models_path(requested: str | Path = "models.yaml") -> Path:
    """Resolve o catálogo customizado de modelos do usuário."""

    requested_path = Path(requested)
    if requested_path == Path("models.yaml"):
        return (get_bauer_home() / "models.yaml").resolve()
    return requested_path.expanduser().resolve()


def ensure_fleet_defaults(
    requested_config: str | Path = "config.yaml",
    *,
    mission: str | None = None,
) -> tuple[Path, list[str]]:
    """Prepara defaults do Fleet sem apagar escolhas existentes."""

    path = fleet_config_path(requested_config)
    if not path.exists():
        raise ConfigError(
            f"Configuração canônica não encontrada: {path}. "
            "Execute 'bauer setup' uma vez para criar o ambiente inicial."
        )
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ConfigError(f"YAML inválido em {path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise ConfigError(f"Conteúdo de {path} precisa ser um mapeamento YAML no topo.")

    changed: list[str] = []
    autopilot = raw.setdefault("autopilot", {})
    fleet = raw.setdefault("fleet", {})
    if not isinstance(autopilot, dict):
        raise ConfigError("A seção autopilot do config precisa ser um mapeamento.")
    if not isinstance(fleet, dict):
        raise ConfigError("A seção fleet do config precisa ser um mapeamento.")

    if "enabled" not in autopilot:
        autopilot["enabled"] = True
        changed.append("autopilot.enabled")
    current_mission = str(autopilot.get("mission") or "").strip()
    desired_mission = mission.strip() if mission is not None else DEFAULT_AUTOPILOT_MISSION
    if mission is not None or not current_mission:
        if current_mission != desired_mission:
            autopilot["mission"] = desired_mission
            changed.append("autopilot.mission")
    if "approval_mode" not in autopilot:
        autopilot["approval_mode"] = "threshold"
        changed.append("autopilot.approval_mode")
    if "enabled" not in fleet:
        fleet["enabled"] = True
        changed.append("fleet.enabled")

    if changed:
        path.write_text(
            yaml.safe_dump(raw, allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )
    load_config(path)
    return path, changed


def summarize_fleet_blockers(status: dict[str, Any]) -> list[str]:
    """Return concise, user-facing blocker messages from a fleet status."""

    blockers: list[str] = []
    for project in status.get("projects", []):
        if not isinstance(project, dict):
            continue
        autopilot = project.get("autopilot") or {}
        if autopilot.get("state") == "blocked":
            name = Path(str(project.get("path", "projeto"))).name
            reason = str(autopilot.get("reason") or "sem motivo informado")
            blockers.append(f"{name}: {reason}")
    return blockers
