"""Testes das features LISO do TelegramBridge — paridade com Hermes.

Cobre: typing heartbeat, chunking com fences, mídia in/out, retry/backoff,
model picker com inline keyboard e streaming draft.
"""

from __future__ import annotations

import json
import time as _time
from pathlib import Path

import httpx
import pytest

from bauer.channel_base import AgentBackend, ChannelMessage
from bauer.telegram_bridge import (
    TelegramBridge,
    _StreamingDraft,
    _TypingHeartbeat,
    chunk_text_fenced,
    extract_outbound_media,
)


class _EchoBackend(AgentBackend):
    """Backend fake — responde sem tocar em LLM/config."""

    def __init__(self):
        super().__init__()
        self.received: list[ChannelMessage] = []

    @property
    def is_ready(self):
        return True

    def process(self, msg: ChannelMessage, on_delta=None, send_fn=None) -> str:
        self.received.append(msg)
        return f"resposta para: {msg.text}"


def _make_bridge(tmp_path: Path, transport_handler, **kw) -> TelegramBridge:
    bridge = TelegramBridge(
        token="123:FAKE",
        backend=_EchoBackend(),
        allowed_users=kw.pop("allowed_users", [42]),
        state_dir=tmp_path / "state",
        **kw,
    )
    bridge._http = httpx.Client(transport=httpx.MockTransport(transport_handler))
    return bridge


def _update(update_id: int, user_id: int, chat_id: int, text: str) -> dict:
    return {
        "update_id": update_id,
        "message": {
            "text": text,
            "chat": {"id": chat_id},
            "from": {"id": user_id, "username": "tester"},
        },
    }


class TestTypingHeartbeat:
    def test_envia_typing_repetidamente(self, tmp_path):
        actions: list[str] = []

        def handler(request):
            if "sendChatAction" in str(request.url):
                actions.append("typing")
            return httpx.Response(200, json={"ok": True, "result": {}})

        bridge = _make_bridge(tmp_path, handler)
        with _TypingHeartbeat(bridge, "42", interval=0.05):
            _time.sleep(0.18)
        count = len(actions)
        assert count >= 2  # re-enviou enquanto ativo
        _time.sleep(0.12)
        assert len(actions) == count  # parou após sair do with


class TestChunkFenced:
    def test_texto_sem_fence_intacto(self):
        assert chunk_text_fenced("oi mundo", 100) == ["oi mundo"]

    def test_fence_cortado_e_reaberto(self):
        code = "x" * 60
        text = f"antes\n```python\n{code}\n{code}\n```\ndepois"
        chunks = chunk_text_fenced(text, 90)
        assert len(chunks) >= 2
        for c in chunks:  # todo chunk renderiza fechado
            assert c.count("```") % 2 == 0


class TestExtractOutboundMedia:
    def test_marcador_media(self, tmp_path):
        img = tmp_path / "grafico.png"
        img.write_bytes(b"\x89PNG fake")
        text = f"Aqui está o gráfico:\n[media: {img}]\nPronto."
        files, clean = extract_outbound_media(text)
        assert files == [img.resolve()]
        assert "[media:" not in clean
        assert "Pronto." in clean

    def test_linha_com_path_puro(self, tmp_path):
        img = tmp_path / "foto.jpg"
        img.write_bytes(b"JPG")
        text = f"Veja:\n{img}\nfim"
        files, clean = extract_outbound_media(text)
        assert files == [img.resolve()]
        assert str(img) not in clean

    def test_path_inexistente_fica_no_texto(self, tmp_path):
        text = f"[media: {tmp_path / 'nada.png'}]"
        files, clean = extract_outbound_media(text)
        assert files == []
        assert "nada.png" in clean

    def test_path_dentro_de_fence_ignorado(self, tmp_path):
        img = tmp_path / "code.png"
        img.write_bytes(b"PNG")
        text = f"```\n{img}\n```"
        files, _clean = extract_outbound_media(text)
        assert files == []


class TestSendMedia:
    def test_roteia_por_extensao(self, tmp_path):
        methods: list[str] = []

        def handler(request):
            methods.append(str(request.url).rsplit("/", 1)[-1])
            return httpx.Response(200, json={"ok": True, "result": {}})

        bridge = _make_bridge(tmp_path, handler)
        for name in ("a.png", "b.ogg", "c.mp3", "d.mp4", "e.pdf"):
            p = tmp_path / name
            p.write_bytes(b"data")
            assert bridge.send_media("42", p) is True
        assert methods == [
            "sendPhoto", "sendVoice", "sendAudio", "sendVideo", "sendDocument",
        ]

    def test_arquivo_inexistente_retorna_false(self, tmp_path):
        bridge = _make_bridge(
            tmp_path, lambda r: httpx.Response(200, json={"ok": True, "result": {}})
        )
        assert bridge.send_media("42", tmp_path / "nada.png") is False


