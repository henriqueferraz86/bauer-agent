"""Settings updates the budget used by the already constructed HTTP Kernel."""

import pytest
from fastapi.testclient import TestClient

from bauer.core.runtime.autonomy import BudgetManager
from tests.test_chat_project_context import chat_projects  # noqa: F401


def test_settings_limit_unblocks_cached_kernel_without_clearing_previous_spend(chat_projects, tmp_path, monkeypatch):
    build, _, _ = chat_projects
    app = build(governed=True)
    manager = BudgetManager(root=tmp_path / "runtime")
    manager.record_run_cost(run_id="past-run", agent_id="serve.chat", company_id=None, cost_usd=9.6945)
    calls = []

    def controlled_turn(ctx, *args, **kwargs):
        calls.append(True)
        ctx.add_assistant("Execução autorizada pelo novo limite")
        return "Execução autorizada pelo novo limite", []

    monkeypatch.setattr("bauer.agent.run_one_turn", controlled_turn)
    with TestClient(app) as client:
        denied = client.post("/chat", json={"message": "continue"})
        assert denied.status_code == 403
        assert "budget exceeded for daily" in denied.json()["detail"]
        assert calls == []
        updated = client.put("/api/obs/budget", json={"daily_budget_usd": 20})
        assert updated.status_code == 200
        assert updated.json()["daily"]["used_usd"] == pytest.approx(9.6945)
        allowed = client.post("/chat", json={"message": "continue"})
        assert allowed.status_code == 200, allowed.text
        assert calls == [True]
        assert manager.status()["daily"]["limit_usd"] == 20
        assert manager.status()["daily"]["used_usd"] == pytest.approx(9.6945)
    assert BudgetManager(root=tmp_path / "runtime").get_profile().daily_budget_usd == 20


def test_zero_daily_limit_blocks_pristine_kernel_without_spending_or_reserving(chat_projects, tmp_path, monkeypatch):
    build, _, _ = chat_projects
    monkeypatch.setattr("bauer.agent.run_one_turn", lambda *a, **k: pytest.fail("LLM invoked"))
    with TestClient(build(governed=True)) as client:
        assert client.put("/api/obs/budget", json={"daily_budget_usd": 0}).status_code == 200
        response = client.post("/chat", json={"message": "continue"})
    assert response.status_code == 403
    assert "limit=$0.0000" in response.json()["detail"]
    assert BudgetManager(root=tmp_path / "runtime").status()["daily"]["used_usd"] == 0
