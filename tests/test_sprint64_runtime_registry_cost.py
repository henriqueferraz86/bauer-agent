"""Sprint 64 — custo de setup do turno governado e hermeticidade do boot.

A causa comum das falhas do CI Windows era o trabalho feito ANTES do turno:
cada listagem de times relia e re-parseava todos os specs de agent uma vez por
agent do time (O(times × agents) parses por turno, em PyYAML puro). O boot do
servidor também rodava `docker ps -a` a cada `create_app`. As asserções aqui
contam trabalho, não medem relógio.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from bauer.core.runtime.agent_registry import RuntimeAgentRegistry
from bauer.core.runtime.team_registry import TeamRegistry, TeamRegistryError


def _write_agent(root: Path, agent_id: str) -> Path:
    path = root / agent_id / "agent.yaml"
    path.parent.mkdir(parents=True)
    path.write_text(yaml.safe_dump({
        "id": agent_id, "name": agent_id.title(), "version": "0.1.0",
        "runtime_adapter": "bauer_native", "permissions": ["filesystem.read"],
    }), encoding="utf-8")
    return path


def _write_team(root: Path, team_id: str, agents: list[str]) -> Path:
    path = root / team_id / "team.yaml"
    path.parent.mkdir(parents=True)
    path.write_text(yaml.safe_dump({"id": team_id, "name": team_id, "agents": agents}),
                    encoding="utf-8")
    return path


@pytest.fixture
def registries(tmp_path):
    agents_root, teams_root = tmp_path / "agents", tmp_path / "teams"
    ids = [f"agent{i}" for i in range(6)]
    for agent_id in ids:
        _write_agent(agents_root, agent_id)
    _write_team(teams_root, "alpha", ids[:4])
    _write_team(teams_root, "beta", ids[2:])
    agents = RuntimeAgentRegistry(roots=[agents_root])
    return TeamRegistry(roots=[teams_root], agent_registry=agents), agents_root, teams_root


@pytest.fixture
def parse_counter(monkeypatch):
    calls: list[int] = []
    original = yaml.load

    def counting(stream, Loader):  # noqa: N803 - mirrors yaml.load
        calls.append(1)
        return original(stream, Loader=Loader)

    monkeypatch.setattr(yaml, "load", counting)
    return calls


def test_team_listing_parses_each_spec_at_most_once(registries, parse_counter):
    teams, _agents_root, _teams_root = registries
    # Conteúdo único por teste: o cache é por conteúdo e não pode mascarar a
    # contagem com specs idênticos de outro teste.
    assert [team.id for team in teams.list()] == ["alpha", "beta"]
    # 6 agents + 2 times. Antes: 1 + 8 listagens completas de agents = 50 parses.
    assert len(parse_counter) <= 8


def test_repeated_listing_does_not_reparse_unchanged_specs(registries, parse_counter):
    teams, _agents_root, _teams_root = registries
    teams.list()
    first = len(parse_counter)
    for _ in range(5):
        teams.list()
    assert len(parse_counter) == first


def test_edited_spec_is_seen_on_next_listing(registries):
    teams, agents_root, teams_root = registries
    assert len(teams.get("alpha").agents) == 4
    _write_agent(agents_root, "agent-late")
    path = teams_root / "alpha" / "team.yaml"
    path.write_text(yaml.safe_dump({"id": "alpha", "name": "alpha",
                                    "agents": ["agent0", "agent-late"]}), encoding="utf-8")
    assert teams.get("alpha").agents == ["agent0", "agent-late"]
    path.write_text(yaml.safe_dump({"id": "alpha", "name": "alpha", "agents": ["ghost"]}),
                    encoding="utf-8")
    with pytest.raises(TeamRegistryError, match="unknown agents: ghost"):
        teams.list()


def test_cached_spec_is_not_shared_between_callers(registries):
    _teams, agents_root, _teams_root = registries
    registry = RuntimeAgentRegistry(roots=[agents_root])
    spec = registry.get("agent0")
    spec.permissions.append("shell.execute")
    assert registry.get("agent0").permissions == ["filesystem.read"]


def test_docker_discovery_can_be_disabled_without_spawning(monkeypatch):
    import bauer.continuous_autonomy as ca

    def explode(*_a, **_k):
        raise AssertionError("docker must not be spawned")

    monkeypatch.setattr(ca.subprocess, "run", explode)
    monkeypatch.setenv("BAUER_DOCKER_DISCOVERY", "0")
    assert ca.discover_docker_containers() is None
    # An explicit runner (the inventory tests) is still honoured.
    class Done:
        returncode, stdout = 0, '{"Names": "web", "ID": "1", "Image": "x", "State": "running"}\n'

    assert ca.discover_docker_containers(runner=lambda *a, **k: Done())[0]["name"] == "web"


def test_suite_boot_is_hermetic_for_docker():
    import os

    assert os.environ.get("BAUER_DOCKER_DISCOVERY") == "0"


# ─── Admissão × aprovador rápido (sprint 64) ─────────────────────────────────
# Falha vista no Windows: o teste (como a aba Approvals ou o Telegram) aprova
# assim que o pedido aparece no store. Se a aprovação chega ANTES de o Kernel
# reler o run para montar o resultado da admissão, o run já está `queued` e o
# /stream respondia "⛔ aprovação humana necessária", abandonando um run
# aprovado. No Linux a janela é de microssegundos; no Windows o publish do
# `approval.requested` (I/O de SQLite) a alarga. Aqui ela é forçada.


class _InstantApprover:
    """ApprovalManager real cujo pedido é decidido logo após ser persistido."""

    def __init__(self, inner, kernel_ref, decision="approve"):
        self._inner, self._kernel_ref, self._decision = inner, kernel_ref, decision

    def request(self, **kwargs):
        record = self._inner.request(**kwargs)
        kernel = self._kernel_ref[0]
        if self._decision == "approve":
            kernel.approve(record.id)
        else:
            kernel.deny(record.id)
        return record

    def __getattr__(self, name):
        return getattr(self._inner, name)


def _ask_kernel(tmp_path, decision="approve"):
    from bauer.core.kernel import BauerKernel
    from bauer.core.policy.approvals import ApprovalManager
    from bauer.core.policy.engine import PolicyEngine
    from bauer.core.runtime.run_manager import RunManager

    runs = RunManager(root=tmp_path)
    ref: list = []
    approvals = _InstantApprover(ApprovalManager(root=tmp_path, event_bus=runs.event_bus), ref,
                                 decision)
    kernel = BauerKernel(
        runs=runs, heartbeat_interval_s=0, approvals=approvals,
        policy=PolicyEngine(workspace=tmp_path, runtime_root=tmp_path, rules=[
            {"id": "ask", "operation": "runtime.execute", "action": "ask"}]),
    )
    ref.append(kernel)
    return kernel, runs


def test_admission_reports_waiting_approval_even_if_approved_before_reread(tmp_path):
    from bauer.core.kernel import KernelRequest

    kernel, runs = _ask_kernel(tmp_path)
    run, early = kernel.admit(KernelRequest(task="x", session_id="s"))
    assert early is not None and early.approval_id
    # O chamador precisa entrar no fluxo de aprovação (que encontra o pedido
    # já aprovado e inicia o worker), não tratar a admissão como bloqueio.
    assert early.status == "waiting_approval"
    assert runs.get_run(run.id).status == "queued"


def test_admission_keeps_terminal_status_when_denied_before_reread(tmp_path):
    from bauer.core.kernel import KernelRequest

    kernel, runs = _ask_kernel(tmp_path, decision="deny")
    run, early = kernel.admit(KernelRequest(task="x", session_id="s"))
    assert early is not None
    assert early.status == runs.get_run(run.id).status == "failed"


def test_memory_worker_releases_the_slot_it_acquired(monkeypatch):
    """A worker that outlives a swap of the slot pool must not free a slot
    of the NEW pool (it never held one there). Flake seen only on Windows,
    where a previous test's memory worker finished inside the next test."""
    import threading

    from bauer import memory_context as mc

    old_pool = threading.BoundedSemaphore(1)
    monkeypatch.setattr(mc, "_MEMORY_WORKER_SLOTS", old_pool)
    release = threading.Event()
    worker = mc._start_memory_worker(lambda: release.wait(timeout=30))
    assert worker is not None

    new_pool = threading.BoundedSemaphore(1)
    assert new_pool.acquire(blocking=False)  # saturated by someone else
    monkeypatch.setattr(mc, "_MEMORY_WORKER_SLOTS", new_pool)
    release.set()
    worker.join(timeout=30)

    assert not new_pool.acquire(blocking=False), "foreign release unsaturated the new pool"
    assert old_pool.acquire(blocking=False), "the acquired slot was never returned"


