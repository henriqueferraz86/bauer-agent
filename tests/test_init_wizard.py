"""Tests for bauer.init_wizard."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from bauer.init_wizard import (
    _write_env,
    _update_config_yaml,
    run_init_wizard,
    _PROVIDERS,
    _PROVIDER_DEFAULTS,
)


# ---------------------------------------------------------------------------
# _write_env
# ---------------------------------------------------------------------------

class TestWriteEnv:
    def test_creates_file_if_absent(self, tmp_path):
        env = tmp_path / ".env"
        _write_env(env, "MY_KEY", "abc123")
        assert env.read_text().strip() == "MY_KEY=abc123"

    def test_appends_new_key(self, tmp_path):
        env = tmp_path / ".env"
        env.write_text("OTHER=x\n")
        _write_env(env, "MY_KEY", "abc123")
        lines = env.read_text().splitlines()
        assert "OTHER=x" in lines
        assert "MY_KEY=abc123" in lines

    def test_updates_existing_key(self, tmp_path):
        env = tmp_path / ".env"
        env.write_text("MY_KEY=old\n")
        _write_env(env, "MY_KEY", "new")
        lines = [l for l in env.read_text().splitlines() if l.strip()]
        assert lines.count("MY_KEY=new") == 1
        assert "MY_KEY=old" not in lines

    def test_updates_commented_key(self, tmp_path):
        env = tmp_path / ".env"
        env.write_text("# MY_KEY=placeholder\n")
        _write_env(env, "MY_KEY", "real")
        assert "MY_KEY=real" in env.read_text()
        assert "# MY_KEY" not in env.read_text()


# ---------------------------------------------------------------------------
# _update_config_yaml
# ---------------------------------------------------------------------------

class TestUpdateConfigYaml:
    def test_creates_new_config(self, tmp_path):
        cfg = tmp_path / "config.yaml"
        _update_config_yaml(cfg, "openai", "gpt-4o-mini", "./workspace")
        text = cfg.read_text()
        assert "openai" in text
        assert "gpt-4o-mini" in text
        assert "workspace" in text

    def test_creates_parent_dirs(self, tmp_path):
        cfg = tmp_path / "sub" / "config.yaml"
        _update_config_yaml(cfg, "anthropic", "claude-haiku-4-5-20251001", "./ws")
        assert cfg.exists()

    def test_merges_with_existing_config(self, tmp_path):
        cfg = tmp_path / "config.yaml"
        cfg.write_text("agent:\n  workspace: ./old\n")
        _update_config_yaml(cfg, "groq", "llama-3.3-70b-versatile", "./new")
        text = cfg.read_text()
        assert "groq" in text
        assert "llama-3.3-70b-versatile" in text

    def test_fallback_without_pyyaml(self, tmp_path):
        cfg = tmp_path / "config.yaml"
        with patch.dict("sys.modules", {"yaml": None}):
            _update_config_yaml(cfg, "deepseek", "deepseek-chat", "./ws")
        text = cfg.read_text()
        assert "deepseek" in text
        assert "deepseek-chat" in text


# ---------------------------------------------------------------------------
# run_init_wizard — happy paths
# ---------------------------------------------------------------------------

def _make_io(answers: list[str]):
    """Return an io_ask callable that pops from a list of pre-canned answers."""
    it = iter(answers)
    def _ask(_prompt: str) -> str:
        try:
            return next(it)
        except StopIteration:
            return ""
    return _ask


class TestRunInitWizardHappyPath:
    def test_basic_openai_run(self, tmp_path):
        cfg = tmp_path / "config.yaml"
        env = tmp_path / ".env"
        messages: list[str] = []

        # provider=2 (openai), api_key=mykey, model=default, workspace=default
        io = _make_io(["2", "mykey", "", ""])
        ok = run_init_wizard(
            config_path=cfg,
            env_path=env,
            force=True,
            io_ask=io,
            print_fn=messages.append,
        )

        assert ok is True
        assert cfg.exists()
        assert "openai" in cfg.read_text()
        assert env.exists()
        assert "OPENAI_API_KEY=mykey" in env.read_text()

    def test_ollama_no_key_required(self, tmp_path):
        cfg = tmp_path / "config.yaml"
        messages: list[str] = []
        # provider=1 (ollama), host=default, model=default, workspace=default
        io = _make_io(["1", "", "", ""])
        ok = run_init_wizard(
            config_path=cfg,
            env_path=tmp_path / ".env",
            force=True,
            io_ask=io,
            print_fn=messages.append,
        )
        assert ok is True
        assert "ollama" in cfg.read_text()

    def test_anthropic_run(self, tmp_path):
        cfg = tmp_path / "config.yaml"
        env = tmp_path / ".env"
        # provider=3 (anthropic), api_key, model default, workspace default
        io = _make_io(["3", "sk-ant-test", "", ""])
        ok = run_init_wizard(
            config_path=cfg,
            env_path=env,
            force=True,
            io_ask=io,
            print_fn=lambda _: None,
        )
        assert ok is True
        assert "anthropic" in cfg.read_text()
        assert "ANTHROPIC_API_KEY=sk-ant-test" in env.read_text()

    def test_custom_workspace(self, tmp_path):
        cfg = tmp_path / "config.yaml"
        # provider=1 ollama, model default, workspace custom
        io = _make_io(["1", "", "", "/custom/path"])
        run_init_wizard(
            config_path=cfg,
            env_path=tmp_path / ".env",
            force=True,
            io_ask=io,
            print_fn=lambda _: None,
        )
        assert "/custom/path" in cfg.read_text()

    def test_custom_model(self, tmp_path):
        cfg = tmp_path / "config.yaml"
        # provider=2 (openai), api_key, custom model
        io = _make_io(["2", "sk-test", "gpt-4o", ""])
        run_init_wizard(
            config_path=cfg,
            env_path=tmp_path / ".env",
            force=True,
            io_ask=io,
            print_fn=lambda _: None,
        )
        assert "gpt-4o" in cfg.read_text()

    def test_skips_key_if_env_var_set(self, tmp_path):
        """If OPENAI_API_KEY is already set in env, wizard should not call io_ask for it."""
        cfg = tmp_path / "config.yaml"
        env = tmp_path / ".env"
        calls: list[str] = []

        def counting_io(prompt: str) -> str:
            calls.append(prompt)
            if "1-8" in prompt or "(1-" in prompt:
                return "2"  # openai
            return ""  # default for model + workspace

        with patch.dict("os.environ", {"OPENAI_API_KEY": "existing-key"}):
            run_init_wizard(
                config_path=cfg,
                env_path=env,
                force=True,
                io_ask=counting_io,
                print_fn=lambda _: None,
            )

        # The API key prompt should NOT appear in calls
        key_prompts = [c for c in calls if "OPENAI_API_KEY" in c]
        assert len(key_prompts) == 0


# ---------------------------------------------------------------------------
# run_init_wizard — cancel flow
# ---------------------------------------------------------------------------

class TestRunInitWizardCancel:
    def test_cancels_when_config_exists_and_user_says_no(self, tmp_path):
        cfg = tmp_path / "config.yaml"
        cfg.write_text("model:\n  provider: ollama\n")
        messages: list[str] = []

        # User answers "n" to overwrite prompt
        io = _make_io(["n"])
        ok = run_init_wizard(
            config_path=cfg,
            env_path=tmp_path / ".env",
            force=False,
            io_ask=io,
            print_fn=messages.append,
        )

        assert ok is False
        assert any("cancelada" in m.lower() for m in messages)

    def test_force_skips_overwrite_prompt(self, tmp_path):
        cfg = tmp_path / "config.yaml"
        cfg.write_text("model:\n  provider: old\n")
        # With force=True, overwrite prompt never appears — io_ask goes straight to provider
        io = _make_io(["1", "", "", ""])
        ok = run_init_wizard(
            config_path=cfg,
            env_path=tmp_path / ".env",
            force=True,
            io_ask=io,
            print_fn=lambda _: None,
        )
        assert ok is True
        assert "ollama" in cfg.read_text()


# ---------------------------------------------------------------------------
# _PROVIDERS / _PROVIDER_DEFAULTS completeness
# ---------------------------------------------------------------------------

class TestProviderDefaults:
    def test_eight_providers(self):
        assert len(_PROVIDERS) == 8

    def test_all_providers_have_required_keys(self):
        required = {"label", "needs_key"}
        for name, pdef in _PROVIDER_DEFAULTS.items():
            missing = required - pdef.keys()
            assert not missing, f"Provider {name!r} missing keys: {missing}"

    def test_key_providers_have_env_var(self):
        for name, pdef in _PROVIDER_DEFAULTS.items():
            if pdef.get("needs_key"):
                assert pdef.get("env_var"), f"{name}: needs_key=True but no env_var"

    def test_ollama_does_not_need_key(self):
        assert _PROVIDER_DEFAULTS["ollama"]["needs_key"] is False

    def test_known_providers_present(self):
        expected = {"openai", "anthropic", "groq", "openrouter", "deepseek", "gemini", "mistral", "ollama"}
        assert set(_PROVIDERS) == expected


# ---------------------------------------------------------------------------
# Summary output
# ---------------------------------------------------------------------------

class TestRunInitWizardOutput:
    def test_summary_includes_provider_and_model(self, tmp_path):
        cfg = tmp_path / "config.yaml"
        messages: list[str] = []
        io = _make_io(["2", "sk-key", "", ""])
        run_init_wizard(
            config_path=cfg,
            env_path=tmp_path / ".env",
            force=True,
            io_ask=io,
            print_fn=messages.append,
        )
        full_output = "\n".join(messages)
        assert "openai" in full_output.lower()
        assert "gpt-4o-mini" in full_output

    def test_next_steps_mention_bauer_chat(self, tmp_path):
        cfg = tmp_path / "config.yaml"
        messages: list[str] = []
        io = _make_io(["1", "", "", ""])
        run_init_wizard(
            config_path=cfg,
            env_path=tmp_path / ".env",
            force=True,
            io_ask=io,
            print_fn=messages.append,
        )
        full_output = "\n".join(messages)
        assert "bauer chat" in full_output
