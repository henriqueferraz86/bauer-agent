"""Testes para set_runtime_ids/reset_runtime_ids (ContextVar de session/run id).

Cobre a regressão que motivou a troca de atributo de instância para
ContextVar: o bauer serve reusa a MESMA instância de ToolRouter entre
requests (o router default, e cada router de projeto). Antes,
`router._runtime_session_id = sid` mutava a instância compartilhada — dois
turnos concorrentes no MESMO router vazavam session/run id um para o outro.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest

from bauer.tool_router import ToolRouter, reset_runtime_ids, set_runtime_ids


@pytest.fixture
def router(tmp_path: Path) -> ToolRouter:
    return ToolRouter(workspace=tmp_path)


class TestRuntimeIdsContextVar:
    def test_falls_back_to_constructor_value_without_contextvar(self, tmp_path: Path):
        r = ToolRouter(workspace=tmp_path, session_id="ctor-sess", run_id="ctor-run")
        assert r._runtime_session_id == "ctor-sess"
        assert r._runtime_run_id == "ctor-run"

    def test_contextvar_overrides_constructor_value(self, router: ToolRouter):
        token = set_runtime_ids("turn-sess", "turn-run")
        try:
            assert router._runtime_session_id == "turn-sess"
            assert router._runtime_run_id == "turn-run"
        finally:
            reset_runtime_ids(token)
        # Após reset, volta ao default do construtor (None, já que não foi passado)
        assert router._runtime_session_id is None
        assert router._runtime_run_id is None

    def test_same_router_instance_two_concurrent_turns_do_not_leak(self, router: ToolRouter):
        """O cenário real do bug: MESMO router (ex.: reusado por dois turnos
        no mesmo projeto) rodando em threads diferentes — cada thread deve
        ver só o seu próprio (session_id, run_id), nunca o da outra."""
        seen: dict[str, tuple] = {}
        barrier = threading.Barrier(2)

        def _turn(sess: str, run: str, delay: float):
            token = set_runtime_ids(sess, run)
            try:
                barrier.wait(timeout=2)  # garante sobreposição real das threads
                time.sleep(delay)
                seen[sess] = (router._runtime_session_id, router._runtime_run_id)
            finally:
                reset_runtime_ids(token)

        t1 = threading.Thread(target=_turn, args=("sess-A", "run-A", 0.05))
        t2 = threading.Thread(target=_turn, args=("sess-B", "run-B", 0.0))
        t1.start()
        t2.start()
        t1.join(timeout=5)
        t2.join(timeout=5)

        assert seen["sess-A"] == ("sess-A", "run-A")
        assert seen["sess-B"] == ("sess-B", "run-B")

    def test_reset_is_safe_to_call_twice(self, router: ToolRouter):
        token = set_runtime_ids("s", "r")
        reset_runtime_ids(token)
        reset_runtime_ids(token)  # não deve levantar
        assert router._runtime_session_id is None
