"""Sprint 72 (plano 066): `--local` vale também para os clients auxiliares.

Num processo marcado com `BAUER_LOCAL_ONLY` (gravado por `bauer agent --local`
e `bauer serve --local`), nenhum client auxiliar pode falar com a nuvem:

- **camada 1 (boot):** `validar_execucao_local` recusa um slot `auxiliary.*`
  com provider de nuvem, e `STT_PROVIDER`/`TTS_PROVIDER` explícitos de nuvem;
- **camada 2 (criação):** com a marca ligada, a criação de um client auxiliar
  de nuvem é recusada antes de qualquer request. Cobre o config relido em
  runtime (o autoload do `context_manager`, por exemplo) e as threads de
  fundo do mesmo processo (a marca está em `os.environ`).

Cada teste por slot usa o consumidor real e um espião no transporte de nuvem
(`OpenAIClient`). Sem a correção, o client de nuvem é construído e chamado.
"""

from __future__ import annotations

import ast
import re
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from bauer.config_loader import AuxiliarySection, BauerConfig

ROOT = Path(__file__).resolve().parents[1]
BAUER = ROOT / "bauer"

CLOUD = {"provider": "openai", "model": "gpt-4o-mini"}


# ─── fixtures ────────────────────────────────────────────────────────────────


def _cfg(slot: str | None = None, spec: dict | None = None) -> BauerConfig:
    aux = {slot: dict(spec or CLOUD)} if slot else {}
    return BauerConfig(
        model={"provider": "ollama", "name": "qwen3:14b",
               "profiles_local": {"fast": {"provider": "ollama", "model": "qwen3:14b"}}},
        auxiliary=aux,
        openai={"api_key": "sk-test-sprint72"},
    )


@pytest.fixture
def local_only(monkeypatch):
    monkeypatch.setenv("BAUER_LOCAL_ONLY", "1")


@pytest.fixture
def cloud_spy(monkeypatch):
    """Registra toda construção e todo request do client de nuvem."""
    import bauer.openai_client as oc

    calls: dict[str, list] = {"built": [], "streamed": []}
    response = {"text": "{}"}
    orig_init = oc.OpenAIClient.__init__

    def _init(self, *a, **kw):
        calls["built"].append(kw.get("host") or (a[0] if a else ""))
        orig_init(self, *a, **kw)

    def _stream(self, model, messages, *a, **kw):
        calls["streamed"].append(model)
        yield response["text"]

    monkeypatch.setattr(oc.OpenAIClient, "__init__", _init)
    monkeypatch.setattr(oc.OpenAIClient, "chat_stream", _stream)
    calls["response"] = response  # type: ignore[assignment]
    return calls


def _no_cloud(spy) -> None:
    assert spy["built"] == [], f"client de nuvem construído: {spy['built']}"
    assert spy["streamed"] == [], f"request de nuvem: {spy['streamed']}"


@pytest.fixture
def bauer_home(tmp_path, monkeypatch):
    monkeypatch.setenv("BAUER_HOME", str(tmp_path / "bauer-home"))
    monkeypatch.delenv("BAUER_KANBAN_BOARD", raising=False)
    return tmp_path / "bauer-home"


# ─── camada 2: um teste por slot, pelo consumidor real ───────────────────────


def _slot_approval_model(spy, tmp_path):
    from bauer.llm_approval import llm_evaluate_tool

    spy["response"]["text"] = '{"approved": false, "reason": "x"}'
    llm_evaluate_tool("shell_execute", {"command": "ls"}, [], cfg=_cfg("approval_model"))


def _slot_compression_model(spy, tmp_path):
    # O ContextManager chama o slot SEM cfg: o autoload lê BAUER_CONFIG/
    # config.yaml do CWD, que o boot do `--local` nunca validou.
    import bauer.auxiliary_client as aux
    from bauer.context_manager import ContextManager

    cfg = _cfg("compression_model")
    orig = aux._try_load_default_config
    aux._try_load_default_config = lambda: cfg
    try:
        cm = ContextManager(applied_context=4096)
        cm._compression_client()
    finally:
        aux._try_load_default_config = orig


def _slot_compression_model_consolidation(spy, tmp_path):
    from bauer.memory_consolidation import ConsolidationError, default_llm

    try:
        llm = default_llm(_cfg("compression_model"))
    except ConsolidationError:
        return
    llm([{"role": "user", "content": "x"}])


