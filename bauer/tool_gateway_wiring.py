"""Explicit, fail-closed registration of the experimental read-only gateway."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any


class ToolGatewayConfigurationError(ValueError):
    """The explicitly enabled gateway is not safe to start."""


def install_tool_gateway(app: Any, *, sessions_dir: Path, run_manager: Any,
                         kernel: Any, policy_rules_path: str | Path | None = None) -> bool:
    """Register only the restricted router; this does not wire AgentOS tools.

    All credential material is supplied by the operator. A valid HTTP route
    does not prove remote approval, write governance, or Telegram connectivity.
    """
    raw = os.environ.get("BAUER_TOOL_GATEWAY_ENABLED", "").strip().lower()
    if raw not in {"", "0", "false", "no", "1", "true", "yes"}:
        raise ToolGatewayConfigurationError("BAUER_TOOL_GATEWAY_ENABLED inválido")
    if raw in {"", "0", "false", "no"}:
        return False
    if kernel is None or not isinstance(sessions_dir, Path):
        raise ToolGatewayConfigurationError("Tool Gateway exige Kernel e diretório de sessões válidos")

    names = ("BAUER_TOOL_GATEWAY_PUBLIC_KEY_FILE", "BAUER_TOOL_GATEWAY_SERVICE_KEY",
             "BAUER_AGENTOS_JWT_KEY", "BAUER_TOOL_GATEWAY_ISSUER", "BAUER_TOOL_GATEWAY_AUDIENCE")
    values = {name: os.environ.get(name, "").strip() for name in names}
    if any(not value for value in values.values()):
        raise ToolGatewayConfigurationError("Tool Gateway exige credenciais, emissor e audiência configurados")
    if len(values["BAUER_TOOL_GATEWAY_SERVICE_KEY"].encode()) < 32 or len(values["BAUER_AGENTOS_JWT_KEY"].encode()) < 32:
        raise ToolGatewayConfigurationError("Chaves do Tool Gateway devem ter pelo menos 32 bytes")
    try:
        from cryptography.hazmat.primitives.serialization import load_pem_public_key
        from cryptography.hazmat.primitives.asymmetric.rsa import RSAPublicKey

        public_key = Path(values["BAUER_TOOL_GATEWAY_PUBLIC_KEY_FILE"]).read_text(encoding="utf-8")
        parsed = load_pem_public_key(public_key.encode())
        if not isinstance(parsed, RSAPublicKey) or parsed.key_size < 2048:
            raise ValueError("invalid key")
    except (OSError, ValueError, TypeError) as exc:
        raise ToolGatewayConfigurationError("Chave pública RSA do Tool Gateway inválida") from exc

    from .channel_projects import ChannelProjectStore
    from .paths import get_bauer_home
    from .remote_run_store import RemoteRunStore
    from .tool_gateway_authorization import ToolGatewayAuthorizer
    from .tool_gateway_endpoint import ToolCallLedger, build_tool_gateway_router

    root = getattr(getattr(run_manager, "store", None), "root", None)
    if not isinstance(root, Path):
        raise ToolGatewayConfigurationError("Store do Kernel não é válido")
    remote_path = sessions_dir / "telegram-agentos.sqlite3"
    bindings_path = sessions_dir / "channel_projects.sqlite3"
    registry_path = get_bauer_home() / "projects.json"
    if not all(path.is_file() for path in (bindings_path, registry_path)):
        raise ToolGatewayConfigurationError("Tool Gateway exige stores de associação e projetos existentes")
    if remote_path.is_symlink() or (remote_path.exists() and not remote_path.is_file()):
        raise ToolGatewayConfigurationError("Store de runs remotas inválido")
    if policy_rules_path is not None and not isinstance(policy_rules_path, (str, Path)):
        raise ToolGatewayConfigurationError("Caminho de política inválido")
    if policy_rules_path:
        try:
            import yaml

            policy = yaml.safe_load(Path(policy_rules_path).read_text(encoding="utf-8"))
            if (not isinstance(policy, dict) or not isinstance(policy.get("rules"), list)
                    or any(not isinstance(rule, dict) for rule in policy["rules"])):
                raise ValueError("invalid policy")
        except (OSError, UnicodeError, ValueError, yaml.YAMLError) as exc:
            raise ToolGatewayConfigurationError("Política do Tool Gateway inválida ou indisponível") from exc
    # The server starts before the first remote turn. Bootstrap an empty store
    # with the regular schema, then require managed session ownership on claim.
    authorizer = ToolGatewayAuthorizer(
        public_key=public_key, identity_key=values["BAUER_AGENTOS_JWT_KEY"],
        issuer=values["BAUER_TOOL_GATEWAY_ISSUER"], audience=values["BAUER_TOOL_GATEWAY_AUDIENCE"],
        runs=run_manager, remote_runs=RemoteRunStore(remote_path, require_managed_session=True),
        bindings=ChannelProjectStore(bindings_path), registry_path=registry_path,
    )
    app.include_router(build_tool_gateway_router(
        authorizer=authorizer, ledger=ToolCallLedger(root / "runtime_state.sqlite3"),
        service_key=values["BAUER_TOOL_GATEWAY_SERVICE_KEY"], policy_root=root,
        policy_rules_path=Path(policy_rules_path) if policy_rules_path else None,
        require_secure_transport=True,
    ))
    return True
