"""Testes do wizard `bauer setup` — lógica pura (escolha de modelo e config).

O comando em si é interativo; aqui cobrimos as partes determinísticas: a
escolha do modelo e a geração do config canônico (incluindo a garantia de
que ele CARREGA no schema estrito do Bauer).
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

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


# ── Contexto derivado do MODELO, nao 8192 cravado ───────────────────────────
#
# `requested_context` e TETO na regra do preflight
# (`min(requested, modelfile, env, ram_seguro)`). Gravar 8192 fixo, sem olhar
# o modelo, prendia quem roda um 27b/30b de janela 32k em 25% da capacidade —
# em silencio, e ainda perdendo tools para o auto-allowlist de contexto pequeno.

class TestContextoInicial:
    def _reg(self):
        from bauer.model_registry import load_registry
        return load_registry()

    def test_modelo_curado_usa_a_janela_dele(self):
        from bauer.commands.setup_cmd import contexto_inicial_para
        assert contexto_inicial_para("qwen3.6:27b", registry=self._reg()) == 32768

    def test_modelo_desconhecido_cai_no_fallback(self):
        """Sem registry, sem Ollama e sem entrada: mantem o 8192 historico em vez
        de chutar um valor que pode nao caber na maquina."""
        from bauer.commands.setup_cmd import contexto_inicial_para
        assert contexto_inicial_para("nao-existe:999b", registry=self._reg()) == 8192

    def test_sem_registry_e_sem_client_nao_quebra(self):
        from bauer.commands.setup_cmd import contexto_inicial_para
        assert contexto_inicial_para("qwen3.6:27b") == 8192

    def test_teto_protege_contra_janela_gigante(self):
        """Modelos anunciam 131072 nativo. Gravar isso no config de quem roda
        local trocaria 'contexto pequeno' por 'nao sobe' — cada 1k custa KV cache."""
        from unittest.mock import MagicMock

        from bauer.commands.setup_cmd import _TETO_CONTEXTO_LOCAL, contexto_inicial_para
        import bauer.model_registry as mr

        info = MagicMock()
        info.max_context_safe = 131072
        with patch.object(mr, "auto_detect_from_ollama", return_value=info):
            ctx = contexto_inicial_para("gigante:1b", registry=None, client=MagicMock())
        assert ctx == _TETO_CONTEXTO_LOCAL == 32768

    def test_registry_tem_prioridade_sobre_autodetect(self):
        """O registry e curado e ja pondera RAM; o /api/show so diz o nativo."""
        from unittest.mock import MagicMock

        from bauer.commands.setup_cmd import contexto_inicial_para
        import bauer.model_registry as mr

        chamou = {"n": 0}

        def _spy(*a, **k):
            chamou["n"] += 1
            return None

        with patch.object(mr, "auto_detect_from_ollama", side_effect=_spy):
            contexto_inicial_para("qwen3.6:27b", registry=self._reg(), client=MagicMock())
        assert chamou["n"] == 0, "consultou o Ollama tendo o registry"

    def test_render_config_propaga_o_contexto(self):
        from bauer.commands.setup_cmd import render_config
        d = render_config("qwen3-coder:30b", "k" * 64, registry=self._reg())
        assert d["model"]["requested_context"] == 32768


class TestModelosRegistrados:
    """Modelo fora do models.yaml cai no perfil auto-detectado, que desliga o
    bloqueio por RAM (ram_per_1k_ctx_mb=0) — o Bauer perde a protecao de
    'nao cabe nesta maquina'. Estes tres apareciam em instalacao real."""

    @pytest.mark.parametrize("modelo,tools", [
        ("qwen3:14b", True),
        ("qwen2.5-coder:14b", True),
        ("deepseek-r1:14b", False),   # raciocinio: nao faz tool calling nativo
    ])
    def test_modelo_esta_no_registry(self, modelo, tools):
        from bauer.model_registry import load_registry
        info = load_registry().get(modelo)
        assert info is not None, f"{modelo} nao registrado"
        assert info.max_context_safe == 32768
        assert info.supports_tools is tools
        assert info.ram_per_1k_ctx_mb > 0, "sem custo por contexto = sem protecao de RAM"
