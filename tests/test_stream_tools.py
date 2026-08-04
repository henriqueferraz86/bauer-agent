"""Testes do acumulador de tool calls fatiadas (bauer/stream_tools.py) — plano 028 F1.

Os três dialetos abaixo são reais e produzem a MESMA chamada. Se o acumulador
errar, o sintoma no usuário não é "streaming feio": é tool call com nome
inválido ou argumento truncado — ou seja, o agente executando a coisa errada.
"""

from __future__ import annotations

import json

from bauer.stream_tools import ToolCallAccumulator

CONHECIDAS = {"read_file", "write_file", "run_command"}


def _fragmentos_openai() -> list[dict]:
    """OpenAI: id e name UMA vez, arguments fatiado."""
    return [
        {"index": 0, "id": "call_a", "type": "function",
         "function": {"name": "read_file", "arguments": ""}},
        {"index": 0, "function": {"arguments": '{"pa'}},
        {"index": 0, "function": {"arguments": 'th": "a'}},
        {"index": 0, "function": {"arguments": 'uth.py"}'}},
    ]


def _fragmentos_repetidor() -> list[dict]:
    """Compatíveis que repetem id e name inteiros em todo fragmento."""
    return [
        {"index": 0, "id": "call_a", "function": {"name": "read_file", "arguments": '{"pa'}},
        {"index": 0, "id": "call_a", "function": {"name": "read_file", "arguments": 'th": "a'}},
        {"index": 0, "id": "call_a", "function": {"name": "read_file", "arguments": 'uth.py"}'}},
    ]


def _fragmentos_fatiador() -> list[dict]:
    """Provider que fatia até o nome da tool."""
    return [
        {"index": 0, "id": "call_a", "function": {"name": "read_", "arguments": ""}},
        {"index": 0, "function": {"name": "file", "arguments": '{"path": '}},
        {"index": 0, "function": {"arguments": '"auth.py"}'}},
    ]


class TestDialetos:
    def test_openai(self):
        acc = ToolCallAccumulator(CONHECIDAS)
        for f in _fragmentos_openai():
            acc.add_fragment(f)
        assert acc.result() == [{
            "id": "call_a", "type": "function",
            "function": {"name": "read_file", "arguments": '{"path": "auth.py"}'},
        }]

    def test_repetidor_nao_duplica_o_nome(self):
        acc = ToolCallAccumulator(CONHECIDAS)
        for f in _fragmentos_repetidor():
            acc.add_fragment(f)
        (tc,) = acc.result()
        assert tc["function"]["name"] == "read_file"  # não "read_fileread_file..."
        assert json.loads(tc["function"]["arguments"]) == {"path": "auth.py"}

    def test_fatiador_reconstroi_o_nome(self):
        acc = ToolCallAccumulator(CONHECIDAS)
        for f in _fragmentos_fatiador():
            acc.add_fragment(f)
        (tc,) = acc.result()
        assert tc["function"]["name"] == "read_file"

    def test_os_tres_dialetos_convergem(self):
        """A garantia que importa: mesma chamada, venha como vier."""
        saidas = []
        for frags in (_fragmentos_openai(), _fragmentos_repetidor(), _fragmentos_fatiador()):
            acc = ToolCallAccumulator(CONHECIDAS)
            for f in frags:
                acc.add_fragment(f)
            (tc,) = acc.result()
            saidas.append((tc["function"]["name"], json.loads(tc["function"]["arguments"])))
        assert saidas[0] == saidas[1] == saidas[2]


