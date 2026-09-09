"""Execução de um turno de function calling nativo, com dependências injetadas."""

from __future__ import annotations

import json
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
    try:
        message = client.chat_with_tools(model_name, ctx.get_payload(), tools=schemas)
    except Exception as exc:
        if is_unsupported_error(exc):
            raise unsupported_error(str(exc)) from exc
        return None
    report_cost(client, model_name)
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
