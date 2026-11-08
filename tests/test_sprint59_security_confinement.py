"""Sprint 59 — confinamento de sessões, mídia e stores internos (lote 1).

Regressões herméticas de WEB-01, T04 e T05 da auditoria 2026-10-12. Todas
usam ``tmp_path``; nenhum provider, bridge ou rede real é tocado.
"""

from __future__ import annotations

import json
import os
import textwrap
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from bauer import confined_files  # noqa: E402
from bauer.session_ids import InvalidSessionIdError, is_valid_session_id, validate_session_id  # noqa: E402
from bauer.session_store import SessionStore  # noqa: E402
from bauer.sqlite_session_store import SqliteSessionStore  # noqa: E402
from bauer.tool_router import SandboxError, ToolError, ToolRouter  # noqa: E402


def _symlink(link: Path, target: Path) -> None:
    try:
        link.symlink_to(target)
    except (OSError, NotImplementedError) as exc:  # Windows sem privilégio
        pytest.skip(f"symlink indisponível: {exc}")


# ── WEB-01: formato único de session_id ─────────────────────────────────────

VALID_IDS = ["a1b2c3d4", "agent-coder", "tg:12345", "tg:-1001234", "dc:99",
             "tg-project-0123456789abcdef01234567-proj_1", "agent:main:main",
             "runtime-test-session-1f0e", "user@example.com"]
INVALID_IDS = ["", "..", ".", "../victim", "..\\victim", "a/b", "a\\b", "/etc/passwd",
               "C:\\Windows\\x", "\\\\server\\share", ".hidden", "x.", "con", "NUL.txt",
               "a b", "a\x00b", "x" * 201, "é", "../../../../tmp/x"]


@pytest.mark.parametrize("sid", VALID_IDS)
def test_web01_valid_session_ids_accepted(sid):
    assert validate_session_id(sid) == sid


@pytest.mark.parametrize("sid", INVALID_IDS)
def test_web01_invalid_session_ids_rejected(sid):
    assert not is_valid_session_id(sid)
    with pytest.raises(InvalidSessionIdError):
        validate_session_id(sid)


@pytest.mark.parametrize("sid", ["../victim", "..\\victim", "/tmp/victim", "a/../../victim"])
def test_web01_session_store_rejects_traversal_on_every_operation(tmp_path, sid):
    victim = tmp_path / "victim.jsonl"
    victim.write_text('{"role": "user", "content": "outside-original"}\n', encoding="utf-8")
    store = SessionStore(tmp_path / "sessions")
    for call in (lambda: store.load(sid), lambda: store.save(sid, [{"role": "user", "content": "x"}]),
                 lambda: store.delete(sid), lambda: store.exists(sid)):
        with pytest.raises(InvalidSessionIdError):
            call()
    with pytest.raises(InvalidSessionIdError):
        with store.turn(sid):
            pass
    assert victim.read_text(encoding="utf-8") == '{"role": "user", "content": "outside-original"}\n'


def test_web01_session_store_symlink_escaping_root_rejected(tmp_path):
    victim = tmp_path / "victim.jsonl"
    victim.write_text('{"role": "user", "content": "outside-original"}\n', encoding="utf-8")
    store = SessionStore(tmp_path / "sessions")
    _symlink(store.dir / "evil.jsonl", victim)
    with pytest.raises(InvalidSessionIdError):
        store.load("evil")
    with pytest.raises(InvalidSessionIdError):
        store.save("evil", [{"role": "user", "content": "overwrite"}])
    assert "overwrite" not in victim.read_text(encoding="utf-8")
    assert "evil" not in store.list_sessions()
    assert store.exists("evil") is False


def test_web01_session_store_normal_persistence(tmp_path):
    store = SessionStore(tmp_path / "sessions")
    msgs = [{"role": "user", "content": "oi"}, {"role": "assistant", "content": "olá"}]
    for sid in ("abc123", "tg:42"):
        store.save(sid, msgs)
        assert store.load(sid) == msgs
        assert store.exists(sid)
    assert store.list_sessions() == ["abc123", "tg:42"]
    assert store.load("missing1") == []
    assert store.delete("abc123") is True
    assert not list(store.dir.glob("*.tmp"))


def test_web01_sqlite_store_validates_and_migration_skips_symlink(tmp_path):
    sessions = tmp_path / "sessions"
    sessions.mkdir()
    victim = tmp_path / "victim.jsonl"
    victim.write_text('{"role": "user", "content": "outside-secret"}\n', encoding="utf-8")
    _symlink(sessions / "leak.jsonl", victim)
    (sessions / "ok1.jsonl").write_text('{"role": "user", "content": "inside"}\n', encoding="utf-8")
    store = SqliteSessionStore(sessions, semantic_indexing_enabled=False)
    assert store.load("ok1") == [{"role": "user", "content": "inside"}]
    assert store.load("leak") == []
    with pytest.raises(InvalidSessionIdError):
        store.save("../victim", [{"role": "user", "content": "x"}])
    with pytest.raises(InvalidSessionIdError):
        store.load("a/b")


