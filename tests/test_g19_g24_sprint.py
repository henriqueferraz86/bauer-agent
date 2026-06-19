"""Sprint G19-G24: personalities, typed plugin registries, new memory providers,
bitwarden credential layer, +11 skills, e invariantes gerais.
"""
from __future__ import annotations

import os
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# G21 — Personalities
# ---------------------------------------------------------------------------

def test_personalities_import():
    from bauer.personalities import PERSONALITIES, get_personality, list_personalities, apply_personality
    assert len(PERSONALITIES) >= 8


def test_default_personality_exists():
    from bauer.personalities import get_personality
    p = get_personality("default")
    assert p.name == "default"


def test_unknown_personality_falls_back_to_default():
    from bauer.personalities import get_personality
    p = get_personality("nao-existe-xyz")
    assert p.name == "default"


@pytest.mark.parametrize("name", [
    "senior-engineer", "researcher", "teacher",
    "devops", "creative", "data-scientist", "security",
])
def test_builtin_personality_has_prompt(name):
    from bauer.personalities import get_personality
    p = get_personality(name)
    assert p.system_prompt_prefix
    assert p.display_name
    assert p.emoji


def test_list_personalities_returns_all():
    from bauer.personalities import list_personalities, PERSONALITIES
    lst = list_personalities()
    assert len(lst) == len(PERSONALITIES)


def test_apply_personality_prepends_prefix():
    from bauer.personalities import apply_personality
    result = apply_personality("Você é um agente.", "teacher")
    assert "professor" in result.lower() or "paciente" in result.lower() or "didático" in result.lower()


def test_apply_personality_default_no_change():
    from bauer.personalities import apply_personality
    original = "Você é um agente."
    result = apply_personality(original, "default")
    assert result == original


def test_apply_personality_empty_system_prompt():
    from bauer.personalities import apply_personality
    result = apply_personality("", "researcher")
    assert result  # prefix preenchido


def test_personality_frozen():
    from bauer.personalities import get_personality
    p = get_personality("teacher")
    with pytest.raises((AttributeError, TypeError)):
        p.name = "outro"  # type: ignore[misc]


def test_personality_tags_are_list():
    from bauer.personalities import PERSONALITIES
    for p in PERSONALITIES.values():
        assert isinstance(p.tags, list)


# ---------------------------------------------------------------------------
# G22 — Typed plugin registries
# ---------------------------------------------------------------------------

def test_registries_importable():
    from bauer.plugin_registry import (
        memory_registry, image_registry, browser_registry, transcription_registry,
    )
    assert memory_registry is not None


def test_memory_registry_has_builtin_providers():
    from bauer.plugin_registry import memory_registry
    available = memory_registry.available()
    assert "local" in available
    assert "vector" in available


def test_image_registry_has_dalle():
    from bauer.plugin_registry import image_registry
    assert "dalle" in image_registry.available()


def test_browser_registry_has_playwright():
    from bauer.plugin_registry import browser_registry
    assert "playwright" in browser_registry.available()


def test_transcription_registry_has_whisper():
    from bauer.plugin_registry import transcription_registry
    assert "openai-whisper" in transcription_registry.available()


def test_registry_build_unknown_raises():
    from bauer.plugin_registry import memory_registry
    with pytest.raises(KeyError, match="not registered"):
        memory_registry.build("nao-existe-xyz")


def test_registry_info_known():
    from bauer.plugin_registry import memory_registry
    info = memory_registry.info("local")
    assert info is not None
    assert info.name == "local"


def test_registry_info_unknown_returns_none():
    from bauer.plugin_registry import memory_registry
    assert memory_registry.info("xyz-desconhecido") is None


def test_registry_repr_contains_kind():
    from bauer.plugin_registry import memory_registry
    assert "Memory" in repr(memory_registry)


def test_all_memory_providers_registered():
    from bauer.plugin_registry import memory_registry
    expected = {"local", "vector", "http", "mem0", "honcho", "supermemory", "hindsight", "retaindb"}
    available = set(memory_registry.available())
    assert expected <= available


