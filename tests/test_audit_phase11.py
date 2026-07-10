from __future__ import annotations

import json
import asyncio
from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from bauer.cli import app
from bauer.core.audit import (
    audit_architecture,
    audit_run,
    build_report,
    build_skill_insights,
    build_weekly_report,
    list_benchmark_reports,
    load_benchmark_scenarios,
    run_benchmark_suite,
    score_run_by_id,
)
from bauer.desktop_api import build_desktop_router
from bauer.server import create_app
from bauer.core.events import EventBus
from bauer.core.policy import ApprovalManager
from bauer.core.runtime.run_manager import RunManager


def _seed_runtime(root: Path) -> tuple[str, str]:
    bus = EventBus(root=root)
    runs = RunManager(root=root, event_bus=bus)

    good = runs.create_run(
        session_id="session-1",
        agent_id="bauer.dev",
        runtime_adapter="bauer_native",
        input={"message": "Crie uma mini API com testes"},
        status="running",
    )
    bus.publish(
        "skill.selected",
        run_id=good.id,
        session_id="session-1",
        agent_id="bauer.dev",
        skill_id="filesystem.write",
        status="selected",
    )
    bus.publish(
        "policy.evaluated",
        run_id=good.id,
        session_id="session-1",
        tool_name="write_file",
        status="allow",
        data={"operation": "filesystem.write", "risk_level": "low"},
    )
    bus.publish(
        "tool.call.completed",
        run_id=good.id,
        session_id="session-1",
        skill_id="filesystem.write",
        tool_name="write_file",
        status="completed",
        data={"args": {"path": "app/main.py"}},
    )
    bus.publish(
        "tool.call.completed",
        run_id=good.id,
        session_id="session-1",
        skill_id="shell.execute",
        tool_name="run_command",
        status="completed",
        data={"args": {"command": "pytest -q"}},
    )
    runs.complete_run(
        good.id,
        output={"response": "Mini API criada com endpoint /health, README e testes executados com sucesso."},
        tool_calls_count=2,
        cost_estimate=0.0123,
    )

    bad = runs.create_run(
        session_id="session-2",
        agent_id="bauer.devops",
        runtime_adapter="agno",
        input={"message": "Diagnosticar Docker"},
        status="running",
    )
    bus.publish(
        "policy.evaluated",
        run_id=bad.id,
        session_id="session-2",
        tool_name="run_command",
        status="ask",
        data={"operation": "shell.execute", "risk_level": "medium"},
    )
    ApprovalManager(root=root, event_bus=bus).request(
        operation="shell.execute",
        tool_name="run_command",
        reason="Shell precisa de aprovacao",
        risk_level="medium",
        run_id=bad.id,
        session_id="session-2",
    )
    bus.publish(
        "tool.call.failed",
        run_id=bad.id,
        session_id="session-2",
        skill_id="shell.execute",
        tool_name="run_command",
        status="failed",
        message="shell timeout after 30s",
        data={"args": {"command": "docker compose logs"}},
    )
    bus.publish(
        "skill.executed",
        run_id=bad.id,
        session_id="session-2",
        skill_id="shell.execute",
        status="failed",
        message="shell timeout after 30s",
    )
    runs.fail_run(bad.id, "shell timeout after 30s")
    return good.id, bad.id


def test_audit_report_aggregates_runtime_records(tmp_path: Path):
    _good_id, _bad_id = _seed_runtime(tmp_path)

    report = build_report(tmp_path)

    assert report.runs_total == 2
    assert report.runs_completed == 1
    assert report.runs_failed == 1
    assert report.success_rate == 0.5
    assert report.approvals_pending == 1
    assert report.policy_allow == 1
    assert report.policy_ask == 1
    assert ("filesystem.write", 1) in report.most_used_skills
    assert report.most_failed_skills[0] == ("shell.execute", 2)
    assert report.top_errors[0][0] == "shell timeout after 30s"
    assert report.estimated_cost_usd == 0.0123


