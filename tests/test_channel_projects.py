"""Project selection is explicit, pinned and isolated across gateway workers."""

from __future__ import annotations

import threading
from contextvars import ContextVar
from pathlib import Path

import pytest

from bauer import projects_registry as registry
from bauer.channel_base import AgentBackend, ChannelMessage
from bauer.channel_projects import ChannelProjectError


def message(text="oi", *, user="42", chat="42", private=True):
    return ChannelMessage("telegram", user, chat, text,
                          raw={"message": {"chat": {"type": "private" if private else "group"}}})


@pytest.fixture
def setup_projects(tmp_path, monkeypatch):
    from bauer.sqlite_session_store import SqliteSessionStore
    from bauer.tool_router import ToolRouter

    monkeypatch.setattr(registry, "_DEFAULT_REGISTRY", tmp_path / "projects.json")
    paths = [tmp_path / name for name in ("alpha", "beta")]
    entries = []
    for path in paths:
        path.mkdir()
        (path / "same.txt").write_text(path.name, encoding="utf-8")
        entries.append(registry.add_project(path))

    def make():
        backend = AgentBackend(tmp_path / "absent.yaml", tmp_path / "sessions")
        backend._client = object()
        backend._model_name = "default"
        backend._provider = "ollama"
        backend._applied_context = 8192
        default_path = tmp_path / "default"
        default_path.mkdir(exist_ok=True)
        backend._router = ToolRouter(default_path)
        backend._store = SqliteSessionStore(tmp_path / "sessions")
        backend._system_prompt = "legacy prompt"
        backend._project_router_builder = lambda path: ToolRouter(path)
        backend._resolver_kernel = lambda: None
        backend._providers_fetcher = lambda: ["ollama"]
        backend._models_fetcher = lambda: ["default", "other"]
        return backend

    return make, entries, paths


def test_selection_restart_and_operator_isolation(setup_projects):
    make, entries, paths = setup_projects
    backend = make()
    assert backend.project_context(message()) is None
    chosen = backend.select_channel_project(message(), entries[0]["id"])
    assert chosen.workspace == paths[0]
    assert backend.project_context(message(user="99")) is None
    assert backend.project_context(message(chat="99")) is None
    restarted = make()
    restored = restarted.project_context(message())
    assert restored.workspace == paths[0]
    assert restored.session_key == chosen.session_key


def test_groups_and_arbitrary_paths_cannot_select(setup_projects):
    make, entries, _ = setup_projects
    backend = make()
    with pytest.raises(ChannelProjectError, match="privada"):
        backend.select_channel_project(message(private=False), entries[0]["id"])
    with pytest.raises(ChannelProjectError):
        backend.select_channel_project(message(), "/tmp/arbitrary")


def test_removed_binding_blocks_instead_of_using_default(setup_projects, monkeypatch):
    make, entries, _ = setup_projects
    backend = make()
    backend.select_channel_project(message(), entries[0]["id"])
    registry.remove_project(entries[0]["id"])
    called = []
    monkeypatch.setattr("bauer.agent.run_one_turn_with_fallback", lambda *a, **kw: called.append(1))
    assert "Projeto removido" in backend.process(message("write file"))
    assert called == []


def test_changed_path_blocks_even_with_cached_router(setup_projects):
    make, entries, paths = setup_projects
    backend = make()
    backend.select_channel_project(message(), entries[0]["id"])
    data = registry.load_registry()
    data["projects"][0]["path"] = str(paths[1])
    registry.save_registry(data)
    assert "caminho" in backend.process(message()).lower()


def test_failed_project_router_never_falls_back(setup_projects):
    make, entries, _ = setup_projects
    backend = make()
    backend._project_router_builder = lambda path: (_ for _ in ()).throw(OSError("broken"))
    with pytest.raises(ChannelProjectError, match="Nenhuma ação"):
        backend.select_channel_project(message(), entries[0]["id"])
    assert backend.project_context(message()) is None


def test_captured_project_and_legacy_none_stay_fixed(setup_projects):
    make, entries, paths = setup_projects
    backend = make()
    legacy = message()
    backend.capture_project_context(legacy)
    backend.select_channel_project(message(), entries[0]["id"])
    active = message()
    backend.capture_project_context(active)
    backend.select_channel_project(message(), entries[1]["id"])
    assert backend.project_context(active).workspace == paths[0]
    assert backend.project_context(legacy) is None
    assert backend.project_context(message()).workspace == paths[1]


