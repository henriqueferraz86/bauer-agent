import pytest

from bauer.telegram_bridge import TelegramBridge


class Backend:
    def __init__(self, root):
        self.sessions_dir = root / "sessions"
        self.config_path = root / "config.yaml"
        self.is_ready = True

    def capture_project_context(self, msg):
        msg.project_snapshot_captured = True
        return None


class Queue:
    def __init__(self):
        self.jobs = []

    def submit(self, func, *args):
        self.jobs.append((func, args))

    def shutdown(self, **kwargs):
        pass

    def drain(self):
        while self.jobs:
            func, args = self.jobs.pop(0)
            func(*args)


def update(i, user=42, text="olá", kind="private"):
    return {"update_id": i, "message": {"chat": {"id": user, "type": kind},
                                          "from": {"id": user}, "text": text}}


@pytest.fixture
def bridge(tmp_path, monkeypatch):
    import bauer.telegram_agentos_http as module

    b = TelegramBridge("test", Backend(tmp_path), allowed_users=[42], state_dir=tmp_path,
                       agentos_http_enabled=True)
    b._executor.shutdown(wait=False)
    b._executor = Queue()
    sent, turns = [], []
    b.send_text = lambda chat, text: sent.append((chat, text))
    b.send_text_result = lambda chat, text: (sent.append((chat, text)) or True)
    b._api = lambda method, **kwargs: (sent.append((kwargs["chat_id"], kwargs["text"])) or {})
    b.handle_message = lambda msg, **kwargs: (turns.append(msg) or "resposta")
    monkeypatch.setattr("bauer.telegram_bridge._TypingHeartbeat", lambda *args: _Heartbeat())
    monkeypatch.setattr(module, "remote_update_state", lambda *args: "COMPLETED")
    return b, sent, turns


class _Heartbeat:
    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass


def test_inbox_precedes_dispatch_and_replay_does_not_execute_twice(bridge):
    b, sent, turns = bridge
    b._handle_update(update(1, user=43))
    assert not b._executor.jobs
    b._handle_update(update(2))
    b._handle_update(update(2))
    assert b._agentos_inbox.recoverable()[0].state == "RECEIVED"
    b._executor.drain()
    assert len(turns) == 1 and turns[0].durable_update
    assert sent == [("42", "resposta")]
    assert not b._agentos_inbox.recoverable()


def test_delivery_error_preserves_response_for_restart(bridge):
    b, sent, turns = bridge
    def broken(*args, **kwargs):
        raise OSError("delivery outcome unknown")
    b._api = broken
    b._handle_update(update(3))
    b._executor.drain()
    item = b._agentos_inbox.recoverable()[0]
    assert item.state == "CLAIMED"
    assert b._agentos_inbox.get_result(channel="telegram", update_id="3") == ("resposta", True)
    b._api = lambda method, **kwargs: (sent.append((kwargs["chat_id"], kwargs["text"])) or {})
    b._recover_agentos_inbox(item)
    assert len(turns) == 1 and sent == []
    assert not b._agentos_inbox.recoverable()
    assert b._agentos_inbox.uncertain_deliveries(channel="telegram", user_id="42", chat_id="42",
                                                 project_id="", before_update_id="4") == ["3"]


def test_recorded_result_before_delivery_recovers_without_post(bridge):
    b, sent, turns = bridge
    b._handle_update(update(30))
    item = b._agentos_inbox.claim(channel="telegram", update_id="30")
    b._agentos_inbox.save_result(channel="telegram", update_id="30", response="pronto", settled=True)
    b._recover_agentos_inbox(item)
    assert sent == [("42", "pronto")] and not turns
    assert not b._agentos_inbox.recoverable()


def test_terminal_recovery_unblocks_after_uncertain_delivery_without_resend(bridge, monkeypatch):
    import bauer.telegram_agentos_http as module
    from contextlib import nullcontext
    from types import SimpleNamespace

    b, sent, turns = bridge
    b.backend.project_scope = lambda _msg: nullcontext()
    b.backend._effective_router = lambda: SimpleNamespace(workspace=b.backend.sessions_dir)
    b.backend._resolver_kernel = lambda **kwargs: object()
    b.backend.session_key = lambda _msg: "tg:42"
    b._handle_update(update(32))
    b._executor.jobs.clear()
    item = b._agentos_inbox.claim(channel="telegram", update_id="32")
    b._agentos_inbox.save_result(channel="telegram", update_id="32", response="ainda verificando", settled=False)
    assert b._agentos_inbox.begin_delivery(channel="telegram", update_id="32")
    item = b._agentos_inbox.settle(channel="telegram", update_id="32", state="UNKNOWN")
    monkeypatch.setattr(module, "recover_remote_update", lambda *args, **kwargs: "⏹ Execução cancelada.")

    b._recover_agentos_inbox(item)

    assert not b._agentos_inbox.recoverable()
    assert sent == []  # previous Telegram send may already have arrived
    assert b._agentos_inbox.uncertain_deliveries(channel="telegram", user_id="42", chat_id="42",
                                                 project_id="", before_update_id="33") == ["32"]
    b._handle_update(update(33, text="nova mensagem"))
    b._executor.drain()
    assert [turn.text for turn in turns] == ["nova mensagem"]
    assert sent == [("42", "resposta")]