def test_audit_run_extracts_tools_policy_approvals_and_score(tmp_path: Path):
    good_id, bad_id = _seed_runtime(tmp_path)

    good_audit = audit_run(tmp_path, good_id)
    assert good_audit is not None
    assert good_audit.status == "completed"
    assert good_audit.files_changed == ["app/main.py"]
    assert good_audit.commands_executed == ["pytest -q"]
    assert score_run_by_id(tmp_path, good_id).score == 5  # type: ignore[union-attr]

    bad_audit = audit_run(tmp_path, bad_id)
    assert bad_audit is not None
    assert bad_audit.status == "failed"
    assert bad_audit.tools_used == ["run_command"]
    assert bad_audit.commands_executed == ["docker compose logs"]
    assert bad_audit.policy_decisions[0].action == "ask"
    assert bad_audit.approvals[0]["type"] == "approval.requested"
    assert score_run_by_id(tmp_path, bad_id).score < 5  # type: ignore[union-attr]


def test_audit_cli_report_run_and_score_json(tmp_path: Path):
    runner = CliRunner()
    good_id, _bad_id = _seed_runtime(tmp_path)

    report_result = runner.invoke(
        app,
        ["audit", "report", "--format", "json", "--state-dir", str(tmp_path)],
    )
    assert report_result.exit_code == 0
    report_payload = json.loads(report_result.output)
    assert report_payload["runs_total"] == 2
    assert report_payload["most_failed_skills"][0] == ["shell.execute", 2]

    run_result = runner.invoke(
        app,
        ["audit", "run", good_id, "--format", "json", "--state-dir", str(tmp_path)],
    )
    assert run_result.exit_code == 0
    run_payload = json.loads(run_result.output)
    assert run_payload["run_id"] == good_id
    assert run_payload["score"]["score"] == 5

    score_result = runner.invoke(
        app,
        ["audit", "score", good_id, "--format", "json", "--state-dir", str(tmp_path)],
    )
    assert score_result.exit_code == 0
    assert json.loads(score_result.output)["score"] == 5


def test_audit_cli_output_writes_json_file(tmp_path: Path):
    runner = CliRunner()
    _seed_runtime(tmp_path)
    output = tmp_path / "reports" / "audit-report.json"

    result = runner.invoke(
        app,
        ["audit", "report", "--output", str(output), "--state-dir", str(tmp_path)],
    )

    assert result.exit_code == 0
    assert output.exists()
    assert json.loads(output.read_text(encoding="utf-8"))["runs_total"] == 2


def _seed_architecture_project(root: Path) -> None:
    (root / "bauer" / "core" / "runtime" / "adapters").mkdir(parents=True)
    (root / "bauer" / "core" / "runtime" / "adapters" / "agno_adapter.py").write_text(
        "from agno import Agent\n",
        encoding="utf-8",
    )
    (root / "bauer" / "bad_agno.py").write_text(
        "from agno import Agent\n",
        encoding="utf-8",
    )
    (root / "bauer" / "tools").mkdir(parents=True)
    (root / "bauer" / "tools" / "unsafe.py").write_text(
        "import subprocess\nsubprocess.run(['echo', 'x'])\n",
        encoding="utf-8",
    )
    (root / "bauer" / "core" / "orchestration").mkdir(parents=True)
    (root / "bauer" / "core" / "orchestration" / "coupled.py").write_text(
        "skill_id = 'docker.diagnose'\n",
        encoding="utf-8",
    )
    (root / "bauer" / "runner.py").write_text(
        "def execute(adapter, request):\n    return adapter.run_agent(request)\n",
        encoding="utf-8",
    )
    (root / "bauer" / "core" / "skills").mkdir(parents=True)
    (root / "bauer" / "core" / "skills" / "unsafe_policy.py").write_text(
        "from bauer.core.policy import PolicyEngine\npolicy_engine.evaluate('x')\n",
        encoding="utf-8",
    )
    (root / "desktop" / "src").mkdir(parents=True)
    (root / "desktop" / "src" / "App.tsx").write_text(
        "import { RunManager } from 'bauer.core.runtime';\n",
        encoding="utf-8",
    )
    manifests = root / "bauer" / "data" / "skill_manifests"
    (manifests / "bad.skill").mkdir(parents=True)
    (manifests / "bad.skill" / "skill.yaml").write_text(
        "id: bad.skill\nname: Bad Skill\nrisk: high\n",
        encoding="utf-8",
    )


def test_architecture_auditor_detects_static_risks(tmp_path: Path):
    _seed_architecture_project(tmp_path)

    report = audit_architecture(tmp_path)
    warning_rules = {finding.rule for finding in report.warnings}
    critical_rules = {finding.rule for finding in report.critical}

    assert report.status == "approved_with_warnings"
    assert "agno-runtime-boundary" in warning_rules
    assert "frontend-runtime-boundary" in warning_rules
    assert "skill-manifest" in warning_rules
    assert "execution-events" in warning_rules
    assert "core-skill-coupling" in warning_rules
    assert "sensitive-tools-policy" in critical_rules
    assert "skill-policy-boundary" in critical_rules
    assert not any(finding.file.endswith("agno_adapter.py") for finding in report.warnings)