def _cfg(tmp_path: Path, *, kernel_enabled: bool) -> Path:
    p = tmp_path / "config.yaml"
    p.write_text(textwrap.dedent(f"""
        model:
          provider: ollama
          name: fake
          requested_context: 4096
        kernel:
          enabled: {str(kernel_enabled).lower()}
        """), encoding="utf-8")
    return p


@pytest.fixture(params=[True, False], ids=["kernel-on", "kernel-off"])
def web_app(request, tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    llm = MagicMock()
    llm.host = "http://localhost:11434"
    llm._provider = "ollama"
    llm.list_models.return_value = ["fake"]
    calls: list = []

    def fake_turn(ctx, *a, **k):
        calls.append(ctx)
        ctx.add_assistant("RESPOSTA")
        return "RESPOSTA", []

    with patch("bauer.projects_registry._DEFAULT_REGISTRY", tmp_path / "p.json"), \
         patch("bauer.agent.run_one_turn_with_fallback", side_effect=fake_turn), \
         patch("bauer.agent.run_one_turn", side_effect=fake_turn):
        from bauer.server import create_app
        app = create_app("fake", 4096, ToolRouter(workspace=ws, audit_enabled=False), llm, "s",
                         tmp_path / "sessions", api_key="k", rate_limit_requests=0, workspace=ws,
                         config_path=_cfg(tmp_path, kernel_enabled=request.param))
        victim = tmp_path / "victim.jsonl"
        victim.write_text('{"role": "user", "content": "outside-original"}\n', encoding="utf-8")
        yield TestClient(app), victim, calls, llm


H = {"X-API-Key": "k"}


def _assert_untouched(victim, calls, llm):
    assert victim.read_text(encoding="utf-8") == '{"role": "user", "content": "outside-original"}\n'
    assert calls == []
    llm.chat.assert_not_called()
    llm.chat_stream.assert_not_called()


@pytest.mark.parametrize("sid", ["../victim", "..\\victim", "/tmp/victim"])
def test_web01_http_chat_rejects_traversal_before_llm(web_app, sid):
    client, victim, calls, llm = web_app
    resp = client.post("/chat", headers=H, json={"message": "overwrite", "session_id": sid})
    assert resp.status_code in (400, 422)
    _assert_untouched(victim, calls, llm)


def test_web01_http_stream_loop_and_openai_reject_traversal(web_app):
    client, victim, calls, llm = web_app
    assert client.get("/stream", headers=H, params={"message": "x", "session_id": "../victim"}).status_code == 400
    assert client.get("/chat/context", headers=H, params={"session_id": "../victim"}).status_code == 400
    assert client.post("/loop", headers=H, json={"message": "x", "session_id": "../victim"}).status_code == 422
    body = {"messages": [{"role": "user", "content": "overwrite"}]}
    assert client.post("/v1/chat/completions", headers=H,
                       json={**body, "session_id": "../victim"}).status_code == 422
    assert client.post("/v1/chat/completions", headers={**H, "X-Hermes-Session-Id": "../victim"},
                       json=body).status_code == 400
    assert client.get("/sessions/..%5Cvictim/messages", headers=H).status_code == 400
    assert client.delete("/sessions/..%5Cvictim", headers=H).status_code == 400
    _assert_untouched(victim, calls, llm)


def test_web01_http_valid_session_still_works(web_app):
    client, _victim, calls, _llm = web_app
    resp = client.post("/chat", headers=H, json={"message": "oi", "session_id": "abc-123"})
    assert resp.status_code == 200
    assert resp.json()["session_id"] == "abc-123"
    resp = client.post("/chat", headers=H, json={"message": "oi"})
    assert resp.status_code == 200 and resp.json()["session_id"]
    assert len(calls) == 2


# ── T04: mídia de saída confinada ao workspace ──────────────────────────────

class _Bridge:
    def __init__(self):
        self.media: list = []
        self.texts: list = []

    def send_text(self, chat_id, text):
        self.texts.append(text)

    def send_media(self, chat_id, path):
        self.media.append(path)
        return True


@pytest.fixture
def media_env(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    outside = tmp_path / "outside-private.txt"
    outside.write_text("audit-only-private", encoding="utf-8")
    return ToolRouter(workspace=ws, audit_enabled=False), ws, outside


def _send(router, media_path, text=""):
    return router.execute({"action": "send_message", "args": {
        "channel": "telegram", "chat_id": "1", "text": text, "media_path": media_path}})


@pytest.mark.parametrize("bridge_alive", [True, False], ids=["bridge", "outbox"])
def test_t04_send_message_rejects_external_media(media_env, bridge_alive):
    router, ws, outside = media_env
    bridge = _Bridge()
    _symlink(ws / "link.txt", outside)
    with patch("bauer.live_bridges.get", lambda ch: bridge if bridge_alive else None), \
         patch("bauer.gateway_outbox.GatewayOutbox.enqueue") as enqueue:
        for raw in (str(outside), "../outside-private.txt", "link.txt"):
            with pytest.raises(SandboxError):
                _send(router, raw, text="segredo")
    assert bridge.media == [] and bridge.texts == []
    enqueue.assert_not_called()


def test_t04_send_message_internal_media_without_text(media_env):
    router, ws, _outside = media_env
    img = ws / "foto.png"
    img.write_bytes(b"PNG")
    bridge = _Bridge()
    with patch("bauer.live_bridges.get", lambda ch: bridge):
        out = _send(router, "foto.png")
    assert "mídia" in out
    assert bridge.media == [str(img.resolve())]


def test_t04_outbox_receives_resolved_absolute_path(media_env):
    router, ws, _outside = media_env
    (ws / "sub").mkdir()
    img = ws / "sub" / "a.png"
    img.write_bytes(b"PNG")
    with patch("bauer.live_bridges.get", lambda ch: None), \
         patch("bauer.gateway_outbox.GatewayOutbox.enqueue") as enqueue:
        enqueue.return_value = MagicMock(message_id="m1")
        _send(router, "sub/../sub/a.png")
    payload = enqueue.call_args.kwargs["payload"]
    assert payload["media_path"] == str(img.resolve())


def test_t04_send_message_missing_media_rejected(media_env):
    router, _ws, _outside = media_env
    with patch("bauer.live_bridges.get", lambda ch: _Bridge()):
        with pytest.raises(ToolError, match="não encontrada"):
            _send(router, "nope.png")


def test_t04_social_post_rejects_external_media_before_upload(media_env):
    router, _ws, outside = media_env
    client = MagicMock()
    with patch.object(router, "_postiz_client", return_value=client):
        with pytest.raises(SandboxError):
            router.execute({"action": "social_post", "args": {
                "content": "x", "channels": ["ig-1"], "media_paths": [str(outside)]}})
    client.upload.assert_not_called()
    client.create_post.assert_not_called()


def test_t04_telegram_reply_media_confined_without_project(tmp_path):
    from bauer.telegram_bridge import TelegramBridge

    ws = tmp_path / "ws"
    ws.mkdir()
    inside = ws / "in.png"
    inside.write_bytes(b"PNG")
    outside = tmp_path / "out.png"
    outside.write_bytes(b"PNG")
    backend = MagicMock()
    backend.config_path = tmp_path / "config.yaml"
    backend._router.workspace = ws
    backend.project_context.return_value = None
    backend.handle_message.return_value = f"[media: {outside}]\n[media: in.png]"
    bridge = TelegramBridge.__new__(TelegramBridge)
    bridge.backend = backend
    sent: list = []
    bridge.send_media = lambda chat, path, caption="": sent.append(Path(path))
    bridge.send_text = lambda chat, text: True
    bridge.handle_message = lambda msg, on_delta=None: backend.handle_message.return_value
    bridge._ingest_media = lambda message, chat_id: None
    msg = MagicMock(text="oi", chat_id="1", session_key="tg:1")

    class _Draft:
        def __init__(self, *a):
            pass

        def finish(self, text):
            return True

    class _Typing:
        def __init__(self, *a):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    with patch("bauer.telegram_bridge._StreamingDraft", _Draft), \
         patch("bauer.telegram_bridge._TypingHeartbeat", _Typing):
        bridge._process_scoped_message(msg, {}, None)
    assert sent == [inside.resolve()]


# ── T05: stores internos não seguem symlink ─────────────────────────────────

@pytest.fixture
def store_env(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    return ToolRouter(workspace=ws, audit_enabled=False), ws, tmp_path


_OUTSIDE = {"leak": {"name": "leak", "description": "OUTSIDE", "content": "OUTSIDE",
                     "value": "OUTSIDE", "command": "x", "mode": "python", "status": "active",
                     "schedule": {"type": "interval", "unit": "minutes", "value": 5}}}


@pytest.mark.parametrize("filename,calls", [
    (".bauer_memory.json", [{"action": "set", "key": "k", "value": "v"}, {"action": "get", "key": "leak"},
                            {"action": "list"}, {"action": "delete", "key": "leak"}]),
])
def test_t05_memory_symlink_rejected_everywhere(store_env, filename, calls):
    router, ws, tmp = store_env
    outside = tmp / "outside.json"
    outside.write_text(json.dumps(_OUTSIDE), encoding="utf-8")
    _symlink(ws / filename, outside)
    for args in calls:
        with pytest.raises(SandboxError):
            router.execute({"action": "memory", "args": args})
    assert json.loads(outside.read_text(encoding="utf-8")) == _OUTSIDE


def test_t05_skills_symlink_rejected_on_read_and_write(store_env):
    router, ws, tmp = store_env
    outside = tmp / "outside.json"
    outside.write_text(json.dumps(_OUTSIDE), encoding="utf-8")
    _symlink(ws / ".bauer_skills.json", outside)
    for action, args in [("skill_manage", {"action": "create", "name": "x", "description": "d", "content": "c"}),
                         ("skill_manage", {"action": "update", "name": "leak", "description": "d", "content": "c"}),
                         ("skill_view", {"name": "leak"}), ("skills_list", {})]:
        with pytest.raises(SandboxError):
            router.execute({"action": action, "args": args})
    assert json.loads(outside.read_text(encoding="utf-8")) == _OUTSIDE


def test_t05_cronjob_symlink_rejected_on_create_list_and_run(store_env):
    router, ws, tmp = store_env
    outside = tmp / "outside.json"
    outside.write_text(json.dumps(_OUTSIDE), encoding="utf-8")
    _symlink(ws / ".bauer_cronjobs.json", outside)
    for args in [{"action": "create", "name": "n", "command": "print(1)", "schedule": "every 30m"},
                 {"action": "list"}, {"action": "run", "name": "leak"}]:
        with pytest.raises(SandboxError):
            router.execute({"action": "cronjob", "args": args})
    assert json.loads(outside.read_text(encoding="utf-8")) == _OUTSIDE


def test_t05_internal_stores_normal_persistence(store_env):
    router, ws, _tmp = store_env
    router.execute({"action": "memory", "args": {"action": "set", "key": "k", "value": "v"}})
    assert "v" in router.execute({"action": "memory", "args": {"action": "get", "key": "k"}})
    router.execute({"action": "skill_manage", "args": {
        "action": "create", "name": "s", "description": "d", "content": "c"}})
    assert "s" in router.execute({"action": "skills_list", "args": {}})
    router.execute({"action": "cronjob", "args": {
        "action": "create", "name": "j", "command": "print(1)", "schedule": "every 30m"}})
    assert "j" in router.execute({"action": "cronjob", "args": {"action": "list"}})
    assert not list(ws.glob(".*.tmp"))
    assert all(not p.is_symlink() for p in ws.iterdir())


def test_t05_atomic_write_never_follows_link_created_after_check(tmp_path, monkeypatch):
    """Mesmo que o symlink apareça entre a checagem e a troca, o alvo fica intacto."""
    root = tmp_path / "ws"
    root.mkdir()
    outside = tmp_path / "outside.json"
    outside.write_text("ORIGINAL", encoding="utf-8")
    target = root / ".bauer_memory.json"
    real_lstat = os.lstat
    raced = {"done": False}
    expected = os.fspath(root.resolve() / target.name)

    def racing_lstat(path, *a, **k):
        if os.fspath(path) == expected and not raced["done"]:
            raced["done"] = True
            result = real_lstat(path, *a, **k) if os.path.lexists(path) else None
            _symlink(target, outside)  # o atacante troca a entrada depois do check
            if result is None:
                raise FileNotFoundError(path)
            return result
        return real_lstat(path, *a, **k)

    monkeypatch.setattr(confined_files.os, "lstat", racing_lstat)
    confined_files.write_text_atomic(target, "NEW", root)
    monkeypatch.undo()
    assert outside.read_text(encoding="utf-8") == "ORIGINAL"
    assert not target.is_symlink() and target.read_text(encoding="utf-8") == "NEW"


def test_t05_hardlinked_internal_file_rejected_on_read(tmp_path):
    root = tmp_path / "ws"
    root.mkdir()
    outside = tmp_path / "outside.json"
    outside.write_text("{}", encoding="utf-8")
    try:
        os.link(outside, root / ".bauer_memory.json")
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"hardlink indisponível: {exc}")
    with pytest.raises(confined_files.ConfinementError):
        confined_files.read_text(root / ".bauer_memory.json", root)


def test_t05_confined_path_outside_root_rejected(tmp_path):
    root = tmp_path / "ws"
    root.mkdir()
    with pytest.raises(confined_files.ConfinementError):
        confined_files.write_text_atomic(tmp_path / "escape.json", "x", root)
    assert not (tmp_path / "escape.json").exists()


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="FIFO só no POSIX")
def test_t05_fifo_in_place_of_store_rejected_without_blocking(tmp_path):
    root = tmp_path / "ws"
    root.mkdir()
    os.mkfifo(root / ".bauer_memory.json")
    with pytest.raises(confined_files.ConfinementError):
        confined_files.read_text(root / ".bauer_memory.json", root)
