"""Channel governance shares the web ledger and preserves Kernel custody."""

from __future__ import annotations

import contextvars
import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from bauer.channel_governance import (
    TurnMetrics, build_channel_kernel, configure_router, runtime_root,
)
from bauer.core.events import EventBus
from bauer.core.kernel import KernelWiringError, run_governed
from bauer.core.runtime.autonomy import BudgetManager
from bauer.cost_meter import cost_sink, report_llm_cost
from bauer.runtime_ids import get_runtime_ids
from bauer.tool_router import ToolRouter


def config(*, enabled=True, evaluate=False):
    return SimpleNamespace(kernel=SimpleNamespace(
        enabled=enabled, evaluator_enabled=evaluate, max_replans=0,
        heartbeat_interval_s=0,
    ))


def test_kernel_and_tool_router_share_web_root_preserving_profile(tmp_path):
    sessions = tmp_path / "shared" / "sessions"
    root = sessions.parent / "runtime"
    manager = BudgetManager(root=root)
    manager.set_profile(daily_budget_usd=1000, max_parallel_runs=7,
                        max_cost_usd_per_run=1.5)
    before = manager.get_profile()
    project = tmp_path / "project"
    project.mkdir()
    router = ToolRouter(workspace=project)
    original_flags = (router._policy_enabled, router._policy_mode)
    bus = configure_router(router, sessions_dir=sessions)
    kernel = build_channel_kernel(config(), workspace=project,
                                  sessions_dir=sessions, event_bus=bus)

    assert runtime_root(sessions) == root
    assert router._policy_root == root
    assert router._event_bus is kernel.bus is bus
    assert (router._policy_enabled, router._policy_mode) == original_flags
    assert kernel.policy.workspace == project
    assert kernel.policy.budget_manager.root == root
    assert kernel.budget.root == root
    assert manager.get_profile() == before


def test_shared_daily_limit_denies_channel_before_executor(tmp_path):
    sessions = tmp_path / "memory" / "sessions"
    BudgetManager(root=runtime_root(sessions)).set_profile(daily_budget_usd=0)
    kernel = build_channel_kernel(config(), workspace=tmp_path,
                                  sessions_dir=sessions)
    called = []
    result = run_governed(kernel, lambda _: called.append(1),
                          agent_id="channel.telegram", session_id="tg:42",
                          task="must not call provider")
    assert result.blocked_before_start
    assert called == []
    assert "budget exceeded for daily" in result.error


@pytest.mark.parametrize("failed", [False, True])
def test_kernel_records_request_cost_once_on_success_and_failure(tmp_path, failed):
    sessions = tmp_path / "memory" / "sessions"
    kernel = build_channel_kernel(config(), workspace=tmp_path,
                                  sessions_dir=sessions)
    metrics = TurnMetrics()

    def executor(payload):
        with metrics.context("tg:42", payload["run_id"]):
            assert get_runtime_ids() == ("tg:42", payload["run_id"])
            # Native paths report each successful/failed request separately.
            report_llm_cost("openai", "controlled", {"cost": 0.1})
            report_llm_cost("openai", "controlled", {"cost": 0.25})
            metrics.record_tools([{"tool": "read_file"}])
        return metrics.result(output="partial" if failed else "done",
                              status="failed" if failed else None,
                              error="provider failed" if failed else None)

    result = run_governed(kernel, executor, agent_id="channel.telegram",
                          session_id="tg:42", task="test")
    run = kernel.runs.get_run(result.run_id)
    assert run.status == ("failed" if failed else "completed")
    assert run.cost_estimate == pytest.approx(0.35)
    assert run.llm_calls_count == 2
    assert run.tool_calls_count == 1
    assert run.usage_known is True
    assert kernel.budget.status()["daily"]["used_usd"] == pytest.approx(0.35)
    assert cost_sink.get() is None
    assert get_runtime_ids() is None


def test_context_copied_into_workers_isolated_and_restored(tmp_path):
    barrier = threading.Barrier(2)

    def turn(session):
        metrics = TurnMetrics()
        with metrics.context(session, f"run:{session}"):
            ctx = contextvars.copy_context()

            def child():
                barrier.wait(timeout=5)
                assert get_runtime_ids() == (session, f"run:{session}")
                report_llm_cost("openai", "controlled", {"cost": 0.1})
                return cost_sink.get()

            with ThreadPoolExecutor(max_workers=1) as pool:
                assert pool.submit(ctx.run, child).result(timeout=10) is metrics
        assert get_runtime_ids() is None and cost_sink.get() is None
        return metrics.result()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(turn, ("a", "b")))
    assert [r["cost_estimate"] for r in results] == [0.1, 0.1]
    assert [r["llm_calls_count"] for r in results] == [1, 1]


def test_unknown_usage_is_not_presented_as_known_zero():
    metrics = TurnMetrics()
    with metrics.context("tg:42", "run:42"):
        report_llm_cost("openai", "controlled", None)
    assert metrics.result()["usage_known"] is False
    assert metrics.result()["llm_calls_count"] == 1
    assert TurnMetrics().result()["usage_known"] is False


def test_local_calls_remain_known_free_even_without_usage():
    metrics = TurnMetrics()
    with metrics.context("tg:42", "run:42"):
        report_llm_cost("ollama", "controlled", None)
    assert metrics.result()["usage_known"] is True
    assert metrics.result()["cost_estimate"] == 0


def test_context_is_restored_after_worker_failure():
    previous = TurnMetrics()
    metrics = TurnMetrics()
    with previous.context("outer", "outer-run"):
        with pytest.raises(RuntimeError, match="provider unavailable"):
            with metrics.context("inner", "inner-run"):
                raise RuntimeError("provider unavailable")
        assert cost_sink.get() is previous
        assert get_runtime_ids() == ("outer", "outer-run")


def test_wrong_store_cannot_split_policy_and_events(tmp_path):
    bus = EventBus(root=tmp_path / "other-runtime")
    router = ToolRouter(workspace=tmp_path / "project")
    with pytest.raises(ValueError, match="runtime root"):
        configure_router(router, sessions_dir=tmp_path / "sessions", event_bus=bus)
    with pytest.raises(KernelWiringError, match="runtime root"):
        build_channel_kernel(config(), workspace=tmp_path,
                             sessions_dir=tmp_path / "sessions", event_bus=bus)


def test_kernel_disabled_keeps_explicit_legacy_contract(tmp_path):
    assert build_channel_kernel(config(enabled=False), workspace=tmp_path,
                                sessions_dir=tmp_path / "sessions") is None


def test_kernel_keeps_custody_over_empty_channel_output(tmp_path):
    kernel = build_channel_kernel(config(evaluate=True), workspace=tmp_path,
                                  sessions_dir=tmp_path / "sessions")
    result = run_governed(kernel, lambda _: TurnMetrics().result(output=""),
                          agent_id="channel.telegram", task="return nothing")
    assert result.status == "failed"
    assert kernel.runs.get_run(result.run_id).status == "failed"