def test_audit_cli_architecture_json(tmp_path: Path):
    runner = CliRunner()
    _seed_architecture_project(tmp_path)

    result = runner.invoke(
        app,
        ["audit", "architecture", "--format", "json", "--project-root", str(tmp_path)],
    )

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["status"] == "approved_with_warnings"
    assert "sensitive-tools-policy" in {item["rule"] for item in payload["critical"]}


def _seed_insight_runs(root: Path) -> None:
    bus = EventBus(root=root)
    runs = RunManager(root=root, event_bus=bus)
    for index in range(2):
        run = runs.create_run(
            session_id=f"insight-{index}",
            agent_id="bauer.devops",
            runtime_adapter="bauer_native",
            input={"message": "Diagnosticar servico"},
            status="running",
        )
        bus.publish(
            "skill.executed",
            run_id=run.id,
            skill_id="docker.diagnose",
            status="failed" if index == 0 else "completed",
            data={"duration_ms": 1000 + index * 500},
        )
        for tool_name in ("docker_ps", "docker_logs", "check_health"):
            bus.publish(
                "tool.call.completed",
                run_id=run.id,
                skill_id="docker.diagnose",
                tool_name=tool_name,
                status="completed",
            )
        runs.complete_run(
            run.id,
            output={"response": "Diagnostico completo com evidencias, verificacoes e proximas acoes recomendadas."},
            tool_calls_count=3,
        )


def test_skill_insights_detects_failures_latency_sequences_and_suggestions(tmp_path: Path):
    _seed_insight_runs(tmp_path)

    insights = build_skill_insights(tmp_path, suggest_new=True)

    assert insights.most_used[0].skill_id == "docker.diagnose"
    assert insights.most_used[0].uses == 2
    assert insights.highest_failure_rate[0].failure_rate == 0.5
    assert insights.slowest[0].average_duration_ms == 1250.0
    assert insights.repeated_sequences[0].occurrences == 2
    assert insights.suggestions[0].requires_human_approval is True


def test_skills_insights_and_weekly_cli(tmp_path: Path):
    _seed_insight_runs(tmp_path)
    runner = CliRunner()

    insight_result = runner.invoke(
        app,
        ["skills", "insights", "--format", "json", "--suggest-new", "--state-dir", str(tmp_path)],
    )
    assert insight_result.exit_code == 0
    assert json.loads(insight_result.output)["suggestions"][0]["requires_human_approval"] is True

    output = tmp_path / "reports" / "weekly.md"
    weekly_result = runner.invoke(
        app,
        ["audit", "weekly", "--state-dir", str(tmp_path), "--output", str(output)],
    )
    assert weekly_result.exit_code == 0
    markdown = output.read_text(encoding="utf-8")
    assert "# Bauer Weekly Audit Report" in markdown
    assert "## Recommended Next Actions" in markdown
    assert "human approval required" in markdown
    assert build_weekly_report(tmp_path, since=None).startswith("# Bauer Weekly Audit Report")


def _benchmark_executor(root: Path):
    bus = EventBus(root=root)
    runs = RunManager(root=root, event_bus=bus)

    def execute(scenario, workspace: Path) -> str:
        run = runs.create_run(
            session_id=f"bench-{scenario.id}",
            agent_id="benchmark",
            runtime_adapter="bauer_native",
            input={"message": scenario.prompt},
            status="running",
        )
        (workspace / "app").mkdir(parents=True, exist_ok=True)
        (workspace / "app" / "main.py").write_text("app = object()\n", encoding="utf-8")
        bus.publish(
            "tool.call.completed",
            run_id=run.id,
            skill_id="filesystem.write",
            tool_name="write_file",
            status="completed",
            data={"args": {"path": "app/main.py"}},
        )
        bus.publish(
            "tool.call.completed",
            run_id=run.id,
            skill_id="shell.execute",
            tool_name="run_command",
            status="completed",
            data={"args": {"command": "pytest -q"}},
        )
        runs.complete_run(
            run.id,
            output={"response": "Entrega concluida, validada com testes e documentada com um resumo objetivo."},
            tool_calls_count=2,
        )
        return run.id

    return execute


