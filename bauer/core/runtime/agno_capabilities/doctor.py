"""``bauer doctor`` summary for Agno capabilities — read-only, no repair."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Iterable

from .catalog import STATES, catalog_entries, validate_agent_tools
from .settings import CapabilitySettings, CapabilitySettingsError, load_capability_settings, settings_path


@dataclass(slots=True)
class CapabilityDoctorReport:
    counts: dict[str, int]
    settings_file: str
    settings_present: bool
    settings_error: str = ""
    agent_problems: dict[str, list[str]] = field(default_factory=dict)
    actionable: list[str] = field(default_factory=list)


def _agents() -> Iterable[tuple[str, list[str]]]:
    try:
        from ..agent_registry import RuntimeAgentRegistry

        for spec in RuntimeAgentRegistry().list():
            yield spec.id, list(spec.tools)
    except Exception:  # noqa: BLE001 - doctor is best-effort
        return


def build_report(agents: Iterable[tuple[str, list[str]]] | None = None) -> CapabilityDoctorReport:
    path = settings_path()
    settings_error = ""
    try:
        settings = load_capability_settings()
    except CapabilitySettingsError as exc:
        settings_error = str(exc)
        settings = CapabilitySettings()
    rows = catalog_entries(settings=settings)
    counts = Counter(row["state"] for row in rows)
    report = CapabilityDoctorReport(
        counts={state: counts.get(state, 0) for state in STATES},
        settings_file=str(path),
        settings_present=path.is_file(),
        settings_error=settings_error,
    )
    for agent_id, tools in agents if agents is not None else _agents():
        problems = validate_agent_tools(tools, settings=settings)
        if problems:
            report.agent_problems[agent_id] = problems
    for row in rows:
        if row["review"] == "reviewed" and row["state"] in {"needs_configuration", "dependency_missing"}:
            missing = ", ".join(row.get("missing") or []) or row.get("reason") or ""
            report.actionable.append(f"{row['id']}: {row['state']} — {missing}")
    return report
