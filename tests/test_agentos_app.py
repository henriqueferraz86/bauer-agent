import pytest

pytest.importorskip("agno")

from bauer.agentos_app import build_agentos_app


def test_agentos_app_builds_from_empty_catalog(tmp_path):
    config = tmp_path / "config.yaml"
    config.write_text("model:\n  provider: openai\n  name: gpt-5.6-luna\n", encoding="utf-8")
    agents = tmp_path / "agents.yaml"
    agents.write_text("agents: []\n", encoding="utf-8")

    app = build_agentos_app(
        config_path=config,
        workspace=tmp_path / "workspace",
        runtime_root=tmp_path / "runtime",
        agent_roots=[agents],
        team_roots=[tmp_path / "teams"],
    )

    paths = {route.path for route in app.routes}
    assert "/docs" in paths
    assert "/health" in paths or "/config" in paths


def test_agentos_app_does_not_start_at_import():
    # The module exposes a factory; starting a listener is reserved for main().
    import bauer.agentos_app as module

    assert callable(module.build_agentos_app)
    assert callable(module.main)
