"""Streaming COM function calling nos dois clients (plano 028 F1).

Até aqui `chat_with_tools` era `"stream": False` nos dois — ou seja, o caminho
NATIVO (o padrão dos modelos capazes) não tinha streaming nenhum, e era por ele
que passava quase todo turno do `bauer agent`. Estes testes fixam o contrato
novo: com `on_delta`, o texto chega em pedaços e o dict de retorno é o mesmo.
"""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from bauer.ollama_client import OllamaClient, OllamaError
from bauer.openai_client import OpenAIClient, OpenAIClientError

TOOLS = [
    {"type": "function", "function": {"name": "read_file", "parameters": {}}},
    {"type": "function", "function": {"name": "run_command", "parameters": {}}},
]


class _RespostaFalsa:
    """Dublê do context manager de httpx.stream."""

    def __init__(self, linhas: list[str], status: int = 200) -> None:
        self._linhas = linhas
        self.status_code = status

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def iter_lines(self):
        yield from self._linhas

    def iter_bytes(self):
        yield "".join(self._linhas).encode()


def _sse(objs: list[dict]) -> list[str]:
    return [f"data: {json.dumps(o)}" for o in objs] + ["data: [DONE]"]


# ─── OpenAI-compat ───────────────────────────────────────────────────────────


class TestOpenAIStreamComTools:
    def _cliente(self) -> OpenAIClient:
        return OpenAIClient(host="http://x", api_key="k")

    def test_texto_chega_em_pedacos(self):
        linhas = _sse([
            {"choices": [{"delta": {"content": "Vou "}}]},
            {"choices": [{"delta": {"content": "ler "}}]},
            {"choices": [{"delta": {"content": "o arquivo."}}]},
        ])
        vistos: list[str] = []
        with patch("httpx.stream", return_value=_RespostaFalsa(linhas)):
            msg = self._cliente().chat_with_tools(
                "m", [], TOOLS, on_delta=vistos.append,
            )
        assert vistos == ["Vou ", "ler ", "o arquivo."]
        assert msg["content"] == "Vou ler o arquivo."
        assert msg["tool_calls"] == []

    def test_tool_call_fatiada_e_remontada(self):
        linhas = _sse([
            {"choices": [{"delta": {"tool_calls": [
                {"index": 0, "id": "c1", "function": {"name": "read_file", "arguments": ""}}
            ]}}]},
            {"choices": [{"delta": {"tool_calls": [
                {"index": 0, "function": {"arguments": '{"path":'}}
            ]}}]},
            {"choices": [{"delta": {"tool_calls": [
                {"index": 0, "function": {"arguments": ' "auth.py"}'}}
            ]}}]},
        ])
        with patch("httpx.stream", return_value=_RespostaFalsa(linhas)):
            msg = self._cliente().chat_with_tools("m", [], TOOLS, on_delta=lambda _: None)
        (tc,) = msg["tool_calls"]
        assert tc["function"]["name"] == "read_file"
        assert json.loads(tc["function"]["arguments"]) == {"path": "auth.py"}

    def test_texto_e_tool_call_no_mesmo_turno(self):
        linhas = _sse([
            {"choices": [{"delta": {"content": "Deixa eu ver."}}]},
            {"choices": [{"delta": {"tool_calls": [
                {"index": 0, "id": "c1", "function": {"name": "read_file", "arguments": "{}"}}
            ]}}]},
        ])
        vistos: list[str] = []
        with patch("httpx.stream", return_value=_RespostaFalsa(linhas)):
            msg = self._cliente().chat_with_tools("m", [], TOOLS, on_delta=vistos.append)
        assert vistos == ["Deixa eu ver."]
        assert msg["tool_calls"][0]["function"]["name"] == "read_file"

    def test_usage_capturado_do_evento_final(self):
        """Sem isto o custo do turno em streaming ficaria zerado."""
        linhas = _sse([
            {"choices": [{"delta": {"content": "ok"}}]},
            {"choices": [], "usage": {"prompt_tokens": 10, "completion_tokens": 3}},
        ])
        c = self._cliente()
        with patch("httpx.stream", return_value=_RespostaFalsa(linhas)):
            c.chat_with_tools("m", [], TOOLS, on_delta=lambda _: None)
        assert c.last_usage["prompt_tokens"] == 10

    def test_erro_http_preserva_deteccao_de_downgrade(self):
        """O agent decide rebaixar para o bridge procurando 'HTTP <code>' na
        mensagem. Se o formato mudar, um provider sem tools derruba a sessão
        em vez de degradar."""
        from bauer.agent import _is_native_unsupported_error

        with patch("httpx.stream", return_value=_RespostaFalsa(["nope"], status=400)):
            with pytest.raises(OpenAIClientError) as exc:
                self._cliente().chat_with_tools("m", [], TOOLS, on_delta=lambda _: None)
        assert _is_native_unsupported_error(exc.value)

    def test_render_quebrado_nao_derruba_a_chamada(self):
        linhas = _sse([{"choices": [{"delta": {"content": "texto"}}]}])

        def _explode(_):
            raise RuntimeError("terminal morreu")

        with patch("httpx.stream", return_value=_RespostaFalsa(linhas)):
            msg = self._cliente().chat_with_tools("m", [], TOOLS, on_delta=_explode)
        assert msg["content"] == "texto"

    def test_sem_on_delta_usa_caminho_nao_streaming(self):
        """Compatibilidade: quem não pede streaming não pode virar streaming."""
        with patch("httpx.stream") as stream, patch("httpx.post") as post:
            post.return_value.status_code = 200
            post.return_value.json.return_value = {
                "choices": [{"message": {"content": "ok", "tool_calls": None}}],
                "usage": {},
            }
            self._cliente().chat_with_tools("m", [], TOOLS)
        stream.assert_not_called()
        post.assert_called_once()


