"""Create an isolated, single-operator Telegram Tool Gateway canary."""
from __future__ import annotations

import json
import os
import secrets
from pathlib import Path

import yaml
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from bauer import projects_registry
from bauer.channel_projects import ChannelProjectStore


ROOT = Path(os.environ.get("BAUER_CANARY_PREP_ROOT", "/prep"))
WORKSPACE = Path(os.environ.get("BAUER_CANARY_WORKSPACE_ROOT", "/app/workspace")) / "telegram-tool-gateway-canary"
MEMORY = Path(os.environ.get("BAUER_CANARY_MEMORY_ROOT", "/app/memory"))
AGENTOS_MEMORY = Path(os.environ.get("BAUER_CANARY_AGENTOS_MEMORY_ROOT", "/app/agentos-memory"))
MARKER = "BAUER_TELEGRAM_GATEWAY_CANARY_READ_OK"


def _write(path: Path, data: str, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())


def _env_line(name: str, value: str) -> str:
    if not value or "\n" in value or "\r" in value:
        raise SystemExit("invalid canary credential material")
    return f"{name}='{value.replace(chr(92), chr(92) * 2).replace(chr(39), chr(92) + chr(39))}'\n"


def _secret_env(path: Path, values: dict[str, str]) -> None:
    _write(path, "".join(_env_line(name, value) for name, value in values.items()))


