"""Turno nativo da sessão interativa, separado do orquestrador principal."""

from __future__ import annotations

import json
import time


def run_native_interactive(ctx, router, client, model_name, console, cli_log, deduper, calls_left,
                           *, guardrail=None, streamer=None, budget=None, parse_tool,
                           thinking_status, tool_status, format_display, context_result, args_signature):
    schemas = router.get_tool_schemas()
    try:
        if streamer is not None:
            try:
                message = client.chat_with_tools(model_name, ctx.get_payload(), tools=schemas,
                                                 on_delta=streamer.on_delta)
            except TypeError as exc:
                if "on_delta" not in str(exc):
                    raise
                with thinking_status(console, model_name):
                    message = client.chat_with_tools(model_name, ctx.get_payload(), tools=schemas)
            streamer.on_round()
        else:
            with thinking_status(console, model_name):
                message = client.chat_with_tools(model_name, ctx.get_payload(), tools=schemas)
    except Exception:
        raise
    calls = message.get("tool_calls") or []
    content = message.get("content") or ""
    if not calls and content:
        action = parse_tool(content, router)
        if action is not None:
            calls = [{"id": "bridge-content-0", "function": {
                "name": action["action"],
                "arguments": json.dumps(action.get("args", {}), ensure_ascii=False),
            }}]
            content = ""
    if not calls:
        if streamer is not None and getattr(streamer, "on_final", None):
            streamer.on_final()
        return "final", content
    if streamer is not None and getattr(streamer, "on_tool", None):
        streamer.on_tool(str(calls[0].get("function", {}).get("name", "?")))
    ctx.messages.append({"role": "assistant", "content": content, "tool_calls": calls})
    for call in calls:
        call_id = call.get("id", "call_0")
        function = call.get("function", {})
        name = function.get("name", "?")
        try:
            args = json.loads(function.get("arguments", "{}") or "{}")
            if not isinstance(args, dict):
                args = {}
        except json.JSONDecodeError:
            args = {}
        if budget is not None:
            if budget.remaining_tool_calls() <= 0:
                result = "[BLOCKED] tool call budget exhausted"
                cli_log.append({"tool": name, "args_sig": args_signature(args), "result": result, "budget_accounted": True})
                ctx.messages.append({"role": "tool", "tool_call_id": call_id, "content": result})
                continue
            budget.consume_tool_call()
        blocked = False
        if guardrail is not None:
            pre = guardrail.before_call(name, args)
            if pre.should_halt:
                ctx.add_user(pre.message)
                result = f"[BLOCKED] {pre.message}"
                blocked = True
                ctx.messages.append({"role": "tool", "tool_call_id": call_id, "content": result})
                cli_log.append({"tool": name, "args_sig": args_signature(args), "result": result[:300], "budget_accounted": budget is not None})
                if pre.action == "halt":
                    console.print(f"  [yellow]⛔ guardrail:[/yellow] {pre.message}")
                    return "guardrail_halt", pre.message
        if not blocked:
            failed = False
            cached = deduper.check(name, args) if deduper is not None else None
            if cached is not None:
                result = cached
            else:
                started = time.perf_counter()
                try:
                    with tool_status(console, name):
                        result = router.execute_native_call(name, args)
                except Exception as exc:
                    from .tool_router import SandboxError, ToolError
                    if not isinstance(exc, (ToolError, SandboxError)):
                        raise
                    result, failed = f"[Erro: {exc}]", True
                elapsed = int((time.perf_counter() - started) * 1000)
                if deduper is not None:
                    deduper.record(name, args, result, failed=failed)
            if guardrail is not None:
                post = guardrail.after_call(name, args, result, failed=failed)
                if post.action == "warn":
                    ctx.add_user(post.message)
                elif post.should_halt:
                    ctx.add_user(post.message)
                    return "guardrail_halt", post.message
            display = format_display(name, result)
            try:
                from .ui import tool_block
                console.print(tool_block(name, display, status=("fail" if failed else "ok"),
                                         elapsed_ms=locals().get("elapsed"), result=result))
            except Exception:
                console.print(f"  [dim]→[/dim] [cyan]{name}[/cyan]  {display}")
        ctx_result, _ = context_result(name, result)
        cli_log.append({"tool": name, "args_sig": args_signature(args), "result": result[:300], "budget_accounted": budget is not None})
        ctx.messages.append({"role": "tool", "tool_call_id": call_id, "content": ctx_result})
    console.print()
    return "continue", None