class TestApiRetry:
    def test_429_respeita_retry_after(self, tmp_path, monkeypatch):
        calls = {"n": 0}
        slept: list[float] = []
        monkeypatch.setattr("bauer.telegram_bridge.time.sleep",
                            lambda s: slept.append(s))

        def handler(request):
            calls["n"] += 1
            if calls["n"] == 1:
                return httpx.Response(
                    429, json={"ok": False, "parameters": {"retry_after": 1}}
                )
            return httpx.Response(200, json={"ok": True, "result": {"done": True}})

        bridge = _make_bridge(tmp_path, handler)
        result = bridge._api("sendMessage", chat_id="1", text="x")
        assert result == {"done": True}
        assert calls["n"] == 2
        assert slept and slept[0] == 1.0

    def test_5xx_faz_backoff_e_recupera(self, tmp_path, monkeypatch):
        calls = {"n": 0}
        monkeypatch.setattr("bauer.telegram_bridge.time.sleep", lambda s: None)

        def handler(request):
            calls["n"] += 1
            if calls["n"] < 3:
                return httpx.Response(502, text="bad gateway")
            return httpx.Response(200, json={"ok": True, "result": {}})

        bridge = _make_bridge(tmp_path, handler)
        bridge._api("sendMessage", chat_id="1", text="x")
        assert calls["n"] == 3


class TestMidiaInbound:
    def _bridge_com_media(self, tmp_path, monkeypatch, transcript="olá"):
        sent: list[dict] = []

        def handler(request):
            url = str(request.url)
            if "getFile" in url:
                return httpx.Response(200, json={
                    "ok": True,
                    "result": {"file_path": "voice/f1.ogg", "file_size": 100},
                })
            if "/file/bot" in url:
                return httpx.Response(200, content=b"OggS fake audio")
            if "sendMessage" in url:
                sent.append(json.loads(request.content))
            return httpx.Response(200, json={"ok": True, "result": {}})

        bridge = _make_bridge(tmp_path, handler)
        monkeypatch.setattr(
            "bauer.transcription.transcribe_audio",
            lambda p: {"success": True, "transcript": transcript, "provider": "groq"},
        )
        return bridge, sent

    def test_voice_transcrita_vira_texto(self, tmp_path, monkeypatch):
        bridge, _ = self._bridge_com_media(tmp_path, monkeypatch,
                                           transcript="bom dia bauer")
        message = {"voice": {"file_id": "F1"}, "chat": {"id": 42}, "from": {"id": 42}}
        text = bridge._ingest_media(message, "42")
        assert text is not None
        assert "bom dia bauer" in text
        assert "🎤" in text

    def test_photo_salva_e_aponta_vision(self, tmp_path, monkeypatch):
        bridge, _ = self._bridge_com_media(tmp_path, monkeypatch)
        message = {
            "photo": [{"file_id": "small"}, {"file_id": "big"}],
            "caption": "o que é isso?",
            "chat": {"id": 42}, "from": {"id": 42},
        }
        text = bridge._ingest_media(message, "42")
        assert text is not None
        assert "vision_analyze" in text
        assert "o que é isso?" in text

    def test_document_salvo_com_nome(self, tmp_path, monkeypatch):
        bridge, _ = self._bridge_com_media(tmp_path, monkeypatch)
        message = {
            "document": {"file_id": "D1", "file_name": "relatorio.pdf"},
            "chat": {"id": 42}, "from": {"id": 42},
        }
        text = bridge._ingest_media(message, "42")
        assert text is not None
        assert "relatorio.pdf" in text

    def test_update_com_voice_processa(self, tmp_path, monkeypatch):
        bridge, _sent = self._bridge_com_media(tmp_path, monkeypatch,
                                               transcript="oi do áudio")
        update = {
            "update_id": 9,
            "message": {
                "voice": {"file_id": "F1"},
                "chat": {"id": 42},
                "from": {"id": 42, "username": "tester"},
            },
        }
        bridge._handle_update(update)
        bridge._executor.shutdown(wait=True)
        backend = bridge.backend
        assert backend.received, "mensagem não chegou ao backend"
        assert "oi do áudio" in backend.received[-1].text


