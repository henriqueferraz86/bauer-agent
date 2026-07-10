"""Markdown weekly governance report built from the audit layers."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from .report import build_report
from .run_auditor import audit_run
from .score import score_run
from .skill_insights import build_skill_insights


def build_weekly_report(
    runtime_root: str | Path,
    *,
    since: datetime | None,
    window_label: str = "7d",
) -> str:
    from ..runtime.run_manager import RunManager

    report = build_report(runtime_root, since=since, window_label=window_label)
    insights = build_skill_insights(runtime_root, since=since, window_label=window_label)
    scored = []
    important = []
    for run in RunManager(root=runtime_root).list_runs():
        if since is not None and not _after(run.started_at, since):
            continue
        audit = audit_run(runtime_root, run.id)
        if audit is not None:
            score = score_run(audit)
            scored.append(score.score)
            if run.status != "completed" or score.score < 4:
                important.append((run.id, run.status, score.score, run.error or ""))

    average_score = round(sum(scored) / len(scored), 2) if scored else 0.0
    recommendations = _recommendations(report, insights, average_score)
    lines = [
        "# Bauer Weekly Audit Report",
        "",
        f"Generated: {datetime.now(UTC).isoformat()}",
        f"Window: {window_label}",
        "",
        "## Summary",
        "",
        f"- Runs: {report.runs_total}",
        f"- Success rate: {report.success_rate * 100:.1f}%",
        f"- Average run score: {average_score:.2f}/5",
        f"- Pending approvals: {report.approvals_pending}",
        f"- Estimated cost: ${report.estimated_cost_usd:.4f}",
        f"- Policy allow / ask / deny: {report.policy_allow} / {report.policy_ask} / {report.policy_deny}",
        "",
        "## Important Runs",
        "",
    ]
    lines.extend(_important_lines(important))
    lines.extend(["", "## Recurring Failures", ""])
    lines.extend(_pair_lines(report.top_errors, "No recurring failures."))
    lines.extend(["", "## Most Used Skills", ""])
    lines.extend(_metric_lines(insights.most_used, "No skill usage recorded."))
    lines.extend(["", "## Skill Candidates", ""])
    if insights.suggestions:
        for suggestion in insights.suggestions:
            tools = " -> ".join(suggestion.tools)
            lines.append(f"- `{suggestion.suggested_id}`: {tools} ({suggestion.occurrences} runs; human approval required)")
    else:
        lines.append("- No repeated tool sequence reached the suggestion threshold.")
    lines.extend(["", "## Risks", ""])
    risks = []
    if report.policy_deny:
        risks.append(f"{report.policy_deny} policy decisions were denied.")
    if report.approvals_pending:
        risks.append(f"{report.approvals_pending} approvals remain pending.")
    if report.runs_failed:
        risks.append(f"{report.runs_failed} runs failed in the period.")
    lines.extend([f"- {risk}" for risk in risks] or ["- No elevated governance risk detected from the recorded data."])
    lines.extend(["", "## Recommended Next Actions", ""])
    lines.extend(f"- {item}" for item in recommendations)
    return "\n".join(lines).rstrip() + "\n"


def _recommendations(report, insights, average_score: float) -> list[str]:
    items = []
    if report.approvals_pending:
        items.append("Review pending approvals and close obsolete requests.")
    if report.success_rate < 0.8 and report.runs_total:
        items.append("Investigate the most frequent run failures before the next release.")
    if average_score < 4 and report.runs_total:
        items.append("Improve validation and final summaries for low-score runs.")
    if insights.suggestions:
        items.append("Review suggested skills with a human owner; do not create them automatically.")
    if report.policy_deny:
        items.append("Review denied operations for missing permissions or unsafe workflows.")
    return items or ["Keep monitoring the same indicators in the next weekly review."]


def _important_lines(items) -> list[str]:
    if not items:
        return ["- No failed or low-score runs in the period."]
    return [f"- `{run_id}`: {status}, score {score}/5{f' - {error}' if error else ''}" for run_id, status, score, error in items[:10]]


def _pair_lines(items, empty: str) -> list[str]:
    return [f"- {name}: {count}" for name, count in items] or [f"- {empty}"]


def _metric_lines(items, empty: str) -> list[str]:
    return [f"- `{item.skill_id}`: {item.uses} uses, {item.failure_rate * 100:.1f}% failure" for item in items] or [f"- {empty}"]


def _after(timestamp: str, since: datetime) -> bool:
    from ._common import parse_iso

    parsed = parse_iso(timestamp)
    if parsed is None:
        return True
    if parsed.tzinfo is not None and since.tzinfo is None:
        parsed = parsed.replace(tzinfo=None)
    elif parsed.tzinfo is None and since.tzinfo is not None:
        since = since.replace(tzinfo=None)
    return parsed >= since
