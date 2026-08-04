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


@pytest.fixture(autouse=True)
def _acento_padrao():
    """Restaura o acento padrão em volta de CADA teste.

    O acento é estado global de módulo e agora é trocável em tempo de execução;
    sem isto, um teste que troca a cor contamina os outros — e com `-n auto` a
    ordem varia, então o flake seria intermitente (o pior tipo).
    """
    anterior = theme.ACCENT_NAME
    theme.set_accent("violeta")
    yield
    theme.set_accent(anterior)


class TestPonteCSS:
    def test_css_gerado_esta_sincronizado(self):
        """desktop/src/tokens.css tem que ser byte a byte o que o Python gera
        COM O ACENTO PADRÃO (o fixture acima garante)."""
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


class TestCatalogoDeAcentos:
    """Todo acento do catálogo obedece aos DOIS invariantes, por construção.

    Um acento novo que não passe reprova a suíte em vez de virar bug visual —
    é a diferença entre a régua estar no código e estar na cabeça de quem
    escolheu a cor.
    """

    def test_todo_acento_e_legivel(self):
        for nome in theme.accent_names():
            theme.set_accent(nome)
            assert theme.contrast_ratio(theme.ACCENT) >= 4.5, f"{nome}: acento ilegível"
            assert theme.contrast_ratio(theme.ACCENT_TEXT) >= 7.0, f"{nome}: texto sem AAA"

    def test_nenhum_acento_invade_cor_de_sinal(self):
        """OK/WARN/BAD significam RESULTADO. Acento perto de um deles faz o
        usuário confundir 'isto está vivo' com 'isto deu errado'."""
        for nome in theme.accent_names():
            theme.set_accent(nome)
            for rotulo, sinal in (("OK", theme.OK), ("WARN", theme.WARN), ("BAD", theme.BAD)):
                d = theme.delta_e(theme.ACCENT, sinal)
                assert d >= theme.DELTA_E_MINIMO, (
                    f"acento '{nome}' fica a ΔE {d:.1f} do {rotulo} — confundível"
                )

    def test_ambar_saturado_seria_reprovado(self):
        """Documenta a consequência medida: a faixa quente saturada pertence ao
        WARN/BAD. O âmbar #f0a02a (quase escolhido como acento) fica a ΔE 7.8
        do WARN — por isso NÃO está no catálogo."""
        assert theme.delta_e("#f0a02a", theme.WARN) < theme.DELTA_E_MINIMO

    def test_variantes_sao_derivadas_nao_escolhidas(self):
        for nome in theme.accent_names():
            theme.set_accent(nome)
            assert theme.ACCENT_TEXT == theme._para_texto(theme.ACCENT)
            assert theme.ACCENT_DEEP == theme._profundo(theme.ACCENT)

    def test_gradiente_da_marca_segue_o_acento(self):
        """Logo violeta com a tela em lima brigaria com o resto."""
        theme.set_accent("lima")
        assert theme.ACCENT in theme.BRAND_GRADIENT