class TestModelPicker:
    def _picker_bridge(self, tmp_path):
        sent: list[dict] = []
        edited: list[dict] = []

        def handler(request):
            url = str(request.url)
            body = json.loads(request.content) if request.content else {}
            if "sendMessage" in url:
                sent.append(body)
                return httpx.Response(200, json={
                    "ok": True, "result": {"message_id": 77}
                })
            if "editMessageText" in url:
                edited.append(body)
            return httpx.Response(200, json={"ok": True, "result": {}})

        bridge = _make_bridge(tmp_path, handler)
        backend = bridge.backend
        backend._providers_fetcher = lambda: ["ollama", "opencode"]
        backend._provider = "ollama"
        backend._model_name = "m1"
        backend._models_fetcher = lambda: ["m1", "m2", "m3"]
        return bridge, sent, edited

    def test_model_sem_args_envia_keyboard(self, tmp_path):
        bridge, sent, _ = self._picker_bridge(tmp_path)
        bridge._handle_update(_update(1, user_id=42, chat_id=42, text="/model"))
        bridge._executor.shutdown(wait=True)
        assert sent, "picker não enviado"
        kb = sent[-1].get("reply_markup", {}).get("inline_keyboard")
        assert kb, "sem teclado inline"
        labels = [b["text"] for row in kb for b in row]
        assert any("ollama" in lb for lb in labels)
        assert any("opencode" in lb for lb in labels)

    def test_model_com_arg_segue_fluxo_texto(self, tmp_path):
        bridge, sent, _ = self._picker_bridge(tmp_path)
        bridge._handle_update(_update(1, user_id=42, chat_id=42, text="/model reset"))
        bridge._executor.shutdown(wait=True)
        # nada de inline keyboard — resposta de texto do backend fake
        assert all("reply_markup" not in m for m in sent)

    def test_callback_provider_lista_modelos(self, tmp_path):
        bridge, _sent, edited = self._picker_bridge(tmp_path)
        bridge._send_model_picker("42", "tg:42")
        bridge._handle_callback({
            "id": "cb1", "data": "mp:p:0", "from": {"id": 42},
            "message": {"message_id": 77, "chat": {"id": 42}},
        })
        assert edited, "não editou para lista de modelos"
        kb = edited[-1].get("reply_markup", {}).get("inline_keyboard")
        labels = [b["text"] for row in kb for b in row]
        assert any("m2" in lb for lb in labels)

    def test_callback_model_troca(self, tmp_path):
        bridge, _sent, _edited = self._picker_bridge(tmp_path)
        bridge._send_model_picker("42", "tg:42")
        bridge._handle_callback({
            "id": "c1", "data": "mp:p:0", "from": {"id": 42},
            "message": {"message_id": 77, "chat": {"id": 42}},
        })
        bridge._handle_callback({
            "id": "c2", "data": "mp:m:0:1", "from": {"id": 42},
            "message": {"message_id": 77, "chat": {"id": 42}},
        })
        assert bridge.backend._model_overrides.get("tg:42") == "m2"

    def test_callback_nao_autorizado_ignorado(self, tmp_path):
        bridge, _sent, edited = self._picker_bridge(tmp_path)
        bridge._send_model_picker("42", "tg:42")
        bridge._handle_callback({
            "id": "c1", "data": "mp:p:0", "from": {"id": 666},
            "message": {"message_id": 77, "chat": {"id": 42}},
        })
        assert edited == []  # estranho não navega


class TestStreamingDraft:
    def _draft_bridge(self, tmp_path):
        sent: list[dict] = []
        edited: list[dict] = []
        deleted: list[dict] = []

        def handler(request):
            url = str(request.url)
            body = json.loads(request.content) if request.content else {}
            if "sendMessage" in url:
                sent.append(body)
                return httpx.Response(200, json={
                    "ok": True, "result": {"message_id": len(sent)}
                })
            if "editMessageText" in url:
                edited.append(body)
            if "deleteMessage" in url:
                deleted.append(body)
            return httpx.Response(200, json={"ok": True, "result": {}})

        return _make_bridge(tmp_path, handler), sent, edited, deleted

    def test_deltas_criam_e_editam_mensagem(self, tmp_path):
        bridge, sent, edited, _ = self._draft_bridge(tmp_path)
        draft = _StreamingDraft(bridge, "42", interval=0.0)
        draft.on_delta("Esta é uma resposta longa o suficiente para exibir")
        draft.on_delta(" e continua crescendo conforme chega")
        assert len(sent) == 1
        assert edited, "segunda delta deveria editar"
        assert draft.finish("Esta é a resposta final formatada") is True
        assert any("final" in e.get("text", "") for e in edited)

    def test_json_de_tool_fica_silencioso(self, tmp_path):
        bridge, sent, _edited, _ = self._draft_bridge(tmp_path)
        draft = _StreamingDraft(bridge, "42", interval=0.0)
        draft.on_delta('{"action": "shell", "args": {"command": "ls -la sb"}}')
        assert sent == []  # JSON não aparece no chat

    def test_on_tool_mostra_progresso(self, tmp_path):
        bridge, sent, edited, _ = self._draft_bridge(tmp_path)
        draft = _StreamingDraft(bridge, "42", interval=0.0)
        draft.on_tool("shell")
        assert sent and "shell" in sent[0]["text"]
        draft.on_tool("read_file")
        assert edited and "read_file" in edited[-1]["text"]
        assert draft.finish("Pronto, executei tudo.") is True

    def test_finish_sem_stream_retorna_false(self, tmp_path):
        bridge, _sent, _edited, _ = self._draft_bridge(tmp_path)
        draft = _StreamingDraft(bridge, "42")
        assert draft.finish("resposta") is False  # caller envia normal

    def test_finish_vazio_apaga_draft(self, tmp_path):
        bridge, _sent, _edited, deleted = self._draft_bridge(tmp_path)
        draft = _StreamingDraft(bridge, "42", interval=0.0)
        draft.on_tool("shell")
        assert draft.finish("") is True
        assert deleted, "draft órfão deveria ser apagado"