def test_parallel_workers_propagate_context_and_use_separate_workspaces(setup_projects, monkeypatch):
    from bauer.runtime_ids import get_runtime_ids, reset_runtime_ids, set_runtime_ids

    make, entries, paths = setup_projects
    backend = make()
    gate = threading.Barrier(2)
    observed = []
    results = []
    marker = ContextVar("project-test-marker", default=None)

    def fake_turn(ctx, router, client, model, fallbacks):
        gate.wait(timeout=5)
        value = (Path(router.workspace) / "same.txt").read_text(encoding="utf-8")
        router.execute({"action": "write_file", "args": {"path": "result.txt", "content": value}})
        observed.append((value, get_runtime_ids(), list(ctx.messages), marker.get()))
        ctx.add_assistant(value)
        return value, []

    monkeypatch.setattr("bauer.agent.run_one_turn_with_fallback", fake_turn)
    messages = []
    for entry in entries:
        backend.select_channel_project(message(), entry["id"])
        msg = message(f"question-{entry['name']}")
        backend.capture_project_context(msg)
        messages.append(msg)

    def run(msg):
        token = set_runtime_ids(backend.session_key(msg), "test-run")
        marker_token = marker.set(msg.text)
        try:
            results.append(backend.process(msg))
        finally:
            marker.reset(marker_token)
            reset_runtime_ids(token)

    workers = [threading.Thread(target=run, args=(msg,)) for msg in messages]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(timeout=10)
    assert sorted(results) == ["alpha", "beta"]
    assert len({entry[1][0] for entry in observed}) == 2
    # Each new admission replaces any parent's run ID, while arbitrary
    # per-turn context still crosses the child worker's thread boundary.
    assert all(entry[1][1] is None for entry in observed)
    assert {entry[3] for entry in observed} == {"question-alpha", "question-beta"}
    assert Path(backend._router.workspace) not in paths
    assert [(path / "result.txt").read_text(encoding="utf-8") for path in paths] == ["alpha", "beta"]
    for value, _, messages, _ in observed:
        contents = " ".join(str(item.get("content", "")) for item in messages)
        assert f"question-{value}" in contents
        assert f"question-{'beta' if value == 'alpha' else 'alpha'}" not in contents


def test_models_persist_and_are_separate_per_project(setup_projects):
    make, entries, _ = setup_projects
    backend = make()
    backend.select_channel_project(message(), entries[0]["id"])
    assert "other" in backend.process(message("/model other"))
    assert "other" in make().process(message("/status"))
    backend.select_channel_project(message(), entries[1]["id"])
    assert "Modelo: default" in backend.process(message("/status"))
    backend.select_channel_project(message(), entries[0]["id"])
    assert "Modelo: other" in backend.process(message("/status"))
    assert "padrão" in backend.process(message("/model reset"))
    assert "Modelo: default" in make().process(message("/status"))


def test_restored_model_is_also_used_for_context_compression(setup_projects):
    make, entries, _ = setup_projects
    backend = make()
    backend.select_channel_project(message(), entries[0]["id"])
    backend.process(message("/model other"))
    restarted = make()
    restarted.process(message("/status"))
    with restarted.project_scope(message()) as project:
        ctx, _ = restarted._get_session(project.session_key)
    assert ctx._llm_client is restarted._client
    assert ctx._llm_model == "other"


def test_captured_removed_project_blocks_before_any_execution(setup_projects, monkeypatch):
    make, entries, _ = setup_projects
    backend = make()
    backend.select_channel_project(message(), entries[0]["id"])
    queued = message("write file")
    backend.capture_project_context(queued)
    registry.remove_project(entries[0]["id"])
    calls = []
    monkeypatch.setattr("bauer.agent.run_one_turn_with_fallback", lambda *a, **kw: calls.append(1))
    assert "Projeto removido" in backend.process(queued)
    assert calls == []


def test_config_reload_restores_project_provider_and_compression_client(setup_projects, monkeypatch):
    from bauer.config_loader import BauerConfig

    make, entries, _ = setup_projects
    backend = make()
    backend.select_channel_project(message(), entries[0]["id"])
    backend.process(message("/model other"))
    with backend.project_scope(message()) as context:
        key = context.session_key
        ctx, _ = backend._get_session(key)
    backend.config_path.write_text("# reload fixture", encoding="utf-8")
    backend._config_mtime = 1.0
    cfg = BauerConfig(model={"provider": "openai", "name": "new-global"})
    monkeypatch.setattr("bauer.config_loader.load_config", lambda *a, **kw: cfg)
    global_client = object()
    previous_provider_client = object()
    backend._client_builder = lambda cfg: global_client
    backend._build_client_for_provider = lambda provider, model: previous_provider_client
    backend._maybe_reload()
    assert not backend._project_model_loaded
    with backend.project_scope(message()):
        backend._restore_project_model(key)
    assert backend._session_overrides[key] == (previous_provider_client, "other", "ollama")
    assert ctx._llm_client is previous_provider_client
    assert ctx._llm_model == "other"


def test_default_workspace_is_an_explicit_choice_only_when_registry_empty(setup_projects):
    make, entries, _ = setup_projects
    backend = make()
    with pytest.raises(ChannelProjectError):
        backend.select_channel_project(message(), "__default__")
    for entry in entries:
        registry.remove_project(entry["id"])
    offered = backend.list_channel_projects(message())
    assert offered[0]["id"] == "__default__"
    assert backend.project_context(message()) is None
    assert backend.select_channel_project(message(), "__default__").workspace == backend._router.workspace


def test_scope_cleanup_keeps_legacy_router(setup_projects):
    make, entries, paths = setup_projects
    backend = make()
    backend.select_channel_project(message(), entries[0]["id"])
    with pytest.raises(RuntimeError), backend.project_scope(message()) as context:
        assert context.workspace == paths[0]
        assert backend._effective_router() is context.router
        raise RuntimeError("stop")
    assert backend._effective_router() is backend._router


def test_legacy_channel_and_rawless_fakes_preserve_session_keys(setup_projects):
    make, entries, _ = setup_projects
    backend = make()
    backend.select_channel_project(message(), entries[0]["id"])
    assert backend.session_key(ChannelMessage("telegram", "42", "42", "oi")) == "tg:42"
    assert backend.session_key(ChannelMessage("discord", "42", "42", "oi")) == "dc:42"
