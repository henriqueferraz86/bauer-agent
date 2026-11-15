"""Opt-in HTTP executor for one authenticated Telegram turn.

Only the caller holding Kernel custody may invoke this function. The remote
service must attest its read-only boundary before any run is submitted.
"""
from __future__ import annotations

import logging
import os
import secrets
from pathlib import Path
from typing import Any

import jwt

from .agentos_http_client import AgentOSHttpClient
from .agentos_capabilities import verify_capability_proof
from .agentos_jwt_issuer import AgentOSJWTIssuer
from .channel_agentos_http import AgentOSChannelExecutor
from .remote_run_store import RemoteRunStore
from .remote_session_store import RemoteSessionStore
from .provider_retry import ProviderRetryInterrupted
from .tool_gateway_protocol import TOOL_CONTINUATION_VERSION

logger = logging.getLogger("bauer.telegram_agentos_http")


class TelegramAgentOSError(Exception):
    """An HTTP turn cannot safely be admitted or confirmed."""


def _identity(config_path: Path, user_id: str) -> tuple[dict[str, Any], str, str]:
    from .config_loader import load_config
    cfg = load_config(config_path)
    if cfg.runtime.telegram_default_adapter != "agentos_http":
        raise TelegramAgentOSError("Adapter alterado; reconciliação remota bloqueada.")
    settings = cfg.runtime.adapters["agentos_http"]
    from .tool_gateway_bridge import secure_service_url

    base_url = settings.get("base_url")
    if not isinstance(base_url, str) or not secure_service_url(base_url):
        raise TelegramAgentOSError("AgentOS HTTP exige HTTPS verificado ou endereço loopback literal.")
    agent_id = settings["agent_id"]
    scopes = [f"agents:{agent_id}:run", f"agents:{agent_id}:read"]
    env_name = settings.get("jwt_key_env") or "BAUER_AGENTOS_JWT_KEY"
    signer = AgentOSJWTIssuer(key=os.environ.get(env_name, ""), issuer=settings["jwt_issuer"],
                              audience=settings["jwt_audience"], allowed_scopes=scopes)
    token = signer.issue(authenticated_user_id=user_id, scopes=scopes)
    return settings, token, jwt.decode(token, options={"verify_signature": False})["sub"]


def _verify_profile(client: AgentOSHttpClient, settings: dict[str, Any], token: str) -> None:
    claims = jwt.decode(token, options={"verify_signature": False})
    challenge = secrets.token_hex(32)
    statement = client.channel_capabilities(challenge)
    capabilities = statement.get("capabilities") if isinstance(statement, dict) else None
    external_tools = settings.get("external_read_tools") is True
    required = {"contract_version": 1, "mode": "read_only", "user_isolation": True,
                "catalog_tools": False, "continuation": external_tools, "cancel_requires_terminal": True}
    if external_tools:
        required["external_read_tools"] = True
        required["tool_continuation_version"] = TOOL_CONTINUATION_VERSION
    identity = {"subject": claims["sub"], "scopes": sorted(set(claims["scopes"]))}
    env_name = settings.get("jwt_key_env") or "BAUER_AGENTOS_JWT_KEY"
    if (not isinstance(capabilities, dict) or any(capabilities.get(k) != v for k, v in required.items())
            or capabilities.get("identity") != identity
            or not verify_capability_proof(os.environ.get(env_name, ""), challenge,
                                           capabilities, statement.get("proof"))):
        raise TelegramAgentOSError("O AgentOS não comprovou o perfil remoto obrigatório.")


def rotate_remote_session(config_path: Path, state_dir: Path, msg: Any) -> None:
    _, _, owner = _identity(config_path, msg.user_id)
    project = msg.project_snapshot
    sessions = RemoteSessionStore(Path(state_dir) / "telegram-agentos.sqlite3")
    sessions.rotate(channel="telegram", owner_id=owner, chat_id=msg.chat_id,
                    project_id=project.project_id if project is not None else "",
                    update_id=str(msg.raw["update_id"]))


