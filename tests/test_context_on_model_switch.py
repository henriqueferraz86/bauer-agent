"""Contexto acompanha o modelo (bug: travado em 8192 apos /models/switch)."""

from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest
import yaml

pytest.importorskip("fastapi", reason="requer bauer-agent[server]")

from bauer.provider_profile import resolver_contexto_aplicado  # noqa: E402


class TestResolverContextoAplicado:
    def test_ollama_respeita_o_requested(self):
        """Local: quem define num_ctx e o usuario. Pedir mais que a VRAM trava a
        maquina, entao o Bauer NAO deve "melhorar" esse numero sozinho."""
        assert resolver_contexto_aplicado("ollama", 8192) == 8192
        assert resolver_contexto_aplicado("ollama", 32768) == 32768

    @pytest.mark.parametrize("provider", ["openai", "anthropic", "openrouter"])
    def test_cloud_sobe_para_a_janela_real(self, provider):
        """8192 e o default do config, pensado para modelo local. Aplica-lo a um
        modelo cloud de 128k desperdicaria 94% da janela e dispararia compressao
        de contexto sem necessidade."""
        assert resolver_contexto_aplicado(provider, 8192) > 8192

    def test_cloud_respeita_pedido_maior_que_o_default(self):
        assert resolver_contexto_aplicado("openai", 1_000_000) == 1_000_000

    def test_provider_vazio_trata_como_local(self):
        assert resolver_contexto_aplicado("", 8192) == 8192


class TestSwitchAtualizaContexto:
    def _app(self, tmp: Path):
        from bauer.server import create_app
        from bauer.tool_router import ToolRouter

        ws = tmp / "ws"
        ws.mkdir()
        cfg = tmp / "config.yaml"
        cfg.write_text(yaml.safe_dump({"model": {
            "name": "qwen3:0.6b", "provider": "ollama", "requested_context": 8192,
        }}), encoding="utf-8")

        cli = MagicMock()
        cli.host = "http://localhost:11434"
        cli._provider = "ollama"
        cli.has_model.return_value = True

        return create_app(
            model_name="qwen3:0.6b", applied_context=8192,
            router=ToolRouter(workspace=ws), client=cli, system_prompt="p",
            sessions_dir=tmp / "s", workspace=ws, config_path=cfg,
        )

    def test_switch_para_cloud_expande_o_contexto(self):
        """REGRESSAO: `applied_context` era parametro do create_app, capturado por
        closure no boot e NUNCA recalculado. Trocar para um modelo de 200k mantinha
        o budget em 8192 (6144 uteis) para sempre."""
        from fastapi.testclient import TestClient

        tmp = Path(tempfile.mkdtemp())
        c = TestClient(self._app(tmp))

        assert c.get("/status").json()["context_tokens"] == 8192

        r = c.post("/models/switch", json={"model": "claude-x", "provider": "anthropic"})
        assert r.status_code == 200
        assert r.json()["context_tokens"] > 100_000, "contexto nao acompanhou o modelo"

        # /status tambem: antes reportava o valor do boot, escondendo o problema
        assert c.get("/status").json()["context_tokens"] > 100_000

    def test_switch_de_volta_para_local_reduz(self):
        """Nao e so subir: voltar para o Ollama tem que devolver o teto local,
        senao o budget ficaria grande demais para a maquina."""
        from fastapi.testclient import TestClient

        tmp = Path(tempfile.mkdtemp())
        c = TestClient(self._app(tmp))
        c.post("/models/switch", json={"model": "claude-x", "provider": "anthropic"})
        r = c.post("/models/switch", json={"model": "qwen3:0.6b", "provider": "ollama"})
        assert r.json()["context_tokens"] == 8192


class TestDeteccaoDeProviderPorHost:
    """A heuristica antiga (`"ollama" in host`) falhava em TODA URL real do
    Ollama — so acertava se o host fosse literalmente `ollama` (Docker Compose).
    O painel exibia "openai" numa sessao local, contradizendo a mensagem
    "Modelo local + contexto 8192" impressa logo acima; e o mesmo valor ia para
    o ContextManager escolher a janela de fallback."""

    @pytest.mark.parametrize("host", [
        "http://localhost:11434",
        "http://127.0.0.1:11434",
        "http://0.0.0.0:11434",
        "http://ollama:11434",
    ])
    def test_urls_reais_do_ollama_sao_reconhecidas(self, host):
        from bauer.agent import _detectar_provider_por_host
        assert _detectar_provider_por_host(host) == "ollama"

    @pytest.mark.parametrize("host", ["https://api.openai.com", "", "http://localhost:1234"])
    def test_nao_confunde_outros_hosts(self, host):
        from bauer.agent import _detectar_provider_por_host
        assert _detectar_provider_por_host(host) == "openai"
