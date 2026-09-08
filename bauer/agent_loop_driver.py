"""Driver do modo autônomo ``/loop``.

As operações que pertencem à sessão interativa são recebidas por callback.
Isso mantém o controle de orçamento e encerramento separado do enorme módulo
de sessão sem criar dependência circular.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Callable, Mapping

if TYPE_CHECKING:
    from .loop_skills import LoopSkill


def run_loop_mode(
    *,
    task_description: str,
    overrides: dict,
    ctx,
    router,
    state,
    console,
    fallback_clients,
    stats,
    tool_timeout_s: float,
    session_store,
    session_id,
    active_workspace,
    memprov,
    loop_skill: "LoopSkill | None" = None,
    bus=None,
    resolve_loop_config: Callable[[dict], Any],
    run_tool_loop_body: Callable[..., Any],
    print_assistant_response: Callable[..., None],
    confirm_nudge: str,
    run_loop_skill_verification: Callable[..., Any],
    print_loop_summary: Callable[..., None],
    stop_reason_labels: Mapping[str, str],
) -> None:
    """Executa o ciclo autônomo até conclusão, limite ou interrupção."""
    try:
        loop_cfg = resolve_loop_config(overrides)
    except ValueError as exc:
        console.print(f"[red]{exc}[/red]")
        return

    from .autonomous_budget import AutonomousBudget
    from .headless_approval import HeadlessApprovalConfig, HeadlessApprovalEngine
    from .tool_guardrails import ToolCallGuardrailController
    from .usage_pricing import format_cost

    budget = AutonomousBudget(
        max_cost_usd=loop_cfg.max_cost_usd,
        max_wall_seconds=loop_cfg.max_minutes * 60,
        max_tool_calls=loop_cfg.max_tool_calls,
    )
    guardrail = ToolCallGuardrailController(bus=bus, session_id=str(session_id or ""))
    engine = HeadlessApprovalEngine(
        HeadlessApprovalConfig(
            mode=loop_cfg.approval_mode,
            risk_threshold=loop_cfg.approval_risk_threshold,
        )
    )

    console.print(
        f"[cyan]▶ /loop iniciado[/cyan] [dim]| aprovação: {loop_cfg.approval_mode} | "
        f"até {loop_cfg.max_minutes}m / {loop_cfg.max_tool_calls} tool calls / "
        f"{format_cost(loop_cfg.max_cost_usd)}. Ctrl+C interrompe.[/dim]"
    )

    router._approval_callback = engine.make_approval_callback()
    ctx.add_user(task_description)

    round_num = 0
    confirm_pending = False
    warned_80 = False
    stop_reason = "completed"
    all_tool_log: list[dict] = []

    try:
        while True:
            if budget.is_exhausted:
                stop_reason = "budget_exhausted"
                break

            round_num += 1
            outcome = run_tool_loop_body(
                ctx=ctx,
                router=router,
                state=state,
                console=console,
                fallback_clients=fallback_clients,
                stats=stats,
                tool_timeout_s=tool_timeout_s,
                session_store=session_store,
                session_id=session_id,
                active_workspace=active_workspace,
                turn_input_text=task_description,
                memprov=memprov,
                budget=budget,
                guardrail=guardrail,
            )
            all_tool_log.extend(outcome.tool_log)

            snap = budget.snapshot()
            minutes, seconds = divmod(int(snap.elapsed_seconds), 60)
            console.print(
                f"[dim][loop] rodada {round_num} | {snap.tool_calls} tool calls "
                f"({snap.tool_calls}/{snap.max_tool_calls}) | {minutes}m{seconds:02d}s/"
                f"{loop_cfg.max_minutes}m | {format_cost(snap.cost_usd)}/"
                f"{format_cost(snap.max_cost_usd)}[/dim]"
            )
            if not warned_80 and budget.is_warning:
                console.print("[yellow]⚠ /loop perto do limite de orçamento (≥80%).[/yellow]")
                warned_80 = True

            if outcome.kind == "final":
                print_assistant_response(console, outcome.display, outcome.turn_cost_line)
                if not outcome.tool_log:
                    if confirm_pending:
                        stop_reason = "completed"
                        break
                    confirm_pending = True
                    ctx.add_user(confirm_nudge)
                else:
                    confirm_pending = False
                continue

            confirm_pending = False
            if outcome.kind == "tool_limit":
                continue
            if outcome.kind in (
                "loop_hard_stop", "guardrail_halt", "provider_error",
                "empty_response", "interrupted", "budget_exhausted",
            ):
                stop_reason = outcome.kind
                break
    finally:
        router._approval_callback = None

    verify_result = None
    if loop_skill is not None and stop_reason == "completed":
        verify_result = run_loop_skill_verification(
            loop_skill=loop_skill,
            active_workspace=active_workspace,
            ctx=ctx,
            router=router,
            state=state,
            console=console,
            fallback_clients=fallback_clients,
            stats=stats,
            tool_timeout_s=tool_timeout_s,
            session_store=session_store,
            session_id=session_id,
            memprov=memprov,
            budget=budget,
            guardrail=guardrail,
        )
        if verify_result is not None and not verify_result.ok:
            stop_reason = "verification_failed"

    print_loop_summary(console, stop_reason, round_num, budget, all_tool_log, task_description)

    if stop_reason != "completed":
        try:
            from .incidents import record_incident

            snap = budget.snapshot()
            record_incident(
                "autonomous_loop_stopped",
                reason=stop_reason,
                task_description=task_description[:200],
                rounds=round_num,
                elapsed_seconds=round(snap.elapsed_seconds, 1),
                tool_calls=snap.tool_calls,
                llm_calls=snap.llm_calls,
                cost_usd=round(snap.cost_usd, 4),
            )
        except Exception:
            pass

    try:
        from .kanban_store import KanbanStore

        kanban_store = KanbanStore(active_workspace)
        kanban_store.append_event(
            task_id="_loop",
            event_type="autonomous_loop_stopped",
            actor="loop",
            message=f"{stop_reason}: {task_description[:120]}",
            metadata=budget.to_dict(),
        )
    except Exception:
        pass

    if loop_skill is not None:
        try:
            from pathlib import Path

            from .decision_memory import DecisionMemory

            snap = budget.snapshot()
            decision_memory = DecisionMemory(db_path=Path(active_workspace) / "decisions.db")
            if verify_result is not None:
                decision_outcome = "good" if verify_result.ok else "bad"
                score = 1.0 if verify_result.ok else 0.0
                verify_line = f" | verificação: {verify_result.summary}"
            elif stop_reason == "completed":
                decision_outcome, score, verify_line = "good", 0.5, " | sem verificação configurada"
            elif stop_reason in (
                "provider_error", "empty_response", "guardrail_halt", "verification_failed",
            ):
                decision_outcome, score, verify_line = "bad", 0.0, ""
            else:
                decision_outcome, score, verify_line = "neutral", 0.5, ""

            tool_names = sorted({entry.get("tool", "?") for entry in all_tool_log})
            decision_memory.record(
                context=task_description[:2000],
                decision=(
                    f"loop-skill '{loop_skill.name}': "
                    f"{stop_reason_labels.get(stop_reason, stop_reason)} "
                    f"em {round_num} rodada(s), {snap.tool_calls} tool calls, "
                    f"{format_cost(snap.cost_usd)}{verify_line}"
                ),
                outcome=decision_outcome,
                tags=[loop_skill.name, "loop-skill"] + tool_names,
                score=score,
            )
        except Exception:
            pass
