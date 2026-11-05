"""Plano 057 / sprint 56 — evals Agno sobre trilhas Bauer e memória de decisões."""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from bauer.core.events.bus import EventBus
from bauer.core.runtime.adapters.agno_adapter import AgnoRuntimeAdapter
from bauer.core.runtime.agno_evals import (
    CapabilityDecisionLog,
    DecisionPrivacyError,
    DecisionSettings,
    auto_record_tool_use,
    evaluate_tool_use,
    trace_from_events,
)
from bauer.core.runtime.agno_capabilities.settings import parse_settings
from bauer.decision_memory import DecisionMemory
from bauer.tools.base import ToolError

pytest.importorskip("agno")


def test_eval_runs_on_real_governed_trace_without_argument_values(tmp_path: Path):
    bus = EventBus(root=tmp_path / "events")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    adapter = AgnoRuntimeAdapter(
        adapter_config={"workspace": str(workspace), "db_file": str(tmp_path / "agno.db")},
        runtime_policy_root=tmp_path / "runtime", runtime_event_bus=bus,
    )
    tools = {fn.name: fn for fn in adapter._map_tools(["agno.calculator:add,divide"])}
    tools["agno_calculator__add"].entrypoint(a=1, b=2)
    with pytest.raises(ToolError):
        tools["agno_calculator__divide"].entrypoint(a=1, b=2, c=3)  # unexpected argument

    calls = trace_from_events(bus.list_events())
    assert [(c.tool_name, c.ok) for c in calls] == [("agno_calculator__add", True), ("agno_calculator__divide", False)]
    assert all(c.args == {} for c in calls)  # capability args are never in telemetry

    verdict = evaluate_tool_use(calls, expected=["agno_calculator__add"], allow_additional=True)
    assert verdict.passed
    strict = evaluate_tool_use(calls, expected=["agno_calculator__add", "agno_calculator__divide"])
    assert not strict.passed
    assert any(m.startswith("agno_calculator__divide") for m in strict.missing_calls)


def test_eval_argument_checks_for_tools_that_record_arguments():
    from bauer.core.events.schema import Event

    events = [Event(event_type="tool.call.completed", tool_name="read_file", data={"args": {"path": "a.md"}})]
    calls = trace_from_events(events)
    ok = evaluate_tool_use(calls, expected=["read_file"], expected_arguments={"read_file": {"path": "a.md"}})
    bad = evaluate_tool_use(calls, expected=["read_file"], expected_arguments={"read_file": {"path": "b.md"}})
    assert ok.passed and not bad.passed


def test_agno_eval_does_not_touch_network_or_db(monkeypatch):
    import socket

    def no_network(*args, **kwargs):
        raise AssertionError("eval tentou abrir conexão de rede")

    monkeypatch.setattr(socket, "create_connection", no_network)
    monkeypatch.setattr(socket.socket, "connect", no_network)
    from bauer.core.runtime.agno_evals import TraceCall

    assert evaluate_tool_use([TraceCall("x", True)], expected=["x"]).passed


# ── decisions ────────────────────────────────────────────────────────────────


def test_structured_decision_roundtrip_scope_and_deletion():
    log = CapabilityDecisionLog(DecisionMemory(":memory:"))
    first = log.record(kind="agent_selection", agent_id="bauer.research", selected="bauer.github",
                       alternatives=["bauer.web_fetch"], outcome="good", evidence="issue encontrada",
                       feedback="útil", scope="proj_a")
    log.record(kind="tool_use", agent_id="bauer.data", selected="bauer_sql__run_query", scope="proj_b")

    a = log.list(scope="proj_a")
    assert [d.id for d in a] == [first]
    assert a[0].alternatives == ("bauer.web_fetch",) and a[0].version == 1 and a[0].outcome == "good"
    assert [d.selected for d in log.list(scope="proj_b")] == ["bauer_sql__run_query"]
    assert log.delete(first) and log.list(scope="proj_a") == []
    assert log.purge_scope("proj_b") == 1


def test_decisions_expire_and_are_purged(monkeypatch):
    log = CapabilityDecisionLog(DecisionMemory(":memory:"))
    log.record(kind="final_answer", agent_id="a", selected="resposta", scope="s", ttl_days=1)
    later = time.time() + 2 * 86_400
    monkeypatch.setattr(time, "time", lambda: later)
    assert log.list(scope="s") == []
    assert len(log.list(scope="s", include_expired=True)) == 1
    assert log.purge_expired() == 1


def test_private_reasoning_and_secrets_are_not_persisted():
    memory = DecisionMemory(":memory:")
    log = CapabilityDecisionLog(memory)
    with pytest.raises(DecisionPrivacyError):
        log.record(kind="tool_use", agent_id="a", selected="t", extra={"chain_of_thought": "passo 1..."})
    with pytest.raises(DecisionPrivacyError):
        log.record(kind="musings", agent_id="a", selected="t")
    log.record(kind="tool_use", agent_id="a", selected="t", scope="s",
               evidence="usou token sk-ant-api03-ABCDEFGHIJKLMNOPQRSTUVWXYZ012345 " + "x" * 1000)
    stored = json.dumps([r.to_dict() for r in memory.list_recent()])
    assert "sk-ant-api03-ABCDEFGHIJ" not in stored
    assert len(json.loads(memory.list_recent()[0].decision)["evidence"]) <= 300


def test_auto_record_is_opt_in():
    from bauer.core.runtime.agno_evals import TraceCall

    log = CapabilityDecisionLog(DecisionMemory(":memory:"))
    calls = [TraceCall("bauer_github__get_issue", True), TraceCall("bauer_sql__run_query", False)]

    assert auto_record_tool_use(log, agent_id="a", calls=calls, settings=None) == []
    assert auto_record_tool_use(log, agent_id="a", calls=calls, settings=DecisionSettings()) == []
    ids = auto_record_tool_use(log, agent_id="a", calls=calls,
                               settings=DecisionSettings(auto_record=True, scope="pilot", ttl_days=7))
    assert len(ids) == 2
    assert {d.outcome for d in log.list(scope="pilot")} == {"good", "bad"}


def test_decision_settings_are_strict():
    from bauer.core.runtime.agno_capabilities.settings import CapabilitySettingsError

    ok = parse_settings({"capabilities": {"bauer.decisions": {"auto_record": True, "scope": "pilot"}}})
    assert ok.section("bauer.decisions").auto_record is True
    with pytest.raises(CapabilitySettingsError):
        parse_settings({"capabilities": {"bauer.decisions": {"store_reasoning": True}}})


def test_harness_scenarios_for_agno_evals_pass():
    from evals.harness.runner import rodar

    relatorio = rodar("56.")
    assert relatorio.total == 3
    assert all(v.passou for v in relatorio.vereditos), [(v.nome, v.detalhe, v.erro) for v in relatorio.vereditos]
