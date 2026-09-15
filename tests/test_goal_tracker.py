"""Durable claims, leases and materialization ledger for GoalTracker."""

from __future__ import annotations

import sqlite3
from concurrent.futures import ThreadPoolExecutor

from bauer.goal_tracker import GoalStatus, GoalTracker


def test_claim_next_is_atomic_between_two_trackers(tmp_path):
    db = tmp_path / "goals.db"
    creator = GoalTracker(db_path=db)
    goal_id = creator.create("single claim")

    def claim(session: str):
        return GoalTracker(db_path=db).claim_next(session, lease_seconds=60)

    with ThreadPoolExecutor(max_workers=2) as pool:
        claimed = list(pool.map(claim, ("controller-a", "controller-b")))

    winners = [record for record in claimed if record is not None]
    assert [record.id for record in winners] == [goal_id]
    assert winners[0].lease_owner in {"controller-a", "controller-b"}
    assert winners[0].attempts == 1


def test_claim_next_respects_priority_and_active_lease(tmp_path):
    db = tmp_path / "goals.db"
    tracker = GoalTracker(db_path=db)
    low = tracker.create("low", priority=8)
    high = tracker.create("high", priority=1)

    claimed = tracker.claim_next("one", lease_seconds=60)
    assert claimed is not None
    assert claimed.id == high
    second = tracker.claim_next("two", lease_seconds=60)
    assert second is not None
    assert second.id == low
    assert tracker.claim_next("three", lease_seconds=60) is None


def test_stale_running_goal_can_be_reclaimed_and_attempt_is_preserved(tmp_path):
    db = tmp_path / "goals.db"
    tracker = GoalTracker(db_path=db)
    goal_id = tracker.create("recoverable")
    first = tracker.claim_next("old-controller", lease_seconds=60)
    assert first is not None
    tracker.update_status(goal_id, GoalStatus.RUNNING, error="last safe error")

    assert tracker.release_or_requeue_stale(now=first.lease_expires_at + 1) == 1
    recovered = tracker.get(goal_id)
    assert recovered is not None
    assert recovered.status == GoalStatus.PENDING
    assert recovered.error == "last safe error"
    assert recovered.attempts == 1

    second = tracker.claim_next("new-controller", lease_seconds=60)
    assert second is not None
    assert second.id == goal_id
    assert second.attempts == 2


def test_heartbeat_requires_current_owner_and_renews_lease(tmp_path):
    tracker = GoalTracker(db_path=tmp_path / "goals.db")
    goal_id = tracker.create("heartbeat")
    claimed = tracker.claim_next("owner", lease_seconds=60)
    assert claimed is not None
    old_expiry = claimed.lease_expires_at

    assert tracker.heartbeat(goal_id, "wrong-owner") is False
    assert tracker.get(goal_id).lease_expires_at == old_expiry
    assert tracker.heartbeat(goal_id, "owner") is True
    assert tracker.get(goal_id).heartbeat_at is not None
    assert tracker.get(goal_id).lease_expires_at > old_expiry


def test_materialized_task_ledger_is_idempotent(tmp_path):
    tracker = GoalTracker(db_path=tmp_path / "goals.db")
    goal_id = tracker.create("materialize")

    assert tracker.record_materialized_task(goal_id, "001", "step-1") is True
    assert tracker.record_materialized_task(goal_id, "001", "step-1") is False
    assert tracker.record_materialized_task(goal_id, "002", "step-1") is False
    assert tracker.get_materialized_task(goal_id, "step-1") == "001"
    assert tracker.list_materialized_tasks(goal_id) == [
        {"task_id": "001", "step_key": "step-1"}
    ]


def test_old_schema_is_migrated_without_dropping_goals(tmp_path):
    db = tmp_path / "legacy.db"
    conn = sqlite3.connect(db)
    conn.execute(
        """
        CREATE TABLE goals (
            id TEXT PRIMARY KEY, title TEXT NOT NULL, description TEXT,
            status TEXT NOT NULL, priority INTEGER NOT NULL, steps_json TEXT,
            created_at REAL NOT NULL, started_at REAL, completed_at REAL,
            session_id TEXT, error TEXT
        )
        """
    )
    conn.execute(
        "INSERT INTO goals (id, title, status, priority, steps_json, created_at) "
        "VALUES ('legacy', 'keep me', 'pending', 5, '[]', 1.0)"
    )
    conn.commit()
    conn.close()

    tracker = GoalTracker(db_path=db)
    record = tracker.get("legacy")
    assert record is not None
    assert record.title == "keep me"
    assert tracker.claim_next("migrated", lease_seconds=30).id == "legacy"
