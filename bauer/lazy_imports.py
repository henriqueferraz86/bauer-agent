"""Lazy import utilities for optional heavy dependencies.

Pattern used throughout Bauer to keep startup fast and make optional deps
truly optional (they only fail at use-time, not at import time).

Usage::

    from bauer.lazy_imports import require

    def do_something_with_playwright():
        pw = require("playwright", "pip install playwright && playwright install")
        # pw.chromium.launch() ...
"""

from __future__ import annotations

import importlib
import sys
from types import ModuleType


def require(package: str, install_hint: str = "", *, attr: str = "") -> ModuleType:
    """Import *package* lazily; raise ImportError with *install_hint* if missing.

    Parameters
    ----------
    package:
        Top-level package name (e.g. ``"playwright"``, ``"openai"``).
    install_hint:
        Human-readable install instruction shown on failure.
    attr:
        Optional dotted attribute to return (e.g. ``"sync_api"`` to return
        ``playwright.sync_api``).

    Returns
    -------
    The imported module (or attribute if *attr* given).

    Raises
    ------
    ImportError
        If the package is not installed, with a clear message including
        *install_hint*.
    """
    try:
        mod = importlib.import_module(package)
    except ImportError as exc:
        hint = f"\n  Instale com: {install_hint}" if install_hint else ""
        raise ImportError(
            f"Dependencia opcional '{package}' nao instalada.{hint}"
        ) from exc

    if attr:
        parts = attr.split(".")
        obj = mod
        for part in parts:
            obj = getattr(obj, part)
        return obj  # type: ignore[return-value]

    return mod


def is_available(package: str) -> bool:
    """Return True if *package* can be imported without side-effects."""
    if package in sys.modules:
        return sys.modules[package] is not None
    try:
        importlib.import_module(package)
        return True
    except ImportError:
        return False


# ---------------------------------------------------------------------------
# Named shortcuts — keep alphabetical
# ---------------------------------------------------------------------------

def require_anthropic() -> ModuleType:
    return require("anthropic", "pip install anthropic")


def require_fastapi() -> ModuleType:
    return require("fastapi", "pip install fastapi uvicorn[standard]")


def require_openai() -> ModuleType:
    return require("openai", "pip install openai")


def require_playwright() -> ModuleType:
    return require(
        "playwright",
        "pip install playwright && playwright install chromium",
    )


def require_pyyaml() -> ModuleType:
    return require("yaml", "pip install pyyaml")


def require_websockets() -> ModuleType:
    return require("websockets", "pip install 'bauer-agent[gateway]' or pip install websockets")
