"""Testes para AgentRegistry — save, get, list, delete, valid_name."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from bauer.agent_registry import (
    ALL_TOOLS,
    DEFAULT_TOOLS,
    PERSONAS,
    AgentDef,
    AgentRegistry,
)


# ─── Fixtures ────────────────────────────────────────────────────────────────


def _make_registry(tmp_path: Path) -> AgentRegistry:
    return AgentRegistry(path=str(tmp_path / "agents.yaml"))


def _sample_agent(name: str = "test-agent") -> AgentDef:
    return AgentDef(
        name=name,
        description="Agente de teste",
        system="Você é um agente de teste.",
        tools=["list_dir", "read_file"],
        model="phi4-mini",
        provider="ollama",
    )


# ─── AgentDef.valid_name ─────────────────────────────────────────────────────


def test_valid_name_ok():
    assert AgentDef.valid_name("meu-agent")
    assert AgentDef.valid_name("python3")
    assert AgentDef.valid_name("ab")


def test_valid_name_too_short():
    assert not AgentDef.valid_name("a")
    assert not AgentDef.valid_name("")


def test_valid_name_uppercase():
    assert not AgentDef.valid_name("MeuAgent")


def test_valid_name_spaces():
    assert not AgentDef.valid_name("meu agent")


def test_valid_name_max_length():
    assert AgentDef.valid_name("a" * 31)
    assert not AgentDef.valid_name("a" * 32)


# ─── AgentDef dataclass ──────────────────────────────────────────────────────


def test_agentdef_defaults():
    a = AgentDef(name="x1", description="d", system="s")
    assert a.tools == DEFAULT_TOOLS
    assert a.capabilities == []
    assert a.lane == ""
    assert a.max_concurrent == 1
    assert a.priority_weight == 1
    assert a.model == ""
    assert a.provider == ""
    assert a.created_at  # deve ter timestamp


def test_agentdef_to_dict():
    a = _sample_agent()
    d = a.to_dict()
    assert d["name"] == a.name
    assert d["description"] == a.description
    assert d["system"] == a.system
    assert d["tools"] == a.tools


def test_agentdef_from_dict_roundtrip():
    original = _sample_agent()
    original.capabilities = ["python", "tests"]
    original.lane = "dev"
    original.max_concurrent = 2
    original.priority_weight = 3
    d = original.to_dict()
    restored = AgentDef.from_dict(d)
    assert restored.name == original.name
    assert restored.system == original.system
    assert restored.tools == original.tools
    assert restored.capabilities == ["python", "tests"]
    assert restored.lane == "dev"
    assert restored.max_concurrent == 2
    assert restored.priority_weight == 3


def test_agentdef_from_dict_defaults():
    a = AgentDef.from_dict({"name": "minimal", "description": "d", "system": "s"})
    assert a.tools == DEFAULT_TOOLS
    assert a.model == ""


# ─── AgentRegistry.save / get ────────────────────────────────────────────────


def test_save_creates_yaml(tmp_path):
    reg = _make_registry(tmp_path)
    agent = _sample_agent()
    reg.save(agent)
    agents_file = tmp_path / "agents.yaml"
    assert agents_file.exists()
    data = yaml.safe_load(agents_file.read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    assert any(a["name"] == agent.name for a in data["agents"])


def test_get_existing(tmp_path):
    reg = _make_registry(tmp_path)
    agent = _sample_agent("meu-agent")
    reg.save(agent)
    loaded = reg.get("meu-agent")
    assert loaded is not None
    assert loaded.name == "meu-agent"
    assert loaded.description == agent.description


def test_get_not_found(tmp_path):
    reg = _make_registry(tmp_path)
    assert reg.get("nao-existe") is None


def test_get_no_file(tmp_path):
    reg = _make_registry(tmp_path)
    assert reg.get("qualquer") is None


def test_list_agents_ignores_malformed_entries(tmp_path):
    agents_file = tmp_path / "agents.yaml"
    agents_file.write_text(
        """
agents:
  - broken-string
  - description: sem nome
  - name: valid-agent
    description: ok
    system: ok
