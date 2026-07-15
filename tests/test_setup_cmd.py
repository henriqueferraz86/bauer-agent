"""Testes do wizard `bauer setup` — lógica pura (escolha de modelo e config).

O comando em si é interativo; aqui cobrimos as partes determinísticas: a
escolha do modelo e a geração do config canônico (incluindo a garantia de
que ele CARREGA no schema estrito do Bauer).
"""

from __future__ import annotations

from bauer.commands.setup_cmd import _SUGGESTED_MODEL, pick_model, render_config


class TestPickModel:
    def test_preferred_when_installed(self):
        assert pick_model(["qwen2.5:7b", "llama3"], preferred="llama3") == "llama3"

    def test_preferred_ignored_when_absent(self):
        # preferido não instalado → cai no primeiro instalado válido
        assert pick_model(["qwen2.5:7b"], preferred="gpt-9") == "qwen2.5:7b"

    def test_skips_embedding_models(self):
        assert pick_model(["bge-m3:latest", "qwen2.5:7b"]) == "qwen2.5:7b"

    def test_all_embedding_falls_back_to_first(self):
        # se só há embeddings, não há escolha melhor — devolve o primeiro
        assert pick_model(["bge-m3:latest"]) == "bge-m3:latest"

    def test_empty_returns_none(self):
        assert pick_model([]) is None


class TestRenderConfig:
    def test_has_core_fields(self):
        data = render_config("qwen2.5:7b", "deadbeef")
        assert data["model"]["provider"] == "ollama"
        assert data["model"]["name"] == "qwen2.5:7b"
        assert data["serve"]["api_key"] == "deadbeef"
        assert data["serve"]["host"] == "127.0.0.1"  # local por padrão

    def test_generated_config_loads_in_strict_schema(self, tmp_path):
        """O config gerado precisa CARREGAR — BauerConfig é estrito e rejeita
        campos desconhecidos. Se algum campo não existir no schema, quebra aqui."""
        import yaml

        from bauer.config_loader import load_config

        data = render_config(_SUGGESTED_MODEL, "k" * 64)
        p = tmp_path / "config.yaml"
        p.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")

        cfg = load_config(p)
        assert cfg.model.provider == "ollama"
        assert cfg.model.name == _SUGGESTED_MODEL
        assert cfg.serve.api_key == "k" * 64


class TestSetupCommand:
    """`bauer setup` end-to-end (Ollama offline → degrada) num BAUER_HOME isolado."""

    def test_creates_config_and_workspace(self, tmp_path, monkeypatch):
        from unittest.mock import patch

        from typer.testing import CliRunner

        from bauer.cli import app

        monkeypatch.setenv("BAUER_HOME", str(tmp_path))
        with patch("bauer.ollama_client.OllamaClient.is_alive", return_value=(False, "offline")):
            result = CliRunner().invoke(app, ["setup", "--force", "--model", "qwen2.5:7b"])

        assert result.exit_code == 0
        assert (tmp_path / "config.yaml").exists()
        assert (tmp_path / "workspace").is_dir()   # criado no setup, não só no 1º uso
