from concurrent.futures import ThreadPoolExecutor

import pytest

from bauer.remote_run_store import RemoteRunConflict, RemoteRunStore
from bauer.remote_session_store import RemoteSessionConflict, RemoteSessionStore


def scope(**kwargs):
    result = dict(channel="telegram", owner_id="user-a", chat_id="chat-a", project_id="project-a")
    result.update(kwargs)
    return result


def test_identity_scope_and_generation_survive_restart(tmp_path):
    path = tmp_path / "remote.db"
    store = RemoteSessionStore(path)
    a = store.current(**scope())
    assert RemoteSessionStore(path).current(**scope()) == a
    assert len({a.session_id, store.current(**scope(owner_id="user-b")).session_id,
                store.current(**scope(chat_id="chat-b")).session_id,
                store.current(**scope(project_id="project-b")).session_id}) == 4
    b = store.rotate(**scope())
    assert b.generation == 1 and b.session_id != a.session_id
    assert RemoteSessionStore(path).current(**scope()) == b


def test_rotation_rejects_pending_or_uncertain_run_and_retired_claims(tmp_path):
    path = tmp_path / "remote.db"
    sessions = RemoteSessionStore(path)
    runs = RemoteRunStore(path, require_managed_session=True)
    a = sessions.current(**scope())
    run = dict(gateway_run_id="gw", channel="telegram", update_id="1", generation=a.generation,
               owner_id="user-a", session_id=a.session_id, entity_type="agent", entity_id="agent")
    runs.claim(**run)
    with pytest.raises(RemoteSessionConflict):
        sessions.rotate(**scope())
    runs.transition("gw", owner_id="user-a", state="SUBMITTING")
    runs.transition("gw", owner_id="user-a", state="UNKNOWN")
    with pytest.raises(RemoteSessionConflict):
        sessions.rotate(**scope())
    runs.transition("gw", owner_id="user-a", state="COMPLETED", remote_run_id="remote")
    b = sessions.rotate(**scope())
    with pytest.raises(RemoteRunConflict):
        runs.claim(**{**run, "gateway_run_id": "gw2", "update_id": "2"})
    with pytest.raises(RemoteRunConflict):
        runs.claim(**{**run, "gateway_run_id": "gw2", "update_id": "2",
                      "session_id": b.session_id, "generation": b.generation, "owner_id": "user-b"})
    with pytest.raises(RemoteRunConflict):
        runs.claim(**{**run, "gateway_run_id": "gw2", "update_id": "2", "session_id": "unmanaged"})


def test_current_creation_is_atomic(tmp_path):
    path = tmp_path / "remote.db"
    RemoteSessionStore(path)
    with ThreadPoolExecutor(max_workers=8) as pool:
        sessions = list(pool.map(lambda _: RemoteSessionStore(path).current(**scope()), range(8)))
    assert len({s.session_id for s in sessions}) == 1


def test_rotate_deduplicates_concurrent_channel_updates_and_replays(tmp_path):
    path = tmp_path / "remote.db"
    store = RemoteSessionStore(path)
    old = store.current(**scope())
    with ThreadPoolExecutor(max_workers=8) as pool:
        rotated = list(pool.map(lambda _: RemoteSessionStore(path).rotate(**scope(), update_id="42"), range(8)))
    assert len({s.session_id for s in rotated}) == 1
    assert rotated[0].generation == old.generation + 1
    newer = store.rotate(**scope(), update_id="43")
    assert newer.generation == rotated[0].generation + 1
    assert store.rotate(**scope(), update_id="42") == rotated[0]
    assert store.current(**scope()) == newer


def test_rotate_rejects_reuse_for_another_owner_or_chat(tmp_path):
    store = RemoteSessionStore(tmp_path / "remote.db")
    first = store.rotate(**scope(), update_id="42")
    with pytest.raises(RemoteSessionConflict):
        store.rotate(**scope(owner_id="user-b"), update_id="42")
    with pytest.raises(RemoteSessionConflict):
        store.rotate(**scope(chat_id="chat-b"), update_id="42")
    assert store.current(**scope()) == first
