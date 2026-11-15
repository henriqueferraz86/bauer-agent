from concurrent.futures import ThreadPoolExecutor

import pytest

from bauer.telegram_inbox import InboxConflict, TelegramInbox


def envelope(**kwargs):
    data = dict(channel="telegram", update_id="123", user_id="u1", chat_id="c1", text="hello")
    data.update(kwargs)
    return data


def test_persist_and_deduplicate_after_restart(tmp_path):
    path = tmp_path / "inbox.db"
    first, created = TelegramInbox(path).put(**envelope())
    second, repeated = TelegramInbox(path).put(**envelope())
    assert created and not repeated and first == second
    assert second.state == "RECEIVED"
    with pytest.raises(InboxConflict):
        TelegramInbox(path).put(**envelope(user_id="other"))
    with pytest.raises(InboxConflict):
        TelegramInbox(path).put(**envelope(text="different"))


def test_concurrent_claim_only_one_winner(tmp_path):
    path = tmp_path / "inbox.db"
    TelegramInbox(path).put(**envelope())
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: TelegramInbox(path).claim(channel="telegram", update_id="123"), range(8)))
    winners = [result for result in results if result is not None]
    assert len(winners) == 1 and winners[0].state == "CLAIMED"


def test_crash_never_reclaims_submitted_or_uncertain_update(tmp_path):
    path = tmp_path / "inbox.db"
    inbox = TelegramInbox(path)
    inbox.put(**envelope())
    inbox.claim(channel="telegram", update_id="123")
    restarted = TelegramInbox(path)
    assert restarted.recoverable()[0].state == "CLAIMED"
    assert restarted.claim(channel="telegram", update_id="123") is None
    restarted.settle(channel="telegram", update_id="123", state="UNKNOWN")
    assert restarted.claim(channel="telegram", update_id="123") is None
    assert restarted.recoverable()[0].state == "UNKNOWN"
    assert restarted.finish_reconciled(channel="telegram", update_id="123").state == "FINISHED"
    assert restarted.recoverable() == []


def test_only_finished_rows_are_purged(tmp_path):
    inbox = TelegramInbox(tmp_path / "inbox.db")
    inbox.put(**envelope())
    inbox.put(**envelope(update_id="124"))
    inbox.claim(channel="telegram", update_id="123")
    inbox.settle(channel="telegram", update_id="123", state="FINISHED")
    assert inbox.purge_finished(older_than=float("inf")) == 1
    assert [item.update_id for item in inbox.recoverable()] == ["124"]


def test_duplicate_put_is_atomic(tmp_path):
    path = tmp_path / "inbox.db"
    TelegramInbox(path)
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: TelegramInbox(path).put(**envelope()), range(8)))
    assert sum(created for _, created in results) == 1
def test_rearm_requires_transactional_absence_of_remote_admission(tmp_path):
    from bauer.remote_run_store import RemoteRunStore
    from bauer.telegram_inbox import TelegramInbox

    path = tmp_path / "inbox.sqlite"
    inbox = TelegramInbox(path)
    runs = RemoteRunStore(path)
    inbox.put(channel="telegram", update_id="1", user_id="7", chat_id="7", text="primeiro")
    inbox.claim(channel="telegram", update_id="1")
    assert inbox.rearm_if_unsubmitted(channel="telegram", update_id="1")
    inbox.claim(channel="telegram", update_id="1")
    runs.claim(gateway_run_id="g", channel="telegram", update_id="1", generation=0,
               owner_id="owner", session_id="s", entity_type="agent", entity_id="a")
    assert not inbox.rearm_if_unsubmitted(channel="telegram", update_id="1")
    inbox.put(channel="telegram", update_id="2", user_id="7", chat_id="7", text="/cancelar")
    inbox.claim(channel="telegram", update_id="2")
    assert not inbox.rearm_if_unsubmitted(channel="telegram", update_id="2")


def test_media_descriptor_is_atomic_and_replay_must_match(tmp_path):
    from bauer.telegram_inbox import InboxConflict, TelegramInbox
    import pytest

    inbox = TelegramInbox(tmp_path / "inbox.sqlite")
    kwargs = {"channel": "telegram", "update_id": "1", "user_id": "7", "chat_id": "7",
              "text": "[Documento de texto recebido]", "media": {"document": {"file_id": "a"}}}
    _, created = inbox.put(**kwargs)
    assert created and inbox.media(channel="telegram", update_id="1") == (kwargs["media"], None)
    assert not inbox.put(**kwargs)[1]
    with pytest.raises(InboxConflict):
        inbox.put(**{**kwargs, "media": {"document": {"file_id": "b"}}})
    inbox.claim(channel="telegram", update_id="1")
    inbox.save_media_text(channel="telegram", update_id="1", text="conteúdo")
    assert inbox.rearm_if_unsubmitted(channel="telegram", update_id="1")
    assert inbox.media(channel="telegram", update_id="1")[1] == "conteúdo"
