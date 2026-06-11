"""Testes do TelegramBridge — httpx.MockTransport, sem rede."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from bauer.channel_base import AgentBackend, ChannelMessage
from bauer.telegram_bridge import MAX_MESSAGE_CHARS, TelegramBridge


class _EchoBackend(AgentBackend):
    """Backend fake — responde sem tocar em LLM/config."""

    def __init__(self):
        super().__init__()
        self.received: list[ChannelMessage] = []

    @property
    def is_ready(self):
        return True

    def process(self, msg: ChannelMessage) -> str:
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


class TestApiETokens:
    def test_get_me_ok(self, tmp_path):
        def handler(request):
            assert "/bot123:FAKE/getMe" in str(request.url)
            return httpx.Response(200, json={"ok": True, "result": {"username": "bauer_bot"}})

        bridge = _make_bridge(tmp_path, handler)
        assert bridge.get_me()["username"] == "bauer_bot"

    def test_api_erro_telegram_levanta(self, tmp_path):
        def handler(request):
            return httpx.Response(200, json={"ok": False, "description": "Unauthorized"})

        bridge = _make_bridge(tmp_path, handler)
        with pytest.raises(RuntimeError, match="Unauthorized"):
            bridge.get_me()

    def test_start_sem_token_falha_claro(self, tmp_path):
        bridge = TelegramBridge(token="", backend=_EchoBackend(), state_dir=tmp_path)
        with pytest.raises(RuntimeError, match="TELEGRAM_BOT_TOKEN"):
            bridge.start()


class TestSendText:
    def test_chunking_4096(self, tmp_path):
        sent: list[str] = []

        def handler(request):
            if "sendMessage" in str(request.url):
                sent.append(json.loads(request.content)["text"])
            return httpx.Response(200, json={"ok": True, "result": {}})

        bridge = _make_bridge(tmp_path, handler)
        bridge.send_text("42", "x" * 9000)
        assert len(sent) == 3
        assert all(len(s) <= MAX_MESSAGE_CHARS for s in sent)

    def test_falha_de_envio_nao_propaga(self, tmp_path):
        def handler(request):
            return httpx.Response(500)

        bridge = _make_bridge(tmp_path, handler)
        bridge.send_text("42", "oi")  # não levanta
        assert "sendMessage" in bridge.last_error


class TestHandleUpdate:
    def test_autorizado_recebe_resposta(self, tmp_path):
        sent: list[dict] = []

        def handler(request):
            if "sendMessage" in str(request.url):
                sent.append(json.loads(request.content))
            return httpx.Response(200, json={"ok": True, "result": {}})

        bridge = _make_bridge(tmp_path, handler)
        bridge._handle_update(_update(1, user_id=42, chat_id=42, text="oi bauer"))
        assert any("resposta para: oi bauer" in m["text"] for m in sent)

    def test_nao_autorizado_sem_resposta(self, tmp_path):
        sent: list[dict] = []

        def handler(request):
            if "sendMessage" in str(request.url):
                sent.append(json.loads(request.content))
            return httpx.Response(200, json={"ok": True, "result": {}})

        bridge = _make_bridge(tmp_path, handler)
        bridge._handle_update(_update(1, user_id=666, chat_id=666, text="hackear"))
        assert sent == []
        assert bridge.msgs_dropped == 1

    def test_allow_all_libera(self, tmp_path):
        bridge = _make_bridge(
            tmp_path, lambda r: httpx.Response(200, json={"ok": True, "result": {}}),
            allowed_users=[], allow_all=True,
        )
        msg = ChannelMessage(channel="telegram", user_id="777", chat_id="777", text="oi")
        assert bridge._is_authorized(msg) is True

    def test_update_sem_texto_ignorado(self, tmp_path):
        bridge = _make_bridge(tmp_path, lambda r: httpx.Response(200, json={"ok": True, "result": {}}))
        bridge._handle_update({"update_id": 5, "message": {"chat": {"id": 1}, "from": {"id": 42}}})
        assert bridge.backend.received == []


class TestOffsetPersistence:
    def test_offset_sobrevive_restart(self, tmp_path):
        handler = lambda r: httpx.Response(200, json={"ok": True, "result": {}})
        bridge = _make_bridge(tmp_path, handler)
        bridge._offset = 12345
        bridge._save_offset()

        bridge2 = _make_bridge(tmp_path, handler)
        assert bridge2._offset == 12345

    def test_offset_corrompido_volta_a_zero(self, tmp_path):
        state = tmp_path / "state"
        state.mkdir(parents=True)
        (state / "telegram_offset.json").write_text("{lixo", encoding="utf-8")
        bridge = _make_bridge(tmp_path, lambda r: httpx.Response(200, json={"ok": True, "result": {}}))
        assert bridge._offset == 0


class TestMarkdownParaHtml:
    def test_negrito_italico_code(self):
        from bauer.telegram_bridge import md_to_telegram_html
        out = md_to_telegram_html("**forte** e *leve* e `x = 1`")
        assert "<b>forte</b>" in out
        assert "<i>leve</i>" in out
        assert "<code>x = 1</code>" in out

    def test_bloco_de_codigo_vira_pre(self):
        from bauer.telegram_bridge import md_to_telegram_html
        out = md_to_telegram_html("antes\n```python\nprint('oi')\n```\ndepois")
        assert "<pre>print(&#x27;oi&#x27;)</pre>" in out or "<pre>print('oi')</pre>" in out

    def test_html_do_modelo_e_escapado(self):
        from bauer.telegram_bridge import md_to_telegram_html
        out = md_to_telegram_html("perigo <script>alert(1)</script>")
        assert "<script>" not in out
        assert "&lt;script&gt;" in out

    def test_link_markdown(self):
        from bauer.telegram_bridge import md_to_telegram_html
        out = md_to_telegram_html("veja [docs](https://example.com/x)")
        assert '<a href="https://example.com/x">docs</a>' in out

    def test_texto_simples_intacto(self):
        from bauer.telegram_bridge import md_to_telegram_html
        assert md_to_telegram_html("ola mundo") == "ola mundo"


class TestMenuDeComandos:
    def test_register_commands_chama_set_my_commands(self, tmp_path):
        calls: list[str] = []

        def handler(request):
            calls.append(str(request.url))
            return httpx.Response(200, json={"ok": True, "result": True})

        bridge = _make_bridge(tmp_path, handler)
        bridge.register_commands()
        assert any("setMyCommands" in c for c in calls)

    def test_falha_no_menu_nao_propaga(self, tmp_path):
        bridge = _make_bridge(tmp_path, lambda r: httpx.Response(500))
        bridge.register_commands()  # não levanta


class TestEnvioHtmlComFallback:
    def test_envia_com_parse_mode_html(self, tmp_path):
        sent: list[dict] = []

        def handler(request):
            if "sendMessage" in str(request.url):
                sent.append(json.loads(request.content))
            return httpx.Response(200, json={"ok": True, "result": {}})

        bridge = _make_bridge(tmp_path, handler)
        bridge.send_text("42", "**negrito**")
        assert sent[0]["parse_mode"] == "HTML"
        assert "<b>negrito</b>" in sent[0]["text"]

    def test_fallback_plain_quando_html_rejeitado(self, tmp_path):
        sent: list[dict] = []

        def handler(request):
            body = json.loads(request.content)
            if "sendMessage" in str(request.url):
                sent.append(body)
                if body.get("parse_mode") == "HTML":
                    return httpx.Response(400, json={"ok": False, "description": "can't parse"})
            return httpx.Response(200, json={"ok": True, "result": {}})

        bridge = _make_bridge(tmp_path, handler)
        bridge.send_text("42", "texto com <tag> esquisita")
        # 1ª tentativa HTML falhou → 2ª sem parse_mode com o texto cru
        assert len(sent) == 2
        assert "parse_mode" not in sent[1]
        assert sent[1]["text"] == "texto com <tag> esquisita"


class TestConflito409:
    def test_409_da_mensagem_acionavel(self, tmp_path):
        def handler(request):
            return httpx.Response(409, json={"ok": False, "description": "Conflict"})

        bridge = _make_bridge(tmp_path, handler)
        with pytest.raises(RuntimeError, match="outro processo"):
            bridge.get_me()


class TestBuildFromConfig:
    def test_monta_do_bauer_config(self, tmp_path, monkeypatch):
        from bauer.config_loader import BauerConfig
        from bauer.telegram_bridge import build_bridge_from_config

        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "env-token")
        cfg = BauerConfig(**{
            "model": {"provider": "ollama", "name": "m",
                      "requested_context": 4096, "minimum_context": 2048},
            "agent": {"workspace": str(tmp_path / "ws")},
            "telegram": {"enabled": True, "allowed_users": [1, 2],
                         "bot_token": "config-token"},
        })
        bridge = build_bridge_from_config(cfg)
        assert bridge.token == "env-token"  # env vence
        assert bridge.allowed_users == {1, 2}
