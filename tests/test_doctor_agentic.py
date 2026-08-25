"""Diagnósticos agênticos do doctor sem provider, Ollama ou filesystem global."""

from __future__ import annotations

from types import SimpleNamespace


def _cfg(workspace="."):
    return SimpleNamespace(
        tools=SimpleNamespace(tool_allowlist=["read_file"]),
        agent=SimpleNamespace(workspace=str(workspace)),
    )


def test_agentic_notes_report_effective_allowlist_bridge_and_factory(monkeypatch, tmp_path):
    from bauer import app_factory
    from bauer.cli import _agentic_doctor_notes

    project = tmp_path / "acme"
    project.mkdir()
    monkeypatch.setattr(
        "bauer.commands._runtime._effective_tool_allowlist", lambda cfg: ["read_file"],
    )
    monkeypatch.setattr(app_factory, "get_active_project", lambda workspace: project)
    monkeypatch.setattr(app_factory, "is_governed", lambda candidate: True)
    monkeypatch.setattr(
        app_factory, "current_gate", lambda candidate: SimpleNamespace(slug="planning"),
    )
    monkeypatch.setattr(app_factory, "missing_planning_docs", lambda candidate: ["SPEC.md"])

    notes = _agentic_doctor_notes(_cfg(tmp_path), SimpleNamespace(tool_mode="bridge"))

    assert any("app_factory_init/status" in note for note in notes)
    assert any("Tool mode = bridge" in note for note in notes)
    assert "App Factory ativo: acme | gate planning | docs pendentes: SPEC.md" in notes


def test_agentic_notes_are_best_effort_and_do_not_warn_when_factory_is_exposed(monkeypatch):
    from bauer.cli import _agentic_doctor_notes

    monkeypatch.setattr(
        "bauer.commands._runtime._effective_tool_allowlist",
        lambda cfg: ["app_factory_init", "app_factory_status"],
    )

    notes = _agentic_doctor_notes(_cfg(), SimpleNamespace(tool_mode="native"))

    assert notes == []