def test_status_reconciles_prior_claimed_inbox_before_reply(bridge, monkeypatch):
    import bauer.telegram_agentos_http as module
    from contextlib import nullcontext
    from types import SimpleNamespace

    b, sent, turns = bridge
    b.backend.project_scope = lambda _msg: nullcontext()
    b.backend._effective_router = lambda: SimpleNamespace(workspace=b.backend.sessions_dir)
    b.backend._resolver_kernel = lambda **kwargs: object()
    b.backend.session_key = lambda _msg: "tg:42"
    b._handle_update(update(34))
    b._executor.jobs.clear()
    item = b._agentos_inbox.claim(channel="telegram", update_id="34")
    b._agentos_inbox.save_result(channel="telegram", update_id="34", response="incerto", settled=False)
    monkeypatch.setattr(module, "cancel_remote_session", lambda *args, **kwargs: "Execução reconciliada.")
    monkeypatch.setattr(module, "recover_remote_update", lambda *args, **kwargs: "resposta confirmada")

    b._handle_update(update(35, text="/status"))
    b._executor.drain()

    assert not b._agentos_inbox.recoverable()
    assert sent == [("42", "resposta confirmada"), ("42", "Execução reconciliada.")]
    assert not turns


def test_claim_crash_before_remote_record_can_be_rearmed(bridge, monkeypatch, tmp_path):
    from contextlib import nullcontext
    from types import SimpleNamespace
    import bauer.telegram_agentos_http as module

    b, sent, turns = bridge
    b._handle_update(update(31))
    b._executor.jobs.clear()
    item = b._agentos_inbox.claim(channel="telegram", update_id="31")
    b.backend.project_scope = lambda msg: nullcontext()
    b.backend._effective_router = lambda: SimpleNamespace(workspace=tmp_path)
    b.backend._resolver_kernel = lambda **kwargs: object()
    b.backend.session_key = lambda msg: "tg:42"
    monkeypatch.setattr(module, "recover_remote_update", lambda *args, **kwargs: None)
    b._recover_agentos_inbox(item)
    assert b._agentos_inbox.get(channel="telegram", update_id="31").state == "RECEIVED"
    b._executor.drain()
    assert len(turns) == 1 and sent == [("42", "resposta")]


def test_private_only_whitespace_and_unknown_id(bridge):
    b, sent, turns = bridge
    b._handle_update(update(4, kind="group"))
    b._handle_update(update(5, text="   "))
    msg = update(6)
    del msg["update_id"]
    with pytest.raises(ValueError):
        b._handle_update(msg)
    assert not turns and not b._executor.jobs
    assert "privada" in sent[0][1]


def test_new_and_cancel_use_remote_helpers(bridge, monkeypatch):
    import bauer.telegram_agentos_http as module

    b, sent, _ = bridge
    calls = []
    monkeypatch.setattr(module, "rotate_remote_session", lambda *args: calls.append("new"))
    monkeypatch.setattr(module, "cancel_remote_session", lambda *args, **kwargs: "Cancelamento solicitado; aguardando confirmação.")
    b._handle_update(update(7, text="/new"))
    b._handle_update(update(8, text="/cancelar"))
    b._executor.drain()
    assert calls == ["new"]
    assert "Nova conversa" in sent[0][1]
    assert "aguardando confirmação" in sent[1][1]


def test_reversed_workers_still_process_same_session_in_update_order(bridge):
    b, _, turns = bridge
    b._handle_update(update(10, text="first"))
    b._handle_update(update(11, text="second"))
    b._executor.jobs.reverse()
    b._executor.drain()
    assert [msg.text for msg in turns] == ["first", "second"]