def cancel_remote_session(config_path: Path, state_dir: Path, msg: Any,
                          run_id: str | None = None, *, request_cancel: bool = True) -> str:
    settings, token, owner = _identity(config_path, msg.user_id)
    path = Path(state_dir) / "telegram-agentos.sqlite3"
    session = RemoteSessionStore(path).current(
        channel="telegram", owner_id=owner, chat_id=msg.chat_id,
        project_id=msg.project_snapshot.project_id if msg.project_snapshot is not None else "",
    )
    store = RemoteRunStore(path, require_managed_session=True)
    runs = [run for run in store.recoverable(owner_id=owner) if run.session_id == session.session_id
            and (not run_id or run_id in {run.gateway_run_id, run.remote_run_id})]
    if not runs:
        return "Nenhuma execução remota ativa correspondente nesta conversa."
    if len(runs) != 1:
        return "Há mais de uma execução ativa; informe o ID da execução."
    run = runs[0]
    stop_ready = True
    if request_cancel:
        from .channel_governance import runtime_root
        from .core.runtime.state_store import SqliteStateStore

        # Guard activation is persisted in the authoritative Kernel store. A
        # missing/invalid activated barrier raises rather than silently opting
        # out. Mark stop before any remote request, even without a remote ID.
        barrier = SqliteStateStore(runtime_root(state_dir)).tool_gateway_barrier()
        if barrier is not None:
            stop_ready = barrier.request_stop(run_id=run.gateway_run_id, reason="cancel")
            if not stop_ready:
                return "⏳ Cancelamento registrado; aguardando a ferramenta em andamento. Tente /cancelar novamente após a ferramenta terminar."
    if not run.remote_run_id:
        if request_cancel:
            return "⏳ Cancelamento registrado no Bauer; o AgentOS ainda não informou ID remoto para confirmar a parada."
        return "A execução ainda não informou ID remoto; não foi possível solicitar cancelamento."
    with AgentOSHttpClient(settings["base_url"], token,
                           connect_timeout_s=float(settings.get("connect_timeout_s", 5)),
                           read_timeout_s=float(settings.get("read_timeout_s", 180)),
                           trust_env=False) as client:
        _verify_profile(client, settings, token)
        if request_cancel:
            client.cancel_run("agents", run.entity_id, run.remote_run_id, run.session_id)
        result = AgentOSChannelExecutor(client, store).reconcile(run.gateway_run_id, owner_id=owner)
    if result.run.state == "CANCELLED":
        return "✅ Cancelamento confirmado pelo AgentOS."
    if result.run.state == "COMPLETED":
        return "A execução remota terminou; o resultado depende da validação do Kernel."
    if result.run.state == "ERROR":
        return "⚠️ O AgentOS confirmou falha na execução remota."
    if request_cancel:
        if not stop_ready:
            return "⏳ Cancelamento registrado; aguardando a ferramenta em andamento e a confirmação terminal do AgentOS."
        return "⏳ Cancelamento solicitado, mas o AgentOS ainda não confirmou o término."
    return f"⏳ Estado da execução remota: {result.run.state}."


def remote_update_state(config_path: Path, state_dir: Path, msg: Any) -> str | None:
    """Inspect owned local state; absence proves no POST was submitted."""
    _, _, owner = _identity(config_path, msg.user_id)
    store = RemoteRunStore(Path(state_dir) / "telegram-agentos.sqlite3", require_managed_session=True)
    record = store.get_by_update(channel="telegram", update_id=str(msg.raw["update_id"]), owner_id=owner)
    return record.state if record is not None else None


def recover_remote_update(config_path: Path, state_dir: Path, msg: Any, *, kernel: Any,
                          session_key: str, workspace: Path) -> str | None:
    """Reconcile a known remote run and recover only terminal Kernel results."""
    from .channel_projects import operator_key

    settings, token, owner = _identity(config_path, msg.user_id)
    store = RemoteRunStore(Path(state_dir) / "telegram-agentos.sqlite3", require_managed_session=True)
    record = store.get_by_update(channel="telegram", update_id=str(msg.raw["update_id"]), owner_id=owner)
    if record is None or kernel is None:
        return None
    if record.state not in {"COMPLETED", "CANCELLED", "ERROR"}:
        if not record.remote_run_id:
            return None
        read_timeout = min(float(settings.get("read_timeout_s", 180)), 10.0)
        with AgentOSHttpClient(settings["base_url"], token,
                               connect_timeout_s=float(settings.get("connect_timeout_s", 5)),
                               read_timeout_s=read_timeout, trust_env=False) as client:
            _verify_profile(client, settings, token)
            record = AgentOSChannelExecutor(client, store).reconcile(
                record.gateway_run_id, owner_id=owner,
            ).run
    if record.state not in {"COMPLETED", "CANCELLED", "ERROR"}:
        return None
    run = kernel.runs.get_run(record.gateway_run_id)
    project = msg.project_snapshot.project_id if msg.project_snapshot is not None else None
    if (run is None or run.agent_id != "channel.telegram"
            or run.runtime_adapter != "agentos_http" or run.session_id != session_key
            or run.input.get("operator") != operator_key(msg) or run.input.get("project_id") != project
            or run.input.get("workspace") != str(workspace)):
        return None
    if record.state == "COMPLETED" and run.status == "completed" and isinstance(run.output, dict):
        return str(run.output.get("output") or "🤔 O agente não retornou texto.")
    if record.state == "CANCELLED" and run.status == "cancelled":
        return "⏹ A execução remota foi cancelada e reconciliada pelo Kernel."
    if record.state == "ERROR" and run.status in {"failed", "error"}:
        return "⚠️ A execução remota terminou com erro e foi reconciliada pelo Kernel."
    return None


