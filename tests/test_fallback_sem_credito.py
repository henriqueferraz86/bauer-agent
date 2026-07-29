"""A cadeia de fallback tem que disparar quando acaba o crédito.

Achado com OpenRouter zerado (35/35 consumidos) num setup real: o usuário
configurou `model.fallback_models` com 4 modelos e a cadeia NUNCA disparava.

Causa: o `OpenAIClient` TRADUZ o erro 402 do provider para português
("Creditos insuficientes no provider"), e o `error_classifier` casava só
padrões em inglês. Sem match → UNKNOWN → `should_fallback=False` → o agent
não tentava o próximo modelo. Ou seja: o Bauer traduzia o próprio erro e
depois não reconhecia a tradução — e isso matava o fallback exatamente na
falha mais provável de todas.

Duas camadas de correção, testadas aqui:
  1. `status_code` como ATRIBUTO da exceção (número não se traduz)
  2. padrões PT-BR no classificador (rede de segurança se o status faltar)
"""

from __future__ import annotations

import pytest

from bauer.error_classifier import FailReason, classify_api_error
from bauer.openai_client import OpenAIClientError


# ─── camada 1: status como atributo ──────────────────────────────────────────


def test_402_classifica_por_atributo_independente_do_idioma():
    """O caminho robusto: número de status não é traduzido."""
    erro = OpenAIClientError("[Provedor HTTP 402] mensagem em QUALQUER idioma", status_code=402)
    c = classify_api_error(erro)
    assert c.status_code == 402
    assert c.should_fallback is True


def test_excecao_sem_status_nao_quebra():
    """Nem todo erro tem status (timeout, DNS). Não pode explodir."""
    c = classify_api_error(OpenAIClientError("conexao recusada"))
    assert c.status_code is None


def test_status_code_e_opcional_na_construcao():
    """Compat: chamadas antigas passam só a mensagem."""
    e = OpenAIClientError("erro simples")
    assert str(e) == "erro simples"
    assert e.status_code is None


# ─── camada 2: padrões PT-BR ─────────────────────────────────────────────────


@pytest.mark.parametrize("msg", [
    "[Provedor] Creditos insuficientes no provider.",
    "[Provedor] Créditos insuficientes no provider.",
    "Este modelo requer saldo — escolha um gratuito",
    "saldo insuficiente na conta",
    "sem saldo para completar a requisicao",
])
def test_mensagem_em_portugues_dispara_fallback(msg):
    """A mensagem exata que o cliente gera hoje, sem depender do status."""
    c = classify_api_error(Exception(msg))
    assert c.should_fallback is True, f"não classificou como fallback: {msg!r}"
    assert c.reason == FailReason.QUOTA_EXCEEDED


def test_ingles_continua_funcionando():
    """Regressão: não trocar um idioma pelo outro."""
    c = classify_api_error(Exception("insufficient credits for this request"))
    assert c.should_fallback is True
    assert c.reason == FailReason.QUOTA_EXCEEDED


# ─── o caso real, ponta a ponta ──────────────────────────────────────────────


def test_erro_real_do_openrouter_zerado():
    """Reproduz a exceção exata medida no Beelink com saldo esgotado."""
    real = (
        "[Provedor] Creditos insuficientes no provider.\n\n"
        "  Este modelo requer saldo — escolha um gratuito:\n"
        "  - OpenRouter: use modelos com ':free' no ID\n"
    )
    c = classify_api_error(OpenAIClientError(real))
    assert c.should_fallback is True, (
        "sem isto, `model.fallback_models` fica inerte justamente quando o "
        "usuário fica sem crédito — que é quando ele mais precisa da cadeia"
    )


def test_erro_de_servidor_nao_vira_cobranca():
    """5xx tenta outro provider, mas pelo motivo certo — não como billing."""
    c = classify_api_error(OpenAIClientError("[Provedor HTTP 500] boom", status_code=500))
    assert c.reason != FailReason.QUOTA_EXCEEDED
