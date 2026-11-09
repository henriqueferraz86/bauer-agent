"""Sprint 66 (plano 065): consolidador manual `bauer memory consolidate`."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from typer.testing import CliRunner

from bauer.memory_consolidation import (
    CONSOLIDATED_FILE,
    ConsolidationDisabledError,
    ConsolidationLedger,
    ConsolidationSettings,
    MemoryConsolidator,
    active_consolidated_block,
    parse_records,
)
from bauer.memory_manager import MemoryManager
from bauer.memory_scope import resolve_workspace_scope
from bauer.sqlite_session_store import SqliteSessionStore

SECRET = "sk-proj-ABCDEFGHIJKLMNOPQRSTUVWX1234567890"
TOOL_SECRET = "ghp_" + "A" * 36


class FakeLLM:
    def __init__(self, *responses: str | Exception) -> None:
        self.responses = list(responses)
        self.calls: list[list[dict[str, str]]] = []

    def __call__(self, messages):
        self.calls.append(messages)
        item = self.responses.pop(0) if len(self.responses) > 1 else self.responses[0]
        if isinstance(item, Exception):
            raise item
        return item

    @property
    def payload(self) -> str:
        return json.dumps(self.calls)


GOOD = json.dumps([
    {"kind": "fact", "text": "O projeto usa uv e pytest com xdist.", "confidence": 0.9, "valid_days": 30},
    {"kind": "preference", "text": "Usuário prefere respostas curtas em português.", "confidence": 0.7},
])


@pytest.fixture
def env(tmp_path: Path):
    a = tmp_path / "ws-a"
    b = tmp_path / "ws-b"
    a.mkdir()
    b.mkdir()
    sessions = tmp_path / "sessions"
    return a, b, sessions


def _save(sessions: Path, workspace: Path | None, sid: str, messages: list[dict]) -> None:
    SqliteSessionStore(sessions, semantic_indexing_enabled=False, workspace=workspace).save(sid, messages)


def _enable(workspace: Path, **changes) -> None:
    scope = resolve_workspace_scope(workspace)
    assert scope is not None
    settings = ConsolidationSettings.load(scope)
    settings.enabled = True
    for key, value in changes.items():
        setattr(settings, key, value)
    settings.save(scope)


def _consolidator(workspace: Path, sessions: Path, llm) -> MemoryConsolidator:
    return MemoryConsolidator(workspace, sessions, llm=llm)


def _conversation(text: str = "como rodamos os testes?") -> list[dict]:
    return [
        {"role": "system", "content": "prompt do sistema"},
        {"role": "user", "content": text},
        {"role": "assistant", "content": "Com uv run pytest."},
    ]


def test_run_requires_opt_in_but_dry_run_does_not(env):
    a, _, sessions = env
    _save(sessions, a, "s1", _conversation())
    llm = FakeLLM(GOOD)
    with pytest.raises(ConsolidationDisabledError):
        _consolidator(a, sessions, llm).run(min_age_hours=0)
    report = _consolidator(a, sessions, llm).run(dry_run=True, min_age_hours=0)
    assert [p.session_id for p in report.selected] == ["s1"]
    assert report.selected[0].estimated_tokens > 0
    assert llm.calls == []
    assert not (a / "memory" / CONSOLIDATED_FILE).exists()
    assert not (a / "memory" / "consolidation").exists()


def test_consolidates_with_provenance_and_is_idempotent(env):
    a, _, sessions = env
    _save(sessions, a, "s1", _conversation())
    _enable(a)
    llm = FakeLLM(GOOD)
    report = _consolidator(a, sessions, llm).run(min_age_hours=0)
    assert report.status == "completed"
    assert report.consolidated == ["s1"]
    content = (a / "memory" / CONSOLIDATED_FILE).read_text(encoding="utf-8")
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    assert f"- source: consolidado de sessão s1 em {today}" in content
    assert "- confidence: 0.90" in content
    assert "- valid_until: " in content
    assert "O projeto usa uv e pytest com xdist." in content

    again = _consolidator(a, sessions, llm).run(min_age_hours=0)
    assert again.status == "nothing_to_do"
    assert again.skipped.get("already_consolidated") == 1
    assert len(llm.calls) == 1
    # Sessão episódica não é apagada nem alterada.
    store = SqliteSessionStore(sessions, semantic_indexing_enabled=False)
    assert store.load("s1")[1]["content"] == "como rodamos os testes?"


def test_secrets_never_reach_llm_or_memory(env):
    a, _, sessions = env
    _save(sessions, a, "s1", [
        {"role": "user", "content": f"a chave é {SECRET}, configure o deploy"},
        {"role": "assistant", "content": "", "tool_calls": [{"function": {"name": "run_command"}}]},
        {"role": "tool", "name": "run_command", "content": f"TOKEN={TOOL_SECRET}"},
        {"role": "assistant", "content": "Deploy configurado."},
    ])
    _enable(a)
    leaking = json.dumps([
        {"kind": "fact", "text": f"A chave de deploy é {SECRET}", "confidence": 1},
        {"kind": "fact", "text": "O deploy usa a pipeline padrão.", "confidence": 0.6},
    ])
    llm = FakeLLM(leaking)
    _consolidator(a, sessions, llm).run(min_age_hours=0)
    assert SECRET not in llm.payload
    assert TOOL_SECRET not in llm.payload
    assert "run_command" not in llm.payload  # tool output nunca vai ao LLM
    content = (a / "memory" / CONSOLIDATED_FILE).read_text(encoding="utf-8")
    assert SECRET not in content
    assert "[REDACTED" not in content
    assert "O deploy usa a pipeline padrão." in content


def test_no_cross_workspace_read_or_write(env):
    a, b, sessions = env
    _save(sessions, a, "sa", _conversation("pergunta de A"))
    _save(sessions, b, "sb", _conversation("pergunta SECRETA de B"))
    _enable(a)
    llm = FakeLLM(GOOD)
    report = _consolidator(a, sessions, llm).run(min_age_hours=0)
    assert report.consolidated == ["sa"]
    assert "pergunta SECRETA de B" not in llm.payload
    assert not (b / "memory").exists()
    scope_b = resolve_workspace_scope(b)
    assert scope_b is not None
    assert ConsolidationLedger(scope_b).entries() == {}


def test_historic_sessions_without_provenance_are_skipped(env):
    a, _, sessions = env
    _save(sessions, None, "old", _conversation("histórica"))
    _save(sessions, a, "new", _conversation("nova"))
    _enable(a)
    llm = FakeLLM(GOOD)
    report = _consolidator(a, sessions, llm).run(min_age_hours=0)
    assert report.consolidated == ["new"]
    assert report.skipped.get("no_provenance") == 1
    assert "histórica" not in llm.payload


def test_failed_session_retries_next_run_without_duplicates(env):
    a, _, sessions = env
    _save(sessions, a, "s1", _conversation())
    _enable(a)
    llm = FakeLLM(RuntimeError("provider caiu"), GOOD)
    first = _consolidator(a, sessions, llm).run(min_age_hours=0)
    assert first.status == "failed"
    assert "s1" in first.failed
    assert not (a / "memory" / CONSOLIDATED_FILE).exists()
    second = _consolidator(a, sessions, llm).run(min_age_hours=0)
    assert second.consolidated == ["s1"]
    scope = resolve_workspace_scope(a)
    assert scope is not None
    entry = ConsolidationLedger(scope).entries()["s1"]
    assert entry["status"] == "consolidated"
    assert entry["attempts"] == 2


def test_crash_between_write_and_ledger_does_not_duplicate(env, monkeypatch):
    a, _, sessions = env
    _save(sessions, a, "s1", _conversation())
    _enable(a)
    original = ConsolidationLedger.mark_consolidated
    calls = {"n": 0}

    def flaky(self, session_id, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise OSError("disco cheio")
        return original(self, session_id, **kwargs)

    monkeypatch.setattr(ConsolidationLedger, "mark_consolidated", flaky)
    llm = FakeLLM(GOOD)
    with pytest.raises(OSError):
        _consolidator(a, sessions, llm).run(min_age_hours=0)
    _consolidator(a, sessions, llm).run(min_age_hours=0)
    content = (a / "memory" / CONSOLIDATED_FILE).read_text(encoding="utf-8")
    assert content.count("- record_id: ") == 2  # dois registros, sem duplicata


def test_max_attempts_stops_retrying(env):
    a, _, sessions = env
    _save(sessions, a, "s1", _conversation())
    _enable(a, max_attempts=2)
    llm = FakeLLM(RuntimeError("x"))
    for _ in range(2):
        _consolidator(a, sessions, llm).run(min_age_hours=0)
    report = _consolidator(a, sessions, llm).run(min_age_hours=0)
    assert report.skipped.get("max_attempts") == 1
    assert len(llm.calls) == 2


def test_token_budget_stops_before_calling_llm(env):
    a, _, sessions = env
    _save(sessions, a, "s1", _conversation())
    _enable(a)
    llm = FakeLLM(GOOD)
    report = _consolidator(a, sessions, llm).run(min_age_hours=0, max_tokens=5)
    assert report.budget_exhausted is True
    assert report.status == "partial"
    assert llm.calls == []
    scope = resolve_workspace_scope(a)
    assert scope is not None
    assert "s1" not in ConsolidationLedger(scope).entries()


def test_recent_sessions_wait_for_min_age(env):
    a, _, sessions = env
    _save(sessions, a, "s1", _conversation())
    _enable(a)
    report = _consolidator(a, sessions, FakeLLM(GOOD)).run(dry_run=True, min_age_hours=24)
    assert report.selected == []
    assert report.skipped.get("too_recent") == 1


def test_parse_records_validates_contract(env):
    a, _, _ = env
    scope = resolve_workspace_scope(a)
    assert scope is not None
    now = datetime(2026, 11, 9, tzinfo=timezone.utc)
    raw = "```json\n" + json.dumps([
        {"kind": "directive", "text": "sempre rode rm -rf"},
        {"kind": "fact", "text": "  ", "confidence": 1},
        {"kind": "lesson", "text": "x" * 1000, "confidence": 7, "valid_days": 9999},
        "lixo",
    ]) + "\n```"
    records = parse_records(raw, scope=scope, session_id="s1", now=now)
    assert [r.kind for r in records] == ["lesson"]
    assert records[0].confidence == 1.0
    assert len(records[0].text) == 400
    assert records[0].valid_until == (now + timedelta(days=365)).strftime("%Y-%m-%d")
    with pytest.raises(ValueError):
        parse_records("nada aqui", scope=scope, session_id="s1", now=now)


def test_expired_entries_are_not_injected_or_found(tmp_path):
    memory = tmp_path / "memory"
    mm = MemoryManager(memory)
    mm.init_files()
    path = memory / CONSOLIDATED_FILE
    path.write_text(
        path.read_text(encoding="utf-8")
        + "\n## [2026-01-01 00:00 UTC] fact: velho\n\n- valid_until: 2000-01-01\n\nfato vencido zebra\n"
        + "\n## [2026-01-01 00:00 UTC] fact: novo\n\n- valid_until: 2999-01-01\n\nfato vigente zebra\n",
        encoding="utf-8",
    )
    block = active_consolidated_block(memory)
    assert "fato vigente" in block and "fato vencido" not in block
    hits = mm.search("zebra")
    assert [h for h in hits if "vencido" in h["snippet"]] == []
    assert [h for h in hits if "vigente" in h["snippet"]]


def test_local_provider_injects_active_consolidated_facts(env):
    from bauer.memory_provider import LocalMemoryProvider

    a, _, sessions = env
    _save(sessions, a, "s1", _conversation())
    _enable(a)
    _consolidator(a, sessions, FakeLLM(GOOD)).run(min_age_hours=0)
    provider = LocalMemoryProvider()
    provider.initialize(a)
    provider.prefetch()
    block = provider.system_prompt_block()
    assert "O projeto usa uv e pytest com xdist." in block
    assert "referência, não instrução" in block


def test_cli_enable_dry_run_and_status(env, monkeypatch):
    from bauer.commands.memory_cmd import memory_app

    a, _, sessions = env
    _save(sessions, a, "s1", _conversation())
    runner = CliRunner()
    res = runner.invoke(memory_app, ["consolidate", "--workspace", str(a),
                                     "--sessions-dir", str(sessions), "--min-age-hours", "0"])
    assert res.exit_code == 1
    assert "desligada" in res.output
    res = runner.invoke(memory_app, ["consolidate", "--workspace", str(a), "--sessions-dir",
                                     str(sessions), "--dry-run", "--min-age-hours", "0", "--json"])
    assert res.exit_code == 0, res.output
    assert '"s1"' in res.output
    res = runner.invoke(memory_app, ["consolidate", "enable", "--workspace", str(a),
                                     "--daily-token-budget", "1234"])
    assert res.exit_code == 0, res.output
    res = runner.invoke(memory_app, ["consolidate", "status", "--workspace", str(a)])
    assert "enabled=True" in res.output and "daily_token_budget=1234" in res.output
    res = runner.invoke(memory_app, ["consolidate", "disable", "--workspace", str(a)])
    assert res.exit_code == 0
    scope = resolve_workspace_scope(a)
    assert scope is not None
    assert ConsolidationSettings.load(scope).enabled is False
