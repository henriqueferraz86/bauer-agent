"""Contrato dos componentes textuais compartilhados pela CLI."""

from __future__ import annotations

import pytest

from bauer import theme, ui


@pytest.fixture(autouse=True)
def _visual_padrao():
    anterior_mode = ui.visual_mode()
    anterior_glyphs = ui.active_glyphs()
    ui.configure(mode="rich", emojis=True)
    yield
    ui.configure(mode=anterior_mode, emojis=anterior_glyphs is not theme.ASCII)
    ui.use_glyphs(anterior_glyphs)


def test_notice_mantem_titulo_detalhe_e_proximo_passo():
    out = ui.render_str(ui.notice(
        "Erro do provider", "timeout", kind="error", hint="Rode bauer doctor"
    ), 80)
    assert "Erro do provider" in out
    assert "timeout" in out
    assert "Rode bauer doctor" in out


def test_status_line_plain_e_ascii_sem_perder_semantica():
    ui.configure(mode="plain", emojis=False)
    out = ui.render_str(ui.status_line("Concluído", kind="success"), 50)
    assert "OK" in out
    assert "Concluído" in out


def test_session_header_compacto_vira_uma_linha_legivel():
    ui.configure(mode="compact")
    out = ui.render_str(ui.session_header(
        "bauer run", workspace="C:/proj", model="qwen", provider="ollama",
        meta=["Kernel ativo", "limites: 30 min"],
    ), 100)
    assert "bauer run" in out
    assert "workspace: C:/proj" in out
    assert "modelo: qwen (ollama)" in out


def test_agent_hud_header_concentra_identidade_e_estado_da_sessao():
    out = ui.render_str(ui.agent_hud_header(
        workspace="BauerAgent",
        model="qwen3-coder:30b",
        provider="ollama",
        tool_count=71,
        tool_mode="bridge",
        local=True,
        resumed=False,
    ), 120)
    assert "BAUER AGENT" in out
    assert "INTERATIVO" in out
    assert "online" in out
    assert "ollama / qwen3-coder:30b" in out
    assert "workspace" in out and "BauerAgent" in out
    assert "71 tools · bridge · local" in out


def test_result_card_nunca_fabrica_metricas():
    out = ui.render_str(ui.result_card("Tarefa concluída", "2 rodadas · 4 tools"), 80)
    assert "2 rodadas · 4 tools" in out
    assert "arquivos alterados" not in out


def test_progress_line_tem_campos_na_ordem_do_run():
    out = ui.render_str(ui.progress_line(
        3, tools=8, tools_limit=20, elapsed="1m02s/30m", cost="~US$ 0.003/2.00"
    ), 100)
    assert "rodada 3" in out
    assert "8/20 tools" in out
    assert "1m02s/30m" in out
    assert "~US$ 0.003/2.00" in out
