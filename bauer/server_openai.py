"""Rotas compatíveis com OpenAI para :mod:`bauer.server`.

Este módulo concentra o contrato ``/v1`` sem conhecer a factory do FastAPI.
O servidor injeta as dependências de runtime já construídas, evitando duplicar
estado de sessões, runs, métricas ou governança.
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from dataclasses import dataclass
from typing import Any, Callable, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field, field_validator

from .session_ids import InvalidSessionIdError, optional_session_id


class OAIMessage(BaseModel):
    """Mensagem no subconjunto do contrato Chat Completions suportado."""

    role: Literal["user", "assistant", "system", "developer"]
    content: str = Field(..., max_length=200_000)


class OAICompletionRequest(BaseModel):
    """Corpo aceito por ``POST /v1/chat/completions``."""

    model: str | None = None
    messages: list[OAIMessage] = Field(..., min_length=1, max_length=200)
    stream: bool = False
    session_id: str | None = None
    history_mode: Literal["transcript", "incremental"] = "transcript"
    max_tokens: int | None = None
    temperature: float | None = None

    _check_session_id = field_validator("session_id")(optional_session_id)


@dataclass
class OpenAIRouteDependencies:
    """Dependências compartilhadas pela factory do servidor e pelas rotas ``/v1``."""

    verify_key: Callable[..., Any]
    store: Any
    session_manager: Any
    run_manager: Any
    kernel: Any
    router: Any
    fallback_clients: list[Any]
    metrics: Any
    state: dict[str, Any]
    new_context: Callable[[], Any]
    resolve_request_context: Callable[[str, Any], dict[str, Any]]
    effective_workspace: Callable[[Any], Any]
    apply_request_context: Callable[[Any, dict[str, Any]], None]
    resolve_turn_model: Callable[[str], tuple[Any, str, Any]]
    publish_selected_skill: Callable[[str, str, str, dict[str, Any]], None]
    publish_route: Callable[[str, str, str, Any], None]
    format_response: Callable[[str], str]
    turn_cost_recorder: Callable[[str], Any]
    record_turn_budget: Callable[[Any, str, str], None]
    run_one_turn_with_fallback: Callable[..., tuple[str, list[Any]]]
    runtime_mode: Callable[[], str]
    run_agno_turn: Callable[..., dict[str, Any]]
    logger: logging.Logger


def build_openai_router(deps: OpenAIRouteDependencies) -> APIRouter:
    """Monta as rotas OpenAI-compatible usando o runtime injetado pelo serve."""

    router = APIRouter()

    @router.post("/v1/chat/completions")
    def oai_chat_completions(
        req: OAICompletionRequest,
        request: Request,
        _: None = Depends(deps.verify_key),
    ):
        """Endpoint OpenAI-compatible para clientes como Claw3D.

        O handler é síncrono de propósito: o Starlette o executa em threadpool,
        sem bloquear o event loop com a busca de memória ou o cliente LLM.
        """
        unsupported = [name for name in ("max_tokens", "temperature") if getattr(req, name) is not None]
        if unsupported:
            raise HTTPException(status_code=422, detail=f"Parâmetros não suportados: {', '.join(unsupported)}.")
        if req.model is not None and req.model != deps.state["model"]:
            raise HTTPException(status_code=422, detail="O modelo solicitado deve ser o modelo ativo; use /models/switch para selecioná-lo.")
        deps.metrics.chat_requests_total += 1

        try:
            header_sid = optional_session_id(request.headers.get("X-Hermes-Session-Id"))
        except InvalidSessionIdError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        sid = header_sid or req.session_id or deps.store.new_id()
        last_user_message = next(
            (msg.content for msg in reversed(req.messages) if msg.role == "user"),
            "",
        )
        resolved = deps.resolve_request_context(
            last_user_message, deps.effective_workspace(deps.router)
        )
        request_agent_id = resolved.get("agent_id") or "serve.openai"
        deps.session_manager.get_or_create_session(
            sid,
            agent_id=request_agent_id,
            state={"transport": "openai", "endpoint": "/v1/chat/completions"},
        )

        if req.model is not None:
            turn_client, active_model, route = deps.state["client"], req.model, None
        else:
            turn_client, active_model, route = deps.resolve_turn_model(last_user_message)
        completion_id = f"chatcmpl-bauer-{uuid.uuid4().hex[:12]}"
        request_input = {
            "messages": [msg.model_dump() for msg in req.messages],
            "stream": req.stream,
            "history_mode": req.history_mode,
            "endpoint": "/v1/chat/completions",
            "message": last_user_message,
            "selected_agent": resolved.get("agent_id") or "",
            "selected_skill": getattr(resolved.get("skill"), "name", ""),
        }
        if deps.kernel is not None:
            from .core.kernel import KernelRequest

            run, early = deps.kernel.admit(KernelRequest(
                task=last_user_message,
                session_id=sid,
                agent_id=request_agent_id,
                input=request_input,
            ))
            if early is not None:
                if early.status == "waiting_approval":
                    raise HTTPException(
                        status_code=202,
                        detail=(f"Aguardando aprovação ({early.approval_id}): "
                                f"{early.policy_reason}"),
                    )
                if early.policy_action == "deny":
                    raise HTTPException(
                        status_code=403,
                        detail=early.error or "Bloqueado pela politica.",
                    )
                raise HTTPException(
                    status_code=503,
                    detail=early.error or "Execução bloqueada.",
                )
        else:
            run = deps.run_manager.create_run(
                session_id=sid,
                agent_id=request_agent_id,
                runtime_adapter="bauer_native",
                input=request_input,
                status="running",
            )
        deps.publish_selected_skill(run.id, sid, request_agent_id, resolved)
        deps.publish_route(run.id, sid, request_agent_id, route)
        response_headers = {"X-Hermes-Session-Id": sid, "X-Bauer-Run-ID": run.id}

        def execute_completion():
            """One governed engine for batch and SSE, including tools/fallback/cost."""
            from .cost_meter import cost_sink
            from .core.kernel import continue_governed
            from .tool_router import (
                reset_policy_approval_wait,
                reset_runtime_ids,
                set_policy_approval_wait,
                set_runtime_ids,
            )

            ctx = deps.new_context()
            # OpenAI clients send a complete transcript by default. Hermes
            # clients that send only new turns request incremental explicitly.
            ctx.messages = deps.store.load(sid) if req.history_mode == "incremental" else []
            deps.apply_request_context(ctx, resolved)
            instructions = [msg.content for msg in req.messages if msg.role in {"system", "developer"}]
            if instructions:
                ctx.system_prompt = "\n\n".join([ctx.system_prompt or "", *instructions]).strip()
            for msg in req.messages:
                if msg.role == "user":
                    ctx.add_user(msg.content)
                elif msg.role == "assistant":
                    ctx.add_assistant(msg.content)

            cost = deps.turn_cost_recorder(sid)
            cost_token = cost_sink.set(cost)
            # `/v1/chat/completions` bloqueia a própria requisição HTTP até o
            # turno terminar (sem SSE para reabrir mais tarde), então a espera
            # de aprovação precisa ficar ligada aqui — sem isso,
            # `_evaluate_runtime_policy` (tool_router.py) levanta
            # "waiting_approval" na hora em vez de bloquear até o operador
            # decidir via POST /api/approvals/{id}/approve (mesmo bug que o
            # sprint 38 corrigiu em `/chat`; aqui é pré-existente e
            # independente do branch Agno criado nesta sprint).
            wait_token = set_policy_approval_wait(True)
            ids_token = set_runtime_ids(sid, run.id)
            turn: dict[str, Any] = {}

            def execute_v1(payload: dict[str, Any]) -> dict[str, Any]:
                feedback = (payload or {}).get("replan_feedback")
                if feedback:
                    ctx.add_user(f"A validação reprovou a resposta anterior: {feedback}\nCorrija e responda de novo.")
                before_cost, before_calls = cost.total_usd, cost.calls
                tool_log: list[dict[str, Any]] = []
                outcome: dict[str, Any] = {}
                try:
                    if deps.runtime_mode() == "agno":
                        result = deps.run_agno_turn(
                            last_user_message, sid, run.id, request_agent_id,
                            deps.router, active_model, resolved,
                        )
                        answer, tool_log = result["response"], result["tool_log"]
                        # O adapter Agno mantém seu próprio contexto interno e
                        # nunca escreve em `ctx` — diferente do caminho nativo,
                        # cujo `run_one_turn` já grava a resposta em
                        # `ctx.messages` como parte do laço de tools. Sem isto,
                        # `/v1/chat/completions` com Agno nunca persistia a
                        # resposta do assistente na sessão (mesmo raciocínio
                        # do fix do sprint 38 em `/chat`).
                        ctx.add_assistant(deps.format_response(answer))
                    else:
                        answer, tool_log = deps.run_one_turn_with_fallback(
                            ctx, deps.router, turn_client, active_model, deps.fallback_clients,
                        )
                    turn["response"] = deps.format_response(answer)
                    outcome["output"] = turn["response"]
                except Exception as exc:  # noqa: BLE001 - Kernel settles partial usage
                    tool_log = getattr(exc, "partial_tool_log", tool_log)
                    deps.logger.exception("Erro interno em /v1/chat/completions: %s", exc)
                    outcome.update(status="failed", error="Erro interno durante a geração.")
                turn["tool_log"] = tool_log
                try:
                    deps.store.save(sid, ctx.messages,
                                    workspace=deps.effective_workspace(deps.router))
                    deps.session_manager.touch_session(sid, state={"last_run_id": run.id})
                except Exception as exc:  # noqa: BLE001 - persistence belongs to executor
                    deps.logger.exception("Falha ao salvar sessão /v1/chat/completions: %s", exc)
                    outcome.update(status="failed", error="Erro interno durante a geração.")
                return {**outcome, "tool_calls_count": len(tool_log),
                        "cost_estimate": round(cost.total_usd - before_cost, 6),
                        "llm_calls_count": cost.calls - before_calls,
                        "usage_known": cost.calls > before_calls and cost.usage_known}

            try:
                governed = continue_governed(deps.kernel, run.id, execute_v1)
                if deps.kernel is None:
                    deps.run_manager.update_run(
                        run.id, cost_estimate=round(cost.total_usd, 6),
                        tool_calls_count=len(turn.get("tool_log", [])), llm_calls_count=cost.calls, usage_known=bool(cost.calls) and cost.usage_known,
                    )
                    if governed.ok:
                        deps.run_manager.complete_run(
                            run.id, output={"response": turn.get("response", "")},
                            tool_calls_count=len(turn.get("tool_log", [])),
                            cost_estimate=round(cost.total_usd, 6),
                        )
                    else:
                        deps.run_manager.fail_run(run.id, governed.error or "Erro interno durante a geração.")
                recorded = deps.run_manager.get_run(run.id)
                deps.metrics.tool_calls_total += recorded.tool_calls_count if recorded else 0
                if not governed.ok:
                    raise HTTPException(status_code=500, detail="Erro interno durante a geração.")
                response = turn.get("response", "")
                if cost.calls:
                    prompt_tokens, completion_tokens = cost.prompt_tokens, cost.completion_tokens
                else:
                    prompt_tokens = sum(len(message.get("content", "")) // 4 for message in ctx.messages[:-1])
                    completion_tokens = len(response) // 4
                return response, {"prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens,
                                  "total_tokens": prompt_tokens + completion_tokens}
            finally:
                cost_sink.reset(cost_token)
                reset_runtime_ids(ids_token)
                reset_policy_approval_wait(wait_token)
                deps.record_turn_budget(cost, run.id, request_agent_id)

        if req.stream:
            deps.metrics.stream_requests_total += 1

            def oai_stream():
                # Keep the existing buffered encoding, executing the same
                # governed turn as batch so native tools/accounting cannot drift.
                try:
                    with deps.store.turn(sid):
                        response, _usage = execute_completion()
                except Exception:  # noqa: BLE001 - boundary SSE
                    deps.logger.exception("Erro no stream OpenAI (run=%s)", run.id)
                    error = json.dumps({"error": {"message": "Erro interno durante a geração.", "type": "server_error"}})
                    yield f"data: {error}\n\n"
                    yield "data: [DONE]\n\n"
                    return
                for delta, finish in (({"content": response}, None), ({}, "stop")):
                    chunk = json.dumps({
                        "id": completion_id, "object": "chat.completion.chunk",
                        "created": int(time.time()), "model": active_model,
                        "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
                    }, ensure_ascii=False)
                    yield f"data: {chunk}\n\n"
                yield "data: [DONE]\n\n"

            return StreamingResponse(oai_stream(), media_type="text/event-stream", headers=response_headers)

        with deps.store.turn(sid):
            response, usage = execute_completion()
        return JSONResponse(content={
            "id": completion_id, "object": "chat.completion", "created": int(time.time()),
            "model": active_model,
            "choices": [{"index": 0, "message": {"role": "assistant", "content": response}, "finish_reason": "stop"}],
            "usage": usage,
        }, headers=response_headers)

    @router.get("/v1/models")
    def oai_models(_: None = Depends(deps.verify_key)):
        """Lista o modelo ativo no formato esperado por clientes OpenAI."""
        return {
            "object": "list",
            "data": [{
                "id": deps.state["model"],
                "object": "model",
                "created": int(time.time()),
                "owned_by": "bauer-agent",
            }],
        }

    return router