def main() -> None:
    operator_path = ROOT / "operator.json"
    if not operator_path.is_file():
        raise SystemExit("protected operator input is missing")
    data = json.loads(operator_path.read_text(encoding="utf-8"))
    token = data.get("telegram_token")
    users = data.get("allowed_users")
    api_key = data.get("openrouter_api_key")
    model = data.get("model")
    if (not isinstance(token, str) or not token.strip() or data.get("allow_all") is not False
            or not isinstance(users, list) or len(users) != 1
            or isinstance(users[0], bool) or not isinstance(users[0], int) or users[0] <= 0
            or not isinstance(api_key, str) or not api_key.strip()
            or not isinstance(model, str) or not model.strip()):
        raise SystemExit("source bot must have one allowlisted operator and valid model credentials")

    protected_targets = [ROOT / name for name in (
        "config.yaml", "models.yaml", "agentos-config.yaml", "agents.yaml",
        ".env.bauer-canary", ".env.agentos-canary",
    )]
    if any(path.exists() for path in protected_targets):
        raise SystemExit("canary config already exists; inspect it before reinitializing")

    user_id = users[0]
    jwt_key = secrets.token_hex(32)
    requirement_key = secrets.token_hex(32)
    continuation_key = secrets.token_hex(32)
    service_key = secrets.token_hex(32)
    issuer = "bauer-telegram-live-canary"
    audience = "bauer-telegram-tool-gateway-canary"

    config = {
        "agent": {"name": "Telegram Tool Gateway Canary", "workspace": "/app/workspace"},
        "model": {"provider": "ollama", "name": "qwen3:0.6b"},
        "kernel": {"enabled": True},
        "runtime": {
            "telegram_default_adapter": "agentos_http",
            "adapters": {
                "agentos_http": {
                    "enabled": True,
                    "experimental_read_only": True,
                    "external_read_tools": True,
                    "base_url": "http://127.0.0.1:7787",
                    "agent_id": "bauer.telegram.canary",
                    "jwt_issuer": issuer,
                    "jwt_audience": "bauer-telegram-agentos-canary",
                }
            },
        },
        "telegram": {"enabled": True, "allowed_users": [user_id], "allow_all": False},
    }
    agentos_config = {
        "model": {"provider": "openrouter", "name": model},
        "runtime": {"adapters": {"agno": {"enabled": True, "mode": "sdk"}}},
    }
    agent = {
        "agents": [{
            "id": "bauer.telegram.canary",
            "name": "Bauer Telegram Read Canary",
            "version": "0.1.0",
            "description": "Canário privado e somente leitura do Tool Gateway.",
            "runtime_adapter": "agno",
            "provider": "openrouter",
            "model": {"provider": "openrouter", "name": model},
            "instructions": (
                "Você está no canário de leitura do Tool Gateway. Atenda somente no projeto "
                "sintético selecionado. Para verificar o canário, leia gateway-canary-marker.txt. "
                "Não alegue que alterou arquivos."
            ),
            "tools": [],
            "permissions": ["runtime.execute"],
            "autonomy": {"mode": "supervised"},
            "limits": {"max_runtime_s": 120, "max_tool_calls": 4},
            "policies": ["default"],
        }]
    }
    _write(ROOT / "config.yaml", yaml.safe_dump(config, sort_keys=False))
    _write(ROOT / "models.yaml", "models: {}\n")
    _write(ROOT / "agentos-config.yaml", yaml.safe_dump(agentos_config, sort_keys=False))
    _write(ROOT / "agents.yaml", yaml.safe_dump(agent, sort_keys=False))

    rsa_key = rsa.generate_private_key(public_exponent=65537, key_size=3072)
    key_dir = MEMORY / "bauer-home"
    key_dir.mkdir(parents=True, exist_ok=True)
    private_path = key_dir / "tool-gateway-private.pem"
    public_path = key_dir / "tool-gateway-public.pem"
    private_path.write_bytes(rsa_key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    public_path.write_bytes(rsa_key.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo))
    os.chmod(private_path, 0o600)
    os.chmod(public_path, 0o600)

    WORKSPACE.mkdir(parents=True, exist_ok=True)
    (WORKSPACE / "gateway-canary-marker.txt").write_text(MARKER + "\n", encoding="utf-8")
    registry = MEMORY / "bauer-home" / "projects.json"
    project = projects_registry.add_project(WORKSPACE, name="Telegram Gateway Canary",
                                             registry_path=registry)
    operator = f"telegram:{user_id}:{user_id}"
    ChannelProjectStore(MEMORY / "sessions" / "channel_projects.sqlite3").bind(
        operator, project["id"], WORKSPACE)

    selected_model = AGENTOS_MEMORY / "runtime" / "serve-model-state.json"
    _write(selected_model, json.dumps({"provider": "openrouter", "model": model}) + "\n")

    _secret_env(ROOT / ".env.bauer-canary", {
        "TELEGRAM_BOT_TOKEN": token,
        "BAUER_AGENTOS_JWT_KEY": jwt_key,
        "BAUER_AGENTOS_JWT_ISSUER": issuer,
        "BAUER_AGENTOS_JWT_AUDIENCE": "bauer-telegram-agentos-canary",
        "BAUER_TOOL_GATEWAY_REQUIREMENT_KEY": requirement_key,
        "BAUER_TOOL_GATEWAY_CONTINUATION_KEY": continuation_key,
        "BAUER_TOOL_GATEWAY_SERVICE_KEY": service_key,
        "BAUER_TOOL_GATEWAY_PUBLIC_KEY_FILE": "/app/memory/bauer-home/tool-gateway-public.pem",
        "BAUER_TOOL_GATEWAY_PRIVATE_KEY_FILE": "/app/memory/bauer-home/tool-gateway-private.pem",
        "BAUER_TOOL_GATEWAY_ISSUER": issuer,
        "BAUER_TOOL_GATEWAY_AUDIENCE": audience,
    })
    _secret_env(ROOT / ".env.agentos-canary", {
        "OPENROUTER_API_KEY": api_key,
        "BAUER_AGENTOS_JWT_KEY": jwt_key,
        "BAUER_AGENTOS_JWT_ISSUER": issuer,
        "BAUER_AGENTOS_JWT_AUDIENCE": "bauer-telegram-agentos-canary",
        "BAUER_TOOL_GATEWAY_REQUIREMENT_KEY": requirement_key,
        "BAUER_TOOL_GATEWAY_CONTINUATION_KEY": continuation_key,
    })

    print("Prepared isolated canary: one operator, one synthetic project, read_file only.")


if __name__ == "__main__":
    os.umask(0o077)
    main()
