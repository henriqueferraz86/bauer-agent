"""Function calling nativo no Ollama + empacotamento do models.yaml.

Contexto (medido no Beelink, 2026-07-27, Ollama 0.32.4 + qwen3-coder:30b):

- O `/api/chat` do Ollama aceita `tools=` e devolve `tool_calls`, mas fala um
  dialeto quase-OpenAI: `arguments` é OBJETO na resposta e é REJEITADO como
  string na requisição (HTTP 400 "Value looks like object, but can't find
  closing '}' symbol"). Como o histórico do agent guarda `arguments` string,
  mandar de volta cru quebrava a 2ª rodada — e o 400 seria lido como "provider
  sem tools", derrubando a sessão inteira para o bridge.
- O gate do agent era `isinstance(client, OpenAIClient)`, então `provider:
  ollama` NUNCA usava tool calling nativo.
- `models.yaml` morava na raiz do repo e o package-data só cobre `bauer/**`:
  toda instalação pip/pipx tinha registry VAZIO.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from bauer.agent import _client_supports_native_tools, _is_native_unsupported_error
from bauer.model_registry import (
    _PACKAGED_MODELS,
    auto_detect_from_ollama,
    load_registry,
    registry_sources,
)
from bauer.ollama_client import (
    ModelfileParams,
    OllamaClient,
    OllamaError,
    _ollama_outbound_message,
    _openai_tool_calls,
)


def _client() -> OllamaClient:
    return OllamaClient(host="http://localhost:11434")


def _resp(status: int = 200, payload: dict | None = None, text: str = "") -> MagicMock:
    m = MagicMock()
    m.status_code = status
    m.text = text
    m.json.return_value = payload if payload is not None else {}
    return m


# ─── normalização de saída (agent → Ollama) ──────────────────────────────────


def test_outbound_converte_arguments_string_em_objeto():
    """O Ollama devolve HTTP 400 se `arguments` chegar como string JSON."""
    msg = {
        "role": "assistant",
        "content": "",
        "tool_calls": [
            {"id": "c1", "type": "function",
             "function": {"name": "write_file", "arguments": '{"path": "a.txt"}'}}
        ],
    }
    out = _ollama_outbound_message(msg)
    assert out["tool_calls"][0]["function"]["arguments"] == {"path": "a.txt"}
    # id/type preservados — o Ollama 0.32 aceita os dois.
    assert out["tool_calls"][0]["id"] == "c1"
    assert out["tool_calls"][0]["type"] == "function"
    # não muta a mensagem original (o ctx do agent segue com string)
    assert msg["tool_calls"][0]["function"]["arguments"] == '{"path": "a.txt"}'


def test_outbound_arguments_ilegivel_vira_objeto_vazio():
    """Argumento corrompido não pode derrubar a requisição inteira com 400."""
    msg = {"role": "assistant", "tool_calls": [
        {"function": {"name": "x", "arguments": "{nao é json"}}
    ]}
    assert _ollama_outbound_message(msg)["tool_calls"][0]["function"]["arguments"] == {}


@pytest.mark.parametrize("msg", [
    {"role": "user", "content": "oi"},
    {"role": "tool", "tool_call_id": "c1", "content": "resultado"},
])
def test_outbound_passa_intacto_sem_tool_calls(msg):
    assert _ollama_outbound_message(msg) is msg


# ─── normalização de entrada (Ollama → agent) ────────────────────────────────


def test_inbound_converte_arguments_objeto_em_string():
    """O agent faz json.loads(fn["arguments"]) — precisa vir string."""
    out = _openai_tool_calls([
        {"id": "call_abc", "function": {"index": 0, "name": "read_file",
                                        "arguments": {"path": "a.txt"}}}
    ])
    assert out[0]["id"] == "call_abc"
    assert out[0]["type"] == "function"
    assert out[0]["function"]["name"] == "read_file"
    assert json.loads(out[0]["function"]["arguments"]) == {"path": "a.txt"}


def test_inbound_sintetiza_id_ausente():
    """Ollama antigo não manda `id`; o agent usa esse valor como tool_call_id."""
    out = _openai_tool_calls([{"function": {"name": "a", "arguments": {}}},
                              {"function": {"name": "b", "arguments": {}}}])
    assert [tc["id"] for tc in out] == ["call_0", "call_1"]


def test_inbound_ignora_entradas_malformadas():
    assert _openai_tool_calls(["lixo", None, {"function": {"name": "ok"}}]) == [
        {"id": "call_2", "type": "function",
         "function": {"name": "ok", "arguments": "{}"}}
    ]


# ─── chat_with_tools ─────────────────────────────────────────────────────────


def test_chat_with_tools_retorna_formato_openai():
    payload = {
        "message": {"role": "assistant", "content": "",
                    "tool_calls": [{"id": "call_x", "function": {
                        "name": "get_weather", "arguments": {"city": "Curitiba"}}}]},
        "prompt_eval_count": 308,
        "eval_count": 24,
    }
    c = _client()
    with patch("httpx.post", return_value=_resp(200, payload)) as post:
        msg = c.chat_with_tools("qwen3-coder:30b", [{"role": "user", "content": "oi"}], tools=[])

    assert msg["content"] == ""
    assert json.loads(msg["tool_calls"][0]["function"]["arguments"]) == {"city": "Curitiba"}
    # usage traduzido para o vocabulário do cost_meter
    assert c.last_usage == {"prompt_tokens": 308, "completion_tokens": 24, "total_tokens": 332}
    body = post.call_args.kwargs["json"]
    assert body["stream"] is False
    assert body["think"] is False


def test_chat_with_tools_normaliza_historico_na_requisicao():
    """A 2ª rodada manda de volta o assistant com tool_calls do turno anterior."""
    historico = [
        {"role": "user", "content": "oi"},
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": "c1", "function": {"name": "f", "arguments": '{"a": 1}'}}]},
        {"role": "tool", "tool_call_id": "c1", "content": "ok"},
    ]
    with patch("httpx.post", return_value=_resp(200, {"message": {"content": "pronto"}})) as post:
        _client().chat_with_tools("m", historico, tools=[])

    enviado = post.call_args.kwargs["json"]["messages"]
    assert enviado[1]["tool_calls"][0]["function"]["arguments"] == {"a": 1}


def test_chat_with_tools_aplica_num_ctx():
    c = _client()
    c.num_ctx = 32768
    with patch("httpx.post", return_value=_resp(200, {"message": {"content": "x"}})) as post:
        c.chat_with_tools("m", [], tools=[])
    assert post.call_args.kwargs["json"]["options"] == {"num_ctx": 32768}


def test_chat_with_tools_400_sinaliza_downgrade_para_bridge():
    """Modelo sem capability "tools" → 400 → o agent cai no Tool Bridge.

    A mensagem PRECISA conter "HTTP 400": é isso que
    _is_native_unsupported_error procura para decidir o downgrade.
    """
    with patch("httpx.post", return_value=_resp(400, text="does not support tools")):
        with pytest.raises(OllamaError) as exc:
            _client().chat_with_tools("bge-m3", [], tools=[])
    assert "HTTP 400" in str(exc.value)
    assert _is_native_unsupported_error(exc.value) is True


def test_chat_with_tools_500_nao_derruba_para_bridge():
    """5xx é transiente — retry em native, não downgrade permanente."""
    with patch("httpx.post", return_value=_resp(500, text="boom")):
        with pytest.raises(OllamaError) as exc:
            _client().chat_with_tools("m", [], tools=[])
    assert _is_native_unsupported_error(exc.value) is False


# ─── gate do agent ───────────────────────────────────────────────────────────


def test_agent_liga_native_para_ollama():
    """O bug: era `isinstance(client, OpenAIClient)` → ollama sempre no bridge."""
    assert _client_supports_native_tools(_client()) is True


def test_agent_nao_liga_native_para_mock():
    """MagicMock responde True a qualquer getattr — o gate checa a classe."""
    assert _client_supports_native_tools(MagicMock()) is False


# ─── capabilities do /api/show ───────────────────────────────────────────────


def test_show_model_expoe_capabilities():
    payload = {"parameters": "", "model_info": {}, "capabilities": ["completion", "tools"]}
    with patch("httpx.post", return_value=_resp(200, payload)):
        params = _client().show_model("qwen3-coder:30b")
    assert params.capabilities == ["completion", "tools"]


def test_model_supports_tools_le_capabilities():
    c = _client()
    with patch.object(c, "show_model", return_value=ModelfileParams(
            num_ctx=None, context_length=None, size_bytes=0, raw={},
            capabilities=["completion", "tools"])):
        assert c.model_supports_tools("qwen3-coder:30b") is True
    with patch.object(c, "show_model", return_value=ModelfileParams(
            num_ctx=None, context_length=None, size_bytes=0, raw={},
            capabilities=["completion"])):
        assert c.model_supports_tools("bge-m3") is False
    with patch.object(c, "show_model", side_effect=OllamaError("fora")):
        assert c.model_supports_tools("x") is False


def test_auto_detect_deriva_supports_tools_das_capabilities():
    """Antes era chumbado False — todo modelo fora do models.yaml virava bridge."""
    client = MagicMock()
    client.show_model.return_value = ModelfileParams(
        num_ctx=None, context_length=262144, size_bytes=0, raw={},
        capabilities=["completion", "tools"])
    client.list_models_with_sizes.return_value = []
    assert auto_detect_from_ollama(client, "qwen3-coder:30b").supports_tools is True

    client.show_model.return_value = ModelfileParams(
        num_ctx=None, context_length=8192, size_bytes=0, raw={}, capabilities=["embedding"])
    assert auto_detect_from_ollama(client, "bge-m3").supports_tools is False


# ─── empacotamento do models.yaml ────────────────────────────────────────────


def test_models_yaml_viaja_dentro_do_pacote():
    """Regressão do bug: na raiz do repo, o pyproject nunca o empacotava.

    `bauer/data/**` está no package-data — se alguém mover o arquivo de volta
    para fora de `bauer/`, toda instalação pip/pipx volta a ter registry vazio.
    """
    assert _PACKAGED_MODELS.exists(), (
        f"{_PACKAGED_MODELS} sumiu — models.yaml precisa ficar sob bauer/ "
        "para o package-data do pyproject alcançá-lo."
    )
    assert _PACKAGED_MODELS.is_relative_to(Path(__file__).parent.parent / "bauer")


def test_registry_carrega_sem_cwd_e_sem_path(monkeypatch, tmp_path):
    """`load_registry()` de um diretório qualquer ainda enxerga os modelos."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("BAUER_HOME", str(tmp_path / "vazio"))
    reg = load_registry()
    assert reg.names(), "registry vazio — o models.yaml empacotado não foi lido"
    assert reg.get("qwen3-coder:30b") is not None