class TestChamadasParalelas:
    def test_duas_tools_no_mesmo_turno(self):
        acc = ToolCallAccumulator(CONHECIDAS)
        for f in [
            {"index": 0, "id": "c0", "function": {"name": "read_file", "arguments": '{"path":'}},
            {"index": 1, "id": "c1", "function": {"name": "run_command", "arguments": '{"command":'}},
            {"index": 0, "function": {"arguments": ' "a.py"}'}},
            {"index": 1, "function": {"arguments": ' "ls"}'}},
        ]:
            acc.add_fragment(f)
        r = acc.result()
        assert [t["function"]["name"] for t in r] == ["read_file", "run_command"]
        assert json.loads(r[0]["function"]["arguments"]) == {"path": "a.py"}
        assert json.loads(r[1]["function"]["arguments"]) == {"command": "ls"}

    def test_ordem_segue_a_chegada_nao_o_indice(self):
        acc = ToolCallAccumulator(CONHECIDAS)
        acc.add_fragment({"index": 5, "id": "c5", "function": {"name": "write_file", "arguments": "{}"}})
        acc.add_fragment({"index": 2, "id": "c2", "function": {"name": "read_file", "arguments": "{}"}})
        assert [t["id"] for t in acc.result()] == ["c5", "c2"]


class TestFormatoOllama:
    def test_tool_call_inteira_com_arguments_objeto(self):
        """O Ollama entrega a call pronta e `arguments` como OBJETO; o agent
        espera STRING — a conversão é aqui, senão o json.loads do agent recebe
        um dict e levanta."""
        acc = ToolCallAccumulator(CONHECIDAS)
        acc.add_complete({"function": {"name": "read_file", "arguments": {"path": "auth.py"}}})
        (tc,) = acc.result()
        assert isinstance(tc["function"]["arguments"], str)
        assert json.loads(tc["function"]["arguments"]) == {"path": "auth.py"}

    def test_id_sintetico_quando_provider_nao_manda(self):
        acc = ToolCallAccumulator(CONHECIDAS)
        acc.add_complete({"function": {"name": "read_file", "arguments": {}}})
        assert acc.result()[0]["id"] == "call_0"

    def test_acentos_preservados(self):
        acc = ToolCallAccumulator(CONHECIDAS)
        acc.add_complete({"function": {"name": "write_file", "arguments": {"texto": "ação"}}})
        assert "ação" in acc.result()[0]["function"]["arguments"]


class TestBordas:
    def test_sem_fragmento_nenhum(self):
        acc = ToolCallAccumulator(CONHECIDAS)
        assert acc.result() == []
        assert bool(acc) is False

    def test_fragmento_so_com_argumentos_e_descartado(self):
        """Sem nome não há o que chamar — devolver isso ao agent viraria
        KeyError ou, pior, uma chamada a tool ''."""
        acc = ToolCallAccumulator(CONHECIDAS)
        acc.add_fragment({"index": 0, "function": {"arguments": '{"path": "x"}'}})
        assert acc.result() == []

    def test_arguments_vazio_vira_objeto_vazio(self):
        """String vazia estouraria o json.loads do agent."""
        acc = ToolCallAccumulator(CONHECIDAS)
        acc.add_fragment({"index": 0, "id": "c", "function": {"name": "read_file"}})
        assert acc.result()[0]["function"]["arguments"] == "{}"

    def test_fragmento_sem_indice_cai_na_chamada_aberta(self):
        acc = ToolCallAccumulator(CONHECIDAS)
        acc.add_fragment({"index": 0, "id": "c", "function": {"name": "read_file", "arguments": '{"p":'}})
        acc.add_fragment({"function": {"arguments": ' 1}'}})
        assert acc.result()[0]["function"]["arguments"] == '{"p": 1}'

    def test_lixo_nao_levanta(self):
        acc = ToolCallAccumulator(CONHECIDAS)
        acc.add_fragment(None)          # type: ignore[arg-type]
        acc.add_fragment({"index": 0})
        acc.add_fragment({"index": 0, "function": "não é dict"})
        acc.add_complete(None)          # type: ignore[arg-type]
        assert acc.result() == []

    def test_sem_lista_de_conhecidas_ainda_funciona(self):
        acc = ToolCallAccumulator()
        for f in _fragmentos_openai():
            acc.add_fragment(f)
        assert acc.result()[0]["function"]["name"] == "read_file"