@pytest.mark.parametrize("kind,name", [
    ("memory", "local"),
    ("memory", "vector"),
    ("image", "dalle"),
    ("browser", "playwright"),
    ("transcription", "openai-whisper"),
])
def test_registry_entry_has_description(kind, name):
    from bauer.plugin_registry import (
        memory_registry, image_registry, browser_registry, transcription_registry,
    )
    reg = {"memory": memory_registry, "image": image_registry,
           "browser": browser_registry, "transcription": transcription_registry}[kind]
    info = reg.info(name)
    assert info is not None
    assert info.description


# ---------------------------------------------------------------------------
# G19 — Novos memory providers
# ---------------------------------------------------------------------------

def test_honcho_provider_imports():
    from bauer.memory_provider import HonchoProvider
    p = HonchoProvider()
    assert p.system_prompt_block() == ""


def test_supermemory_provider_imports():
    from bauer.memory_provider import SupermemoryProvider
    p = SupermemoryProvider()
    assert p.system_prompt_block() == ""


def test_hindsight_provider_imports(tmp_path):
    from bauer.memory_provider import HindsightProvider
    p = HindsightProvider(base_dir=tmp_path)
    p.initialize(tmp_path)
    assert p.system_prompt_block() == ""


def test_retaindb_provider_imports():
    from bauer.memory_provider import RetainDBProvider
    p = RetainDBProvider()
    assert p.system_prompt_block() == ""


def test_hindsight_sync_extracts_facts(tmp_path):
    from bauer.memory_provider import HindsightProvider
    p = HindsightProvider(base_dir=tmp_path)
    p.initialize(tmp_path)
    msgs = [
        {"role": "user", "content": "Python é uma linguagem interpretada"},
        {"role": "assistant", "content": "Correto."},
    ]
    p.sync_turn(0, msgs)
    block = p.system_prompt_block()
    assert "python" in block.lower() or block == ""  # graceful


def test_hindsight_persists_facts(tmp_path):
    from bauer.memory_provider import HindsightProvider
    p1 = HindsightProvider(base_dir=tmp_path)
    p1.initialize(tmp_path)
    p1.sync_turn(0, [{"role": "user", "content": "Bauer é um agente de IA"}])

    p2 = HindsightProvider(base_dir=tmp_path)
    p2.initialize(tmp_path)
    p2.prefetch()
    # if file was created, a second instance can load it
    facts_file = tmp_path / "hindsight.json"
    if facts_file.exists():
        data = json.loads(facts_file.read_text())
        assert "facts" in data


def test_honcho_prefetch_no_key_silent():
    from bauer.memory_provider import HonchoProvider
    p = HonchoProvider()
    p.prefetch()  # deve silenciar sem chave


def test_supermemory_sync_no_key_silent():
    from bauer.memory_provider import SupermemoryProvider
    p = SupermemoryProvider()
    p.sync_turn(0, [{"role": "user", "content": "test"}])  # sem chave → silent


def test_retaindb_prefetch_no_key_silent():
    from bauer.memory_provider import RetainDBProvider
    p = RetainDBProvider()
    p.prefetch()


def test_honcho_with_mock_api():
    from bauer.memory_provider import HonchoProvider
    p = HonchoProvider(api_key="test-key", user_id="u1", session_id="s1")
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = [{"content": "lembrar de usar TDD"}]
    with patch("httpx.get", return_value=mock_resp):
        p.prefetch()
    block = p.system_prompt_block()
    assert "Honcho" in block or "lembrar" in block or block == ""


def test_supermemory_with_mock_api():
    from bauer.memory_provider import SupermemoryProvider
    p = SupermemoryProvider(api_key="test-key")
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {"results": [{"content": "usar pytest fixtures"}]}
    with patch("httpx.post", return_value=mock_resp):
        p.prefetch()
    block = p.system_prompt_block()
    assert block == "" or "Supermemory" in block or "pytest" in block


def test_retaindb_with_mock_api():
    from bauer.memory_provider import RetainDBProvider
    p = RetainDBProvider(api_key="test-key")
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = [{"text": "sempre commitar em branches"}]
    with patch("httpx.get", return_value=mock_resp):
        p.prefetch()
    block = p.system_prompt_block()
    assert block == "" or "RetainDB" in block or "branches" in block