def test_text_document_is_inlined_and_persisted_before_execution(bridge):
    import httpx

    b, sent, turns = bridge
    payload = "conteúdo do arquivo".encode("utf-8")
    b._http.close()
    b._http = httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(200, stream=httpx.ByteStream(payload))))

    def api(method, **kwargs):
        if method == "getFile":
            return {"file_path": "documents/file.txt", "file_size": len(payload)}
        sent.append((kwargs["chat_id"], kwargs["text"]))
        return {}

    b._api = api
    item = update(40)
    item["message"].pop("text")
    item["message"]["caption"] = "Resuma"
    item["message"]["document"] = {"file_id": "id", "file_name": "file.txt", "file_size": len(payload),
                                    "mime_type": "text/plain"}
    b._handle_update(item)
    persisted = b._agentos_inbox.recoverable()[0]
    assert persisted.text == "[Documento de texto recebido]"
    assert b._agentos_inbox.media(channel="telegram", update_id="40")[1] is None
    b._executor.drain()
    assert len(turns) == 1 and "conteúdo do arquivo" in turns[0].text
    assert "documents/" not in turns[0].text and b.token not in turns[0].text
    assert sent == [("42", "resposta")]
    b._http.close()


def test_media_with_detected_secret_is_rejected_without_dispatch(bridge):
    import httpx

    b, sent, turns = bridge
    payload = b"-----BEGIN PRIVATE KEY-----"
    b._http.close()
    b._http = httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(200, stream=httpx.ByteStream(payload))))

    def api(method, **kwargs):
        if method == "getFile":
            return {"file_path": "documents/file.txt", "file_size": len(payload)}
        sent.append((kwargs["chat_id"], kwargs["text"]))
        return {}
    b._api = api
    item = update(41)
    item["message"].pop("text")
    item["message"]["document"] = {"file_id": "id", "file_name": "file.txt", "file_size": len(payload)}
    b._handle_update(item)
    assert b._agentos_inbox.recoverable()
    b._executor.drain()
    assert not turns and not b._executor.jobs and not b._agentos_inbox.recoverable()
    assert sent and "possível segredo" in sent[0][1]
    b._http.close()


def test_document_download_is_off_poll_thread_and_transient_failure_is_retried(bridge):
    import httpx

    b, sent, turns = bridge
    payload = b"safe text"
    b._http.close()
    b._http = httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(200, stream=httpx.ByteStream(payload))))
    attempts = []

    def api(method, **kwargs):
        if method == "getFile":
            attempts.append(1)
            if len(attempts) == 1:
                raise httpx.ConnectError("transient")
            return {"file_path": "documents/file.txt", "file_size": len(payload)}
        sent.append((kwargs["chat_id"], kwargs["text"]))
        return {}

    b._api = api
    item = update(50)
    item["message"].pop("text")
    item["message"]["document"] = {"file_id": "id", "file_name": "file.txt", "file_size": len(payload)}
    b._handle_update(item)
    b._handle_update(item)  # Telegram replay before processing
    assert attempts == [] and b._agentos_inbox.recoverable()[0].state == "RECEIVED"
    # Consume one scheduled worker. It records the transient failure, without POST.
    fn, args = b._executor.jobs.pop(0)
    fn(*args)
    assert len(attempts) == 1 and not turns
    assert b._agentos_inbox.recoverable()[0].state == "RECEIVED"
    b._executor.drain()
    assert len(attempts) == 2 and len(turns) == 1
    assert sent == [("42", "resposta")]
    b._http.close()


def test_invalid_document_update_id_never_downloads(bridge):
    b, _, _ = bridge
    attempts = []
    b._api = lambda *args, **kwargs: attempts.append(1)
    item = update(51)
    del item["update_id"]
    item["message"].pop("text")
    item["message"]["document"] = {"file_id": "id", "file_name": "file.txt", "file_size": 2}
    with pytest.raises(ValueError):
        b._handle_update(item)
    assert not attempts and not b._agentos_inbox.recoverable()


def test_permanent_getfile_rejection_is_not_retried(bridge):
    from bauer.telegram_bridge import TelegramAPIError

    b, sent, turns = bridge
    attempts = []

    def api(method, **kwargs):
        if method == "getFile":
            attempts.append(1)
            raise TelegramAPIError(method, "bad file id", 400)
        sent.append((kwargs["chat_id"], kwargs["text"]))
        return {}

    b._api = api
    item = update(52)
    item["message"].pop("text")
    item["message"]["document"] = {"file_id": "id", "file_name": "file.txt", "file_size": 2}
    b._handle_update(item)
    b._executor.drain()
    assert attempts == [1] and not turns and not b._agentos_inbox.recoverable()
    assert "recusou o documento" in sent[0][1]
