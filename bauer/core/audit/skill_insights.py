"""Usage, failure and repetition insights derived from runtime events."""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path

from ._common import parse_iso
from .schemas import RepeatedToolSequence, SkillInsights, SkillMetric, SkillSuggestion


def build_skill_insights(
    runtime_root: str | Path,
    *,
    since: datetime | None = None,
    window_label: str = "all",
    top_n: int = 10,
    suggest_new: bool = True,
) -> SkillInsights:
    from ..events import EventBus

    events = [event for event in EventBus(root=runtime_root).list_events() if _in_window(event.timestamp, since)]
    uses: Counter[str] = Counter()
    failures: Counter[str] = Counter()
    durations: dict[str, list[float]] = defaultdict(list)
    selected_without_execution: Counter[str] = Counter()
    tools_by_run: dict[str, list[str]] = defaultdict(list)
    skills_with_execution = {
        event.skill_id for event in events
        if event.event_type == "skill.executed" and event.skill_id
    }
    skills_with_selection = {
        event.skill_id for event in events
        if event.event_type == "skill.selected" and event.skill_id
    }

    for event in events:
        if event.event_type == "skill.selected" and event.skill_id:
            if event.skill_id not in skills_with_execution:
                selected_without_execution[event.skill_id] += 1
        elif event.event_type == "skill.executed" and event.skill_id:
            uses[event.skill_id] += 1
            if (event.status or "").lower() == "failed":
                failures[event.skill_id] += 1
            elapsed = _duration(event.data)
            if elapsed is not None:
                durations[event.skill_id].append(elapsed)
        elif event.event_type in ("tool.call.completed", "tool.call.failed"):
            if event.run_id and event.tool_name:
                tools_by_run[event.run_id].append(event.tool_name)
            if (
                event.skill_id
                and event.skill_id not in skills_with_execution
                and event.skill_id not in skills_with_selection
            ):
                uses[event.skill_id] += 1
                if event.event_type == "tool.call.failed":
                    failures[event.skill_id] += 1
                elapsed = _duration(event.data)
                if elapsed is not None:
                    durations[event.skill_id].append(elapsed)

    uses.update({skill_id: count for skill_id, count in selected_without_execution.items() if count > 0})
    metrics = [
        SkillMetric(
            skill_id=skill_id,
            uses=count,
            failures=failures[skill_id],
            failure_rate=round(failures[skill_id] / count, 4) if count else 0.0,
            average_duration_ms=(round(sum(durations[skill_id]) / len(durations[skill_id]), 2)
                                 if durations[skill_id] else None),
        )
        for skill_id, count in uses.items()
    ]
    repeated = _repeated_sequences(tools_by_run)
    suggestions = [_suggest(sequence) for sequence in repeated] if suggest_new else []

    insight = SkillInsights(
        window=window_label,
        generated_at=datetime.now(UTC).isoformat(),
        most_used=sorted(metrics, key=lambda item: (-item.uses, item.skill_id))[:top_n],
        highest_failure_rate=sorted(
            [item for item in metrics if item.failures],
            key=lambda item: (-item.failure_rate, -item.failures, item.skill_id),
        )[:top_n],
        slowest=sorted(
            [item for item in metrics if item.average_duration_ms is not None],
            key=lambda item: (-(item.average_duration_ms or 0), item.skill_id),
        )[:top_n],
        never_used=_never_used(set(uses))[:top_n],
        repeated_sequences=repeated[:top_n],
        suggestions=suggestions[:top_n],
    )
    return insight


def _repeated_sequences(tools_by_run: dict[str, list[str]]) -> list[RepeatedToolSequence]:
    counts: Counter[tuple[str, ...]] = Counter()
    run_ids: dict[tuple[str, ...], set[str]] = defaultdict(set)
    for run_id, tools in tools_by_run.items():
        compact = [tool for index, tool in enumerate(tools) if index == 0 or tool != tools[index - 1]]
        found: set[tuple[str, ...]] = set()
        for size in range(min(4, len(compact)), 1, -1):
            for start in range(0, len(compact) - size + 1):
                found.add(tuple(compact[start:start + size]))
        for sequence in found:
            counts[sequence] += 1
            run_ids[sequence].add(run_id)
    repeated = [
        RepeatedToolSequence(list(sequence), count, sorted(run_ids[sequence]))
        for sequence, count in counts.items()
        if count >= 2
    ]
    return sorted(repeated, key=lambda item: (-item.occurrences, -len(item.tools), item.tools))


def _suggest(sequence: RepeatedToolSequence) -> SkillSuggestion:
    stem = "_then_".join(_slug(tool) for tool in sequence.tools[:3])
    return SkillSuggestion(
        suggested_id=f"suggested.{stem}",
        reason=f"A sequencia apareceu em {sequence.occurrences} runs e pode ser encapsulada.",
        tools=sequence.tools,
        occurrences=sequence.occurrences,
        requires_human_approval=True,
    )


def _never_used(used: set[str]) -> list[str]:
    try:
        from ..skills import SkillRegistry

        return sorted(manifest.id for manifest in SkillRegistry().list() if manifest.id not in used)
    except Exception:  # noqa: BLE001
        return []


def _duration(data: dict) -> float | None:
    for key in ("duration_ms", "elapsed_ms"):
        value = data.get(key)
        if isinstance(value, (int, float)) and value >= 0:
            return float(value)
    return None


def _in_window(timestamp: str, since: datetime | None) -> bool:
    if since is None:
        return True
    parsed = parse_iso(timestamp)
    if parsed is None:
        return True
    if parsed.tzinfo is not None and since.tzinfo is None:
        parsed = parsed.replace(tzinfo=None)
    elif parsed.tzinfo is None and since.tzinfo is not None:
        since = since.replace(tzinfo=None)
    return parsed >= since


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_") or "tool"
