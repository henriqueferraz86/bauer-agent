"""Sprint 71 (plano 066): higiene — nomes de tools do Kanban e delete exato."""

from __future__ import annotations

import io
import re
import tokenize
from pathlib import Path

import pytest

import bauer.agent as agent_module
from bauer.sqlite_session_store import SqliteSessionStore, _parse_vector_source_id
from bauer.tool_router import ToolRouter
from bauer.vector_store import VectorStore


# ---------------------------------------------------------------------------
# Kanban: o prompt só cita tools que existem
# ---------------------------------------------------------------------------

def _kanban_names_in_string_literals(path: Path) -> set[str]:
    source = path.read_text(encoding="utf-8")
    names: set[str] = set()
    string_types = {tokenize.STRING}
    if hasattr(tokenize, "FSTRING_MIDDLE"):  # Python 3.12+
        string_types.add(tokenize.FSTRING_MIDDLE)
    for tok in tokenize.generate_tokens(io.StringIO(source).readline):
        if tok.type in string_types:
            names.update(re.findall(r"\bkanban_[a-z_]+\b", tok.string))
    return names


def test_agent_prompts_only_name_exposed_kanban_tools(tmp_path):
    exposed = set(ToolRouter(workspace=tmp_path, audit_enabled=False)._tools)
    cited = _kanban_names_in_string_literals(Path(agent_module.__file__))
    assert cited, "o prompt de Kanban deveria citar as tools do board"
    assert cited <= exposed, f"tools inexistentes citadas no prompt: {sorted(cited - exposed)}"
    assert {"kanban_read", "kanban_write"} <= cited


def test_worker_guidance_only_uses_actions_allowed_for_workers():
    from bauer.tools.kanban import KanbanToolsMixin

    guidance = agent_module.KANBAN_WORKER_GUIDANCE
    actions = set(re.findall(r'kanban_write\(action=\\?"(\w+)"', guidance))
    modes = set(re.findall(r'kanban_read\(mode=\\?"(\w+)"', guidance))
    assert actions and modes == {"show"}
    assert actions <= KanbanToolsMixin._WRITE_ACTIONS_ALLOWED_IN_WORKER


def test_ledger_block_names_real_tools(tmp_path):
    from bauer.workspace_manager import WorkspaceManager

    WorkspaceManager(str(tmp_path)).add_task("Fazer algo")
    block = agent_module._ledger_block(str(tmp_path))
    cited = set(re.findall(r"\bkanban_[a-z_]+\b", block))
    exposed = set(ToolRouter(workspace=tmp_path, audit_enabled=False)._tools)
    assert cited and cited <= exposed


# ---------------------------------------------------------------------------
# SqliteSessionStore.delete: vetores por identidade exata
# ---------------------------------------------------------------------------

class _Engine:
    backend = "stub"

    def embed(self, text: str) -> list[float]:
        return [1.0, float(len(text) % 5 + 1), 0.5]


@pytest.fixture
def vectors(monkeypatch):
    store = VectorStore(":memory:", engine=_Engine())
    monkeypatch.setattr("bauer.vector_store.get_default_store", lambda: store)
    return store


@pytest.mark.parametrize(
    ("victim", "neighbours"),
    [
        ("a", ["a:b", "A"]),
        ("tg:1", ["tg:12", "tg:1:extra"]),
        ("agent:x", ["agent:x:1"]),
    ],
)
def test_delete_removes_only_vectors_of_the_exact_session(tmp_path, vectors, victim, neighbours):
    sessions = SqliteSessionStore(tmp_path / "sessions", semantic_indexing_enabled=False)
    for sid in [victim, *neighbours]:
        sessions.save(sid, [{"role": "user", "content": f"msg {sid}"}])
        vectors.store(f"{sid}:user:0", "session_msg", f"msg {sid}")
        vectors.store(f"{sid}:assistant:1", "session_msg", f"resp {sid}")
    assert sessions.delete(victim)
    remaining = set(vectors.list_source_ids("session_msg"))
    assert not any(_parse_vector_source_id(s)[0] == victim for s in remaining)
    for sid in neighbours:
        assert {f"{sid}:user:0", f"{sid}:assistant:1"} <= remaining, sid
        assert sessions.exists(sid)


def test_vector_source_id_parses_session_ids_with_colons():
    assert _parse_vector_source_id("tg:123:user:4") == ("tg:123", "user")
    assert _parse_vector_source_id("abc:assistant:0") == ("abc", "assistant")
    assert _parse_vector_source_id("abc:user") is None
    assert _parse_vector_source_id("abc:user:x") is None


def test_semantic_search_finds_channel_sessions_with_colon_ids(tmp_path, vectors):
    sessions = SqliteSessionStore(tmp_path / "sessions", semantic_indexing_enabled=False)
    sessions.save("tg:42", [{"role": "user", "content": "pergunta sobre deploy"}])
    vectors.store("tg:42:user:0", "session_msg", "pergunta sobre deploy")
    # Direto no caminho vetorial (o FTS mascararia a falha do parse).
    hits = sessions._vector_search("pergunta sobre deploy", 3, None)
    assert [(hit["session_id"], hit["role"]) for hit in hits] == [("tg:42", "user")]