def test_benchmark_suite_yaml_run_score_report_and_repeat(tmp_path: Path):
    scenarios_dir = tmp_path / "scenarios"
    scenarios_dir.mkdir()
    (scenarios_dir / "mini-api.yaml").write_text(
        """id: mini-api
name: Mini API
prompt: Crie uma API com testes
expected:
  files: [app/main.py]
  commands: [pytest]
  events: [run.completed]
min_score: 4
""",
        encoding="utf-8",
    )
    scenarios = load_benchmark_scenarios(scenarios_dir)
    execute = _benchmark_executor(tmp_path / "runtime")

    first = run_benchmark_suite(
        tmp_path / "runtime", scenarios, execute, workspace_root=tmp_path / "workspaces"
    )
    second = run_benchmark_suite(
        tmp_path / "runtime", scenarios, execute, workspace_root=tmp_path / "workspaces"
    )

    assert first.passed == 1
    assert first.results[0].run_id.startswith("run-")
    assert first.results[0].score >= 4
    assert second.passed == 1
    assert len(list_benchmark_reports(tmp_path / "runtime")) == 2


def test_benchmark_cli_run_all_with_runtime_executor_hook(tmp_path: Path):
    scenarios_dir = tmp_path / "scenarios"
    scenarios_dir.mkdir()
    (scenarios_dir / "mini-api.yaml").write_text(
        """id: mini-api
name: Mini API
prompt: Crie uma API com testes
expected:
  files: [app/main.py]
  commands: [pytest]
  events: [run.completed]
min_score: 4
""",
        encoding="utf-8",
    )
    runtime_root = tmp_path / "runtime"
    runner = CliRunner()
    with patch("bauer.commands.benchmark_cmd._build_executor", return_value=_benchmark_executor(runtime_root)):
        result = runner.invoke(app, [
            "benchmark", "run", "--all", "--format", "json",
            "--scenarios-dir", str(scenarios_dir),
            "--state-dir", str(runtime_root),
            "--workspace-root", str(tmp_path / "workspaces"),
        ])
    assert result.exit_code == 0
    assert json.loads(result.output)["passed"] == 1


def test_phase11_desktop_api_report_run_score_and_skills(tmp_path: Path):
    good_id, _ = _seed_runtime(tmp_path)
    api_app = FastAPI()
    api_app.include_router(build_desktop_router(runtime_root=tmp_path))
    client = TestClient(api_app)

    report = client.get("/api/audit/report?last=7d")
    detail = client.get(f"/api/audit/runs/{good_id}")
    score = client.get(f"/api/audit/runs/{good_id}/score")
    skills = client.get("/api/audit/skills?last=7d")
    skills_expected_route = client.get("/api/audit/skills/insights?last=7d")

    assert report.status_code == 200
    assert report.json()["runs_total"] == 2
    assert detail.json()["run_id"] == good_id
    assert detail.json()["event_details"]
    assert score.json()["score"] == 5
    assert "most_used" in skills.json()
    assert skills_expected_route.status_code == 200


def test_phase11_primary_server_audit_endpoints(tmp_path: Path):
    router = MagicMock()
    router.available_tools.return_value = []
    router.tool_info.side_effect = lambda name: {"name": name}
    client = MagicMock()
    client.chat_stream.return_value = iter(["Resposta final completa para a auditoria da run."])
    client.list_models.return_value = []
    server = create_app(
        model_name="test-model",
        applied_context=4096,
        router=router,
        client=client,
        system_prompt="system",
        sessions_dir=tmp_path / "sessions",
        rate_limit_requests=0,
    )

    async def exercise():
        transport = httpx.ASGITransport(app=server)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
            await http.post("/chat", json={"message": "audite isto", "session_id": "phase11"})
            runs = (await http.get("/runs")).json()["runs"]
            run_id = runs[0]["id"]
            return (
                await http.get("/audit/report?last=7d"),
                await http.get(f"/audit/runs/{run_id}"),
                await http.get(f"/audit/runs/{run_id}/score"),
                await http.get("/audit/skills/insights?last=7d"),
            )

    report, detail, score, insights = asyncio.run(exercise())
    assert report.status_code == 200
    assert report.json()["runs_total"] == 1
    assert detail.status_code == 200
    assert detail.json()["event_details"]
    assert score.status_code == 200
    assert score.json()["max_score"] == 5
    assert insights.status_code == 200
    assert "suggestions" in insights.json()
