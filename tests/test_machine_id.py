"""Testes do machine_id (Decisão 5)."""

from __future__ import annotations

from bauer.machine_id import machine_id, machine_summary


def test_machine_id_is_short_hex():
    mid = machine_id()
    assert isinstance(mid, str)
    assert len(mid) == 12
    int(mid, 16)  # precisa ser hex


def test_machine_id_is_deterministic():
    assert machine_id() == machine_id()


def test_machine_summary_has_expected_keys():
    s = machine_summary()
    for key in ("machine_id", "hostname", "arch", "system", "ram_total_mb", "ram_available_mb"):
        assert key in s
    assert s["machine_id"] == machine_id()
    assert s["ram_total_mb"] > 0