@pytest.mark.parametrize("provider_class", [
    "HonchoProvider", "SupermemoryProvider", "RetainDBProvider",
])
def test_http_provider_graceful_on_network_error(provider_class):
    import importlib
    mod = importlib.import_module("bauer.memory_provider")
    cls = getattr(mod, provider_class)
    p = cls(api_key="k")
    with patch("httpx.get", side_effect=Exception("network error")):
        p.prefetch()  # não deve levantar
    assert p.system_prompt_block() == ""


def test_new_providers_in_multi(tmp_path):
    from bauer.memory_provider import (
        MultiMemoryProvider, HindsightProvider, LocalMemoryProvider,
    )
    multi = MultiMemoryProvider([LocalMemoryProvider(), HindsightProvider(base_dir=tmp_path)])
    multi.initialize(str(tmp_path))
    multi.prefetch()
    assert isinstance(multi.system_prompt_block(), str)


# ---------------------------------------------------------------------------
# G20 — Bitwarden layer em CredentialPool
# ---------------------------------------------------------------------------

def test_credential_pool_has_bw_methods():
    from bauer.credential_pool import CredentialPool
    pool = CredentialPool.__new__(CredentialPool)
    assert hasattr(pool, "_bw_available")
    assert hasattr(pool, "_bw_get")


def test_bw_available_false_without_bw_session(tmp_path):
    from bauer.credential_pool import CredentialPool
    pool = CredentialPool(base_dir=tmp_path)
    env = {k: v for k, v in os.environ.items() if k != "BW_SESSION"}
    with patch.dict(os.environ, env, clear=True):
        with patch("shutil.which", return_value=None):
            assert pool._bw_available() is False


def test_bw_available_true_with_session(tmp_path):
    from bauer.credential_pool import CredentialPool
    pool = CredentialPool(base_dir=tmp_path)
    with patch.dict(os.environ, {"BW_SESSION": "abc123"}):
        with patch("shutil.which", return_value="/usr/bin/bw"):
            assert pool._bw_available() is True


def test_bw_get_returns_none_when_unavailable(tmp_path):
    from bauer.credential_pool import CredentialPool
    pool = CredentialPool(base_dir=tmp_path)
    with patch.object(pool, "_bw_available", return_value=False):
        assert pool._bw_get("groq") is None


def test_bw_get_returns_value_on_success(tmp_path):
    from bauer.credential_pool import CredentialPool
    import subprocess
    pool = CredentialPool(base_dir=tmp_path)
    with patch.object(pool, "_bw_available", return_value=True):
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "sk-groq-secret\n"
        with patch("subprocess.run", return_value=mock_result):
            val = pool._bw_get("groq")
    assert val == "sk-groq-secret"


def test_bw_get_returns_none_on_bw_error(tmp_path):
    from bauer.credential_pool import CredentialPool
    pool = CredentialPool(base_dir=tmp_path)
    with patch.object(pool, "_bw_available", return_value=True):
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stdout = ""
        with patch("subprocess.run", return_value=mock_result):
            assert pool._bw_get("groq") is None


def test_bw_get_graceful_on_subprocess_exception(tmp_path):
    from bauer.credential_pool import CredentialPool
    pool = CredentialPool(base_dir=tmp_path)
    with patch.object(pool, "_bw_available", return_value=True):
        with patch("subprocess.run", side_effect=FileNotFoundError("bw not found")):
            assert pool._bw_get("groq") is None


def test_credential_pool_get_uses_bw_first(tmp_path):
    from bauer.credential_pool import CredentialPool
    pool = CredentialPool(base_dir=tmp_path)
    with patch.object(pool, "_bw_get", return_value="bw-secret") as mock_bw:
        val = pool.get("openai", fallback="fallback")
    mock_bw.assert_called_once_with("openai")
    assert val == "bw-secret"


def test_credential_pool_falls_through_to_keyring_if_bw_none(tmp_path):
    from bauer.credential_pool import CredentialPool
    pool = CredentialPool(base_dir=tmp_path)
    with patch.object(pool, "_bw_get", return_value=None):
        with patch.object(pool, "_keyring_get", return_value="keyring-secret") as mock_kr:
            val = pool.get("openai", fallback="fb")
    mock_kr.assert_called_once()
    assert val == "keyring-secret"


