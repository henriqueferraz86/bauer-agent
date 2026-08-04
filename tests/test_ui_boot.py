"""Boot instrumentado (bauer/ui_boot.py) — plano 028 F4.

O teste mais importante deste arquivo é `TestNaoMedeParaExibir`: a fase inteira
existe sob a restrição de NÃO regredir o startup. A memória do projeto registra
23s→2s conquistados com SSL compartilhado e AuthManager preguiçoso; um boot
"bonito" que force checagem para ter o que animar joga isso fora.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from bauer import ui
from bauer.ui_boot import _idade, boot_line, boot_lines, boot_panel, serve_panel


def _agora_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _antes(**kw) -> str:
    return (datetime.now(timezone.utc) - timedelta(**kw)).isoformat(timespec="seconds")


ESTADO = {
    "configured_model": "qwen3-coder:30b",
    "configured_provider": "ollama",
    "active_model": "qwen3-coder:30b",
    "model_available": True,
    "ollama_alive": True,
    "context": {"requested": 32768, "applied": 32768, "reason": "ok"},
    "tool_mode": "native",
    "ram_available_mb": 47104,
    "ram_total_mb": 62464,
    "status": "ok",
    "notes": [],
    # SEM `generated_at` de propósito: aqui ele seria avaliado no IMPORT e o
    # relógio congelaria. Localmente a suíte roda em segundos e passava; no
    # runner Windows do CI, 3 minutos separavam o import do teste, e o painel
    # dizia (corretamente) "do cache, medido há 3 min" enquanto o teste exigia
    # "verificado agora" — o código estava certo, o teste é que media errado.
    # Quem precisa de um instante específico passa o seu (ver abaixo).
}


class TestLinhasDeBoot:
    def test_modelo_e_provider(self):
        out = ui.render_str(boot_panel(ESTADO), 100)
        assert "qwen3-coder:30b" in out and "ollama" in out

    def test_modelo_ausente_marca_falha(self):
        estado = {**ESTADO, "model_available": False}
        out = ui.render_str(boot_panel(estado), 100)
        assert "não encontrado" in out
        assert ui.GLYPH_FAIL in out

    def test_contexto_aplicado_aparece(self):
        out = ui.render_str(boot_panel(ESTADO), 100)
        assert "32.768 tokens" in out

    def test_contexto_cortado_mostra_o_pedido(self):
        """O aplicado quase nunca é o pedido — RAM, modelfile e teto do provider
        cortam. Mostrar só o pedido foi por anos a origem de 'configurei 128k e
        ele truncou'."""
        estado = {**ESTADO, "context": {"requested": 131072, "applied": 32768}}
        out = ui.render_str(boot_panel(estado), 100)
        assert "32.768 tokens" in out
        assert "131.072" in out  # o pedido aparece como divergência

    def test_memoria_em_gb(self):
        out = ui.render_str(boot_panel(ESTADO), 100)
        assert "46.0 GB livres" in out and "61 GB" in out

    def test_tools_e_modo(self):
        out = ui.render_str(boot_panel(ESTADO, tool_mode="native",
                                       tools=["read_file", "patch"]), 100)
        assert "2 carregadas" in out and "native" in out

    def test_modo_none_e_falha(self):
        """`none` = o modelo não chama tool nenhuma. É a diferença entre um
        agente e um chat, e passava despercebido."""
        out = ui.render_str(boot_panel(ESTADO, tool_mode="none", tools=[]), 100)
        assert ui.GLYPH_FAIL in out

    def test_selo_local_e_nuvem(self):
        g = ui.active_glyphs()
        assert g.seal_local in ui.render_str(boot_panel(ESTADO, local=True), 100)
        assert g.seal_cloud in ui.render_str(boot_panel(ESTADO, local=False), 100)

    def test_estado_vazio_nao_quebra(self):
        ui.render_str(boot_panel({}), 100)

    def test_avisos_do_doctor_aparecem(self):
        estado = {**ESTADO, "notes": ["contexto reduzido por RAM"]}
        out = ui.render_str(boot_panel(estado), 100)
        assert "contexto reduzido por RAM" in out

    def test_avisos_tem_teto(self):
        """Doctor tagarela não pode empurrar a sessão para fora da tela."""
        estado = {**ESTADO, "notes": [f"aviso {i}" for i in range(20)]}
        out = ui.render_str(boot_panel(estado), 100)
        assert "aviso 0" in out
        assert "aviso 15" not in out


