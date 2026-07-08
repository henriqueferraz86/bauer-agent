"""Context-aware ToolRouter policy loading.

The built-in policy is the safe fallback. Workspace YAML can narrow or extend
contexts without editing ToolRouter code.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


TOOL_CONTEXTS = ("supervisor", "orchestrator", "chat", "worker")


@dataclass(frozen=True)
class ToolContextRule:
    mode: str = "allow_all"
    allow: frozenset[str] = field(default_factory=frozenset)
    deny: frozenset[str] = field(default_factory=frozenset)

    def allows(self, tool_name: str) -> bool:
        if self.mode == "allowlist":
            return tool_name in self.allow and tool_name not in self.deny
        return tool_name not in self.deny


@dataclass(frozen=True)
class ToolPolicy:
    contexts: dict[str, ToolContextRule]
    source: str = "builtin"

    def allows(self, context: str, tool_name: str) -> bool:
        rule = self.contexts.get(context) or self.contexts.get("supervisor") or ToolContextRule()
        return rule.allows(tool_name)

    def allowed_contexts(self, tool_name: str) -> list[str]:
        return [context for context in TOOL_CONTEXTS if self.allows(context, tool_name)]


def load_tool_policy(
    workspace: str | Path,
    *,
    explicit_path: str | Path | None = None,
    default_contexts: dict[str, ToolContextRule] | None = None,
) -> ToolPolicy:
    contexts = dict(default_contexts or default_tool_contexts())
    for candidate in _candidate_paths(workspace, explicit_path):
        if not candidate.exists():
            continue
        try:
            raw = yaml.safe_load(candidate.read_text(encoding="utf-8")) or {}
        except Exception:
            continue
        if not isinstance(raw, dict):
            continue
        loaded = _parse_contexts(raw.get("contexts", {}), contexts)
        return ToolPolicy(contexts=loaded, source=str(candidate.resolve()))
    return ToolPolicy(contexts=contexts, source="builtin")


def default_tool_contexts() -> dict[str, ToolContextRule]:
    return {
        "supervisor": ToolContextRule(mode="allow_all"),
        "orchestrator": ToolContextRule(mode="allow_all"),
        "chat": ToolContextRule(mode="allow_all", deny=frozenset({
            "kanban_heartbeat",
            "kanban_complete",
            "kanban_block",
        })),
        "worker": ToolContextRule(mode="allowlist", allow=frozenset({
            "list_dir",
            "read_file",
            "search_text",
            "glob_files",
            "regex_search",
            "diff_files",
            "calculate",
            "datetime_now",
            "json_query",
            "encode_decode",
            "todo",
            "skills_list",
            "skill_view",
            "memory",
            "session_search",
            "kanban_list",
            "kanban_show",
            "process",
            "write_file",
            "append_file",
            "patch",
            "create_dir",
            "move_file",
            "delete_file",
            "execute_code",
            "run_command",
            "clarify",
            "kanban_heartbeat",
            "kanban_comment",
            "kanban_complete",
            "kanban_block",
        })),
    }


def _candidate_paths(workspace: str | Path, explicit_path: str | Path | None) -> list[Path]:
    if explicit_path is not None:
        return [Path(explicit_path)]
    workspace_path = Path(workspace)
    return [
        workspace_path / ".bauer" / "tool_policy.yaml",
        Path("config") / "tool_policy.yaml",
        Path("tool_policy.yaml"),
    ]


def _parse_contexts(raw_contexts: Any, defaults: dict[str, ToolContextRule]) -> dict[str, ToolContextRule]:
    contexts = dict(defaults)
    if not isinstance(raw_contexts, dict):
        return contexts
    for raw_name, raw_rule in raw_contexts.items():
        name = str(raw_name).strip().lower()
        if name not in TOOL_CONTEXTS or not isinstance(raw_rule, dict):
            continue
        base = contexts.get(name, ToolContextRule())
        mode = str(raw_rule.get("mode", base.mode)).strip().lower() or base.mode
        if mode not in {"allow_all", "allowlist"}:
            mode = base.mode
        allow = _as_str_set(raw_rule.get("allow", base.allow))
        deny = _as_str_set(raw_rule.get("deny", base.deny))
        contexts[name] = ToolContextRule(mode=mode, allow=frozenset(allow), deny=frozenset(deny))
    return contexts


def _as_str_set(value: Any) -> set[str]:
    if value is None:
        return set()
    if isinstance(value, str):
        return {value.strip()} if value.strip() else set()
    if isinstance(value, (list, tuple, set, frozenset)):
        return {str(item).strip() for item in value if str(item).strip()}
    return set()
