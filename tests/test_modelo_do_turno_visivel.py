"""O que a tela anuncia tem de ser o modelo que respondeu.

Bug medido em uso real (2026-07-30), com print. O usuário baixou um GGUF, apontou
o config para ele, e o Bauer exibiu:

    Modelo    hf.co/unsloth/Qwen3.6-27B-MTP-GGUF:Q5_K_M
    Provider  ollama                              ← cabeçalho
    → tier fast: minimax/minimax-m2.5             ← o que REALMENTE rodou
    ◆ BAUER  hf.co/unsloth/Qwen3.6-27B-...  $0.007876   ← rodapé

Cabeçalho e rodapé afirmavam o modelo local; o turno rodou no OpenRouter e o
custo subiu lá. É a mesma família do bug do `tool_mode`: o harness sabe o que
está fazendo e não conta — e foi assim que "o modelo local está lento/rápido"
virou conclusão sobre um modelo que nunca executou.

A regra que estes testes travam: **quem exibe estado exibe o estado REAL**. Se
divergir do configurado, a divergência aparece; nunca se mostra a intenção como
se fosse o fato.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent
AGENT = (RAIZ / "bauer" / "agent.py").read_text(encoding="utf-8")


# ─── a barra de status ───────────────────────────────────────────────────────


# A montagem do rótulo vivia dentro de `_bottom_toolbar`. Desde o plano 028 F2
# ela vive em `_hud_state_atual` — a fonte ÚNICA que alimenta as duas
# superfícies que desenham o rodapé (a bottom_toolbar do prompt_toolkit
# enquanto o prompt espera, e o Live do Rich durante o turno). A garantia é a
# mesma; o que mudou foi o lugar, e o teste segue o lugar.
def _fonte_do_rotulo() -> str:
    trecho = AGENT[AGENT.index("def _hud_state_atual"):]
    return trecho[:trecho.index("# Cria sessão prompt_toolkit")]


def test_barra_nao_exibe_o_modelo_configurado_direto():
    """A barra lia `model_name` — o CONFIGURADO — mesmo com o turno roteado."""
    barra = _fonte_do_rotulo()

    assert "_modelo_do_turno" in barra, (
        "a barra voltou a exibir o modelo configurado em vez do que rodou")
    assert not re.search(r"modelo=str\(model_name\)", barra), (
        "`model_name` cru na barra é exatamente o bug: ele não muda quando o "
        "roteamento manda o turno para outro provider")


def test_divergencia_fica_marcada():
    """Trocar o texto sem sinalizar seria pior: o usuário veria um modelo que
    não escolheu e nem saberia que houve troca."""
    barra = _fonte_do_rotulo()

    assert '_m == model_name' in barra, "sem comparação não há como marcar"
    assert 'f"→ {_m}"' in barra, "a divergência precisa de marca visível"


def test_variavel_inicializada_antes_do_closure():
    """O closure roda no PRIMEIRO prompt, antes de qualquer turno. Sem
    inicialização daria NameError — engolido pelo `except` da barra, que cairia
    para " BAUER " e esconderia tudo, inclusive o modelo."""
    init = AGENT.index("_modelo_do_turno = model_name")
    uso = AGENT.index("def _hud_state_atual")
    assert init < uso, "inicialização precisa vir antes do closure da barra"


def test_selo_local_nuvem_segue_o_provider_do_turno():
    """Mesma família do bug: o selo ◆local/☁nuvem tem que seguir o provider que
    REALMENTE rodou. Com roteamento, uma sessão que começou local pode mandar o
    turno para a nuvem — dizer "local" ali seria a mesma mentira do modelo."""
    barra = _fonte_do_rotulo()
    assert "provider_from_client" in barra, (
        "o selo precisa inferir do CLIENT do turno, não de uma flag de sessão")


# ─── o banner ────────────────────────────────────────────────────────────────


def test_banner_avisa_quando_o_padrao_nao_e_o_que_roda():
    """Com roteamento, `model_name` só entra se o router cair nele. Anunciá-lo
    como "Modelo" sem ressalva foi o que fez um GGUF local aparecer como o
    modelo da sessão enquanto todo turno ia para a nuvem."""
    assert "o turno pode ir p/ outro tier" in AGENT
    assert '_linhas_extra.append(("Roteando"' in AGENT


def test_banner_gated_por_route_profiles_e_nao_por_routing():
    """`_boot_tier_profiles`/`_tier_profiles` (resolução por-provider de
    `route_profiles`) é o gate REAL do router heurístico no laço do turno.
    Gatear o banner por `routing` — o ModelRouter legado, outro caminho — o
    deixaria mudo justamente na configuração que produziu o bug."""
    trecho = AGENT[AGENT.index("_linhas_extra: list"):AGENT.index("from .ascii_intro import session_panel")]
    assert "if _boot_tier_profiles:" in trecho
    assert "routing and route_profiles" not in trecho

    laco = AGENT[AGENT.index("_hr_routed = False"):]
    assert "if _tier_profiles:" in laco[:600], (
        "o gate do laço mudou — o do banner precisa acompanhar")


def test_perfis_sao_objetos_nao_dicionarios():
    """`profiles_from_config` devolve `dict[str, ModelProfile]`. Um `.get()` no
    valor levantaria AttributeError e derrubaria o banner inteiro."""
    trecho = AGENT[AGENT.index("_linhas_extra: list"):AGENT.index("from .ascii_intro import session_panel")]
    assert "getattr(v, 'model'" in trecho
    assert ".get('model'" not in trecho


# ─── comportamento, não só texto ─────────────────────────────────────────────


def test_session_panel_aceita_linhas_extra():
    """O banner usa `extra_rows`; se a assinatura mudar, quebra silencioso."""
    from bauer.ascii_intro import session_panel

    p = session_panel("t", "modelo-x", 8192, provider="ollama",
                      extra_rows=[("Roteando", "fast: minimax/minimax-m2.5")])
    assert p is not None


def test_profiles_do_config_real_do_usuario():
    """Reproduz o config que gerou o print: 4 tiers, todos em outro provider."""
    from bauer.model_router import profiles_from_config

    class _M:
        router_enabled = True
        profiles = {
            "fast": {"provider": "openrouter", "model": "minimax/minimax-m2.5"},
            "balanced": {"provider": "openrouter", "model": "z-ai/glm-5.2"},
        }

    class _Cfg:
        model = _M()

    perfis = profiles_from_config(_Cfg())

    assert set(perfis) == {"fast", "balanced"}
    assert perfis["fast"].model == "minimax/minimax-m2.5"
    # a linha que o banner monta, com os objetos reais
    linha = ", ".join(f"{k}: {getattr(v, 'model', '?')}"
                      for k, v in sorted(perfis.items()))
    assert "fast: minimax/minimax-m2.5" in linha


@pytest.mark.parametrize("configurado,do_turno,esperado", [
    ("modelo-a", "modelo-a", "modelo-a"),
    ("modelo-a", "minimax/minimax-m2.5", "→ minimax/minimax-m2.5"),
    ("modelo-a", "", "modelo-a"),          # antes do 1º turno
])
def test_rotulo_da_barra(configurado, do_turno, esperado):
    """A regra da barra, isolada da UI."""
    m = do_turno or configurado
    assert (str(m) if m == configurado else f"→ {m}") == esperado
