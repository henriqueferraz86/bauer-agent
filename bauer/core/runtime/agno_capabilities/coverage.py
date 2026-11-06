"""Coverage matrix and upstream snapshot of the Agno catalog (plano 057, sprint 58).

* ``coverage_matrix()`` — every module under ``agno/tools`` of the installed
  SDK with its state and justification, grouped by category; reproducible
  (sorted, no timestamps) so two runs on the same lock produce the same JSON.
* ``snapshot_diff()`` — compares the installed SDK with the committed
  ``sdk_snapshot.json`` (toolkits, classes, functions, env vars). An Agno bump
  shows up here as a reviewable diff; nothing new is ever enabled by it — new
  toolkits are ``unreviewed`` and new functions are not in any manifest.

Read-only: nothing here imports a connector or writes outside an explicit
``write_snapshot`` call (development tool).
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from .catalog import catalog_entries
from .discovery import discover_sdk_modules

SNAPSHOT_PATH = Path(__file__).with_name("sdk_snapshot.json")
_NOT_A_TOOLKIT = "Módulo de infraestrutura do SDK (sem classe Toolkit): não é integração."


def agno_version() -> str:
    try:
        from importlib.metadata import version

        return version("agno")
    except Exception:  # noqa: BLE001 - metadata missing ⇒ unknown version
        return "não instalado"


def coverage_matrix() -> dict[str, Any]:
    rows_by_id = {row["id"]: row for row in catalog_entries()}
    rows: list[dict[str, Any]] = []
    for module in discover_sdk_modules():
        row = rows_by_id.get(module.id)
        if not module.is_toolkit or row is None:
            rows.append({
                "id": module.id, "category": "SDK interno", "state": "not_a_toolkit",
                "justification": _NOT_A_TOOLKIT, "actions": [], "requirements": [],
            })
            continue
        if row["state"] == "unreviewed":
            justification = row["reason"]
        elif row["state"] in {"incompatible", "blocked_by_policy", "dependency_missing", "needs_configuration"}:
            justification = row["reason"] or ", ".join(row.get("missing") or [])
        else:
            justification = f"Ações revisadas: {', '.join(row['actions'])}"
        rows.append({
            "id": module.id,
            "category": row["category"],
            "state": row["state"],
            "justification": justification or "—",
            "actions": list(row["actions"]),
            "requirements": sorted(set(row.get("required_config") or []) | ({row["package"]} if row.get("package") else set())),
        })
    # Bauer-native adapters that fill SDK gaps are part of the coverage too.
    sdk_ids = {row["id"] for row in rows}
    for row in rows_by_id.values():
        if row["id"] not in sdk_ids and row["source"] == "bauer":
            rows.append({
                "id": row["id"], "category": row["category"], "state": row["state"],
                "justification": f"Adapter Bauer: {', '.join(row['actions'])}",
                "actions": list(row["actions"]),
                "requirements": list(row.get("required_config") or []),
            })
    rows.sort(key=lambda item: item["id"])
    totals = Counter(row["state"] for row in rows)
    for state in ("unreviewed",):
        totals.setdefault(state, 0)
    by_category: dict[str, dict[str, int]] = defaultdict(dict)
    for row in rows:
        bucket = by_category[row["category"]]
        bucket[row["state"]] = bucket.get(row["state"], 0) + 1
    return {
        "agno_version": agno_version(),
        "totals": dict(sorted(totals.items())),
        "by_category": {key: dict(sorted(value.items())) for key, value in sorted(by_category.items())},
        "rows": rows,
    }


def coverage_markdown(matrix: dict[str, Any] | None = None) -> str:
    matrix = matrix or coverage_matrix()
    lines = [
        f"# Cobertura do catálogo Agno {matrix['agno_version']}",
        "",
        "| estado | total |",
        "|---|---|",
        *[f"| {state} | {count} |" for state, count in matrix["totals"].items()],
        "",
        "| integração | categoria | estado | justificativa |",
        "|---|---|---|---|",
    ]
    for row in matrix["rows"]:
        reason = str(row["justification"]).replace("|", "/")
        lines.append(f"| {row['id']} | {row['category']} | {row['state']} | {reason} |")
    return "\n".join(lines) + "\n"


def current_snapshot() -> dict[str, Any]:
    return {
        "agno_version": agno_version(),
        "modules": {
            module.id: {
                "classes": list(module.toolkit_classes),
                "functions": list(module.functions),
                "env_vars": list(module.env_vars),
                "optional_imports": list(module.optional_imports),
            }
            for module in discover_sdk_modules()
        },
    }


def load_snapshot(path: Path | None = None) -> dict[str, Any]:
    target = path or SNAPSHOT_PATH
    try:
        return json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"agno_version": None, "modules": {}}


def snapshot_diff(path: Path | None = None) -> dict[str, list[Any]]:
    """Added/removed toolkits and changed functions/env/imports vs. the snapshot."""
    old = load_snapshot(path).get("modules", {})
    new = current_snapshot()["modules"]
    changed = []
    for module_id in sorted(set(old) & set(new)):
        before, after = old[module_id], new[module_id]
        delta = {
            "id": module_id,
            "functions_added": sorted(set(after["functions"]) - set(before.get("functions", []))),
            "functions_removed": sorted(set(before.get("functions", [])) - set(after["functions"])),
            "env_vars_added": sorted(set(after["env_vars"]) - set(before.get("env_vars", []))),
            "classes_changed": sorted(set(after["classes"]) ^ set(before.get("classes", []))),
        }
        if any(value for key, value in delta.items() if key != "id"):
            changed.append(delta)
    return {
        "added": sorted(set(new) - set(old)),
        "removed": sorted(set(old) - set(new)),
        "changed": changed,
    }


def write_snapshot(path: Path | None = None) -> Path:
    """Development tool: record the reviewed SDK catalog (after reviewing the diff)."""
    target = path or SNAPSHOT_PATH
    target.write_text(json.dumps(current_snapshot(), indent=1, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
    return target