# ─── Ollama ──────────────────────────────────────────────────────────────────


class TestOllamaStreamComTools:
    def _cliente(self) -> OllamaClient:
        return OllamaClient("http://localhost:11434")

    def test_texto_chega_em_pedacos(self):
        linhas = [
            json.dumps({"message": {"content": "Vou "}}),
            json.dumps({"message": {"content": "ler."}}),
            json.dumps({"message": {}, "done": True,
                        "prompt_eval_count": 12, "eval_count": 4}),
        ]
        vistos: list[str] = []
        with patch("httpx.stream", return_value=_RespostaFalsa(linhas)):
            msg = self._cliente().chat_with_tools("m", [], TOOLS, on_delta=vistos.append)
        assert vistos == ["Vou ", "ler."]
        assert msg["content"] == "Vou ler."

    def test_tool_call_inteira_com_arguments_objeto(self):
        """O Ollama manda a call pronta e `arguments` como dict — o agent
        espera string JSON."""
        linhas = [
            json.dumps({"message": {"content": "", "tool_calls": [
                {"function": {"name": "read_file", "arguments": {"path": "auth.py"}}}
            ]}}),
            json.dumps({"message": {}, "done": True}),
        ]
        with patch("httpx.stream", return_value=_RespostaFalsa(linhas)):
            msg = self._cliente().chat_with_tools("m", [], TOOLS, on_delta=lambda _: None)
        (tc,) = msg["tool_calls"]
        assert tc["function"]["name"] == "read_file"
        assert json.loads(tc["function"]["arguments"]) == {"path": "auth.py"}

    def test_contadores_viram_usage(self):
        linhas = [json.dumps({"message": {"content": "x"}, "done": True,
                              "prompt_eval_count": 100, "eval_count": 20})]
        c = self._cliente()
        with patch("httpx.stream", return_value=_RespostaFalsa(linhas)):
            c.chat_with_tools("m", [], TOOLS, on_delta=lambda _: None)
        assert c.last_usage == {"prompt_tokens": 100, "completion_tokens": 20,
                                "total_tokens": 120}

    def test_erro_http_preserva_deteccao_de_downgrade(self):
        from bauer.agent import _is_native_unsupported_error

        with patch("httpx.stream", return_value=_RespostaFalsa(["nope"], status=400)):
            with pytest.raises(OllamaError) as exc:
                self._cliente().chat_with_tools("m", [], TOOLS, on_delta=lambda _: None)
        assert _is_native_unsupported_error(exc.value)

    def test_sem_on_delta_usa_caminho_nao_streaming(self):
        with patch("httpx.stream") as stream, patch("httpx.post") as post:
            post.return_value.status_code = 200
            post.return_value.json.return_value = {"message": {"content": "ok"}}
            self._cliente().chat_with_tools("m", [], TOOLS)
        stream.assert_not_called()
        post.assert_called_once()


class TestParidadeEntreClients:
    def test_mesmo_contrato_de_retorno(self):
        """Os dois têm que devolver o mesmo formato — o turno native do agent
        não sabe (nem deve saber) qual provider está atrás."""
        sse = _sse([
            {"choices": [{"delta": {"content": "oi"}}]},
            {"choices": [{"delta": {"tool_calls": [
                {"index": 0, "id": "c1", "function": {"name": "read_file", "arguments": "{}"}}
            ]}}]},
        ])
        ndjson = [
            json.dumps({"message": {"content": "oi", "tool_calls": [
                {"function": {"name": "read_file", "arguments": {}}}
            ]}}),
            json.dumps({"message": {}, "done": True}),
        ]
        with patch("httpx.stream", return_value=_RespostaFalsa(sse)):
            a = OpenAIClient(host="http://x", api_key="k").chat_with_tools(
                "m", [], TOOLS, on_delta=lambda _: None)
        with patch("httpx.stream", return_value=_RespostaFalsa(ndjson)):
            b = OllamaClient("http://localhost:11434").chat_with_tools(
                "m", [], TOOLS, on_delta=lambda _: None)

        assert a.keys() == b.keys()
        assert a["content"] == b["content"] == "oi"
        assert a["tool_calls"][0]["function"]["name"] == b["tool_calls"][0]["function"]["name"]
        assert isinstance(a["tool_calls"][0]["function"]["arguments"], str)
        assert isinstance(b["tool_calls"][0]["function"]["arguments"], str)
