"""Inferência local não custa dinheiro — e o guardrail não pode achar que custa.

Encontrado validando o `--local` no Beelink, rodando de verdade. A tela disse
"nada sai desta máquina" e, na mesma linha, `sess total: $0.007013`.

Nada tinha saído. O número era invenção: `get_price("ollama", ...)` não achava o
par na tabela e caía no fallback conservador de $1/$4 por 1M tokens — que existe
para modelo de NUVEM desconhecido e não tem sentido nenhum sobre inferência
local.

Duas consequências, e a segunda é a que dói:

1. `--local` promete que nada sai da máquina e exibe um valor em dólar. Além de
   contraditório, apaga a única pista que distinguiria um vazamento real de
   ruído de display.

2. **`bauer run` PARA no `--max-cost`.** Medido: 30 rodadas locais com contexto
   de 20k acumulavam $0.64 fantasma; com o teto default de $2.00, um laço
   autônomo 100% local abortava em ~94 rodadas sem ter gastado um centavo.
   Guardrail disparando contra dinheiro imaginário.
"""

from __future__ import annotations

import pytest

from bauer.usage_pricing import estimate_cost_usd, get_price

USO = {"prompt_tokens": 20_000, "completion_tokens": 300}


@pytest.mark.parametrize("modelo", [
    "qwen2.5:7b", "gpt-oss:20b", "qwen3-coder:30b",
    "hf.co/unsloth/Qwen3.6-27B-MTP-GGUF:Q5_K_M",   # nome de repo HF
    "modelo-que-nao-existe-na-tabela",
])
def test_ollama_nunca_custa(modelo):
    assert get_price("ollama", modelo) == (0.0, 0.0)
    assert estimate_cost_usd("ollama", modelo, USO) == 0.0


def test_lmstudio_tambem():
    """Não é só Ollama — LM Studio roda na mesma máquina."""
    assert estimate_cost_usd("lmstudio", "qualquer", USO) == 0.0


def test_caixa_do_provider_nao_importa():
    assert estimate_cost_usd("OLLAMA", "qwen2.5:7b", USO) == 0.0


# ─── o que NÃO pode mudar ────────────────────────────────────────────────────


def test_nuvem_fora_da_tabela_mantem_o_fallback():
    """O fallback conservador continua valendo para quem realmente fatura —
    zerar tudo seria trocar um erro por outro, e o `--max-cost` deixaria de
    proteger justamente onde há dinheiro em jogo."""
    assert estimate_cost_usd("openrouter", "provider/modelo-novissimo", USO) > 0


def test_custo_REAL_reportado_continua_vencendo():
    """Quando o provider devolve `usage.cost`, é ele que vale — a tabela é plano
    B. Regra que já existia e não pode ter sido quebrada pela mudança."""
    assert estimate_cost_usd("openrouter", "x", {**USO, "cost": 0.42}) == 0.42


def test_local_com_custo_reportado_e_ignorado():
    """Fronteira: se algum dia um adapter local reportar `cost`, ele ainda é
    local. Mas a regra do `cost` reportado vem ANTES na função — este teste
    documenta o comportamento real em vez de supor."""
    valor = estimate_cost_usd("ollama", "x", {**USO, "cost": 0.99})
    assert valor == 0.99, (
        "custo reportado vence a tabela; se isso mudar, decida conscientemente")


# ─── o efeito no guardrail ───────────────────────────────────────────────────


def test_laco_local_longo_nao_estoura_orcamento():
    """O bug de verdade: 200 rodadas locais têm de somar zero, senão o
    `--max-cost` do `bauer run` aborta trabalho que não custou nada."""
    total = sum(estimate_cost_usd("ollama", "qwen2.5:7b", USO) for _ in range(200))

    assert total == 0.0, f"{total:.2f} de custo fantasma em 200 rodadas locais"


def test_o_mesmo_laco_na_nuvem_soma():
    """Contrapeso: na nuvem o acumulado tem de crescer, senão o guardrail
    deixaria de existir."""
    total = sum(estimate_cost_usd("openrouter", "modelo/desconhecido", USO)
                for _ in range(200))

    assert total > 1.0
