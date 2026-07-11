"""Baseline de performance (Fase 12, Sprint 33) — profiler + CLI.

Mede a partir de dados já persistidos: wall-clock da run + duration_ms dos
eventos de tool. Sem instrumentação nova."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

pytest.importorskip("typer")
from typer.testing import CliRunner

from bauer.cli import app
from bauer.core.events import EventBus
from bauer.core.performance import build_perf_report, run_perf
from bauer.core.runtime.run_manager import RunManager

runner = CliRunner()


def _run_with_tools(root: Path, *, wall_ms: int, tools: list[tuple[str, float]], status: str = "completed"):
    """Cria uma run terminal com wall_ms de duração e eventos de tool com duração."""
    rm = RunManager(root=root)
    bus = EventBus(root=root)
    run = rm.create_run(session_id="s", agent_id="dev", runtime_adapter="bauer_native",
                        input={"message": "x"}, status="running")
    for name, dur in tools:
        bus.publish("tool.call.completed", run_id=run.id, tool_name=name,
                    status="completed", data={"duration_ms": dur})
    start = datetime.fromisoformat(run.started_at)
    rm.update_run(run.id, status=status,
                  finished_at=(start + timedelta(milliseconds=wall_ms)).isoformat())
    return run.id


class TestRunPerf:
    def test_breakdown(self, tmp_path):
        rid = _run_with_tools(tmp_path, wall_ms=5000,
                              tools=[("run_command", 1200), ("read_file", 50), ("run_command", 800)])
        p = run_perf(tmp_path, rid)
        assert p is not None
        assert p.wall_ms == 5000
        assert p.tool_ms == 2050          # 1200+50+800
        assert p.non_tool_ms == 2950       # 5000-2050 (modelo+overhead)
        assert p.tool_calls == 3
        # run_command agregado (2 calls, 2000ms) vem antes de read_file
        assert p.tools[0].tool == "run_command"
        assert p.tools[0].calls == 2 and p.tools[0].total_ms == 2000

    def test_unknown_run_returns_none(self, tmp_path):
        assert run_perf(tmp_path, "run-nope") is None

    def test_non_tool_never_negative(self, tmp_path):
        # tool_ms > wall (relógios/monotonic) → non_tool_ms clamp em 0, não negativo
        rid = _run_with_tools(tmp_path, wall_ms=100, tools=[("run_command", 999)])
        p = run_perf(tmp_path, rid)
        assert p.non_tool_ms == 0.0


class TestPerfReport:
    def test_aggregate(self, tmp_path):
        _run_with_tools(tmp_path, wall_ms=1000, tools=[("read_file", 100)])
        _run_with_tools(tmp_path, wall_ms=3000, tools=[("run_command", 2000)])
        rep = build_perf_report(tmp_path)
        assert rep.runs_total == 2
        assert rep.avg_wall_ms == 2000
        assert rep.total_tool_ms == 2100
        assert rep.total_wall_ms == 4000
        assert rep.tool_time_share == round(2100 / 4000, 4)
        # run_command domina o tempo de tool
        assert rep.top_tools[0].tool == "run_command"
        # a run de 3000ms é a mais lenta
        assert rep.slowest[0][1] == 3000

    def test_window_filters_old_runs(self, tmp_path):
        _run_with_tools(tmp_path, wall_ms=1000, tools=[("read_file", 100)])
        # janela de 1h pega tudo que acabou de ser criado (UTC, como os timestamps)
        assert build_perf_report(tmp_path, since=datetime.now(UTC) - timedelta(hours=1)).runs_total == 1
        # janela no futuro não pega nada
        assert build_perf_report(tmp_path, since=datetime.now(UTC) + timedelta(hours=1)).runs_total == 0


class TestPerfCli:
    def test_report_json(self, tmp_path):
        _run_with_tools(tmp_path, wall_ms=2000, tools=[("run_command", 1500)])
        res = runner.invoke(app, ["perf", "report", "--format", "json", "--state-dir", str(tmp_path)])
        assert res.exit_code == 0
        data = json.loads(res.stdout)
        assert data["runs_total"] == 1
        assert data["top_tools"][0]["tool"] == "run_command"

    def test_run_json(self, tmp_path):
        rid = _run_with_tools(tmp_path, wall_ms=2000, tools=[("run_command", 1500)])
        res = runner.invoke(app, ["perf", "run", rid, "--format", "json", "--state-dir", str(tmp_path)])
        assert res.exit_code == 0
        data = json.loads(res.stdout)
        assert data["tool_ms"] == 1500
        assert data["wall_ms"] == 2000

    def test_run_not_found_exits_1(self, tmp_path):
        res = runner.invoke(app, ["perf", "run", "run-nope", "--state-dir", str(tmp_path)])
        assert res.exit_code == 1
