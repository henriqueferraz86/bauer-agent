"""Testes do AgentBackend + BaseBridge (bauer/channel_base.py)."""

from __future__ import annotations

import threading
from pathlib import Path

import pytest

from bauer.channel_base import (
    AgentBackend,
    BaseBridge,
    ChannelMessage,
    RateLimiter,
    chunk_text,
)


class _EchoClient:
    """Cliente fake: responde 'eco: <última msg de user>'."""

    host = "http://localhost:11434"

    def chat_stream(self, model, messages):
        last_user = next(
            (m["content"] for m in reversed(messages) if m["role"] == "user"), ""
        )
        yield f"eco: {last_user[:60]}"


def _make_backend(tmp_path: Path, client=None) -> AgentBackend:
    """Backend com dependências injetadas (sem config.yaml / sem rede)."""
    from bauer.sqlite_session_store import SqliteSessionStore
    from bauer.tool_router import ToolRouter

    backend = AgentBackend(sessions_dir=tmp_path / "sessions")
    backend._client = client or _EchoClient()
    backend._model_name = "fake-model"
    backend._provider = "ollama"
    backend._applied_context = 8192
    backend._router = ToolRouter(workspace=tmp_path / "ws")
    backend._store = SqliteSessionStore(tmp_path / "sessions")
    backend._system_prompt = "Você é um assistente de teste."
    return backend


def _msg(text: str, user="42", chat="42", channel="telegram") -> ChannelMessage:
    return ChannelMessage(channel=channel, user_id=user, chat_id=chat, text=text)


class TestChannelMessage:
    def test_session_key_telegram(self):
        assert _msg("oi").session_key == "tg:42"

    def test_session_key_discord(self):
        m = _msg("oi", channel="discord", chat="999")
        assert m.session_key == "dc:999"

    def test_session_key_canal_desconhecido(self):
        m = _msg("oi", channel="slack", chat="1")
        assert m.session_key == "slack:1"


class TestChunkText:
    def test_curto_retorna_inteiro(self):
        assert chunk_text("abc", 100) == ["abc"]

    def test_vazio_retorna_lista_vazia(self):
        assert chunk_text("", 100) == []

    def test_divide_em_quebra_de_linha(self):
        text = "linha1\n" + "x" * 90 + "\nlinha3"
        chunks = chunk_text(text, 100)
        assert all(len(c) <= 100 for c in chunks)
        assert "".join(chunks).replace("\n", "") == text.replace("\n", "")

    def test_sem_quebra_corta_seco(self):
        chunks = chunk_text("a" * 250, 100)
        assert [len(c) for c in chunks] == [100, 100, 50]


class TestAgentBackendProcess:
    def test_responde_via_modelo(self, tmp_path):
        backend = _make_backend(tmp_path)
        resp = backend.process(_msg("qual a capital do brasil?"))
        assert "eco:" in resp

    def test_help_e_start_retornam_ajuda(self, tmp_path):
        backend = _make_backend(tmp_path)
        assert "Comandos" in backend.process(_msg("/help"))
        assert "Comandos" in backend.process(_msg("/start"))

    def test_status_mostra_modelo(self, tmp_path):
        backend = _make_backend(tmp_path)
        resp = backend.process(_msg("/status"))
        assert "fake-model" in resp
        assert "tg:42" in resp

    def test_clear_apaga_historico(self, tmp_path):
        backend = _make_backend(tmp_path)
        backend.process(_msg("primeira mensagem"))
        ctx, _ = backend._get_session("tg:42")
        assert len(ctx.messages) >= 2
        backend.process(_msg("/clear"))
        ctx2, _ = backend._get_session("tg:42")
        assert len(ctx2.messages) == 0

    def test_sessao_persiste_no_store(self, tmp_path):
        backend = _make_backend(tmp_path)
        backend.process(_msg("lembre disto"))
        saved = backend._store.load("tg:42")
        assert any("lembre disto" in m.get("content", "") for m in saved)

    def test_sessoes_separadas_por_chat(self, tmp_path):
        backend = _make_backend(tmp_path)
        backend.process(_msg("msg do chat A", chat="100"))
        backend.process(_msg("msg do chat B", chat="200"))
        a = backend._store.load("tg:100")
        b = backend._store.load("tg:200")
        assert any("chat A" in m.get("content", "") for m in a)
        assert not any("chat A" in m.get("content", "") for m in b)

    def test_sessao_recarregada_apos_evict(self, tmp_path):
        backend = _make_backend(tmp_path)
        backend.process(_msg("contexto importante", chat="500"))
        # Simula evict do cache (restart)
        backend._sessions.clear()
        ctx, _ = backend._get_session("tg:500")
        assert any("contexto importante" in m.get("content", "") for m in ctx.messages)

    def test_erro_no_modelo_vira_mensagem_amigavel(self, tmp_path):
        class BrokenClient:
            host = "http://localhost:11434"

            def chat_stream(self, model, messages):
                raise RuntimeError("provider caiu")
                yield  # pragma: no cover

        backend = _make_backend(tmp_path, client=BrokenClient())
        resp = backend.process(_msg("oi"))
        assert "Erro" in resp or "erro" in resp
        assert "Traceback" not in resp

    def test_mensagem_vazia_ignorada(self, tmp_path):
        backend = _make_backend(tmp_path)
        assert backend.process(_msg("   ")) == ""


class TestRateLimiter:
    def test_permite_ate_o_limite(self):
        rl = RateLimiter(max_per_minute=3)
        assert all(rl.allow("u1") for _ in range(3))
        assert rl.allow("u1") is False

    def test_usuarios_independentes(self):
        rl = RateLimiter(max_per_minute=1)
        assert rl.allow("u1") is True
        assert rl.allow("u2") is True
        assert rl.allow("u1") is False


class _FakeBridge(BaseBridge):
    name = "fake"

    def __init__(self, backend, allowed=None, **kw):
        super().__init__(backend, **kw)
        self.allowed = allowed or set()
        self.sent: list[tuple[str, str]] = []

    def start(self):
        pass

    def send_text(self, chat_id, text):
        self.sent.append((chat_id, text))

    def _is_authorized(self, msg):
        return msg.user_id in self.allowed


class TestBaseBridge:
    def test_nao_autorizado_descartado(self, tmp_path):
        bridge = _FakeBridge(_make_backend(tmp_path), allowed={"99"})
        resp = bridge.handle_message(_msg("oi", user="1"))
        assert resp is None
        assert bridge.msgs_dropped == 1

    def test_autorizado_processa(self, tmp_path):
        bridge = _FakeBridge(_make_backend(tmp_path), allowed={"42"})
        resp = bridge.handle_message(_msg("oi", user="42"))
        assert resp and "eco:" in resp

    def test_rate_limit_recusa_educada(self, tmp_path):
        bridge = _FakeBridge(
            _make_backend(tmp_path), allowed={"42"},
            rate_limiter=RateLimiter(max_per_minute=1),
        )
        bridge.handle_message(_msg("um", user="42"))
        resp = bridge.handle_message(_msg("dois", user="42"))
        assert "limite" in resp.lower()

    def test_status_dict(self, tmp_path):
        bridge = _FakeBridge(_make_backend(tmp_path))
        s = bridge.status()
        assert s["name"] == "fake"
        assert s["running"] is True
        bridge.stop()
        assert bridge.status()["running"] is False
