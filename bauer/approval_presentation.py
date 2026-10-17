"""Safe approval labels: fixed vocabulary, never command arguments or paths."""
from __future__ import annotations

import shlex


def approval_summary(record) -> str:
    if record is None or record.tool_name != "run_command":
        return "Autorizar operação da tarefa"
    command = record.payload.get("command")
    if not isinstance(command, str):
        return "Executar comando"
    try:
        args = shlex.split(command)
    except ValueError:
        return "Executar comando"
    if not args:
        return "Executar comando"
    binary = args[0].replace("\\", "/").rsplit("/", 1)[-1].lower().removesuffix(".exe")
    if binary in {"python", "python3", "py"}:
        return "Executar código Python"
    if binary in {"docker", "docker-compose"}:
        compose = binary == "docker-compose" or args[1:2] == ["compose"]
        label = "Executar Docker Compose" if compose else "Executar Docker"
        tail = args[2:] if binary == "docker" and compose else args[1:]
        # Only the first subcommand is considered. Never search later tokens:
        # a quoted script/path can itself contain a familiar command word.
        subcommand = tail[0] if tail else ""
        if subcommand in {"version", "ps", "logs", "inspect", "build", "up", "down", "run", "exec", "start", "stop", "restart", "port", "config", "images", "pull", "volume", "network"}:
            label += f": {subcommand}"
        return label
    if binary == "git":
        return "Executar Git"
    if binary in {"ss", "netstat"}:
        return "Inspecionar rede do ambiente de execução"
    return "Executar comando"
