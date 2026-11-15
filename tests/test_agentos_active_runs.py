"""Ownership mapping from authenticated server-side SSE, never request body."""
import json
from concurrent.futures import ThreadPoolExecutor

import pytest

from bauer.agentos_active_runs import ActiveRunConflict, ActiveRunRegistry, StartedEventTracker


def event(**changes):
    payload = {"event": "RunStarted", "agent_id": "bauer.dev", "session_id": "s1", "run_id": "r1"}
    payload.update(changes)
    return ("event: RunStarted\ndata: " + json.dumps(payload) + "\n\n").encode()


def test_fragmented_start_is_bound_before_frame_is_exposed(tmp_path):
    registry = ActiveRunRegistry(tmp_path / "active.db")
    tracker = StartedEventTracker(registry, owner="alice", entity_type="agents",
                                  entity_id="bauer.dev", session_id="s1")
    frame = event()
    assert tracker.feed(frame[:12]) == []
    assert not registry.owns(run_id="r1", owner="alice", entity_type="agents", entity_id="bauer.dev", session_id="s1")
    assert tracker.feed(frame[12:]) == [frame]
    assert registry.owns(run_id="r1", owner="alice", entity_type="agents", entity_id="bauer.dev", session_id="s1")
    for owner, agent, session in [("bob", "bauer.dev", "s1"), ("alice", "bauer.qa", "s1"),
                                  ("alice", "bauer.dev", "s2")]:
        assert not registry.owns(run_id="r1", owner=owner, entity_type="agents", entity_id=agent, session_id=session)


@pytest.mark.parametrize("changes", [
    {"agent_id": "bauer.qa"}, {"session_id": "other"}, {"parent_run_id": "parent"},
    {"run_id": ""}, {"run_id": None},
])
def test_mismatched_event_is_not_forwarded_or_registered(tmp_path, changes):
    registry = ActiveRunRegistry(tmp_path / "active.db")
    tracker = StartedEventTracker(registry, owner="alice", entity_type="agents",
                                  entity_id="bauer.dev", session_id="s1")
    with pytest.raises(ValueError):
        tracker.feed(event(**changes))
    assert not registry.owns(run_id="r1", owner="alice", entity_type="agents", entity_id="bauer.dev", session_id="s1")


def test_run_id_cannot_be_rebound_concurrently_or_after_restart(tmp_path):
    path = tmp_path / "active.db"
    registry = ActiveRunRegistry(path)

    def register(owner):
        try:
            ActiveRunRegistry(path).record(run_id="r1", owner=owner, entity_type="agents",
                                           entity_id="bauer.dev", session_id="s1")
            return True
        except ActiveRunConflict:
            return False

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(register, ("alice", "bob")))
    assert sorted(results) == [False, True]
    assert sum(registry.owns(run_id="r1", owner=o, entity_type="agents", entity_id="bauer.dev", session_id="s1")
               for o in ("alice", "bob")) == 1


def test_frames_are_bounded_and_truncation_refused(tmp_path):
    registry = ActiveRunRegistry(tmp_path / "active.db")
    tracker = StartedEventTracker(registry, owner="alice", entity_type="agents",
                                  entity_id="bauer.dev", session_id="s1", max_frame_bytes=32)
    with pytest.raises(ValueError, match="large"):
        tracker.feed(b"x" * 33)
    other = StartedEventTracker(registry, owner="alice", entity_type="agents",
                                entity_id="bauer.dev", session_id="s1")
    with pytest.raises(ValueError, match="truncated"):
        other.feed(event()[:-2], final=True)


@pytest.mark.parametrize("terminal", ["RunCompleted", "RunCancelled", "RunError"])
def test_terminal_event_retires_active_run_but_keeps_identity(tmp_path, terminal):
    registry = ActiveRunRegistry(tmp_path / "active.db")
    tracker = StartedEventTracker(registry, owner="alice", entity_type="agents",
                                  entity_id="bauer.dev", session_id="s1")
    tracker.feed(event())
    frame = ("data: " + json.dumps({"event": terminal, "run_id": "r1"}) + "\n\n").encode()
    assert tracker.feed(frame) == [frame]
    assert not registry.owns(run_id="r1", owner="alice", entity_type="agents",
                             entity_id="bauer.dev", session_id="s1")
    with pytest.raises(ActiveRunConflict):
        registry.record(run_id="r1", owner="alice", entity_type="agents",
                        entity_id="bauer.dev", session_id="s1")


def test_active_run_from_other_process_is_not_cancellable_here(tmp_path):
    path = tmp_path / "active.db"
    original = ActiveRunRegistry(path)
    original.record(run_id="r1", owner="alice", entity_type="agents",
                    entity_id="bauer.dev", session_id="s1")
    restarted = ActiveRunRegistry(path)
    assert restarted.owns(run_id="r1", owner="alice", entity_type="agents",
                          entity_id="bauer.dev", session_id="s1")
    assert not restarted.owned_here(run_id="r1", owner="alice", entity_type="agents",
                                    entity_id="bauer.dev", session_id="s1")


def test_closed_stream_retires_run_without_claiming_terminal_state(tmp_path):
    registry = ActiveRunRegistry(tmp_path / "active.db")
    tracker = StartedEventTracker(registry, owner="alice", entity_type="agents",
                                  entity_id="bauer.dev", session_id="s1")
    tracker.feed(event())
    tracker.close()
    assert not registry.owns(run_id="r1", owner="alice", entity_type="agents",
                             entity_id="bauer.dev", session_id="s1")
