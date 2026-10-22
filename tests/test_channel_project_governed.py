"""Project turns keep real Kernel custody and metering across worker threads."""

from pathlib import Path

import pytest

from bauer.channel_base import AgentBackend, ChannelMessage
from bauer.core.runtime.autonomy import BudgetManager
from bauer.core.runtime.run_manager import RunManager
from bauer.cost_meter import cost_sink
from bauer.runtime_ids import get_runtime_ids
from bauer.sqlite_session_store import SqliteSessionStore
from bauer.tool_router import ToolRouter


def setup_backend(tmp_path, monkeypatch):
    from bauer import projects_registry

    monkeypatch.setattr(projects_registry, "_DEFAULT_REGISTRY", tmp_path / "projects.json")
    workspace = tmp_path / "default"
    workspace.mkdir()
    config = tmp_path / "config.yaml"
    config.write_text(f"agent:\n  workspace: {workspace.as_posix()}\n"
                      "model:\n  provider: ollama\n  name: fake\n"
                      "kernel:\n  enabled: true\n", encoding="utf-8")
    backend = AgentBackend(config, tmp_path / "memory" / "sessions")
    backend._client = object()
    backend._provider = "ollama"
    backend._model_name = "fake"
    backend._applied_context = 4096
    backend._router = ToolRouter(workspace=workspace)
    backend._store = SqliteSessionStore(backend.sessions_dir)
    backend._project_router_builder = lambda path: ToolRouter(workspace=path)
    project = tmp_path / "project"
    project.mkdir()
    entry = projects_registry.add_project(project, "Projeto isolado")
    msg = ChannelMessage(channel="telegram", user_id="42", chat_id="42", text="trabalhe",
                         raw={"message": {"chat": {"id": 42, "type": "private"}}})
    backend.select_channel_project(msg, entry["id"])
    return backend, msg, project


def test_project_worker_preserves_run_ids_cost_and_tool_policy_root(tmp_path, monkeypatch):
    backend, msg, project = setup_backend(tmp_path, monkeypatch)
    root = backend.sessions_dir.parent / "runtime"
    budget = BudgetManager(root=root)
    budget.set_profile(daily_budget_usd=1000)
    seen = []

    def turn(ctx, router, *_args, **_kwargs):
        session, run_id = get_runtime_ids()
        seen.append((session, run_id, Path(router._policy_root), Path(router.workspace)))
        assert cost_sink.get() is not None
        cost_sink.get()("openai", "fake", {"prompt_tokens": 10}, 0.25)
        router.execute({"action": "write_file", "args": {"path": "result.txt", "content": "ok"}})
        ctx.add_assistant("Trabalho concluído")
        return "Trabalho concluído", [{"tool": "write_file"}]

    monkeypatch.setattr("bauer.agent.run_one_turn_with_fallback", turn)
    assert backend.process(msg) == "Trabalho concluído"
    runs = RunManager(root=root).list_runs()
    assert len(runs) == 1
    run = runs[0]
    assert run.status == "completed"
    assert seen == [(run.session_id, run.id, root, project)]
    assert run.cost_estimate == pytest.approx(0.25)
    assert run.tool_calls_count == 1
    assert run.llm_calls_count == 1
    assert (project / "result.txt").read_text() == "ok"
    assert not (backend._router.workspace / "result.txt").exists()
    assert budget.get_profile().daily_budget_usd == 1000
    assert budget.status()["daily"]["used_usd"] == pytest.approx(0.25)
    assert get_runtime_ids() is None


def test_daily_zero_blocks_project_worker_before_provider(tmp_path, monkeypatch):
    backend, msg, project = setup_backend(tmp_path, monkeypatch)
    root = backend.sessions_dir.parent / "runtime"
    BudgetManager(root=root).set_profile(daily_budget_usd=0)

    def forbidden(*args, **kwargs):
        pytest.fail("A zero daily budget must prevent the model call")

    monkeypatch.setattr("bauer.agent.run_one_turn_with_fallback", forbidden)
    result = backend.process(msg)
    assert "budget" in result.lower()
    assert not (project / "result.txt").exists()
    runs = RunManager(root=root).list_runs()
    assert len(runs) == 1 and runs[0].status == "failed"


def test_lost_production_config_does_not_disable_governance(tmp_path, monkeypatch):
    from bauer.core.kernel import KernelWiringError

    backend, msg, _ = setup_backend(tmp_path, monkeypatch)
    backend._config_mtime = 1.0
    backend.config_path.unlink()
    with backend.project_scope(msg), pytest.raises(KernelWiringError):
        backend._resolver_kernel()


def test_project_session_search_never_reads_sibling_history_or_parent_files(tmp_path, monkeypatch):
    from bauer.runtime_ids import reset_runtime_ids, set_runtime_ids

    backend, msg, _ = setup_backend(tmp_path, monkeypatch)
    context = backend.project_context(msg)
    backend._store.save("tg-project-other", [{"role": "user", "content": "SIBLING_MARKER"}])
    backend._store.save(context.session_key, [{"role": "user", "content": "OWN_MARKER"}])
    (tmp_path / "other.json").write_text('"PARENT_MARKER"', encoding="utf-8")
    token = set_runtime_ids(context.session_key, None)
    try:
        result = context.router._session_search({"source": "sessions", "query": "MARKER"})
        assert "OWN_MARKER" in result
        assert "SIBLING_MARKER" not in result
        assert "PARENT_MARKER" not in result
    finally:
        reset_runtime_ids(token)
    assert context.router._search_session_files("OWN_MARKER") == []


@pytest.mark.parametrize("action", ["get", "set"])
def test_memory_symlink_cannot_cross_project_workspace(tmp_path, action):
    from bauer.tools.base import ToolError

    workspace = tmp_path / "alpha"
    workspace.mkdir()
    sibling = tmp_path / "beta.json"
    sibling.write_text('{"secret": {"value": "PRIVATE"}}', encoding="utf-8")
    try:
        (workspace / ".bauer_memory.json").symlink_to(sibling)
    except OSError:
        pytest.skip("Creating symlinks is unavailable on this platform")
    router = ToolRouter(workspace=workspace)
    with pytest.raises(ToolError, match="workspace"):
        router.execute({"action": "memory", "args": {"action": action, "key": "secret", "value": "change"}})
    assert sibling.read_text() == '{"secret": {"value": "PRIVATE"}}'
