"""Strict authenticated daily-cap updates against the real budget profile."""

import json
from dataclasses import asdict

import pytest

from bauer.core.events import EventBus
from bauer.core.runtime.autonomy import BudgetExceededError, BudgetManager
from tests.test_desktop_api import env  # noqa: F401


@pytest.mark.parametrize("payload", [
    {}, {"weekly_budget_usd": 10}, {"daily_budget_usd": 3, "weekly_budget_usd": 10},
    {"daily_budget_usd": "3"}, {"daily_budget_usd": True}, {"daily_budget_usd": False},
    {"daily_budget_usd": None}, {"daily_budget_usd": -0.01}, {"daily_budget_usd": []},
    {"daily_budget_usd": {}}, {"daily_budget_usd": float("nan")},
    {"daily_budget_usd": float("inf")}, {"daily_budget_usd": -float("inf")},
    {"daily_budget_usd": 10 ** 400}, [], 3, "3", True, None, float("nan"), float("inf"),
])
def test_invalid_budget_updates_are_422_before_any_write(env, payload):
    manager = BudgetManager(root=env["runtime_root"])
    manager.set_profile(daily_budget_usd=2, weekly_budget_usd=30)
    before = asdict(manager.get_profile())
    events_before = EventBus(root=env["runtime_root"]).store.list("events")
    response = env["client"].put("/api/obs/budget", content=json.dumps(payload), headers={"Content-Type": "application/json"})
    assert response.status_code == 422, response.text
    assert asdict(manager.get_profile()) == before
    assert EventBus(root=env["runtime_root"]).store.list("events") == events_before


@pytest.mark.parametrize("limit", [0, 0.0, 3, 12.34])
def test_daily_update_returns_real_status_preserves_other_fields_and_consumption(env, limit, monkeypatch):
    def unexpected_provider(*args, **kwargs):
        pytest.fail("budget settings called a provider")
    monkeypatch.setattr("bauer.agent.run_one_turn", unexpected_provider)
    manager = BudgetManager(root=env["runtime_root"])
    manager.set_profile(mode="autonomous", daily_budget_usd=5, weekly_budget_usd=80,
                        monthly_budget_usd=200, max_tool_calls_per_run=35,
                        max_runtime_s_per_run=120, max_parallel_runs=4,
                        max_cost_usd_per_run=25, agent_budgets_usd={"agent-a": 40},
                        company_budgets_usd={"company-a": 100})
    manager.record_run_cost(run_id="recorded", agent_id="agent-a", company_id="company-a", cost_usd=1.25)
    before = manager.status()
    response = env["client"].put("/api/obs/budget", json={"daily_budget_usd": limit})
    assert response.status_code == 200, response.text
    result = response.json()
    assert result == env["client"].get("/api/obs/budget").json()
    assert set(result) == {"profile", "daily", "weekly", "monthly", "by_agent", "by_company"}
    assert result["daily"] == {"limit_usd": float(limit), "used_usd": 1.25,
                               "remaining_usd": max(0, limit - 1.25), "exceeded": 1.25 >= limit}
    for field in ("weekly", "monthly", "by_agent", "by_company"):
        assert result[field] == before[field]
    for field, value in before["profile"].items():
        if field not in {"daily_budget_usd", "updated_at"}:
            assert result["profile"][field] == value
    # A fresh reader simulates a restart and observes the same persisted cap.
    assert BudgetManager(root=env["runtime_root"]).status() == result


def test_cached_manager_enforces_lowered_cap_then_immediately_sees_raise(env):
    cached = BudgetManager(root=env["runtime_root"])
    cached.set_profile(daily_budget_usd=20)
    cached.record_run_cost(run_id="used", agent_id="agent-a", cost_usd=9.6945)
    assert env["client"].put("/api/obs/budget", json={"daily_budget_usd": 2}).status_code == 200
    with pytest.raises(BudgetExceededError, match="daily"):
        cached.ensure_can_start(agent_id="agent-a")
    response = env["client"].put("/api/obs/budget", json={"daily_budget_usd": 15})
    assert response.status_code == 200
    cached.ensure_can_start(agent_id="agent-a")
    assert cached.status()["daily"]["used_usd"] == 9.6945
    assert cached.status()["daily"]["limit_usd"] == 15


def test_zero_budget_is_saved_truthfully_and_blocks_cached_admission_without_usage(env):
    cached = BudgetManager(root=env["runtime_root"])
    response = env["client"].put("/api/obs/budget", json={"daily_budget_usd": 0})
    assert response.status_code == 200
    assert response.json()["daily"] == {"limit_usd": 0.0, "used_usd": 0.0,
                                        "remaining_usd": 0.0, "exceeded": True}
    with pytest.raises(BudgetExceededError, match="daily"):
        cached.ensure_can_start(estimated_cost_usd=0)


def test_failed_profile_persistence_does_not_change_cap_or_consume_budget(env, monkeypatch):
    from fastapi.testclient import TestClient

    manager = BudgetManager(root=env["runtime_root"])
    manager.set_profile(daily_budget_usd=5)
    manager.record_run_cost(run_id="used", agent_id="agent-a", cost_usd=1)
    before = manager.status()
    original_upsert = manager.store.__class__.upsert
    def failed_upsert(store, table, data):
        if table == "autonomy":
            raise OSError("controlled write failure")
        return original_upsert(store, table, data)
    monkeypatch.setattr(manager.store.__class__, "upsert", failed_upsert)
    with TestClient(env["client"].app, raise_server_exceptions=False) as client:
        response = client.put("/api/obs/budget", json={"daily_budget_usd": 12})
    assert response.status_code >= 500
    assert "controlled write failure" not in response.text
    assert manager.status() == before


def test_daily_budget_update_requires_auth_and_cookie_csrf(tmp_path, monkeypatch):
    from tests.test_web_auth_http import API_KEY, _client, _setup

    monkeypatch.setattr("bauer.projects_registry._DEFAULT_REGISTRY", tmp_path / "projects.json")
    with _client(tmp_path) as client:
        response = client.put("/api/obs/budget", json={"daily_budget_usd": 11})
        assert response.status_code == 401
        assert _setup(client).status_code == 201
        baseline = client.get("/api/obs/budget").json()["profile"]["daily_budget_usd"]
        assert client.put("/api/obs/budget", json={"daily_budget_usd": 11}).status_code == 403
        assert client.get("/api/obs/budget").json()["profile"]["daily_budget_usd"] == baseline
        csrf = client.cookies.get("bauer_csrf")
        assert client.put("/api/obs/budget", json={"daily_budget_usd": 11}, headers={"X-CSRF-Token": csrf}).status_code == 200
        # Existing API-key authentication keeps its established CSRF semantics.
        assert client.put("/api/obs/budget", json={"daily_budget_usd": 12}, headers={"X-API-Key": API_KEY}).status_code == 200
