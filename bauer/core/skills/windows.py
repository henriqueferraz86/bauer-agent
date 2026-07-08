"""Windows skill handlers used by the formal SkillExecutor."""

from __future__ import annotations

import os
import subprocess
import sys
import webbrowser
from typing import Any


class WindowsSkillError(RuntimeError):
    pass


def execute_windows_skill(skill_id: str, inputs: dict[str, Any] | None = None) -> dict[str, Any]:
    data = inputs or {}
    handlers = {
        "windows.open_app": _open_app,
        "windows.browser": _open_browser,
        "windows.control_panel": _open_control_panel,
        "windows.powershell_safe": _run_powershell_safe,
    }
    handler = handlers.get(skill_id)
    if handler is None:
        raise WindowsSkillError(f"Unsupported Windows skill: {skill_id}")
    return handler(data)


def supports_windows_skill(skill_id: str) -> bool:
    return skill_id in {
        "windows.open_app",
        "windows.browser",
        "windows.control_panel",
        "windows.powershell_safe",
    }


def _open_app(inputs: dict[str, Any]) -> dict[str, Any]:
    target = _required(inputs, "app", aliases=("name", "path", "command"))
    process = subprocess.Popen([target], close_fds=True)  # noqa: S603 - target is policy-gated by SkillExecutor.
    return {"opened": True, "target": target, "pid": process.pid}


def _open_browser(inputs: dict[str, Any]) -> dict[str, Any]:
    url = str(inputs.get("url") or "about:blank").strip()
    browser = str(inputs.get("browser") or "").strip()
    if browser:
        process = subprocess.Popen([browser, url], close_fds=True)  # noqa: S603 - target is policy-gated by SkillExecutor.
        return {"opened": True, "url": url, "browser": browser, "pid": process.pid}
    opened = webbrowser.open(url, new=2)
    return {"opened": bool(opened), "url": url, "browser": "default"}


def _open_control_panel(inputs: dict[str, Any]) -> dict[str, Any]:
    target = str(inputs.get("target") or "settings").strip().lower()
    if target in {"settings", "windows_settings", "ms-settings"}:
        _start_uri("ms-settings:")
        return {"opened": True, "target": "ms-settings:"}
    if target in {"control", "control_panel", "painel"}:
        process = subprocess.Popen(["control.exe"], close_fds=True)  # noqa: S603 - fixed Windows executable.
        return {"opened": True, "target": "control.exe", "pid": process.pid}
    raise WindowsSkillError(f"Unsupported control panel target: {target}")


def _run_powershell_safe(inputs: dict[str, Any]) -> dict[str, Any]:
    command = _required(inputs, "command", aliases=("script",))
    timeout_s = float(inputs.get("timeout_s") or 30)
    completed = subprocess.run(  # noqa: S603 - command is policy-gated and not run through a shell.
        [
            "powershell",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            command,
        ],
        capture_output=True,
        text=True,
        timeout=timeout_s,
        check=False,
    )
    return {
        "exit_code": completed.returncode,
        "stdout": completed.stdout[-8000:],
        "stderr": completed.stderr[-8000:],
        "timeout_s": timeout_s,
    }


def _start_uri(uri: str) -> None:
    starter = getattr(os, "startfile", None)
    if sys.platform == "win32" and starter is not None:
        starter(uri)
        return
    raise WindowsSkillError(f"Windows URI launch is only available on Windows: {uri}")


def _required(inputs: dict[str, Any], key: str, *, aliases: tuple[str, ...] = ()) -> str:
    for candidate in (key, *aliases):
        value = str(inputs.get(candidate) or "").strip()
        if value:
            return value
    raise WindowsSkillError(f"Missing required input: {key}")
