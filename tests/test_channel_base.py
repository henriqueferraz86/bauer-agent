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
    """Backend com dependências injetadas (sem config.yaml / sem rede).

    config_path aponta para um arquivo INEXISTENTE de propósito: o default
    ("config.yaml") acharia o config real do repo no CWD e o _maybe_reload
    substituiria o client fake por um provider REAL — foi exatamente o que
    aconteceu no CI (testes responderam com o opencode de verdade).
    """
    from bauer.sqlite_session_store import SqliteSessionStore
    from bauer.tool_router import ToolRouter

    backend = AgentBackend(
        config_path=tmp_path / "no-such-config.yaml",
        sessions_dir=tmp_path / "sessions",
    )
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


class TestComandoModel:
    def test_model_mostra_ativo(self, tmp_path):
        backend = _make_backend(tmp_path)
        backend._models_fetcher = lambda: []
        resp = backend.process(_msg("/model"))
        assert "fake-model" in resp
        assert "ollama" in resp

    def test_model_lista_numerada(self, tmp_path):
        backend = _make_backend(tmp_path)
        backend._models_fetcher = lambda: ["alfa", "fake-model", "gama"]
        resp = backend.process(_msg("/model"))
        assert "1. alfa" in resp
        assert "2. fake-model ←" in resp  # marca o ativo
        assert "/model <número ou nome>" in resp

    def test_model_troca_por_numero(self, tmp_path):
        backend = _make_backend(tmp_path)
        backend._models_fetcher = lambda: ["alfa", "beta", "gama"]
        resp = backend.process(_msg("/model 3"))
        assert "gama" in resp and "✅" in resp
        assert backend._model_overrides["tg:42"] == "gama"

    def test_model_troca_por_nome(self, tmp_path):
        backend = _make_backend(tmp_path)
        backend._models_fetcher = lambda: ["alfa", "beta"]
        resp = backend.process(_msg("/model beta"))
        assert backend._model_overrides["tg:42"] == "beta"
        assert "⚠️" not in resp

    def test_model_nome_fora_da_lista_avisa_mas_aceita(self, tmp_path):
        backend = _make_backend(tmp_path)
        backend._models_fetcher = lambda: ["alfa"]
        resp = backend.process(_msg("/model modelo-exotico"))
        assert backend._model_overrides["tg:42"] == "modelo-exotico"
        assert "⚠️" in resp

    def test_model_numero_invalido(self, tmp_path):
        backend = _make_backend(tmp_path)
        backend._models_fetcher = lambda: ["alfa", "beta"]
        resp = backend.process(_msg("/model 99"))
        assert "fora da lista" in resp
        assert "tg:42" not in backend._model_overrides

    def test_model_reset_volta_ao_global(self, tmp_path):
        backend = _make_backend(tmp_path)
        backend._models_fetcher = lambda: ["alfa"]
        backend.process(_msg("/model alfa"))
        resp = backend.process(_msg("/model reset"))
        assert "tg:42" not in backend._model_overrides
        assert "fake-model" in resp

    def test_override_e_por_conversa(self, tmp_path):
        backend = _make_backend(tmp_path)
        backend._models_fetcher = lambda: ["alfa"]
        backend.process(_msg("/model alfa", chat="111"))
        assert backend._model_overrides.get("tg:111") == "alfa"
        assert "tg:222" not in backend._model_overrides

    def test_run_turn_usa_o_override(self, tmp_path):
        used: list[str] = []

        class RecordingClient:
            host = "http://localhost:11434"

            def chat_stream(self, model, messages):
                used.append(model)
                yield "ok"

        backend = _make_backend(tmp_path, client=RecordingClient())
        backend._models_fetcher = lambda: ["modelo-x"]
        backend.process(_msg("/model modelo-x"))
        backend.process(_msg("oi"))
        assert used[-1] == "modelo-x"

    def test_status_mostra_override(self, tmp_path):
        backend = _make_backend(tmp_path)
        backend._models_fetcher = lambda: ["alfa"]
        backend.process(_msg("/model alfa"))
        resp = backend.process(_msg("/status"))
        assert "alfa" in resp
        assert "global: fake-model" in resp

    def test_comando_com_sufixo_de_bot(self, tmp_path):
        # Telegram em grupo: "/status@MeuBot"
        backend = _make_backend(tmp_path)
        resp = backend.process(_msg("/status@bauer_bot"))
        assert "fake-model" in resp

    def test_cache_da_lista_de_modelos(self, tmp_path):
        calls = {"n": 0}

        def fetcher():
            calls["n"] += 1
            return ["alfa"]

        backend = _make_backend(tmp_path)
        backend._models_fetcher = fetcher
        backend.process(_msg("/model"))
        backend.process(_msg("/model"))
        assert calls["n"] == 1  # segunda chamada veio do cache