class TestProcedencia:
    """Um painel que afirma '46 GB livres' sem dizer que a medição é de três
    dias atrás mente com números verdadeiros."""

    def test_medido_agora(self):
        # `_agora_iso()` resolvido AQUI, na execução do teste — não no import.
        out = ui.render_str(boot_panel({**ESTADO, "generated_at": _agora_iso()}), 100)
        assert "verificado agora" in out

    def test_cache_diz_a_idade(self):
        estado = {**ESTADO, "generated_at": _antes(days=3)}
        out = ui.render_str(boot_panel(estado), 100)
        assert "cache" in out and "3 d" in out
        assert "bauer doctor" in out  # e diz como remedir

    def test_idade_em_varias_escalas(self):
        assert _idade(_agora_iso()) == "agora"
        assert _idade(_antes(minutes=10)) == "10 min"
        assert _idade(_antes(hours=5)) == "5 h"
        assert _idade(_antes(days=2)) == "2 d"

    def test_timestamp_invalido_nao_quebra(self):
        assert _idade("nao-e-data") == ""
        assert _idade("") == ""
        ui.render_str(boot_panel({**ESTADO, "generated_at": "lixo"}), 100)

    def test_timestamp_sem_timezone_nao_quebra(self):
        """`datetime.now().isoformat()` (sem tz) aparece em state antigo."""
        ingenuo = datetime.now().isoformat(timespec="seconds")
        assert _idade(ingenuo) != ""

    def test_fixture_nao_congela_o_relogio(self):
        """Trava a regressão: `ESTADO` não pode voltar a trazer `generated_at`.

        Um timestamp no dict de módulo é avaliado no import, e a distância até
        a execução do teste vira função da velocidade da máquina — passa local,
        falha em runner lento. Foi assim que este arquivo quebrou o CI do
        Windows (import → teste levou 3 min lá, segundos aqui).
        """
        assert "generated_at" not in ESTADO, (
            "ESTADO voltou a fixar generated_at no import — o relógio congela "
            "e o teste passa a medir a velocidade da máquina, não o código"
        )


class TestNaoMedeParaExibir:
    """A restrição que manda na fase: nada de checagem nova para ter o que
    mostrar. O painel lê o `runtime_state` que o boot já tinha."""

    def test_boot_nao_faz_chamada_de_rede(self):
        from unittest.mock import patch

        with patch("httpx.get") as g, patch("httpx.post") as p, patch("httpx.stream") as s:
            ui.render_str(boot_panel(ESTADO, tool_mode="native", tools=["x"]), 100)
        g.assert_not_called()
        p.assert_not_called()
        s.assert_not_called()

    def test_boot_nao_chama_o_doctor(self):
        from unittest.mock import patch

        with patch("bauer.preflight.run_doctor") as doctor:
            ui.render_str(boot_panel(ESTADO), 100)
        doctor.assert_not_called()

    def test_render_e_barato(self):
        """Teto grosseiro, para pegar regressão de ordem de grandeza (ex.:
        alguém trocar a leitura do dict por uma chamada de sistema)."""
        import time

        t0 = time.perf_counter()
        for _ in range(200):
            boot_panel(ESTADO, tool_mode="native", tools=["a", "b", "c"])
        assert (time.perf_counter() - t0) < 1.0


class TestServePanel:
    def test_cockpit_em_destaque(self):
        """O SPA é servido em '/' desde sempre; o boot só imprimia 'HTTP' e
        ninguém sabia que existia."""
        out = ui.render_str(serve_panel("http://127.0.0.1:8000", cockpit=True,
                                        auth=True), 100)
        assert "cockpit" in out and "http://127.0.0.1:8000" in out

    def test_sem_cockpit_nao_promete(self):
        """Instalação sem os assets do SPA não pode anunciar uma tela que não
        vai abrir."""
        out = ui.render_str(serve_panel("http://x:8000", cockpit=False, auth=True), 100)
        assert "cockpit" not in out

    def test_auth_desabilitada_e_aviso_explicito(self):
        out = ui.render_str(serve_panel("http://0.0.0.0:8000", cockpit=True,
                                        auth=False), 100)
        assert "desabilitada" in out
        assert "qualquer um na rede" in out

    def test_auth_habilitada(self):
        out = ui.render_str(serve_panel("http://x:8000", cockpit=True, auth=True), 100)
        assert "habilitada" in out


class TestSegueOTema:
    def test_linhas_usam_o_acento_atual(self):
        from bauer import theme

        anterior = theme.ACCENT_NAME
        try:
            theme.set_accent("lima")
            painel = serve_panel("http://x", cockpit=True, auth=True)
            assert painel is not None  # renderiza no acento novo sem erro
            ui.render_str(painel, 80)
        finally:
            theme.set_accent(anterior)

    def test_boot_line_respeita_glifos_ascii(self):
        from bauer import theme

        anterior = ui.active_glyphs()
        try:
            ui.use_glyphs(theme.ASCII)
            out = ui.render_str(boot_line("modelo", "x", status="ok"), 60)
            out.encode("ascii")  # não pode levantar
        finally:
            ui.use_glyphs(anterior)
