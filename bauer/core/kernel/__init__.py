"""Bauer Kernel — fachada de orquestração sobre os componentes existentes."""

from __future__ import annotations

__all__ = [
    "BauerKernel",
    "KernelRequest",
    "KernelRun",
    "KernelStateError",
    "build_kernel",
    "kernel_enabled",
]


def __getattr__(name: str):
    if name in {"BauerKernel", "build_kernel", "kernel_enabled"}:
        from .kernel import BauerKernel, build_kernel, kernel_enabled

        return {"BauerKernel": BauerKernel, "build_kernel": build_kernel,
                "kernel_enabled": kernel_enabled}[name]
    if name in {"KernelRequest", "KernelRun"}:
        from .schemas import KernelRequest, KernelRun

        return {"KernelRequest": KernelRequest, "KernelRun": KernelRun}[name]
    if name == "KernelStateError":
        from .states import KernelStateError

        return KernelStateError
    raise AttributeError(name)
