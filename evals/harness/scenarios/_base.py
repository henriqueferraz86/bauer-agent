"""Utilitários compartilhados dos cenários.

Todo cenário monta um Kernel real sobre um store temporário e roda com executor
stub. Real o suficiente para o veredito valer; hermético o bastante para rodar em
CI sem provider, sem rede e sem modelo.
"""

from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path
from typing import Any


def kernel_temp(*, evaluator: Any = None, control: Any = None,
                policy: Any = None, gates: Any = None):
    """(kernel, raiz) — Kernel de verdade sobre store descartável."""
    from bauer.core.events.bus import EventBus
    from bauer.core.kernel import BauerKernel
    from bauer.core.kernel.evaluator import Evaluator
    from bauer.core.runtime.run_manager import RunManager
    from bauer.core.runtime.state_store import JsonlStateStore

    raiz = Path(tempfile.mkdtemp()) / "rt"
    store = JsonlStateStore(str(raiz))
    bus = EventBus(store=store)
    if gates is not None and evaluator is None:
        evaluator = Evaluator(gates, max_replans=0)
    k = BauerKernel(runs=RunManager(store=store, event_bus=bus), bus=bus,
                    evaluator=evaluator, control=control, policy=policy)
    return k, raiz


def pedido(**kw):
    from bauer.core.kernel import KernelRequest

    base = {"agent_id": "eval", "operation": "runtime.execute", "input": {}}
    base.update(kw)
    return KernelRequest(**base)


def trajetoria(kernel, run_id: str) -> list[str]:
    seen: list[str] = []
    for rec in kernel.runs.store.list("runs"):
        if (rec.get("id") or rec.get("run_id")) == run_id:
            s = rec.get("status")
            if not seen or seen[-1] != s:
                seen.append(str(s))
    return seen


def repo_temp() -> Path:
    """Repo git descartável com um commit — para cenários de escopo/isolamento."""
    r = Path(tempfile.mkdtemp()) / "proj"
    (r / "tests").mkdir(parents=True)
    for cmd in (["git", "init", "-q", "-b", "main"],
                ["git", "config", "user.email", "e@e"],
                ["git", "config", "user.name", "e"]):
        subprocess.run(cmd, cwd=r, check=True, capture_output=True)
    (r / "app.py").write_text("VALOR = 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=r, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-qm", "base"], cwd=r, check=True, capture_output=True)
    return r


class GatePlantado:
    """Gate com veredito fixo — para exercitar o CICLO, não o gate."""

    __test__ = False

    def __init__(self, nome: str, passa: bool, motivo: str = ""):
        self.name = nome
        self._passa = passa
        self._motivo = motivo

    def check(self, *, request, result):
        from bauer.core.kernel.evaluator import GateResult

        return GateResult(self.name, self._passa, self._motivo)
