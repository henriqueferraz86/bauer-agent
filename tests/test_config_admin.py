"""Testes do bauer/config_admin.py — leitura/escrita de config.yaml e .env."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from bauer.config_admin import (
    coerce_value,
    get_config_value,
    get_nested,
    is_env_key,
    read_env_value,
    redact_secret,
    remove_env_value,
    save_env_value,
    set_config_value,
    set_nested,
    unset_config_value,
)


class TestCoerceValue:
    @pytest.mark.parametrize("raw,expected", [
        ("true", True), ("True", True), ("yes", True), ("on", True),
        ("false", False), ("no", False), ("off", False),
        ("42", 42), ("-7", -7),
        ("3.14", 3.14), ("-0.5", -0.5),
        ("null", None), ("none", None),
        ("qwen2.5:7b", "qwen2.5:7b"),
        ("8192", 8192),
        ("hello world", "hello world"),
    ])
    def test_coercao(self, raw, expected):
        assert coerce_value(raw) == expected


class TestIsEnvKey:
    @pytest.mark.parametrize("key,is_env", [
        ("GROQ_API_KEY", True),
        ("TELEGRAM_BOT_TOKEN", True),
        ("AZURE_OPENAI_ENDPOINT", True),
        ("OLLAMA_HOST", True),
        ("GITHUB_TOKEN", True),
        ("model.name", False),
        ("runtime.safety_margin_mb", False),
        ("telegram.enabled", False),
        ("mcp.servers.0.url", False),
    ])
    def test_roteamento(self, key, is_env):
        assert is_env_key(key) is is_env


class TestRedact:
    def test_segredo_longo(self):
        out = redact_secret("gsk_abcdefghijklmnopqrstuvwxyz")
        assert out.startswith("gsk_")
        assert out.endswith("wxyz")
        assert "…" in out

    def test_segredo_curto(self):
        assert redact_secret("abc") == "•••"

    def test_vazio(self):
        assert redact_secret("") == "(não definido)"
        assert redact_secret(None) == "(não definido)"


class TestNested:
    def test_get_aninhado(self):
        data = {"model": {"name": "x", "ctx": 4096}}
        assert get_nested(data, "model.name") == "x"
        assert get_nested(data, "model.ctx") == 4096

    def test_get_indice_de_lista(self):
        data = {"mcp": {"servers": [{"url": "a"}, {"url": "b"}]}}
        assert get_nested(data, "mcp.servers.1.url") == "b"

    def test_get_faltando(self):
        assert get_nested({"a": 1}, "a.b.c") is None
        assert get_nested({}, "x") is None

    def test_set_cria_caminho(self):
        data = {}
        set_nested(data, "a.b.c", 5)
        assert data == {"a": {"b": {"c": 5}}}

    def test_set_preserva_irmaos(self):
        data = {"model": {"name": "x", "ctx": 10}}
        set_nested(data, "model.name", "y")
        assert data == {"model": {"name": "y", "ctx": 10}}

    def test_set_indice_de_lista_existente(self):
        data = {"servers": [{"url": "a"}, {"url": "b"}]}
        set_nested(data, "servers.0.url", "z")
        assert data["servers"][0]["url"] == "z"

    def test_set_indice_fora_da_lista_erro(self):
        data = {"servers": [{"url": "a"}]}
        with pytest.raises(KeyError):
            set_nested(data, "servers.5.url", "z")


class TestEnvFile:
    def test_cria_arquivo(self, tmp_path):
        env = tmp_path / ".env"
        save_env_value("GROQ_API_KEY", "gsk_test", env)
        assert "GROQ_API_KEY=gsk_test" in env.read_text(encoding="utf-8")

    def test_atualiza_preservando_outras(self, tmp_path):
        env = tmp_path / ".env"
        env.write_text("# comentário\nOPENAI_API_KEY=old\nFOO=bar\n", encoding="utf-8")
        save_env_value("OPENAI_API_KEY", "new", env)
        text = env.read_text(encoding="utf-8")
        assert "OPENAI_API_KEY=new" in text
        assert "old" not in text
        assert "# comentário" in text
        assert "FOO=bar" in text

    def test_atualiza_linha_com_export(self, tmp_path):
        env = tmp_path / ".env"
        env.write_text("export GROQ_API_KEY=old\n", encoding="utf-8")
        save_env_value("GROQ_API_KEY", "new", env)
        # nova linha sem export, antiga removida
        assert env.read_text(encoding="utf-8").count("GROQ_API_KEY") == 1
        assert "new" in env.read_text(encoding="utf-8")

    def test_read_value(self, tmp_path):
        env = tmp_path / ".env"
        env.write_text('FOO="quoted value"\nBAR=plain\n', encoding="utf-8")
        assert read_env_value("FOO", env) == "quoted value"
        assert read_env_value("BAR", env) == "plain"
        assert read_env_value("MISSING", env) is None

    def test_remove(self, tmp_path):
        env = tmp_path / ".env"
        env.write_text("A=1\nB=2\nC=3\n", encoding="utf-8")
        assert remove_env_value("B", env) is True
        text = env.read_text(encoding="utf-8")
        assert "B=2" not in text
        assert "A=1" in text and "C=3" in text

    def test_remove_inexistente(self, tmp_path):
        env = tmp_path / ".env"
        env.write_text("A=1\n", encoding="utf-8")
        assert remove_env_value("Z", env) is False


class TestSetConfigValue:
    def test_segredo_vai_pro_env(self, tmp_path):
        cfg = tmp_path / "config.yaml"
        env = tmp_path / ".env"
        dest, path = set_config_value("GROQ_API_KEY", "gsk_x", cfg, env)
        assert dest == "env"
        assert "GROQ_API_KEY=gsk_x" in env.read_text(encoding="utf-8")
        assert not cfg.exists()  # não tocou no yaml

    def test_chave_normal_vai_pro_yaml(self, tmp_path):
        cfg = tmp_path / "config.yaml"
        cfg.write_text("model:\n  name: old\n", encoding="utf-8")
        env = tmp_path / ".env"
        dest, path = set_config_value("model.name", "qwen2.5:7b", cfg, env)
        assert dest == "config"
        data = yaml.safe_load(cfg.read_text(encoding="utf-8"))
        assert data["model"]["name"] == "qwen2.5:7b"

    def test_coercao_no_yaml(self, tmp_path):
        cfg = tmp_path / "config.yaml"
        cfg.write_text("telegram:\n  enabled: false\n", encoding="utf-8")
        set_config_value("telegram.enabled", "true", cfg, tmp_path / ".env")
        data = yaml.safe_load(cfg.read_text(encoding="utf-8"))
        assert data["telegram"]["enabled"] is True

    def test_nao_dumpa_defaults(self, tmp_path):
        """set num arquivo só com model deve manter o arquivo enxuto."""
        cfg = tmp_path / "config.yaml"
        cfg.write_text("model:\n  name: x\n", encoding="utf-8")
        set_config_value("runtime.safety_margin_mb", "512", cfg, tmp_path / ".env")
        data = yaml.safe_load(cfg.read_text(encoding="utf-8"))
        # só as chaves tocadas — sem explosão de defaults
        assert set(data.keys()) == {"model", "runtime"}


class TestGetConfigValue:
    def test_le_do_yaml(self, tmp_path):
        cfg = tmp_path / "config.yaml"
        cfg.write_text("model:\n  name: foo\n", encoding="utf-8")
        assert get_config_value("model.name", cfg, tmp_path / ".env") == "foo"

    def test_le_do_env(self, tmp_path, monkeypatch):
        monkeypatch.delenv("GROQ_API_KEY", raising=False)
        env = tmp_path / ".env"
        env.write_text("GROQ_API_KEY=gsk_y\n", encoding="utf-8")
        assert get_config_value("GROQ_API_KEY", tmp_path / "config.yaml", env) == "gsk_y"


class TestUnset:
    def test_unset_yaml(self, tmp_path):
        cfg = tmp_path / "config.yaml"
        cfg.write_text("model:\n  name: x\n  ctx: 10\n", encoding="utf-8")
        dest, removed = unset_config_value("model.ctx", cfg, tmp_path / ".env")
        assert dest == "config" and removed
        data = yaml.safe_load(cfg.read_text(encoding="utf-8"))
        assert "ctx" not in data["model"]
        assert data["model"]["name"] == "x"

    def test_unset_env(self, tmp_path):
        env = tmp_path / ".env"
        env.write_text("GROQ_API_KEY=z\n", encoding="utf-8")
        dest, removed = unset_config_value("GROQ_API_KEY", tmp_path / "config.yaml", env)
        assert dest == "env" and removed