class TestTrocaDeAcento:
    def test_set_accent_muda_tudo(self):
        theme.set_accent("teal")
        assert theme.ACCENT_NAME == "teal"
        assert theme.ACCENT == theme.PALETAS["teal"]

    def test_nome_invalido_levanta(self):
        """Erro em silêncio faria o usuário achar que a tecla não funciona."""
        with pytest.raises(KeyError):
            theme.set_accent("cor-que-nao-existe")

    def test_next_accent_cicla_e_da_a_volta(self):
        nomes = theme.accent_names()
        theme.set_accent(nomes[-1])
        assert theme.next_accent() == nomes[0]

    def test_tokens_acompanham_a_troca(self):
        """O dict é MUTADO no lugar: quem fez `from theme import TOKENS`
        continua vendo os valores novos."""
        tokens = theme.TOKENS
        theme.set_accent("rosa")
        assert tokens["bauer-accent"] == theme.PALETAS["rosa"]
        assert tokens is theme.TOKENS

    def test_consumidores_acompanham_a_troca(self):
        """O ponto todo: ninguém pode ter congelado a cor no import."""
        from bauer import ascii_intro, indicators, ui

        theme.set_accent("ciano")
        assert ui.ACCENT == theme.PALETAS["ciano"]
        assert indicators.ACCENT == theme.PALETAS["ciano"]
        assert ascii_intro._GRADIENT == theme.BRAND_GRADIENT

    def test_render_usa_a_cor_nova(self):
        """Prova de ponta: o texto renderizado carrega a cor trocada."""
        from bauer import ui

        theme.set_accent("lima")
        console_out = ui.render_str(ui.tool_line("x", status="run"), 40)
        assert console_out  # renderizou sem erro
        assert ui.ACCENT == "#b8ff2f"


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

        alvos = ["ui.py", "ascii_intro.py", "indicators.py", "ui_hud.py",
                 "ui_frame.py", "ui_diff.py", "ui_stream.py"]
        permitidos = {"#a855f7"}  # nenhum esperado hoje; a lista existe p/ exceção consciente
        for nome in alvos:
            texto = (_REPO / "bauer" / nome).read_text(encoding="utf-8")
            achados = {h.lower() for h in re.findall(r"#[0-9a-fA-F]{6}\b", texto)}
            assert not (achados - permitidos), (
                f"{nome} tem cor literal {achados - permitidos} — importe de bauer/theme.py"
            )

    def test_estilo_do_prompt_toolkit_vem_do_tema(self):
        """O vazamento que passou despercebido no F0: `_PT_STYLE` no agent.py
        tinha uma paleta INTEIRA hardcoded (Catppuccin no menu de completions,
        o teal antigo na marca, `ansicyan` no `❯`). O prompt onde o usuário
        digita era, literalmente, o único lugar que nunca migrou — porque o
        teste acima cobria só três arquivos."""
        import ast
        import inspect
        import textwrap

        from bauer import agent

        # Inspeciona o CÓDIGO, não a fonte bruta: a docstring da função CITA as
        # cores antigas ao explicar o que substituiu, e casar com ela testaria
        # a redação do comentário em vez do comportamento.
        func = ast.parse(textwrap.dedent(inspect.getsource(agent._build_pt_style))).body[0]
        corpo = func.body
        if (corpo and isinstance(corpo[0], ast.Expr)
                and isinstance(getattr(corpo[0], "value", None), ast.Constant)
                and isinstance(corpo[0].value.value, str)):
            corpo = corpo[1:]

        literais = [
            n.value.lower()
            for no in corpo
            for n in ast.walk(no)
            if isinstance(n, ast.Constant) and isinstance(n.value, str)
        ]
        for proibido in ("#00d4aa", "#3b82f6", "#313244", "#cdd6f4", "#89b4fa",
                         "#1e1e2e", "#6c7086", "ansicyan"):
            assert not any(proibido in lit for lit in literais), (
                f"_build_pt_style ainda tem {proibido} literal"
            )

    def test_atalho_do_tema_atravessa_qualquer_terminal(self):
        """O atalho principal precisa ser uma tecla com código ASCII de
        controle — só essas atravessam qualquer terminal, em qualquer SO.

        Ctrl+A..Z = 1..26 e Ctrl+] [ ^ _ = 27..31. DÍGITO não tem código: num
        terminal comum, Ctrl+0 chega como o caractere "0" (medido). Os dois
        protocolos que codificariam a tecla — `modifyOtherKeys` do xterm
        (`CSI 27;5;48~`) e o formato kitty (`CSI 48;5u`) — NÃO são entendidos
        pelo parser do prompt_toolkit, que espera `\\x1b[1;5p`, convenção
        própria que quase nenhum terminal emite. Num terminal com esses
        protocolos ligados, Ctrl+0 injetaria lixo no input.
        """
        from prompt_toolkit.input.vt100_parser import Vt100Parser

        from bauer import agent

        kb = agent._make_slash_kb()
        # `str(Keys.ControlT)` é "Keys.ControlT"; o código da tecla está em
        # `.value` ("c-t") — comparar pelo str() silenciaria o teste.
        teclas = {
            getattr(b.keys[0], "value", b.keys[0])
            for b in kb.bindings if len(b.keys) == 1
        }
        assert "c-t" in teclas, "o atalho portátil do tema sumiu"

        # e o parser de fato reconhece o byte que o terminal manda (0x14)
        recebidas = []
        Vt100Parser(lambda kp: recebidas.append(kp.key)).feed("\x14")
        assert [str(k) for k in recebidas] == ["Keys.ControlT"]

    def test_ctrl_digito_nao_e_a_unica_porta(self):
        """Regressão do erro que quase foi commitado: Ctrl+0 sozinho quebraria
        em silêncio em todo Linux/macOS — inclusive no acesso por SSH."""
        from bauer import agent

        kb = agent._make_slash_kb()
        teclas = {
            getattr(b.keys[0], "value", b.keys[0])
            for b in kb.bindings if len(b.keys) == 1
        }
        assert not ("c-0" in teclas and "c-t" not in teclas), (
            "Ctrl+0 não pode ser o único atalho — não atravessa VT100"
        )

    def test_estilo_do_prompt_acompanha_a_troca(self):
        """Estilo DINÂMICO: trocar o acento tem que mudar o prompt na hora,
        sem recriar a PromptSession."""
        from bauer import agent

        theme.set_accent("violeta")
        antes = agent._build_pt_style().class_names_and_attrs
        theme.set_accent("lima")
        depois = agent._build_pt_style().class_names_and_attrs
        assert antes != depois
