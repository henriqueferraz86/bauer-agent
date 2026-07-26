"""Deteccao de resposta degenerada do modelo (loop de repeticao).

Caso real (qwen3.6:27b, modo bridge, contexto apertado): o modelo tentou emitir
um tool call e degenerou em `{"{"{"{"{"...` por dezenas de repeticoes. Como nao
e JSON valido, `_try_parse_tool` devolvia None e o agente tratava a string como
RESPOSTA FINAL — imprimindo o lixo para o usuario como se fosse a answer.

E o pior desfecho possivel: nao parece erro, parece resposta.
"""

from __future__ import annotations

import pytest

from bauer.agent import _e_resposta_degenerada


class TestDetectaDegenerada:
    def test_o_caso_real(self):
        """Exatamente o que apareceu na tela do usuario."""
        assert _e_resposta_degenerada('{"' * 24) is True

    @pytest.mark.parametrize("txt", [
        "{" * 80,
        '{"' * 40,
        "ababababababababababababababababababababababab",
    ])
    def test_variacoes_de_loop(self, txt):
        assert _e_resposta_degenerada(txt) is True


class TestNaoCensuraRespostaLegitima:
    """Falso positivo aqui e PIOR que deixar passar: censuraria uma resposta
    boa do modelo. Por isso a heuristica e conservadora."""

    @pytest.mark.parametrize("txt", [
        "Vou criar o aplicativo de noticias sobre IA. Primeiro os requisitos, "
        "depois a estrutura dos documentos de planejamento.",
        '{"action": "write_file", "args": {"path": "docs/SPEC.md", '
        '"content": "# Especificacao do buscador de noticias"}}',
        "| col a | col b |\n|---|---|\n| 1 | 2 |\n| 3 | 4 |\n| 5 | 6 |\n| 7 | 8 |",
        "- item um\n- item dois\n- item tres\n- item quatro\n- item cinco",
        "ok",
        "",
        "Sim.",
        "\n".join(f"linha {i}" for i in range(20)),
    ])
    def test_passa_limpo(self, txt):
        assert _e_resposta_degenerada(txt) is False

    def test_so_whitespace_fica_com_o_caminho_de_vazio(self):
        """Divisao de responsabilidade: whitespace puro vira "" no strip() e e
        tratado pelo check de resposta VAZIA, que ja existia e tem o seu proprio
        recovery. O detector so cuida de lixo com conteudo."""
        assert _e_resposta_degenerada(chr(10) * 60) is False
        assert _e_resposta_degenerada("   " * 40) is False

    def test_texto_curto_repetitivo_nao_conta(self):
        """Abaixo do minimo nao ha evidencia suficiente de loop."""
        assert _e_resposta_degenerada("{{{{") is False

    def test_codigo_com_muita_chave_nao_e_degenerado(self):
        codigo = (
            "def handler(evento):\n"
            "    if evento:\n"
            "        return {'ok': True, 'dados': {'id': 1, 'nome': 'x'}}\n"
            "    return {'ok': False}\n"
        )
        assert _e_resposta_degenerada(codigo) is False