def test_bauer_home_sobrepoe_por_modelo(monkeypatch, tmp_path):
    """O ~/.bauer/models.yaml do usuário nunca era lido (só load_config caía lá)."""
    home = tmp_path / "home"
    home.mkdir()
    (home / "models.yaml").write_text(
        'models:\n'
        '  "qwen3-coder:30b":\n'
        '    provider: ollama\n'
        '    ram_base_mb: 19000\n'
        '    ram_per_1k_ctx_mb: 200\n'
        '    max_context_safe: 65536\n'
        '    supports_tools: true\n'
        '    ram_profile: high\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("BAUER_HOME", str(home))
    monkeypatch.chdir(tmp_path)

    reg = load_registry()
    # entrada do usuário venceu
    assert reg.get("qwen3-coder:30b").max_context_safe == 65536
    # e o resto do registry curado continua lá (merge, não substituição)
    assert len(reg.names()) > 1


def test_registry_sources_ordena_do_mais_fraco_ao_mais_forte(monkeypatch, tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    (home / "models.yaml").write_text("models: {}\n", encoding="utf-8")
    explicito = tmp_path / "custom.yaml"
    explicito.write_text("models: {}\n", encoding="utf-8")
    monkeypatch.setenv("BAUER_HOME", str(home))

    srcs = registry_sources(explicito)
    assert srcs[0] == _PACKAGED_MODELS
    assert srcs[-1] == explicito
