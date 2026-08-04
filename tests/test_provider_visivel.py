"""O provider anunciado tem de ser o provider real.

Bug visto em uso (captura de tela, 2026-08-04): na MESMA tela, o boot dizia
`openrouter` e o painel de sessão dizia `openai`, sobre a mesma sessão. Duas
afirmações contraditórias, e a errada era a mais visível.

Causa: o boot lê `state["configured_provider"]` — o que o config DECLARA. O
painel inferia pelo host com uma função que só sabia separar Ollama do resto,
então todo provider de nuvem (OpenRouter, Groq, DeepSeek, Anthropic…) saía
como "openai".

É a mesma família do bug que `test_modelo_do_turno_visivel` cobre: quem exibe
estado exibe o estado REAL, e nunca a intenção como se fosse o fato.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from bauer.agent import _detectar_provider_por_host, _provider_declarado


class TestInferenciaPorHost:
    @pytest.mark.parametrize("host,esperado", [
        ("https://openrouter.ai/api/v1", "openrouter"),
        ("https://api.anthropic.com", "anthropic"),
        ("https://api.deepseek.com", "deepseek"),
        ("https://api.mistral.ai", "mistral"),
        ("https://api.together.xyz", "together"),
        ("https://api.moonshot.cn", "moonshot"),
        ("https://opencode.ai/zen/v1", "opencode"),
        ("http://localhost:11434", "ollama"),
        ("http://127.0.0.1:11434", "ollama"),
        ("https://api.openai.com/v1", "openai"),
    ])
    def test_provider_reconhecido(self, host, esperado):
        assert _detectar_provider_por_host(host) == esperado

    def test_groq_nao_vira_openai(self):
        """Groq serve em `/openai/v1` — casar "openai" antes de "groq" o
        renomearia. A ordem da tabela é significativa."""
        assert _detectar_provider_por_host("https://api.groq.com/openai/v1") == "groq"

    def test_ollama_em_localhost_sem_o_nome(self):
        """A heurística ANTIGA olhava só o nome do host, e falhava em
        localhost — ou seja, em praticamente toda instalação real."""
        assert _detectar_provider_por_host("http://192.168.3.65:11434") == "ollama"

    def test_desconhecido_assume_openai_compat(self):
        """Aqui "openai" é honesto: é um endpoint OpenAI-compat de origem
        desconhecida, e não uma etiqueta errada num provider conhecido."""
        assert _detectar_provider_por_host("https://meu-vllm-interno.corp") == "openai"
        assert _detectar_provider_por_host("") == "openai"

    def test_porta_generica_nao_vira_palpite(self):
        """1234 é o default do LM Studio — e também porta genérica de qualquer
        servidor OpenAI-compat local. Deduzir "lmstudio" dela seria o mesmo
        tipo de chute que esta função existe para consertar. Só `:11434` entra
        por porta, porque é convenção fixa do Ollama e de mais nada."""
        assert _detectar_provider_por_host("http://localhost:1234") == "openai"
        assert _detectar_provider_por_host("http://localhost:8080/v1") == "openai"


class TestProviderDeclarado:
    def test_le_do_config(self):
        cfg = type("C", (), {"model": type("M", (), {"provider": "openrouter"})()})()
        with patch("bauer.config_loader.load_config", return_value=cfg):
            assert _provider_declarado() == "openrouter"

    def test_config_ilegivel_devolve_vazio(self):
        """Vazio, não um palpite: quem chama cai na inferência por host."""
        with patch("bauer.config_loader.load_config", side_effect=RuntimeError("x")):
            assert _provider_declarado() == ""


class TestNaoContradizerOBoot:
    def test_declarado_tem_prioridade_sobre_o_host(self):
        """O caso da captura: client OpenRouter (host que a inferência ANTIGA
        chamaria de openai) + config declarando openrouter. O painel tem de
        dizer o mesmo que o boot."""
        cfg = type("C", (), {"model": type("M", (), {"provider": "openrouter"})()})()
        with patch("bauer.config_loader.load_config", return_value=cfg):
            declarado = _provider_declarado()
        assert declarado == "openrouter"

        # e a inferência, agora, concorda em vez de contradizer
        assert _detectar_provider_por_host("https://openrouter.ai/api/v1") == "openrouter"

    def test_toda_marca_da_tabela_e_alcancavel(self):
        """Uma marca escondida por outra anterior seria código morto que
        renomeia o provider do usuário em silêncio."""
        from bauer.agent import _MARCAS_DE_HOST

        for marca, nome in _MARCAS_DE_HOST:
            resolvido = _detectar_provider_por_host(f"https://{marca}/v1")
            assert resolvido == nome, (
                f"marca {marca!r} deveria dar {nome!r}, deu {resolvido!r} — "
                "outra entrada da tabela a está ofuscando"
            )