def execute_http_turn(*, settings: dict[str, Any], execution: Any, gateway_run_id: str,
                      text: str, state_dir: Path, on_delta: Any = None, kernel: Any = None) -> str:
    if (settings.get("enabled") is not True or settings.get("experimental_read_only") is not True
            or execution is None or execution.channel != "telegram" or not execution.private_chat
            or not execution.update_id or not execution.inbox_persisted or not gateway_run_id):
        raise TelegramAgentOSError("AgentOS HTTP requer perfil read-only, Kernel e Telegram privado autorizado.")
    agent_id = str(settings.get("agent_id") or "").strip()
    base_url = str(settings.get("base_url") or "").strip()
    issuer = str(settings.get("jwt_issuer") or "").strip()
    audience = str(settings.get("jwt_audience") or "").strip()
    env_name = str(settings.get("jwt_key_env") or "BAUER_AGENTOS_JWT_KEY").strip()
    if not agent_id or not base_url or not issuer or not audience or not env_name:
        raise TelegramAgentOSError("Configuração AgentOS HTTP incompleta.")
    external_tools = settings.get("external_read_tools") is True
    if external_tools and (kernel is None or not execution.project_id or env_name != "BAUER_AGENTOS_JWT_KEY"):
        raise TelegramAgentOSError("Ferramentas externas requerem Kernel, projeto e chave padrão.")
    from .tool_gateway_bridge import secure_service_url

    if not secure_service_url(base_url):
        raise TelegramAgentOSError("AgentOS HTTP exige HTTPS verificado ou endereço loopback literal.")
    scope = f"agents:{agent_id}:run"
    read_scope = f"agents:{agent_id}:read"
    try:
        signer = AgentOSJWTIssuer(key=os.environ.get(env_name, ""), issuer=issuer,
                                  audience=audience, allowed_scopes=[scope, read_scope])
        user_id = execution.owner.rsplit(":", 1)[-1]

        def issue_identity_token() -> str:
            return signer.issue(authenticated_user_id=user_id, scopes=[scope, read_scope])

        token = issue_identity_token()
        owner = jwt.decode(token, options={"verify_signature": False})["sub"]
        state_dir.mkdir(parents=True, exist_ok=True)
        path = state_dir / "telegram-agentos.sqlite3"
        sessions = RemoteSessionStore(path)
        runs = RemoteRunStore(path, require_managed_session=True)
        session = sessions.current(channel="telegram", owner_id=owner, chat_id=execution.chat_id,
                                   project_id=execution.project_id or "")
        with AgentOSHttpClient(base_url, token,
                               connect_timeout_s=float(settings.get("connect_timeout_s", 5)),
                               read_timeout_s=float(settings.get("read_timeout_s", 180)),
                               trust_env=False, token_factory=issue_identity_token) as client:
            _verify_profile(client, settings, token)
            gateway_client = None
            on_pause = None
            try:
                if external_tools:
                    from .tool_gateway_bridge import build_telegram_tool_bridge

                    bridge, gateway_client = build_telegram_tool_bridge(
                        client=client, kernel=kernel, state_dir=state_dir, remote_runs=runs)
                    original_invoke = bridge.invoke

                    def controlled_invoke(capability, action, args):
                        execution.control.before_tool()
                        result = original_invoke(capability, action, args)
                        execution.control.checkpoint()
                        return result

                    bridge.invoke = controlled_invoke

                    def on_pause(record):
                        execution.control.checkpoint()
                        try:
                            return bridge.continue_paused(
                                authenticated_user_id=user_id,
                                gateway_run_id=gateway_run_id, agent_id=agent_id,
                                remote_run_id=record.remote_run_id, remote_session_id=record.session_id)
                        except ProviderRetryInterrupted:
                            # The bridge settles its effect before the control
                            # checkpoint raises. Only now is it safe to cancel
                            # the paused AgentOS run without racing an effect.
                            try:
                                client.cancel_run("agents", agent_id, record.remote_run_id, record.session_id)
                            except Exception:  # noqa: BLE001 — retain uncertainty; never continue after cancel
                                logger.warning("Não foi possível confirmar cancelamento AgentOS após ferramenta", exc_info=True)
                            raise

                result = AgentOSChannelExecutor(client, runs).submit_stream(
                    gateway_run_id=gateway_run_id, channel="telegram", update_id=execution.update_id,
                    generation=session.generation, owner_id=owner, session_id=session.session_id,
                    entity_type="agent", entity_id=agent_id, message=text,
                    on_delta=getattr(on_delta, "on_delta", None), on_pause=on_pause,
                )
            finally:
                if gateway_client is not None:
                    gateway_client.close()
        control = getattr(execution, "control", None)
        if control is not None:
            control.checkpoint()
    except TelegramAgentOSError:
        raise
    except ProviderRetryInterrupted:
        raise
    except Exception as exc:
        raise TelegramAgentOSError("Não foi possível confirmar a execução AgentOS; não será reenviada automaticamente.") from exc
    if result.run.state != "COMPLETED" or result.replay:
        raise TelegramAgentOSError("Execução AgentOS pendente ou incerta; não será reenviada automaticamente.")
    output = str(result.content or "🤔 O agente não retornou texto.")
    if execution.project_id is not None and not external_tools:
        output += "\n\nℹ️ O projeto selecionado isola a sessão; seu conteúdo não foi enviado ao AgentOS."
    return output
