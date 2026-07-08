"""Linux and macOS skill handlers used by the formal SkillExecutor."""

from __future__ import annotations

import subprocess
from typing import Any


class PosixSkillError(RuntimeError):
    pass


def supports_posix_skill(skill_id: str) -> bool:
    return skill_id in {
        "linux.open_app",
        "linux.shell_safe",
        "macos.open_app",
        "macos.shell_safe",
    }


def execute_posix_skill(skill_id: str, inputs: dict[str, Any] | None = None) -> dict[str, Any]:
    data = inputs or {}
    if skill_id == "linux.open_app":
        return _linux_open_app(data)
    if skill_id == "linux.shell_safe":
        return _run_shell(data, executable="bash")
    if skill_id == "macos.open_app":
        return _macos_open_app(data)
    if skill_id == "macos.shell_safe":
        return _run_shell(data, executable="zsh")
    raise PosixSkillError(f"Unsupported POSIX skill: {skill_id}")


def _linux_open_app(inputs: dict[str, Any]) -> dict[str, Any]:
    target = _required(inputs, "app", aliases=("name", "path", "command"))
    args = _str_list(inputs.get("args"))
    process = subprocess.Popen([target, *args], close_fds=True)  # noqa: S603 - target is policy-gated.
    return {"opened": True, "target": target, "args": args, "pid": process.pid}


def _macos_open_app(inputs: dict[str, Any]) -> dict[str, Any]:
    target = _required(inputs, "app", aliases=("name", "path", "command"))
    args = ["open"]
    if bool(inputs.get("bundle")) or target.endswith(".app"):
        args.extend(["-a", target])
    else:
        args.append(target)
    process = subprocess.Popen(args, close_fds=True)  # noqa: S603 - target is policy-gated.
    return {"opened": True, "target": target, "pid": process.pid}


def _run_shell(inputs: dict[str, Any], *, executable: str) -> dict[str, Any]:
    command = _required(inputs, "command", aliases=("script",))
    timeout_s = float(inputs.get("timeout_s") or 30)
    completed = subprocess.run(  # noqa: S603 - command is policy-gated and not run through shell=True.
        [executable, "-lc", command],
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
        "shell": executable,
    }


def _required(inputs: dict[str, Any], key: str, *, aliases: tuple[str, ...] = ()) -> str:
    for candidate in (key, *aliases):
        value = str(inputs.get(candidate) or "").strip()
        if value:
            return value
    raise PosixSkillError(f"Missing required input: {key}")


def _str_list(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value] if value else []
    if isinstance(value, (list, tuple)):
        return [str(item) for item in value]
    return []
