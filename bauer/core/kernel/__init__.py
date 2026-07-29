"""Bauer Kernel — fachada de orquestração sobre os componentes existentes."""

from __future__ import annotations

__all__ = [
    "BauerKernel",
    "CallableGate",
    "Evaluator",
    "GateResult",
    "GovernedResult",
    "continue_governed",
    "run_governed",
    "KernelRequest",
    "KernelRun",
    "KernelStateError",
    "KernelWiringError",
    "Verdict",
    "build_kernel",
    "kernel_enabled",
    "require_kernel",
]

_KERNEL_EXPORTS = frozenset({"BauerKernel", "build_kernel", "kernel_enabled",
                             "require_kernel", "KernelWiringError"})


def __getattr__(name: str):
    if name in _KERNEL_EXPORTS:
        from . import kernel as _mod

        return getattr(_mod, name)
    if name in {"KernelRequest", "KernelRun"}:
        from .schemas import KernelRequest, KernelRun

        return {"KernelRequest": KernelRequest, "KernelRun": KernelRun}[name]
    if name == "KernelStateError":
        from .states import KernelStateError

        return KernelStateError
    if name in {"GovernedResult", "continue_governed", "run_governed"}:
        from . import entry as _entry

        return getattr(_entry, name)
    if name in {"CallableGate", "Evaluator", "GateResult", "Verdict"}:
        from .evaluator import CallableGate, Evaluator, GateResult, Verdict

        return {"CallableGate": CallableGate, "Evaluator": Evaluator,
                "GateResult": GateResult, "Verdict": Verdict}[name]
    raise AttributeError(name)
