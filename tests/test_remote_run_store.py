from concurrent.futures import ThreadPoolExecutor

import pytest

from bauer.remote_run_store import RemoteRunConflict, RemoteRunNotFound, RemoteRunStore


def claim(store, **overrides):
    args = dict(gateway_run_id="gw1", channel="telegram", update_id="123", generation=0,
                owner_id="user-a", session_id="session-a", entity_type="agent", entity_id="agent-a")
    args.update(overrides)
    return store.claim(**args)


def test_claim_persists_and_duplicate_does_not_create_run(tmp_path):
    path = tmp_path / "remote.db"
    first, created = claim(RemoteRunStore(path))
    second, again = claim(RemoteRunStore(path), gateway_run_id="gw2")
    assert created and not again
    assert first == second
    assert second.state == "PREPARED"
    with pytest.raises(RemoteRunConflict):
        claim(RemoteRunStore(path), owner_id="user-b")


def test_ownership_and_unknown_state_prevent_duplicate_submission(tmp_path):
    store = RemoteRunStore(tmp_path / "remote.db")
    claim(store)
    store.transition("gw1", owner_id="user-a", state="SUBMITTING")
    with pytest.raises(RemoteRunConflict):
        store.transition("gw1", owner_id="user-a", state="SUBMITTING")
    store.transition("gw1", owner_id="user-a", state="UNKNOWN")
    with pytest.raises(RemoteRunConflict):
        store.transition("gw1", owner_id="user-a", state="CANCELLED")
    with pytest.raises(RemoteRunNotFound):
        store.transition("gw1", owner_id="user-b", state="CANCELLED")
    with pytest.raises(RemoteRunConflict):
        claim(store, gateway_run_id="gw2", update_id="124")
    assert len(RemoteRunStore(tmp_path / "remote.db").recoverable(owner_id="user-a")) == 1
    assert store.recoverable(owner_id="user-b") == []
    store.transition("gw1", owner_id="user-a", state="RUNNING", remote_run_id="remote-1")
    with pytest.raises(RemoteRunConflict):
        store.transition("gw1", owner_id="user-a", state="COMPLETED", remote_run_id="remote-2")
    store.transition("gw1", owner_id="user-a", state="COMPLETED")
    with pytest.raises(RemoteRunConflict):
        claim(store, gateway_run_id="gw3", update_id="125", owner_id="user-b")
    assert claim(store, gateway_run_id="gw2", update_id="124")[1]
    with pytest.raises(RemoteRunConflict):
        store.transition("gw1", owner_id="user-a", state="RUNNING")


def test_concurrent_claims_allow_only_one_submission(tmp_path):
    path = tmp_path / "remote.db"
    RemoteRunStore(path)

    def try_claim(n):
        store = RemoteRunStore(path)
        return claim(store, gateway_run_id=f"gw{n}")

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(try_claim, range(8)))
    assert sum(created for _, created in results) == 1
    assert len({record.gateway_run_id for record, _ in results}) == 1


def test_only_one_worker_can_begin_submission(tmp_path):
    path = tmp_path / "remote.db"
    claim(RemoteRunStore(path))

    def begin(_):
        try:
            RemoteRunStore(path).transition("gw1", owner_id="user-a", state="SUBMITTING")
            return True
        except RemoteRunConflict:
            return False

    with ThreadPoolExecutor(max_workers=8) as pool:
        assert sum(pool.map(begin, range(8))) == 1


def test_only_expired_terminal_records_are_purged(tmp_path):
    store = RemoteRunStore(tmp_path / "remote.db")
    claim(store)
    store.transition("gw1", owner_id="user-a", state="CANCELLED")
    claim(store, gateway_run_id="gw2", update_id="124")
    assert store.purge_terminal(older_than=float("inf")) == 1
    assert store.get("gw2", owner_id="user-a").state == "PREPARED"
    with pytest.raises(RemoteRunNotFound):
        store.get("gw1", owner_id="user-a")
