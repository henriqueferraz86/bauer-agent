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
from typing import Any, Callable

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field


class OAIMessage(BaseModel):
    """Mensagem no subconjunto do contrato Chat Completions suportado."""

    role: str
    content: str = Field(..., max_length=200_000)


class OAICompletionRequest(BaseModel):
    """Corpo aceito por ``POST /v1/chat/completions``."""

    model: str | None = None
    messages: list[OAIMessage] = Field(..., min_length=1, max_length=200)
    stream: bool = False
    session_id: str | None = None
    max_tokens: int | None = None
    temperature: float | None = None


@dataclass
class OpenAIRouteDependencies:
    """Dependências compartilhadas pela factory do servidor e pelas rotas ``/v1``."""

    verify_key: Callable[..., Any]
    store: Any
    session_manager: Any
    run_manager: Any
    kernel: Any
    router: Any
    metrics: Any
    state: dict[str, Any]
    new_context: Callable[[], Any]
    resolve_request_context: Callable[[str, Any], dict[str, Any]]
    effective_workspace: Callable[[Any], Any]
    resolve_turn_model: Callable[[str], tuple[Any, str, Any]]
    publish_selected_skill: Callable[[str, str, str, dict[str, Any]], None]
    publish_route: Callable[[str, str, str, Any], None]
    format_response: Callable[[str], str]
    turn_cost_recorder: Callable[[str], Any]
    record_turn_budget: Callable[[Any, str, str], None]
    run_one_turn: Callable[[Any, Any, Any, str], tuple[str, list[Any]]]
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
        deps.metrics.chat_requests_total += 1

        sid = (
            request.headers.get("X-Hermes-Session-Id")
            or req.session_id
            or deps.store.new_id()
        )
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

        ctx = deps.new_context()
        ctx.messages = deps.store.load(sid)
        for msg in req.messages:
            if msg.role == "user":
                ctx.add_user(msg.content)
            elif msg.role == "assistant":
                ctx.add_assistant(msg.content)

        turn_client, active_model, route = deps.resolve_turn_model(last_user_message)
        completion_id = f"chatcmpl-bauer-{uuid.uuid4().hex[:12]}"
        request_input = {
            "messages": [msg.model_dump() for msg in req.messages],
            "stream": req.stream,
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
            if req.stream:
                deps.run_manager.start_run(run.id)
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

        if req.stream:
            deps.metrics.stream_requests_total += 1

            def oai_stream():
                from .agent import MAX_TOOL_TURNS, _try_parse_tool
                from .tool_router import reset_runtime_ids, set_runtime_ids

                ids_token = set_runtime_ids(sid, run.id)
                tool_count = 0
                try:
                    while True:
                        current_run = deps.run_manager.get_run(run.id)
                        if current_run is not None and current_run.status == "cancelled":
                            deps.store.save(sid, ctx.messages)
                            deps.session_manager.touch_session(
                                sid, state={"last_run_id": run.id}
                            )
                            yield "data: [DONE]\n\n"
                            return

                        parts: list[str] = []
                        try:
                            for chunk in turn_client.chat_stream(active_model, ctx.get_payload()):
                                current_run = deps.run_manager.get_run(run.id)
                                if current_run is not None and current_run.status == "cancelled":
                                    deps.store.save(sid, ctx.messages)
                                    deps.session_manager.touch_session(
                                        sid, state={"last_run_id": run.id}
                                    )
                                    yield "data: [DONE]\n\n"
                                    return
                                parts.append(chunk)
                                delta = json.dumps({
                                    "id": completion_id,
                                    "object": "chat.completion.chunk",
                                    "created": int(time.time()),
                                    "model": active_model,
                                    "choices": [{
                                        "index": 0,
                                        "delta": {"content": chunk},
                                        "finish_reason": None,
                                    }],
                                }, ensure_ascii=False)
                                yield f"data: {delta}\n\n"
                        except Exception as exc:  # noqa: BLE001 - boundary SSE
                            deps.logger.exception(
                                "Erro interno durante stream OpenAI (run=%s)", run.id
                            )
                            error = json.dumps({
                                "error": {
                                    "message": "Erro interno durante a geração.",
                                    "type": "server_error",
                                }
                            })
                            yield f"data: {error}\n\n"
                            deps.store.save(sid, ctx.messages)
                            deps.session_manager.touch_session(
                                sid, state={"last_run_id": run.id}
                            )
                            deps.run_manager.fail_run(run.id, str(exc))
                            yield "data: [DONE]\n\n"
                            return

                        response = deps.format_response("".join(parts))
                        ctx.add_assistant(response)
                        action_dict = _try_parse_tool(response, deps.router)
                        if action_dict and tool_count < MAX_TOOL_TURNS:
                            action_name = action_dict.get("action", "tool")
                            try:
                                tool_result = deps.router.execute(action_dict)
                            except Exception:  # noqa: BLE001 - tool boundary
                                deps.logger.exception(
                                    "Erro ao executar tool no stream OpenAI (run=%s)", run.id
                                )
                                tool_result = "[Erro interno ao executar a ferramenta.]"
                            tool_event = json.dumps({"tool": action_name, "label": action_name})
                            yield f"event: hermes.tool.progress\ndata: {tool_event}\n\n"
                            ctx.add_user(f"[Resultado de {action_name}]\n{tool_result}")
                            tool_count += 1
                            deps.metrics.tool_calls_total += 1
                        else:
                            final_delta = json.dumps({
                                "id": completion_id,
                                "object": "chat.completion.chunk",
                                "created": int(time.time()),
                                "model": active_model,
                                "choices": [{
                                    "index": 0,
                                    "delta": {},
                                    "finish_reason": "stop",
                                }],
                            })
                            yield f"data: {final_delta}\n\n"
                            deps.store.save(sid, ctx.messages)
                            deps.session_manager.touch_session(
                                sid, state={"last_run_id": run.id}
                            )
                            deps.run_manager.complete_run(
                                run.id,
                                output={"response": response},
                                tool_calls_count=tool_count,
                            )
                            yield "data: [DONE]\n\n"
                            break
                finally:
                    reset_runtime_ids(ids_token)

            return StreamingResponse(
                oai_stream(), media_type="text/event-stream", headers=response_headers
            )

        from .cost_meter import cost_sink
        from .core.kernel import continue_governed
        from .tool_router import reset_runtime_ids, set_runtime_ids

        cost = deps.turn_cost_recorder(sid)
        cost_token = cost_sink.set(cost)
        ids_token = set_runtime_ids(sid, run.id)
        turn: dict[str, Any] = {}

        def execute_v1(payload: dict[str, Any]) -> dict[str, Any]:
            feedback = (payload or {}).get("replan_feedback")
            if feedback:
                ctx.add_user(
                    "A validação reprovou a resposta anterior: "
                    f"{feedback}\nCorrija e responda de novo."
                )
            answer, tool_log = deps.run_one_turn(ctx, deps.router, turn_client, active_model)
            turn["response"] = deps.format_response(answer)
            turn["tool_log"] = tool_log
            return {
                "output": turn["response"],
                "tool_calls_count": len(tool_log),
                "cost_estimate": round(cost.total_usd, 6),
            }

        try:
            governed = continue_governed(deps.kernel, run.id, execute_v1)
        except Exception as exc:  # noqa: BLE001 - HTTP boundary
            deps.logger.exception("Erro interno em /v1/chat/completions: %s", exc)
            if deps.kernel is None:
                deps.run_manager.fail_run(run.id, str(exc))
            raise HTTPException(
                status_code=500,
                detail="Erro interno — consulte os logs do servidor.",
            ) from exc
        finally:
            cost_sink.reset(cost_token)
            reset_runtime_ids(ids_token)

        if not governed.ok:
            deps.logger.warning("/v1/chat/completions não concluiu: %s", governed.error)
            raise HTTPException(
                status_code=500,
                detail=governed.error or "Erro interno — consulte os logs do servidor.",
            )

        response = turn.get("response", "")
        tool_log = turn.get("tool_log", [])
        deps.metrics.tool_calls_total += len(tool_log)
        deps.record_turn_budget(cost, run.id, request_agent_id)
        deps.store.save(sid, ctx.messages)
        deps.session_manager.touch_session(sid, state={"last_run_id": run.id})
        if deps.kernel is None:
            deps.run_manager.complete_run(
                run.id,
                output={"response": response},
                tool_calls_count=len(tool_log),
                cost_estimate=round(cost.total_usd, 6),
            )

        prompt_tokens = sum(len(message.get("content", "")) // 4 for message in ctx.messages[:-1])
        completion_tokens = len(response) // 4
        return JSONResponse(
            content={
                "id": completion_id,
                "object": "chat.completion",
                "created": int(time.time()),
                "model": active_model,
                "choices": [{
                    "index": 0,
                    "message": {"role": "assistant", "content": response},
                    "finish_reason": "stop",
                }],
                "usage": {
                    "prompt_tokens": prompt_tokens,
                    "completion_tokens": completion_tokens,
                    "total_tokens": prompt_tokens + completion_tokens,
                },
            },
            headers=response_headers,
        )

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
