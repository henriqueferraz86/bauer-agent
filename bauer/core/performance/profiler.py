"""Baseline de performance — lê runs + eventos de tool e mede duração.

Read-only sobre dados já persistidos (RunManager + EventBus). A duração por tool
vem do `data.duration_ms` dos eventos tool.call.completed/failed (o tool_router
já emite). Nada é instrumentado aqui — só agregado."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from pathlib import Path

from ..audit._common import duration_ms, parse_iso
from .schemas import PerfReport, RunPerf, ToolTiming

_TERMINAL = {"completed", "failed", "cancelled"}
_TOOL_DONE = {"tool.call.completed", "tool.call.failed"}


def _percentile(values: list[float], pct: float) -> float | None:
    if not values:
        return None
    s = sorted(values)
    k = max(0, min(len(s) - 1, int(round((pct / 100.0) * (len(s) - 1)))))
    return round(s[k], 2)


def _event_duration_ms(event) -> float | None:
    try:
        val = (event.data or {}).get("duration_ms")
        return float(val) if isinstance(val, (int, float)) else None
    except Exception:  # noqa: BLE001
        return None


def run_perf(runtime_root: str | Path, run_id: str) -> RunPerf | None:
    """RunPerf de `run_id`, ou None se a run não existe."""
    from ..events import EventBus
    from ..runtime.run_manager import RunManager

    run = RunManager(root=runtime_root).get_run(run_id)
    if run is None:
        return None

    wall = duration_ms(run.started_at, getattr(run, "finished_at", None), getattr(run, "updated_at", None))
    perf = RunPerf(run_id=run.id, status=run.status, wall_ms=wall)

    per_tool: dict[str, ToolTiming] = {}
    for ev in EventBus(root=runtime_root).list_events(run_id=run_id):
        if ev.event_type not in _TOOL_DONE or not ev.tool_name:
            continue
        perf.tool_calls += 1
        d = _event_duration_ms(ev)
        t = per_tool.setdefault(ev.tool_name, ToolTiming(tool=ev.tool_name))
        t.calls += 1
        if d is not None:
            t.total_ms += d
            perf.tool_ms += d

    perf.tool_ms = round(perf.tool_ms, 2)
    perf.tools = sorted(per_tool.values(), key=lambda x: x.total_ms, reverse=True)
    for t in perf.tools:
        t.total_ms = round(t.total_ms, 2)
    if wall is not None:
        perf.non_tool_ms = round(max(0.0, wall - perf.tool_ms), 2)
    return perf


def build_perf_report(
    runtime_root: str | Path,
    *,
    since: datetime | None = None,
    window_label: str = "all",
    top_n: int = 5,
) -> PerfReport:
    """Agrega performance das runs terminais (janela opcional)."""
    from ..events import EventBus
    from ..runtime.run_manager import RunManager

    runs = RunManager(root=runtime_root).list_runs()
    if since is not None:
        runs = [r for r in runs if _after(r.started_at, since)]
    runs = [r for r in runs if r.status in _TERMINAL]

    report = PerfReport(window=window_label, runs_total=len(runs))
    run_ids: set[str] = {r.id for r in runs}

    walls: list[tuple[str, float]] = []
    for r in runs:
        w = duration_ms(r.started_at, getattr(r, "finished_at", None), getattr(r, "updated_at", None))
        if w is not None:
            walls.append((r.id, w))
            report.total_wall_ms += w

    wall_values = [w for _, w in walls]
    report.total_wall_ms = round(report.total_wall_ms, 2)
    report.avg_wall_ms = round(sum(wall_values) / len(wall_values), 2) if wall_values else None
    report.p50_wall_ms = _percentile(wall_values, 50)
    report.p95_wall_ms = _percentile(wall_values, 95)
    report.slowest = sorted(walls, key=lambda x: x[1], reverse=True)[:top_n]

    # Agrega durações por tool nos eventos das runs da janela (uma passada).
    agg: dict[str, ToolTiming] = defaultdict(lambda: ToolTiming(tool=""))
    for ev in EventBus(root=runtime_root).list_events():
        if ev.event_type not in _TOOL_DONE or not ev.tool_name:
            continue
        if ev.run_id is not None and ev.run_id not in run_ids:
            continue
        t = agg[ev.tool_name]
        t.tool = ev.tool_name
        t.calls += 1
        d = _event_duration_ms(ev)
        if d is not None:
            t.total_ms += d
            report.total_tool_ms += d

    report.total_tool_ms = round(report.total_tool_ms, 2)
    for t in agg.values():
        t.total_ms = round(t.total_ms, 2)
    report.top_tools = sorted(agg.values(), key=lambda x: x.total_ms, reverse=True)[:top_n]
    if report.total_wall_ms > 0:
        report.tool_time_share = round(report.total_tool_ms / report.total_wall_ms, 4)
    return report


def _after(ts: str, since: datetime) -> bool:
    """`ts` (started_at, normalmente UTC-aware) >= `since`.

    Normaliza AMBOS para UTC-aware. Um `since` naive é assumido em UTC — sem
    isso, comparar timestamp UTC contra `datetime.now()` local erra pelo offset
    do fuso (uma run "de agora" caía numa janela "futura")."""
    from datetime import timezone

    parsed = parse_iso(ts)
    if parsed is None:
        return True
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    if since.tzinfo is None:
        since = since.replace(tzinfo=timezone.utc)
    return parsed >= since
