from __future__ import annotations

from pathlib import Path

from bauer.memory_facade import UnifiedMemory
from bauer.memory_provider import MemoryProvider
from bauer.maturity import MaturityReport, build_maturity_report


def test_unified_memory_is_the_local_provider_contract():
    assert issubclass(UnifiedMemory, MemoryProvider)
    assert hasattr(UnifiedMemory, "on_turn_start")
    assert hasattr(UnifiedMemory, "system_prompt_block")


def test_maturity_report_has_explicit_gates(monkeypatch, tmp_path: Path):
    monkeypatch.setattr("bauer.maturity._ruff_issue_count", lambda root: (0, "0 avisos"))
    for relative in (
        "bauer/core/kernel/entry.py",
        "bauer/memory_facade.py",
        "bauer/routing_runtime.py",
        "bauer/decision_router.py",
        "tests/test_arquitetura_custodia_kernel.py",
        "tests/conftest.py",
    ):
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# fixture\n", encoding="utf-8")
    (tmp_path / "tests" / "test_one.py").write_text("def test_one(): pass\n", encoding="utf-8")

    report = build_maturity_report(tmp_path)

    assert isinstance(report, MaturityReport)
    assert report.score == 9.2
    assert all(report.gates.values())