class TestComandosNovosETasks:
    def test_new_e_alias_de_clear(self, tmp_path):
        backend = _make_backend(tmp_path)
        backend.process(_msg("lembra disso"))
        backend.process(_msg("/new"))
        ctx, _ = backend._get_session("tg:42")
        assert len(ctx.messages) == 0

    def test_tasks_vazio_orienta(self, tmp_path):
        backend = _make_backend(tmp_path)
        resp = backend.process(_msg("/tasks"))
        assert "Nenhuma tarefa" in resp

    def test_tasks_lista_kanban(self, tmp_path):
        from bauer.workspace_manager import WorkspaceManager

        backend = _make_backend(tmp_path)
        wm = WorkspaceManager(backend._router.workspace)
        wm.add_task("Revisar relatório de vendas")
        resp = backend.process(_msg("/tasks"))
        assert "Revisar relatório" in resp
        assert "TODO" in resp

    def test_help_menciona_novos_comandos(self, tmp_path):
        backend = _make_backend(tmp_path)
        resp = backend.process(_msg("/help"))
        assert "/model" in resp and "/tasks" in resp and "/new" in resp


class TestErrosDeProviderAmigaveis:
    def _backend_que_falha(self, tmp_path, exc: Exception):
        class FailingClient:
            host = "http://localhost:11434"

            def chat_stream(self, model, messages):
                raise exc
                yield  # pragma: no cover

        return _make_backend(tmp_path, client=FailingClient())

    def test_rate_limit_vira_mensagem_de_espera(self, tmp_path):
        backend = self._backend_que_falha(
            tmp_path, RuntimeError("HTTP 429: rate limit exceeded, retry later")
        )
        resp = backend.process(_msg("oi"))
        assert "limite" in resp.lower()
        assert "429" in resp or "rate" in resp.lower()

    def test_auth_orienta_doctor(self, tmp_path):
        backend = self._backend_que_falha(
            tmp_path, RuntimeError("HTTP 401: invalid api key")
        )
        resp = backend.process(_msg("oi"))
        assert "autentica" in resp.lower() or "key" in resp.lower()

    def test_erro_generico_inclui_detalhe(self, tmp_path):
        backend = self._backend_que_falha(tmp_path, RuntimeError("explodiu sem motivo"))
        resp = backend.process(_msg("oi"))
        assert "explodiu sem motivo" in resp
        assert "Traceback" not in resp


class TestHotReload:
    def test_mudanca_no_config_troca_modelo(self, tmp_path, monkeypatch):
        import time as _time

        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text(
            "model:\n  provider: ollama\n  name: modelo-velho\n"
            "  requested_context: 4096\n  minimum_context: 2048\n"
            f"agent:\n  workspace: {(tmp_path / 'ws').as_posix()}\n",
            encoding="utf-8",
        )
        backend = _make_backend(tmp_path)
        backend.config_path = cfg_file
        backend._config_mtime = cfg_file.stat().st_mtime
        backend._model_name = "modelo-velho"

        # builder injetável: não exige provider real nem importa bauer.cli
        new_client = _EchoClient()
        backend._client_builder = lambda cfg: new_client

        # muda o modelo no disco (mtime precisa diferir)
        _time.sleep(0.05)
        cfg_file.write_text(
            cfg_file.read_text(encoding="utf-8").replace("modelo-velho", "modelo-novo"),
            encoding="utf-8",
        )
        import os as _os
        _os.utime(cfg_file, (cfg_file.stat().st_atime, cfg_file.stat().st_mtime + 5))

        backend.process(_msg("oi"))
        assert backend._model_name == "modelo-novo"
        assert backend._client is new_client

    def test_reload_com_config_quebrado_mantem_atual(self, tmp_path):
        import os as _os

        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text(
            "model:\n  provider: ollama\n  name: m\n"
            "  requested_context: 4096\n  minimum_context: 2048\n",
            encoding="utf-8",
        )
        backend = _make_backend(tmp_path)
        backend.config_path = cfg_file
        backend._config_mtime = cfg_file.stat().st_mtime

        cfg_file.write_text("model:\n  invalido: {{{", encoding="utf-8")
        _os.utime(cfg_file, (cfg_file.stat().st_atime, cfg_file.stat().st_mtime + 5))

        resp = backend.process(_msg("ainda funciona?"))
        assert "eco:" in resp  # client antigo continua respondendo
        assert backend._model_name == "fake-model"


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
