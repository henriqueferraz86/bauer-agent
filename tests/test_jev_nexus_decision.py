from __future__ import annotations

from types import SimpleNamespace

from bauer.core.kernel.kernel import BauerKernel
from bauer.core.kernel.schemas import KernelRequest
from bauer.core.runtime.run_manager import RunManager
from bauer.decision_router import decide_with_fallback
from bauer.routing_runtime import decision_catalog


def _config(*, enabled: bool = False):
    return SimpleNamespace(
        decision=SimpleNamespace(
            jev_enabled=enabled,
            fallback_enabled=True,
            api_key="",
            endpoint="https://api.typesafe.ai/v1/systemone",
            model="jev-latest",
            timeout_seconds=2.0,
            min_confidence=0.65,
            memory_enabled=True,
        )
    )


def test_local_fallback_selects_formal_team_agent_tools_and_probabilities():
    decision = decide_with_fallback(
        "analise a arquitetura e depois implemente os testes",
        config=_config(),
        teams=["bauer.software_team"],
        agents=["bauer.product", "bauer.dev", "bauer.qa", "bauer.devops"],
        available_tools=["read_file", "search_text", "write_file", "delegate_task"],
    )

    assert decision.runtime == "agno"
    assert decision.team_id == "bauer.software_team"
    assert decision.agent_id == "bauer.dev"
    assert decision.strategy == "team"
    assert decision.plan
    assert decision.tools == ["read_file", "search_text", "write_file", "delegate_task"]
    assert round(sum(decision.probabilities.values()), 6) == 1.0
    assert {item.option for item in decision.alternatives} == set(decision.probabilities)


def test_decision_catalog_exposes_formal_runtime_choices():
    teams, agents, tools = decision_catalog()

    assert "bauer.software_team" in teams
    assert {"bauer.product", "bauer.dev", "bauer.qa", "bauer.devops"}.issubset(agents)
    assert {"read_file", "delegate_task", "web_search"}.issubset(tools)


def test_kernel_persists_explicit_decision_snapshot_and_event(tmp_path):
    runs = RunManager(root=tmp_path / "runtime")
    kernel = BauerKernel(runs=runs, config=_config())
    selected = {
        "profile": "coding",
        "runtime": "agno",
        "team_id": "bauer.software_team",
        "agent_id": "bauer.dev",
        "probabilities": {"coding": 0.8, "heavy": 0.2},
        "strategy": "team",
        "plan": ["implementar", "validar"],
    }

    result = kernel.execute(
        KernelRequest(
            task="melhorar o Bauer",
            agent_id="bauer.product",
            decision=selected,
        ),
        executor=lambda payload: {"output": "ok"},
    )

    assert result.ok
    run = runs.get_run(result.run_id)
    assert run is not None
    assert run.input["decision"] == selected
    events = runs.store.list("events")
    assert any(event.get("event_type") == "decision.selected" for event in events)
