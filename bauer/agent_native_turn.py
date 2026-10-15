"""Execução de um turno de function calling nativo, com dependências injetadas."""

from __future__ import annotations

import json
import logging
from typing import Any, Callable


def run_native_turn(
    ctx: Any,
    router: Any,
    client: Any,
    model_name: str,
    tool_log: list[dict],
    *,
    guardrail: Any = None,
    deduper: Any = None,
    tool_timeout_s: float = 0.0,
    trace: Any = None,
    max_tool_turns: int,
    args_signature: Callable[[object], str],
    context_result: Callable[[str, str], tuple[str, bool]],
    report_cost: Callable[[Any, str], None],
    call_with_timeout: Callable[..., tuple[str, bool]],
    unsupported_error: type[Exception],
    is_unsupported_error: Callable[[Exception], bool],
) -> str | None:
    """Executa tools nativas e atualiza contexto/log in-place."""
    schemas = router.get_tool_schemas()
    # Usage belongs to this provider call. A previous successful call must not
    # be charged again if this one fails before receiving a response.
    previous_usage = None
    cleared_usage = False
    try:
        previous_usage = getattr(client, "last_usage", None)
        if isinstance(previous_usage, dict):
            previous_usage = previous_usage.copy()
        client.last_usage = None
        cleared_usage = True
    except Exception:
        logging.getLogger("bauer.agent_native_turn").debug("Provider usage reset unavailable", exc_info=True)

    def report_current_cost() -> None:
        usage_unavailable = False
        try:
            current_usage = getattr(client, "last_usage", None)
        except Exception:
            logging.getLogger("bauer.agent_native_turn").debug("Provider usage unavailable", exc_info=True)
            current_usage = None
            usage_unavailable = True
        if cleared_usage or usage_unavailable or (isinstance(current_usage, dict) and current_usage and current_usage != previous_usage):
            report_cost(client, model_name)
        else:
            # Read-only telemetry may still contain the previous call's usage.
            # Record this attempt as unknown without charging that stale value.
            try:
                from .cost_meter import provider_from_client, report_llm_cost
                report_llm_cost(provider_from_client(client), model_name, None)
            except Exception:
                logging.getLogger("bauer.agent_native_turn").debug("Provider usage reporting failed", exc_info=True)

    try:
        message = client.chat_with_tools(model_name, ctx.get_payload(), tools=schemas)
    except BaseException as exc:
        report_current_cost()
        if isinstance(exc, Exception) and is_unsupported_error(exc):
            raise unsupported_error(str(exc)) from exc
        try:
            # Only this request boundary is eligible for in-turn retry; errors
            # from schemas, tool dispatch and context handling are not requests.
            exc._bauer_native_request_failed = True  # type: ignore[attr-defined]
        except Exception:
            logging.getLogger("bauer.agent_native_turn").debug("Provider error marker unavailable", exc_info=True)
        # Provider failure is not another tool round. Let the shared retry/
        # fallback boundary classify it instead of exhausting the turn budget.
        raise
    report_current_cost()
    calls = message.get("tool_calls") or []
    content = message.get("content") or ""
    if not calls:
        if content.strip():
            ctx.add_assistant(content)
        return content
    if len(tool_log) >= max_tool_turns:
        result = content or "[Limite de tool calls atingido]"
        ctx.add_assistant(result)
        return result
    ctx.messages.append({"role": "assistant", "content": content, "tool_calls": calls})
    for call in calls:
        call_id = call.get("id", "call_0")
        function = call.get("function", {})
        name = function.get("name", "?")
        args = _parse_arguments(function.get("arguments", "{}"))
        blocked = False
        if guardrail is not None:
            pre = guardrail.before_call(name, args)
            if pre.should_halt:
                ctx.add_user(pre.message)
                result = f"[BLOCKED] {pre.message}"
                blocked = True
                tool_log.append({"tool": name, "args_sig": args_signature(args), "result": result[:300]})
                ctx.messages.append({"role": "tool", "tool_call_id": call_id, "content": result})
                if pre.action == "halt":
                    return pre.message
        if not blocked:
            failed = False
            cached = deduper.check(name, args) if deduper is not None else None
            if cached is not None:
                result = cached
            else:
                try:
                    from .delta_stream import emit_tool
                    emit_tool(name)
                except Exception:
                    pass
                span = trace.span(f"tool:{name}", input={"args": args}) if trace is not None else None
                try:
                    result, timed_out = call_with_timeout(
                        lambda: router.execute_native_call(name, args), tool_timeout_s, name,
                    )
                    failed = timed_out
                    if span is not None:
                        span.end(output=str(result)[:500], level="WARNING" if timed_out else "DEFAULT")
                except Exception as exc:
                    from .tool_router import SandboxError, ToolError
                    if not isinstance(exc, (ToolError, SandboxError)):
                        raise
                    result = f"[Erro: {exc}]"
                    failed = True
                    if span is not None:
                        span.end(output=result, level="ERROR")
                if deduper is not None:
                    deduper.record(name, args, result, failed=failed)
            if guardrail is not None:
                post = guardrail.after_call(name, args, result, failed=failed)
                if post.action == "warn":
                    ctx.add_user(post.message)
        ctx_result, _ = context_result(name, result)
        tool_log.append({"tool": name, "args_sig": args_signature(args), "result": result[:300]})
        ctx.messages.append({"role": "tool", "tool_call_id": call_id, "content": ctx_result})
    return None


def _parse_arguments(raw: str) -> dict:
    try:
        value = json.loads(raw or "{}")
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}