def _slot_background_reviewer(spy, tmp_path):
    # Roda numa thread, como o `review_turn` real: a marca é de processo.
    from bauer.background_review import _run_review

    t = threading.Thread(target=_run_review, args=(
        "faça x", "resposta longa o bastante para revisar", [], _cfg("background_reviewer")))
    t.start()
    t.join(5)


def _slot_vision_model(spy, tmp_path):
    from bauer.commands._runtime import _build_router

    router = _build_router(_cfg("vision_model"), tmp_path, llm_client=object())
    assert router._vision_client is None


def _slot_intent_router(spy, tmp_path):
    from bauer import os_intent

    manifest = SimpleNamespace(id="x.skill", description="d",
                               platforms=[os_intent.current_platform()], inputs={})
    os_intent.route_intent("abre o navegador", [manifest], cfg=_cfg("intent_router"))


def _slot_kanban_decomposer(spy, tmp_path):
    from bauer import kanban_db as kb
    from bauer.kanban_decompose import decompose_task

    with kb.connect() as conn:
        kb.init_db(conn)
        tid = kb.create_task(conn, "t", body="b", status="triage")
    out = decompose_task(tid, cfg=_cfg("kanban_decomposer"))
    assert out.reason == "auxiliary_unavailable"


def _slot_triage_specifier(spy, tmp_path):
    from bauer import kanban_db as kb
    from bauer.kanban_specify import specify_task

    with kb.connect() as conn:
        kb.init_db(conn)
        tid = kb.create_task(conn, "t", body="b", status="triage")
    out = specify_task(tid, cfg=_cfg("triage_specifier"))
    assert out.reason == "auxiliary_unavailable"


#: Um consumidor real por slot de `AuxiliarySection`. A catraca abaixo exige
#: que todo slot do schema tenha entrada aqui.
SLOT_CONSUMERS = {
    "approval_model": [_slot_approval_model],
    "compression_model": [_slot_compression_model, _slot_compression_model_consolidation],
    "background_reviewer": [_slot_background_reviewer],
    "vision_model": [_slot_vision_model],
    "intent_router": [_slot_intent_router],
    "kanban_decomposer": [_slot_kanban_decomposer],
    "triage_specifier": [_slot_triage_specifier],
}

_CASES = [(slot, fn) for slot, fns in SLOT_CONSUMERS.items() for fn in fns]


@pytest.mark.parametrize("slot,consumer", _CASES, ids=[f.__name__ for _, f in _CASES])
def test_slot_cloud_refused_under_local(slot, consumer, local_only, cloud_spy, bauer_home, tmp_path):
    consumer(cloud_spy, tmp_path)
    _no_cloud(cloud_spy)


def test_slot_cloud_still_works_without_local(cloud_spy, bauer_home, tmp_path):
    """Controle: sem a marca, o slot de nuvem continua sendo usado."""
    _slot_approval_model(cloud_spy, tmp_path)
    assert cloud_spy["streamed"] == ["gpt-4o-mini"]


@pytest.mark.parametrize("slot", sorted(AuxiliarySection.model_fields))
def test_get_text_auxiliary_client_refuses_cloud_slot(slot, local_only, cloud_spy):
    from bauer.auxiliary_client import get_text_auxiliary_client

    assert get_text_auxiliary_client(slot, _cfg(slot)) == (None, None)
    _no_cloud(cloud_spy)


@pytest.mark.parametrize("slot", sorted(AuxiliarySection.model_fields))
def test_local_slot_and_main_fallback_allowed_under_local(slot, local_only, cloud_spy):
    """Slot local, ou vazio (cai no principal, já local), continua valendo."""
    from bauer.auxiliary_client import get_text_auxiliary_client

    client, model = get_text_auxiliary_client(slot, _cfg(slot, {"provider": "ollama", "model": "llava"}))
    assert client is not None and model == "llava"
    client, model = get_text_auxiliary_client(slot, _cfg())
    assert client is not None and model == "qwen3:14b"
    _no_cloud(cloud_spy)


def test_slot_model_only_inherits_main_local_provider(local_only, cloud_spy):
    from bauer.auxiliary_client import get_text_auxiliary_client

    client, model = get_text_auxiliary_client("approval_model", _cfg("approval_model", {"model": "llama3"}))
    assert getattr(client, "_provider", "") == "ollama" and model == "llama3"
    _no_cloud(cloud_spy)