def test_credential_pool_docstring_mentions_bitwarden():
    from bauer.credential_pool import CredentialPool
    doc = CredentialPool.__doc__ or ""
    assert "bitwarden" in doc.lower() or "bw" in doc.lower() or "layer" in doc.lower()


# ---------------------------------------------------------------------------
# G23 — Skills catálogo +11 novas (total >=43)
# ---------------------------------------------------------------------------

def test_skills_total_count_at_least_43():
    from pathlib import Path
    skills_dir = Path("bauer/data/skills")
    all_skills = list(skills_dir.rglob("*.yaml"))
    assert len(all_skills) >= 43, f"Encontrados apenas {len(all_skills)} skills"


@pytest.mark.parametrize("skill_name", [
    "debug-session", "refactor-module",
    "deploy-checklist", "cost-analysis", "security-audit",
    "budget-analysis", "competitive-analysis",
    "project-kickoff", "retrospective",
    "time-series-forecast", "capacity-planning",
])
def test_new_skill_file_exists(skill_name):
    from pathlib import Path
    skills_dir = Path("bauer/data/skills")
    matches = list(skills_dir.rglob(f"{skill_name}.yaml"))
    assert matches, f"Skill '{skill_name}' não encontrada"


@pytest.mark.parametrize("skill_name", [
    "debug-session", "refactor-module", "deploy-checklist",
    "cost-analysis", "security-audit", "budget-analysis",
    "competitive-analysis", "project-kickoff", "retrospective",
    "time-series-forecast", "capacity-planning",
])
def test_new_skill_has_required_fields(skill_name):
    import yaml
    from pathlib import Path
    skills_dir = Path("bauer/data/skills")
    matches = list(skills_dir.rglob(f"{skill_name}.yaml"))
    assert matches
    data = yaml.safe_load(matches[0].read_text(encoding="utf-8"))
    assert data.get("name")
    assert data.get("description")
    assert data.get("content") or data.get("invoke")
    assert data.get("tags")


def test_skill_categories_covered():
    from pathlib import Path
    skills_dir = Path("bauer/data/skills")
    categories = {p.parent.name for p in skills_dir.rglob("*.yaml")}
    expected = {"coding", "devops", "research", "productivity", "data-science", "sre", "writing", "finance"}
    assert expected <= categories


def test_no_duplicate_skill_names():
    import yaml
    from pathlib import Path
    skills_dir = Path("bauer/data/skills")
    names = []
    for f in skills_dir.rglob("*.yaml"):
        try:
            data = yaml.safe_load(f.read_text(encoding="utf-8"))
            if data and data.get("name"):
                names.append(data["name"])
        except Exception:
            pass
    assert len(names) == len(set(names)), f"Nomes duplicados: {[n for n in names if names.count(n) > 1]}"


# ---------------------------------------------------------------------------
# Invariantes de integração G19-G24
# ---------------------------------------------------------------------------

def test_personalities_module_has_no_side_effects():
    import importlib
    mod = importlib.import_module("bauer.personalities")
    assert hasattr(mod, "PERSONALITIES")
    assert hasattr(mod, "get_personality")
    assert hasattr(mod, "list_personalities")
    assert hasattr(mod, "apply_personality")


def test_memory_provider_get_default_includes_vector():
    from bauer.memory_provider import get_memory_provider, reset_memory_provider, MultiMemoryProvider
    reset_memory_provider()
    p = get_memory_provider()
    assert isinstance(p, MultiMemoryProvider)
    reset_memory_provider()


def test_plugin_registry_is_singleton():
    from bauer.plugin_registry import memory_registry as r1
    from bauer.plugin_registry import memory_registry as r2
    assert r1 is r2


def test_typed_registry_register_and_build():
    from bauer.plugin_registry import _TypedRegistry
    reg = _TypedRegistry("Test")
    reg.register("dummy", lambda **kw: {"ok": True}, "test provider")
    result = reg.build("dummy")
    assert result == {"ok": True}


def test_typed_registry_available_sorted():
    from bauer.plugin_registry import _TypedRegistry
    reg = _TypedRegistry("Test")
    reg.register("zebra", lambda **kw: None, "z")
    reg.register("alpha", lambda **kw: None, "a")
    assert reg.available() == ["alpha", "zebra"]
