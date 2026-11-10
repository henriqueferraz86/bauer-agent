"""Rota HTTP ``/chat`` do Bauer Server.

O módulo recebe o runtime já montado pela factory, mantendo a rota isolada sem
criar uma segunda fonte de verdade para sessões, runs, policy ou métricas.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from contextlib import nullcontext
from typing import Any, Callable

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator

from .session_ids import optional_session_id


class ChatRequest(BaseModel):
    message: str = Field(..., max_length=100_000)
    session_id: str | None = None
    project_id: str | None = None

    _check_session_id = field_validator("session_id")(optional_session_id)


class ToolCallLog(BaseModel):
    tool: str
    result: str


class ChatResponse(BaseModel):
    response: str
    session_id: str
    model: str
    tool_calls: list[ToolCallLog] = []


@dataclass
class ChatRouteDependencies:
    """Colaboradores já construídos pela factory de ``bauer serve``."""

    verify_key: Callable[..., Any]
    metrics: Any
    store: Any
    session_manager: Any
    run_manager: Any
    kernel: Any
    router: Any
    fallback_clients: list[Any]
    state: dict[str, Any]
    resolve_project_router: Callable[[str | None, str | None], tuple[Any, str | None]]
    resolve_request_context: Callable[[str, Any], dict[str, Any]]
    effective_workspace: Callable[[Any], Any]
    apply_request_context: Callable[[Any, dict[str, Any]], None]
    new_context: Callable[[], Any]
    resolve_turn_model: Callable[[str], tuple[Any, str, Any]]
    run_input: Callable[[str, str, dict[str, Any]], dict[str, Any]]
    publish_selected_skill: Callable[[str, str, str, dict[str, Any]], None]
    publish_route: Callable[[str, str, str, Any], None]
    turn_cost_recorder: Callable[[str], Any]
    record_turn_budget: Callable[[Any, str, str], None]
    format_response: Callable[[str], str]
    public_tool_log: Callable[[list[dict] | None], list[dict]]
    run_one_turn_with_fallback: Callable[[Any, Any, Any, str, list[Any]], tuple[str, list[Any]]]
    runtime_mode: Callable[[], str]
    run_agno_turn: Callable[[str, str, str, str, Any, str, dict[str, Any]], dict[str, Any]]
    logger: logging.Logger
    new_project_context: Callable[[Any], Any] | None = None
    shell_approvals: Any | None = None


def _allowlist_authorization_prompt(tool_log: list[dict] | None) -> str | None:
    """Converte um bloqueio de shell em pergunta explícita ao operador.

    O modelo pode resumir um erro de tool de forma vaga. O gateway precisa
    preservar o gate humano de allowlist de forma determinística, inclusive
    quando a resposta será encaminhada ao TTS.
    """
    for item in tool_log or []:
        if not isinstance(item, dict):
            continue
        result = str(item.get("result", ""))
        if "Antes de continuar, pergunte ao usuario" not in result:
            continue
        match = re.search(r"Comando '([^']+)' nao esta na allowlist", result)
        base = match.group(1) if match else "este comando"
        return (
            f"O comando '{base}' está fora da allowlist. "
            f"Você autoriza adicioná-lo permanentemente à allowlist para eu "
            f"executá-lo?"
        )
    return None


def build_chat_router(deps: ChatRouteDependencies) -> APIRouter:
    """Cria a rota ``/chat`` e preserva os dois caminhos de execução."""

    router = APIRouter()

    def run_chat_turn(req, session_id, run_id, active_router, turn_model, resolved, ctx, turn_client, message=None):
        approvals = deps.shell_approvals
        if message is None:
            message = approvals.resolve(session_id, req.message, active_router) if approvals else req.message
        if message != req.message:
            ctx.add_ephemeral_system(message)
        capture = approvals.capture(session_id, deps.effective_workspace(active_router), message) if approvals else nullcontext()
        with capture:
            if deps.runtime_mode() == "agno":
                result = deps.run_agno_turn(
                    message, session_id, run_id, resolved.get("agent_id") or "serve.chat",
                    active_router, turn_model, resolved,
                )
                response, tool_log = result["response"], result["tool_log"]
                # O adapter Agno mantém seu próprio contexto interno e nunca
                # escreve em `ctx` — diferente do caminho nativo, cujo
                # `run_one_turn` já grava a resposta em `ctx.messages` como
                # parte do laço de tools. Sem isto, `/chat` com Agno nunca
                # persistia a resposta do assistente na sessão: o próximo
                # turno carregava o histórico sem o que o modelo disse antes
                # (`/stream` já faz este append no worker de `bauer/server.py`).
                ctx.add_assistant(deps.format_response(response))
            else:
                response, tool_log = deps.run_one_turn_with_fallback(
                    ctx, active_router, turn_client, turn_model, deps.fallback_clients,
                )
        prompt = approvals.prompt(session_id, deps.effective_workspace(active_router), presented=False) if approvals else _allowlist_authorization_prompt(tool_log)
        return prompt or response, tool_log

    def mark_shell_question_presented(session_id, active_router, response):
        if deps.shell_approvals is None:
            return
        workspace = deps.effective_workspace(active_router)
        prompt = deps.shell_approvals.prompt(session_id, workspace, presented=False)
        if prompt and prompt in response:
            deps.shell_approvals.prompt(session_id, workspace)

    def chat_via_kernel(
        req: ChatRequest,
        session_id: str,
        active_router: Any,
        resolved: dict[str, Any],
        request_agent_id: str,
        ctx: Any,
        turn_client: Any,
        turn_model: str,
        route: Any,
    ) -> ChatResponse | JSONResponse:
        """Executa um turno pelo Kernel, que mantém custódia da conclusão."""
        from .core.kernel import KernelRequest

        captured: dict[str, Any] = {}
        base_messages = len(ctx.messages)

        def executor(payload: dict[str, Any]) -> dict[str, Any]:
            run_id = payload["run_id"]
            if payload.get("replan_attempt"):
                del ctx.messages[base_messages:]
                ctx.add_ephemeral_system(
                    "Sua resposta anterior foi reprovada pelo quality gate: "
                    f"{payload.get('replan_feedback', '')}. Responda novamente "
                    "corrigindo esse problema."
                )
            else:
                deps.publish_selected_skill(run_id, session_id, request_agent_id, resolved)
                deps.publish_route(run_id, session_id, request_agent_id, route)

            from .cost_meter import cost_sink
            from .tool_router import (
                reset_policy_approval_wait,
                reset_runtime_ids,
                set_policy_approval_wait,
                set_runtime_ids,
            )

            cost = deps.turn_cost_recorder(session_id)
            cost_token = cost_sink.set(cost)
            # `/chat` bloqueia a própria requisição HTTP até o turno terminar
            # (sem SSE para reabrir mais tarde), então a espera de aprovação
            # precisa ficar ligada aqui — sem isso, `_evaluate_runtime_policy`
            # (tool_router.py) levanta "waiting_approval" na hora em vez de
            # bloquear até o operador decidir, que é o que `/stream` já faz.
            wait_token = set_policy_approval_wait(True)
            ids_token = set_runtime_ids(session_id, run_id)
            tool_log = []
            try:
                if "effective_message" not in captured:
                    captured["effective_message"] = (
                        deps.shell_approvals.resolve(session_id, req.message, active_router)
                        if deps.shell_approvals else req.message
                    )
                response, tool_log = run_chat_turn(
                    req, session_id, run_id, active_router, turn_model, resolved, ctx, turn_client,
                    captured["effective_message"],
                )
                formatted = deps.format_response(response)
                deps.store.save(
                    session_id, ctx.messages,
                    workspace=deps.effective_workspace(active_router),
                )
                deps.session_manager.touch_session(session_id, state={"last_run_id": run_id})
                captured["tool_log"] = tool_log
                return {"status": "completed", "output": formatted,
                        "tool_calls_count": len(tool_log), "cost_estimate": round(cost.total_usd, 6),
                        "llm_calls_count": cost.calls, "usage_known": bool(cost.calls) and cost.usage_known}
            except Exception as exc:  # noqa: BLE001 - Kernel registra o resultado
                tool_log = getattr(exc, "partial_tool_log", tool_log)
                return {"status": "failed", "error": str(exc), "tool_calls_count": len(tool_log),
                        "cost_estimate": round(cost.total_usd, 6),
                        "llm_calls_count": cost.calls, "usage_known": bool(cost.calls) and cost.usage_known}
            finally:
                deps.metrics.tool_calls_total += len(tool_log)
                cost_sink.reset(cost_token)
                reset_runtime_ids(ids_token)
                reset_policy_approval_wait(wait_token)
                deps.record_turn_budget(cost, run_id, request_agent_id)

        out = deps.kernel.execute(
            KernelRequest(
                task=req.message,
                session_id=session_id,
                agent_id=request_agent_id,
                input=deps.run_input(req.message, "/chat", resolved),
                runtime_adapter=deps.runtime_mode(),
            ),
            executor=executor,
        )
        if out.status == "waiting_approval":
            return JSONResponse(status_code=202, content={
                "status": "waiting_approval",
                "run_id": out.run_id,
                "approval_id": out.approval_id,
                "reason": out.policy_reason,
            })
        if out.status == "cancelled":
            raise HTTPException(
                status_code=503,
                detail=out.error or "Execução bloqueada (kill switch).",
            )
        if out.status != "completed":
            if out.policy_action == "deny":
                raise HTTPException(
                    status_code=403,
                    detail=out.error or "Bloqueado pela politica.",
                )
            deps.logger.error("Erro interno em /chat (kernel): %s", out.error)
            raise HTTPException(
                status_code=500,
                detail="Erro interno — consulte os logs do servidor.",
            )
        mark_shell_question_presented(session_id, active_router, out.output or "")
        return ChatResponse(
            response=out.output or "",
            session_id=session_id,
            model=deps.state["model"],
            tool_calls=[ToolCallLog(**item) for item in deps.public_tool_log(captured.get("tool_log"))],
        )

    @router.post("/chat", response_model=ChatResponse)
    def chat(req: ChatRequest, _: None = Depends(deps.verify_key)):
        deps.metrics.chat_requests_total += 1
        session_id = req.session_id or deps.store.new_id()
        with deps.store.turn(session_id):
            return execute_chat(req, session_id)

    def execute_chat(req: ChatRequest, session_id: str):
        active_router, active_project_id = deps.resolve_project_router(
            session_id, req.project_id
        )
        resolved = deps.resolve_request_context(
            req.message, deps.effective_workspace(active_router)
        )
        resolved["project_id"] = active_project_id
        request_agent_id = resolved.get("agent_id") or "serve.chat"
        session_state = {"transport": "http", "endpoint": "/chat"}
        if active_project_id:
            session_state["project_id"] = active_project_id
        deps.session_manager.get_or_create_session(
            session_id, agent_id=request_agent_id, state=session_state
        )

        ctx = deps.new_project_context(active_router) if deps.new_project_context else deps.new_context()
        ctx.messages = deps.store.load(session_id)
        deps.apply_request_context(ctx, resolved)
        ctx.add_user(req.message)
        turn_client, turn_model, route = deps.resolve_turn_model(req.message)

        if deps.kernel is not None:
            return chat_via_kernel(
                req, session_id, active_router, resolved, request_agent_id, ctx,
                turn_client, turn_model, route,
            )

        run = deps.run_manager.create_run(
            session_id=session_id,
            agent_id=request_agent_id,
            runtime_adapter=deps.runtime_mode(),
            input=deps.run_input(req.message, "/chat", resolved),
            status="running",
        )
        deps.publish_selected_skill(run.id, session_id, request_agent_id, resolved)

        from .cost_meter import cost_sink
        from .tool_router import (
            reset_policy_approval_wait,
            reset_runtime_ids,
            set_policy_approval_wait,
            set_runtime_ids,
        )

        deps.publish_route(run.id, session_id, request_agent_id, route)
        cost = deps.turn_cost_recorder(session_id)
        cost_token = cost_sink.set(cost)
        # Mesma razão do caminho via Kernel acima: sem isto, aprovação pedida
        # durante este turno nunca teria a chance de ser decidida — levantaria
        # "waiting_approval" na hora em vez de esperar o operador.
        wait_token = set_policy_approval_wait(True)
        ids_token = set_runtime_ids(session_id, run.id)
        tool_log = []
        try:
            response, tool_log = run_chat_turn(
                req, session_id, run.id, active_router, turn_model, resolved, ctx, turn_client,
            )
            response = deps.format_response(response)
            deps.store.save(
                session_id, ctx.messages,
                workspace=deps.effective_workspace(active_router),
            )
            deps.session_manager.touch_session(session_id, state={"last_run_id": run.id})
            deps.run_manager.complete_run(
                run.id, output={"response": response}, tool_calls_count=len(tool_log),
                cost_estimate=round(cost.total_usd, 6), llm_calls_count=cost.calls,
                usage_known=bool(cost.calls) and cost.usage_known,
            )
            mark_shell_question_presented(session_id, active_router, response)
            return ChatResponse(
                response=response, session_id=session_id, model=deps.state["model"],
                tool_calls=[ToolCallLog(**item) for item in deps.public_tool_log(tool_log)],
            )
        except Exception as exc:  # noqa: BLE001 - HTTP boundary
            tool_log = getattr(exc, "partial_tool_log", tool_log)
            deps.logger.exception("Erro interno em /chat: %s", exc)
            deps.run_manager.update_run(run.id, cost_estimate=round(cost.total_usd, 6),
                                        tool_calls_count=len(tool_log), llm_calls_count=cost.calls,
                                        usage_known=bool(cost.calls) and cost.usage_known)
            deps.run_manager.fail_run(run.id, str(exc))
            raise HTTPException(status_code=500, detail="Erro interno — consulte os logs do servidor.") from exc
        finally:
            deps.metrics.tool_calls_total += len(tool_log)
            cost_sink.reset(cost_token)
            reset_runtime_ids(ids_token)
            reset_policy_approval_wait(wait_token)
            deps.record_turn_budget(cost, run.id, request_agent_id)

    return router