def test_build_client_for_provider_refuses_cloud_under_local(local_only, cloud_spy):
    """O server usa `_build_client_for_provider` direto (troca de modelo,
    restauração do modelo salvo, OAuth). A recusa vale ali também."""
    from bauer.auxiliary_client import AuxiliaryLocalOnlyRefused, _build_client_for_provider

    with pytest.raises(AuxiliaryLocalOnlyRefused):
        _build_client_for_provider("openai", "gpt-4o-mini", _cfg())
    with pytest.raises(AuxiliaryLocalOnlyRefused):
        _build_client_for_provider("anthropic", "claude", _cfg())
    assert _build_client_for_provider("ollama", "qwen3:14b", _cfg()) is not None
    _no_cloud(cloud_spy)


def test_auth_store_token_not_used_under_local(local_only, cloud_spy, monkeypatch):
    """O token do `bauer auth` também não pode virar client de nuvem."""
    import bauer.auxiliary_client as aux

    hits: list[str] = []
    monkeypatch.setattr(aux, "_try_build_from_auth_store",
                        lambda p, m, c=None: hits.append(p) or object())
    assert aux.get_text_auxiliary_client("approval_model", _cfg("approval_model")) == (None, None)
    assert hits == []


# ─── transcrição (STT) ──────────────────────────────────────────────────────