# ─── Histórico Agno fora do cwd (sprint 64) ──────────────────────────────────
# O default era `memory/runtime/agno/sessions.db` RELATIVO ao cwd: a suíte
# gravava um único banco no repositório, compartilhado por todos os testes e
# workers xdist (e entre execuções). A sessão `tg:42` acumulava o histórico de
# todos os testes; o modelo offline ecoa o histórico e um turno simples chegou
# a 44 mil eventos (84s no Linux), além da disputa de lock SQLite no Windows.


def test_agno_history_defaults_next_to_the_callers_runtime_root(tmp_path):
    pytest.importorskip("agno")
    from bauer.core.runtime.adapters.agno_adapter import AgnoRuntimeAdapter

    adapter = AgnoRuntimeAdapter(adapter_config={"workspace": str(tmp_path)},
                                 runtime_policy_root=tmp_path / "runtime")
    assert adapter.db_file == tmp_path / "runtime" / "agno" / "sessions.db"


def test_agno_history_without_runtime_root_lives_under_bauer_home(tmp_path, monkeypatch):
    pytest.importorskip("agno")
    from bauer.core.runtime.adapters.agno_adapter import AgnoRuntimeAdapter

    monkeypatch.setenv("BAUER_HOME", str(tmp_path / "home"))
    adapter = AgnoRuntimeAdapter(adapter_config={"workspace": str(tmp_path)})
    assert adapter.db_file.is_absolute()
    assert adapter.db_file == tmp_path / "home" / "memory" / "runtime" / "agno" / "sessions.db"


def test_agno_history_explicit_db_file_wins(tmp_path):
    pytest.importorskip("agno")
    from bauer.core.runtime.adapters.agno_adapter import AgnoRuntimeAdapter

    explicit = tmp_path / "custom.db"
    adapter = AgnoRuntimeAdapter(
        adapter_config={"workspace": str(tmp_path), "db_file": str(explicit)},
        runtime_policy_root=tmp_path / "runtime",
    )
    assert adapter.db_file == explicit


def test_tests_run_outside_the_checkout():
    """Defaults relativos ao cwd (`memory/runtime`, `memory/*.md`, `runtime/`)
    não podem cair no repositório durante a suíte (ver tests/conftest.py)."""
    import os

    from tests.conftest import REPO_ROOT, _root_entries

    cwd = Path(os.getcwd()).resolve()
    assert REPO_ROOT not in (cwd, *cwd.parents)
    assert not {"memory", "runtime", "workspace"} & _root_entries()