""".strip(),
        encoding="utf-8",
    )
    agents = AgentRegistry(agents_file).list_agents()
    assert [agent.name for agent in agents] == ["valid-agent"]


def test_save_overwrites_existing(tmp_path):
    reg = _make_registry(tmp_path)
    reg.save(_sample_agent("meu-agent"))
    updated = AgentDef(name="meu-agent", description="Nova desc", system="novo system")
    reg.save(updated)
    loaded = reg.get("meu-agent")
    assert loaded.description == "Nova desc"


def test_save_multiple_agents(tmp_path):
    reg = _make_registry(tmp_path)
    for name in ["alpha", "beta", "gamma"]:
        reg.save(_sample_agent(name))
    agents = reg.list_agents()
    names = {a.name for a in agents}
    assert names == {"alpha", "beta", "gamma"}


# ─── AgentRegistry.list_agents ───────────────────────────────────────────────


def test_list_agents_empty(tmp_path):
    reg = _make_registry(tmp_path)
    assert reg.list_agents() == []


def test_list_agents_returns_all(tmp_path):
    reg = _make_registry(tmp_path)
    reg.save(_sample_agent("agent-a"))
    reg.save(_sample_agent("agent-b"))
    agents = reg.list_agents()
    assert len(agents) == 2


# ─── AgentRegistry.delete ────────────────────────────────────────────────────


def test_delete_existing(tmp_path):
    reg = _make_registry(tmp_path)
    reg.save(_sample_agent("to-delete"))
    assert reg.delete("to-delete") is True
    assert reg.get("to-delete") is None


def test_delete_not_found(tmp_path):
    reg = _make_registry(tmp_path)
    assert reg.delete("nao-existe") is False


def test_delete_keeps_other_agents(tmp_path):
    reg = _make_registry(tmp_path)
    reg.save(_sample_agent("keep-me"))
    reg.save(_sample_agent("delete-me"))
    reg.delete("delete-me")
    agents = reg.list_agents()
    assert len(agents) == 1
    assert agents[0].name == "keep-me"


# ─── PERSONAS e constantes ───────────────────────────────────────────────────


def test_personas_have_required_keys():
    for name, persona in PERSONAS.items():
        assert "description" in persona, f"Persona '{name}' sem 'description'"
        assert "system" in persona, f"Persona '{name}' sem 'system'"
        assert persona["description"]
        assert persona["system"]


def test_default_tools_subset_of_all():
    assert set(DEFAULT_TOOLS).issubset(set(ALL_TOOLS))


def test_all_tools_list_not_empty():
    assert len(ALL_TOOLS) >= 4


# ─── SessionStore (bonus — 0% de cobertura) ──────────────────────────────────


def test_session_store_save_and_load(tmp_path):
    from bauer.session_store import SessionStore
    store = SessionStore(sessions_dir=tmp_path / "sessions")
    msgs = [
        {"role": "user", "content": "olá"},
        {"role": "assistant", "content": "oi"},
    ]
    store.save("sess01", msgs)
    loaded = store.load("sess01")
    assert loaded == msgs


def test_session_store_load_not_found(tmp_path):
    from bauer.session_store import SessionStore
    store = SessionStore(sessions_dir=tmp_path / "sessions")
    assert store.load("inexistente") == []


def test_session_store_list_sessions(tmp_path):
    from bauer.session_store import SessionStore
    store = SessionStore(sessions_dir=tmp_path / "sessions")
    store.save("s1", [{"role": "user", "content": "a"}])
    store.save("s2", [{"role": "user", "content": "b"}])
    sessions = store.list_sessions()
    assert "s1" in sessions
    assert "s2" in sessions


def test_session_store_delete(tmp_path):
    from bauer.session_store import SessionStore
    store = SessionStore(sessions_dir=tmp_path / "sessions")
    store.save("del-me", [])
    assert store.delete("del-me") is True
    assert not store.exists("del-me")


def test_session_store_delete_not_found(tmp_path):
    from bauer.session_store import SessionStore
    store = SessionStore(sessions_dir=tmp_path / "sessions")
    assert store.delete("nao-existe") is False


def test_session_store_new_id_unique(tmp_path):
    from bauer.session_store import SessionStore
    store = SessionStore(sessions_dir=tmp_path / "sessions")
    ids = {store.new_id() for _ in range(100)}
    assert len(ids) > 90  # IDs praticamente únicos


def test_session_store_handles_corrupted_line(tmp_path):
    from bauer.session_store import SessionStore
    store = SessionStore(sessions_dir=tmp_path / "sessions")
    p = tmp_path / "sessions" / "corrupt.jsonl"
    p.write_text('{"role": "user", "content": "ok"}\nNAO_E_JSON\n{"role": "assistant", "content": "resp"}\n', encoding="utf-8")
    msgs = store.load("corrupt")
    assert len(msgs) == 2  # linha corrompida ignorada