@pytest.fixture
def audio(tmp_path, monkeypatch):
    for k in ("STT_PROVIDER", "OPENROUTER_API_KEY", "GROQ_API_KEY",
              "DEEPGRAM_API_KEY", "OPENAI_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    p = tmp_path / "voice.ogg"
    p.write_bytes(b"OggS" + b"\0" * 64)
    return p


@pytest.fixture
def http_spy(monkeypatch):
    import httpx

    posts: list[str] = []

    def _post(url, *a, **kw):
        posts.append(str(url))
        raise AssertionError(f"request de rede: {url}")

    monkeypatch.setattr(httpx, "post", _post)
    return posts


@pytest.mark.parametrize("pref", [None, "openrouter", "groq", "deepgram", "openai", "auto"])
def test_transcription_cloud_refused_under_local(pref, audio, http_spy, local_only, monkeypatch):
    from bauer import transcription

    if pref:
        monkeypatch.setenv("STT_PROVIDER", pref)
    for k in ("OPENROUTER_API_KEY", "GROQ_API_KEY", "DEEPGRAM_API_KEY", "OPENAI_API_KEY"):
        monkeypatch.setenv(k, "sk-test")
    monkeypatch.setattr(transcription, "_faster_whisper_available", lambda: False)
    result = transcription.transcribe_audio(audio)
    assert result["success"] is False
    assert "--local" in result["error"]
    assert http_spy == []
    assert transcription.available_stt_provider() is None


def test_transcription_auto_uses_only_local_under_local(audio, http_spy, local_only, monkeypatch):
    from bauer import transcription

    monkeypatch.setenv("STT_PROVIDER", "auto")
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")
    monkeypatch.setattr(transcription, "_faster_whisper_available", lambda: True)
    monkeypatch.setattr(transcription, "_transcribe_local",
                        lambda path, mdl, lang: {"success": True, "transcript": "oi"})
    result = transcription.transcribe_audio(audio)
    assert result["success"] and result["provider"] == "local"
    assert transcription.available_stt_provider() == "local"
    assert http_spy == []


def test_transcription_default_unchanged_without_local(audio, monkeypatch):
    from bauer import transcription

    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")
    seen: list[str] = []
    monkeypatch.setattr(transcription, "_post_openrouter_whisper",
                        lambda key, mdl, path, lang: seen.append(mdl) or {"success": True, "transcript": "oi"})
    assert transcription.transcribe_audio(audio)["provider"] == "openrouter"
    assert seen


# ─── síntese de voz (TTS) ───────────────────────────────────────────────────


@pytest.fixture
def tts_env(monkeypatch):
    from bauer import tts

    for k in ("TTS_PROVIDER", "BAUER_TTS_PROVIDER", "OPENAI_API_KEY",
              "GOOGLE_TTS_API_KEY", "GOOGLE_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(tts, "_kokoro_available", lambda: False)
    monkeypatch.setattr(tts, "_coqui_tts_available", lambda: False)
    return tts


@pytest.mark.parametrize("pref", ["openai", "google", "auto"])
def test_tts_cloud_refused_under_local(pref, tts_env, http_spy, local_only, monkeypatch, tmp_path):
    tts = tts_env
    monkeypatch.setenv("TTS_PROVIDER", pref)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("GOOGLE_TTS_API_KEY", "g-test")
    result = tts.synthesize_speech("olá mundo", tmp_path / "o.wav")
    assert result["success"] is False and "--local" in result["error"]
    assert http_spy == []
    assert tts.available_tts_provider() is None


def test_tts_local_still_works_under_local(tts_env, local_only, monkeypatch, tmp_path):
    tts = tts_env
    monkeypatch.setenv("TTS_PROVIDER", "auto")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setattr(tts, "_coqui_tts_available", lambda: True)
    monkeypatch.setattr(tts, "_synthesize_local", lambda text, dest, model=None: dest.write_bytes(b"RIFF"))
    result = tts.synthesize_speech("olá mundo", tmp_path / "o.wav")
    assert result["success"] and result["provider"] == "local"


# ─── tools de mídia de nuvem (image_generate, text_to_speech) ───────────────


def _router(tmp_path):
    from bauer.tool_router import ToolRouter

    class _LocalClient:
        _provider = "ollama"
        model = "qwen3:14b"

    return ToolRouter(tmp_path, llm_client=_LocalClient())


@pytest.mark.parametrize("args", [
    {"prompt": "um gato", "provider": "openai"},
    {"prompt": "um gato", "provider": "xai"},
    {"prompt": "um gato", "provider": "openrouter"},
    {"prompt": "um gato"},
])
def test_image_generate_refused_under_local(args, tmp_path, http_spy, local_only, monkeypatch):
    from bauer.tools.base import ToolError

    for k in ("OPENAI_API_KEY", "XAI_API_KEY", "OPENROUTER_API_KEY"):
        monkeypatch.setenv(k, "sk-test")
    import openai

    monkeypatch.setattr(openai, "OpenAI", lambda **kw: (_ for _ in ()).throw(AssertionError("openai.OpenAI")))
    with pytest.raises(ToolError, match="--local"):
        _router(tmp_path)._image_generate(dict(args))
    assert http_spy == []


def test_text_to_speech_tool_refused_under_local(tmp_path, local_only, monkeypatch):
    from bauer.tools.base import ToolError

    import openai

    monkeypatch.setattr(openai, "OpenAI", lambda **kw: (_ for _ in ()).throw(AssertionError("openai.OpenAI")))
    with pytest.raises(ToolError, match="--local"):
        _router(tmp_path)._text_to_speech({"text": "oi", "output_file": "o.mp3"})


# ─── camada 1: boot do `--local` ────────────────────────────────────────────


@pytest.fixture
def clean_voice_env(monkeypatch):
    for k in ("STT_PROVIDER", "TTS_PROVIDER", "BAUER_TTS_PROVIDER"):
        monkeypatch.delenv(k, raising=False)


@pytest.mark.parametrize("slot", sorted(AuxiliarySection.model_fields))
def test_boot_refuses_cloud_auxiliary_slot(slot, clean_voice_env):
    from bauer.model_router import validar_execucao_local

    assert validar_execucao_local(_cfg()) == []
    problemas = validar_execucao_local(_cfg(slot))
    assert any(f"auxiliary.{slot}" in p for p in problemas), problemas


@pytest.mark.parametrize("slot", sorted(AuxiliarySection.model_fields))
def test_boot_accepts_local_or_inherited_slot(slot, clean_voice_env):
    from bauer.model_router import validar_execucao_local

    assert validar_execucao_local(_cfg(slot, {"provider": "ollama", "model": "llava"})) == []
    assert validar_execucao_local(_cfg(slot, {"model": "llava"})) == []


def test_boot_slot_uses_same_locality_rule_as_main(clean_voice_env):
    """`lmstudio` é local; `custom` segue `provider_e_local` (sem host local
    verificável, conta como nuvem — falha fechado)."""
    from bauer.model_router import validar_execucao_local

    assert validar_execucao_local(_cfg("approval_model", {"provider": "lmstudio", "model": "m"})) == []
    assert any("auxiliary.approval_model" in p
               for p in validar_execucao_local(_cfg("approval_model", {"provider": "custom", "model": "m"})))


@pytest.mark.parametrize("var,value,refused", [
    ("STT_PROVIDER", "openrouter", True),
    ("STT_PROVIDER", "groq", True),
    ("STT_PROVIDER", "local", False),
    ("STT_PROVIDER", "auto", False),
    ("TTS_PROVIDER", "openai", True),
    ("BAUER_TTS_PROVIDER", "google", True),
    ("TTS_PROVIDER", "kokoro", False),
    ("TTS_PROVIDER", "local", False),
    ("TTS_PROVIDER", "auto", False),
])
def test_boot_refuses_explicit_cloud_voice_provider(var, value, refused, clean_voice_env, monkeypatch):
    from bauer.model_router import validar_execucao_local

    monkeypatch.setenv(var, value)
    problemas = validar_execucao_local(_cfg())
    assert bool(problemas) is refused, problemas
    if refused:
        assert var.replace("BAUER_", "") in problemas[0] or var in problemas[0]


def test_agent_and_serve_local_boot_use_validar_execucao_local():
    """O `agent --local` e o `serve --local` usam a checagem estendida."""
    for mod in ("agent_cmd.py", "serve_cmd.py"):
        src = (BAUER / "commands" / mod).read_text(encoding="utf-8")
        assert "validar_execucao_local(" in src, mod


# ─── catraca do inventário ──────────────────────────────────────────────────


def test_inventory_slots_match_schema():
    """Slot novo no schema precisa entrar em VALID_SLOTS e ter teste aqui."""
    from bauer.auxiliary_client import VALID_SLOTS

    schema = set(AuxiliarySection.model_fields)
    assert VALID_SLOTS == schema
    assert set(SLOT_CONSUMERS) == schema


#: Nomes de slot usados no código que NÃO existem no schema. Hoje só um:
#: `headless_approval` chama um slot que nunca foi declarado, então
#: `get_text_auxiliary_client` sempre devolve (None, None) e o modo `smart`
#: cai no `threshold`. Nunca constrói client — não é porta para a nuvem.
SLOTS_FORA_DO_SCHEMA = {"headless_approval"}


def test_inventory_slot_literals_in_code():
    from bauer.auxiliary_client import VALID_SLOTS

    used: set[str] = set()
    for path in BAUER.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            fn = node.func
            name = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", "")
            if name in {"get_text_auxiliary_client", "call_aux_text"} and node.args:
                first = node.args[0]
                if isinstance(first, ast.Constant) and isinstance(first.value, str):
                    used.add(first.value)
    assert used - VALID_SLOTS == SLOTS_FORA_DO_SCHEMA
    assert VALID_SLOTS - used <= {"compression_model"}  # via get_compression_client


#: Onde o código constrói, fora do `_build_client` do turno principal, um
#: client que pode ser de nuvem. Contagem e motivo por arquivo; site novo ou
#: contagem que muda falha o teste e obriga a decidir como o `--local` o cobre.
CONSTRUCAO_PERMITIDA = {
    "bauer/auxiliary_client.py": (5, "fábrica dos slots: guarda AuxiliaryLocalOnlyRefused no topo"),
    "bauer/commands/_runtime.py": (38, "_build_client do turno principal: boot valida model.provider"),
    "bauer/server.py": (3, "troca/restauração/OAuth: _provider_allowed + guarda da fábrica"),
    "bauer/tools/media.py": (2, "image_generate/text_to_speech: recusados sob a marca"),
    "bauer/channel_base.py": (2, "/model dos canais: processo do gateway, sem --local"),
    "bauer/cli.py": (1, "bauer doctor: comando próprio, sem --local"),
    "bauer/learning_engine.py": (1, "usa model.provider principal (local sob --local)"),
    "bauer/core/runtime/adapters/agno_adapter.py": (1, "modelo do turno Agno (principal)"),
}

_SITE = re.compile(r"\b(OpenAIClient|AnthropicClient|ChatGPTBackendClient)\(|openai\.OpenAI\(|_build_client_for_provider\(")


def test_inventory_cloud_client_construction_sites():
    found: dict[str, int] = {}
    for path in BAUER.rglob("*.py"):
        rel = path.relative_to(ROOT).as_posix()
        for line in path.read_text(encoding="utf-8").splitlines():
            s = line.strip()
            if s.startswith(("#", "def ", "class ")):
                continue
            if _SITE.search(s):
                found[rel] = found.get(rel, 0) + 1
    assert found == {k: v[0] for k, v in CONSTRUCAO_PERMITIDA.items()}
