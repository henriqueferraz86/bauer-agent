"""Testes da fonte única de tokens visuais (bauer/theme.py) — F0 do plano 028.

O teste que mais importa aqui é `test_css_gerado_esta_sincronizado`: sem ele o
Python e o CSS do SPA voltam a divergir em silêncio, que é exatamente como as
três paletas apareceram.
"""

from __future__ import annotations

import io
from pathlib import Path

import pytest

from bauer import theme

_REPO = Path(__file__).resolve().parents[1]


class TestPonteCSS:
    def test_css_gerado_esta_sincronizado(self):
        """desktop/src/tokens.css tem que ser byte a byte o que o Python gera."""
        css = _REPO / theme.CSS_PATH
        assert css.exists(), f"rode: python -m bauer.theme  (falta {theme.CSS_PATH})"
        assert css.read_text(encoding="utf-8") == theme.export_css_vars(), (
            "tokens.css divergiu de bauer/theme.py — regenere com "
            "`python -m bauer.theme` em vez de editar o CSS à mão"
        )

    def test_todo_token_vira_variavel_css(self):
        css = theme.export_css_vars()
        for name, value in theme.TOKENS.items():
            assert f"--{name}: {value};" in css

    def test_alias_aponta_para_token_existente(self):
        for alias, target in theme._ALIASES.items():
            assert target in theme.TOKENS, f"alias --{alias} aponta para token inexistente"

    def test_css_e_ascii_puro(self):
        """O CSS é lido por ferramentas de build com encodings variados —
        não-ASCII aqui só cria chance de mojibake (e o gerador já quebrou
        uma vez por causa disso, no cp1252 do console Windows)."""
        theme.export_css_vars().encode("ascii")


class TestContraste:
    def test_acento_de_texto_passa_em_aa(self):
        """ACCENT_TEXT carrega palavra legível — precisa de >= 4.5:1."""
        assert theme.contrast_ratio(theme.ACCENT_TEXT) >= 4.5

    def test_acento_puro_passa_em_aa_mas_sem_folga(self):
        """O acento puro passa em AA (>=4.5) — medido 4.95. A folga é de 0.45,
        e é POR ISSO que existe o ACCENT_TEXT: qualquer mexida no fundo derruba
        o acento puro abaixo do piso, e aí o dano seria em texto."""
        r = theme.contrast_ratio(theme.ACCENT)
        assert r >= 4.5
        assert r < 7.0  # não chega em AAA — não sirva corpo de texto com ele

    def test_acento_de_texto_tem_folga_aaa(self):
        assert theme.contrast_ratio(theme.ACCENT_TEXT) >= 7.0

    def test_cores_de_sinal_sao_legiveis(self):
        for cor in (theme.OK, theme.WARN, theme.BAD, theme.WHITE, theme.CLOUD):
            assert theme.contrast_ratio(cor) >= 4.5

    def test_contraste_conhecido(self):
        # branco puro sobre preto puro = 21:1 (âncora da fórmula)
        assert theme.contrast_ratio("#ffffff", "#000000") == pytest.approx(21.0, abs=0.01)
        assert theme.contrast_ratio("#ffffff", "#ffffff") == pytest.approx(1.0, abs=0.01)


class _FakeStream:
    """StringIO tem `encoding` somente-leitura; a detecção só precisa do
    atributo, então um objeto simples serve — e prova que qualquer stream
    (não só arquivos reais) é aceito."""

    def __init__(self, encoding: str) -> None:
        self.encoding = encoding


class TestQuedaParaAscii:
    def test_console_cp1252_cai_para_ascii(self):
        """cmd legado não codifica ▰◆⟐ — a decisão é por teste de encode real,
        não por adivinhar pela plataforma."""
        stream = _FakeStream("cp1252")
        assert theme.unicode_enabled({}, stream=stream) is False
        assert theme.glyphs({}, stream=stream) is theme.ASCII

    def test_console_utf8_mantem_unicode(self):
        stream = _FakeStream("utf-8")
        assert theme.unicode_enabled({}, stream=stream) is True
        assert theme.glyphs({}, stream=stream) is theme.UNICODE

    def test_encoding_desconhecido_nao_quebra(self):
        assert theme.unicode_enabled({}, stream=_FakeStream("codec-que-nao-existe")) is False

    def test_stream_sem_encoding_assume_unicode(self):
        assert theme.unicode_enabled({}, stream=io.StringIO()) is True

    def test_bauer_ui_plain_forca_ascii(self):
        assert theme.glyphs({"BAUER_UI": "plain"}, stream=_FakeStream("utf-8")) is theme.ASCII

    def test_no_color_desliga_cor(self):
        assert theme.color_enabled({"NO_COLOR": "1"}) is False
        assert theme.color_enabled({}) is True
        assert theme.color_enabled({"BAUER_UI": "plain"}) is False

    def test_ascii_cobre_todos_os_glifos(self):
        """Todo glifo Unicode tem par ASCII não-vazio e codificável."""
        for campo in theme.UNICODE.__dataclass_fields__:
            valor = getattr(theme.ASCII, campo)
            assert valor, f"glifo ASCII vazio: {campo}"
            valor.encode("ascii")


class TestFonteUnica:
    def test_ui_kit_usa_o_theme(self):
        from bauer import ui

        assert ui.ACCENT == theme.ACCENT
        assert ui.WHITE == theme.WHITE
        assert ui.GRADIENT == theme.BRAND_GRADIENT

    def test_intro_e_indicators_usam_o_theme(self):
        from bauer import ascii_intro, indicators

        assert ascii_intro._GRADIENT == theme.BRAND_GRADIENT
        assert indicators.ACCENT == theme.ACCENT
        assert indicators.SUCCESS == theme.OK

    def test_nenhum_modulo_de_ui_tem_hex_solto(self):
        """Regressão da divergência: cor literal fora do theme.py volta a criar
        uma segunda paleta. Vale para o kit visual, não para o resto do repo."""
        import re

        alvos = ["ui.py", "ascii_intro.py", "indicators.py"]
        permitidos = {"#a855f7"}  # nenhum esperado hoje; a lista existe p/ exceção consciente
        for nome in alvos:
            texto = (_REPO / "bauer" / nome).read_text(encoding="utf-8")
            achados = {h.lower() for h in re.findall(r"#[0-9a-fA-F]{6}\b", texto)}
            assert not (achados - permitidos), (
                f"{nome} tem cor literal {achados - permitidos} — importe de bauer/theme.py"
            )
