"""Roteamento heurístico por turno na CLI `bauer agent` (Fase 12).

Espelho do comportamento do serve: opt-in via model.router_enabled +
model.profiles; cada turno escolhe o modelo do tier via classify_task.
Conservador: sem profiles/sem client → o turno usa o modelo padrão.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from bauer.model_router import ModelProfile
from bauer.tool_router import ToolRouter


def _capture_client(seen: dict, provider: str = "openrouter") -> MagicMock:
    """Mock de client que grava o modelo de cada chamada e responde texto."""
    client = MagicMock()

    def _cap(model, messages, *a, **k):
        seen.setdefault("models", []).append(model)
        return iter(["ok"])

    client.chat_stream.side_effect = _cap
    client._provider = provider
    return client


_PROFILES = {
    "fast": ModelProfile(name="fast", provider="openrouter", model="deepseek/deepseek-v4-flash"),
    "coding": ModelProfile(name="coding", provider="openrouter", model="qwen/qwen3-coder-flash"),
    "heavy": ModelProfile(name="heavy", provider="openrouter", model="deepseek/deepseek-r1"),
}


def _run_session(tmp_path: Path, message: str, *, profiles=None, route_client_fn=None):
    from bauer.agent import run_agent_session
    from rich.console import Console

    ws = tmp_path / "ws"
    ws.mkdir(exist_ok=True)
    router = ToolRouter(workspace=ws)
    seen: dict = {}
    client = _capture_client(seen)
    console = Console(record=True, width=120)

    with patch("builtins.input", side_effect=[message, EOFError]), \
         patch("bauer.agent._try_parse_tool", return_value=None):
        run_agent_session(
            client, "primary-model", 4096, console, router,
            route_profiles=profiles, route_client_fn=route_client_fn,
        )
    return seen, console.export_text()


def test_no_profiles_uses_primary_model(tmp_path: Path):
    seen, _ = _run_session(tmp_path, "oi, tudo bem?")
    assert seen["models"] == ["primary-model"]


def test_conversation_routes_to_fast(tmp_path: Path):
    seen, out = _run_session(tmp_path, "oi, tudo bem?", profiles=_PROFILES)
    assert seen["models"] == ["deepseek/deepseek-v4-flash"]
    assert "tier fast" in out  # chip visível no console


def test_coding_routes_to_coding_tier(tmp_path: Path):
    seen, _ = _run_session(
        tmp_path, "crie um script python que ordena uma lista", profiles=_PROFILES
    )
    assert seen["models"] == ["qwen/qwen3-coder-flash"]


def test_architecture_routes_to_heavy(tmp_path: Path):
    seen, _ = _run_session(
        tmp_path,
        "redesenhe a arquitetura do sistema inteiro para escalar com múltiplos backends",
        profiles=_PROFILES,
    )
    assert seen["models"] == ["deepseek/deepseek-r1"]


def test_tier_without_profile_falls_back_to_primary(tmp_path: Path):
    # Só profile heavy — conversa (tier fast) não tem profile → primário.
    only_heavy = {"heavy": _PROFILES["heavy"]}
    seen, _ = _run_session(tmp_path, "oi, tudo bem?", profiles=only_heavy)
    assert seen["models"] == ["primary-model"]


def test_other_provider_uses_route_client_fn(tmp_path: Path):
    other_seen: dict = {}
    other = _capture_client(other_seen, provider="ollama")
    profiles = {"fast": ModelProfile(name="fast", provider="ollama", model="qwen3:0.6b")}

    def factory(provider: str):
        assert provider == "ollama"
        return other

    seen, _ = _run_session(tmp_path, "oi, tudo bem?", profiles=profiles,
                           route_client_fn=factory)
    # turno rodou no client do outro provider, não no da sessão
    assert other_seen["models"] == ["qwen3:0.6b"]
    assert "models" not in seen


def test_other_provider_without_factory_falls_back(tmp_path: Path):
    profiles = {"fast": ModelProfile(name="fast", provider="ollama", model="qwen3:0.6b")}
    seen, _ = _run_session(tmp_path, "oi, tudo bem?", profiles=profiles)
    assert seen["models"] == ["primary-model"]


def test_routed_client_not_sticky_across_turns(tmp_path: Path):
    """O client roteado vale só para o turno — o seguinte volta ao da sessão."""
    from bauer.agent import run_agent_session
    from rich.console import Console

    ws = tmp_path / "ws"
    ws.mkdir(exist_ok=True)
    router = ToolRouter(workspace=ws)
    seen: dict = {}
    client = _capture_client(seen)
    other_seen: dict = {}
    other = _capture_client(other_seen, provider="ollama")
    profiles = {"coding": ModelProfile(name="coding", provider="ollama", model="qwen3-coder")}

    with patch("builtins.input", side_effect=["crie um script python", "qual a capital da frança e por que ela é historicamente relevante?", EOFError]), \
         patch("bauer.agent._try_parse_tool", return_value=None):
        run_agent_session(
            client, "primary-model", 4096, Console(record=True, width=120), router,
            route_profiles=profiles, route_client_fn=lambda p: other,
        )
    assert other_seen["models"] == ["qwen3-coder"]     # turno 1 roteado
    assert seen["models"] == ["primary-model"]         # turno 2 de volta na sessão


# ─── heuristic_route_kit (helper do agent_cmd) ───────────────────────────────


def test_route_kit_disabled_returns_none():
    from bauer.commands._runtime import heuristic_route_kit
    from bauer.config_loader import BauerConfig, ModelSection

    cfg = BauerConfig(model=ModelSection(provider="openrouter", name="x"))
    profiles, factory = heuristic_route_kit(cfg)
    assert profiles is None and factory is None


def test_route_kit_enabled_returns_profiles_and_factory():
    from bauer.commands._runtime import heuristic_route_kit
    from bauer.config_loader import BauerConfig, ModelProfileSpec, ModelSection

    cfg = BauerConfig(model=ModelSection(
        provider="openrouter", name="x", router_enabled=True,
        profiles={"fast": ModelProfileSpec(provider="openrouter", model="deepseek/deepseek-v4-flash")},
    ))
    profiles, factory = heuristic_route_kit(cfg)
    assert profiles is not None and "fast" in profiles
    assert callable(factory)


def test_route_kit_enabled_without_profiles_returns_none():
    from bauer.commands._runtime import heuristic_route_kit
    from bauer.config_loader import BauerConfig, ModelSection

    cfg = BauerConfig(model=ModelSection(provider="openrouter", name="x", router_enabled=True))
    profiles, factory = heuristic_route_kit(cfg)
    assert profiles is None and factory is None
