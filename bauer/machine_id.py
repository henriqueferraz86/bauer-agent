"""Fingerprint da máquina para tornar o aprendizado portável (Decisão 5).

Hash curto, determinístico e legível. RAM arredondada em GB evita que oscilação
mínima invalide tudo, mas mudança real (4GB → 32GB) conta como máquina nova.
"""

from __future__ import annotations

import hashlib
import platform

import psutil


def machine_id() -> str:
    parts = [
        platform.node(),
        platform.machine(),
        str(round(psutil.virtual_memory().total / 1e9)),
    ]
    raw = "|".join(parts)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]


def machine_summary() -> dict[str, str | int]:
    vm = psutil.virtual_memory()
    return {
        "machine_id": machine_id(),
        "hostname": platform.node(),
        "arch": platform.machine(),
        "system": platform.system(),
        "ram_total_mb": int(vm.total / 1024 / 1024),
        "ram_available_mb": int(vm.available / 1024 / 1024),
    }
