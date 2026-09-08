"""Auxiliares desacoplados do modo autônomo ``/loop``.

O driver do loop continua em :mod:`bauer.agent` enquanto a migração é feita
em etapas. Dependências de volta para o driver são recebidas como callbacks,
evitando um import circular e mantendo estes fluxos testáveis isoladamente.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Callable, Mapping

if TYPE_CHECKING:
    from .app_verify import VerifyResult
    from .loop_skills import LoopSkill


def run_loop_skill_verification(
    *, loop_skill: "LoopSkill", active_workspace, ctx, router, state, console,
    fallback_clients, stats, tool_timeout_s, session_store, session_id,
    memprov, budget, guardrail, run_tool_loop_body: Callable[..., Any],
) -> "VerifyResult | None":
    """Executa o gate de um loop-skill e, se preciso, uma única correção."""
    if not loop_skill.verify_command and not loop_skill.verify_auto:
        return None

    from .app_verify import Step, VerifyResult, verify_project

    def _run_once() -> VerifyResult:
        if loop_skill.verify_command:
            import shlex
            import subprocess

            try:
                proc = subprocess.run(
                    shlex.split(loop_skill.verify_command),
                    cwd=str(active_workspace), capture_output=True,
                    text=True, encoding="utf-8", errors="replace", timeout=300,
                )
                out = (proc.stdout or "") + (proc.stderr or "")
                ok = proc.returncode == 0
                step = Step(
                    "verify_command", [loop_skill.verify_command],
                    rc=proc.returncode, ok=ok, output=out[-2000:],
                )
                return VerifyResult(
                    str(active_workspace), "custom", ok, [step],
                    "verificação customizada ok" if ok
                    else f"verify_command falhou (rc={proc.returncode})",
                )
            except Exception as exc:
                step = Step(
                    "verify_command", [loop_skill.verify_command],
                    rc=-1, ok=False, output=str(exc)[:2000],
                )
                return VerifyResult(
                    str(active_workspace), "custom", False, [step],
                    f"erro ao rodar verify_command: {exc}",
                )
        return verify_project(active_workspace)

    console.print(f"[cyan]verificação do loop-skill '{loop_skill.name}'...[/cyan]")
    result = _run_once()
    if result.ok:
        console.print(f"[green]verificação passou:[/green] {result.summary}")
        return result

    console.print(f"[yellow]verificação falhou, tentando 1 correção:[/yellow] {result.summary}")
    ctx.add_user(
        f"A verificação automática falhou:\n{result.summary}\n\n"
        "Corrija o problema. Esta é sua ÚLTIMA chance antes da verificação final."
    )
    try:
        run_tool_loop_body(
            ctx=ctx, router=router, state=state, console=console,
            fallback_clients=fallback_clients, stats=stats,
            tool_timeout_s=tool_timeout_s, session_store=session_store,
            session_id=session_id, active_workspace=active_workspace,
            turn_input_text="", memprov=memprov, budget=budget, guardrail=guardrail,
        )
    except Exception:
        pass  # mesmo se a correção falhar, tenta verificar uma última vez

    console.print(f"[cyan]reverificando '{loop_skill.name}'...[/cyan]")
    result2 = _run_once()
    if result2.ok:
        console.print(f"[green]verificação passou após correção:[/green] {result2.summary}")
    else:
        console.print(
            f"[red]verificação falhou de novo — encerrando (sem mais tentativas):[/red] {result2.summary}"
        )
    return result2


def print_loop_summary(
    console, stop_reason: str, round_num: int, budget, all_tool_log,
    task_description: str, *, stop_reason_labels: Mapping[str, str],
) -> None:
    """Exibe o painel final do ``/loop`` sem deixar observabilidade derrubá-lo."""
    try:
        from collections import Counter

        from rich.panel import Panel

        from .usage_pricing import format_cost as format_cost

        snap = budget.snapshot()
        label = stop_reason_labels.get(stop_reason, stop_reason)
        lines = [
            f"[bold]Motivo:[/bold] {label}",
            f"[bold]Rodadas:[/bold] {round_num}",
            f"[bold]Duração:[/bold] {int(snap.elapsed_seconds)}s",
            f"[bold]Tool calls:[/bold] {snap.tool_calls}/{snap.max_tool_calls}",
            f"[bold]LLM calls:[/bold] {snap.llm_calls}/{snap.max_llm_calls}",
            f"[bold]Custo:[/bold] {format_cost(snap.cost_usd)}/{format_cost(snap.max_cost_usd)}",
        ]
        tool_counts = Counter(entry.get("tool", "?") for entry in all_tool_log)
        if tool_counts:
            lines.append(
                "[bold]Por tool:[/bold] "
                + ", ".join(f"{name}={count}" for name, count in tool_counts.most_common())
            )
        if stop_reason == "completed" and snap.tool_calls == 0:
            lines.append(
                "\n[yellow]⚠ O modelo 'concluiu' sem executar NENHUMA tool — "
                "provavelmente só respondeu em texto. Verifique se a tarefa "
                "foi mesmo executada; se não, reformule com passos concretos "
                "(ex.: 'use run_command para ...').[/yellow]"
            )
        console.print(Panel("\n".join(lines), title="/loop encerrado", border_style="cyan"))
    except Exception:
        pass  # observabilidade nunca derruba o /loop


def handle_loop_skill_cmd(
    user_input: str, console, *, ctx, router, state,
    fallback_clients, stats, tool_timeout_s, session_store, session_id,
    active_workspace, memprov, run_loop_mode: Callable[..., None],
) -> None:
    """Executa ``/loop-skill list`` ou ``/loop-skill run``."""
    from .loop_skills import LoopSkillNotFound, LoopSkillRegistry

    rest = user_input.split(None, 1)
    sub = rest[1].strip() if len(rest) > 1 else ""
    registry = LoopSkillRegistry()

    if not sub or sub.lower() == "list":
        loop_skills = registry.list()
        if not loop_skills:
            console.print(
                "[dim]Nenhum loop-skill instalado. Crie um YAML em "
                "~/.bauer/loop_skills/ — veja o formato no plano/README.[/dim]"
            )
            return
        for skill in loop_skills:
            console.print(
                f"[cyan]{skill.name}[/cyan] — {skill.description} "
                f"[dim]({skill.trigger_pattern})[/dim]"
            )
        return

    if sub.lower().startswith("run "):
        name_and_rest = sub[4:].strip()
        name, _, free_text = name_and_rest.partition(" ")
        try:
            skill = registry.get(name)
        except LoopSkillNotFound as exc:
            console.print(f"[red]{exc}[/red]")
            return
        task = free_text.strip() or skill.task_template
        console.print(f"[cyan]rodando loop-skill '{skill.name}' manualmente...[/cyan]")
        run_loop_mode(
            task_description=task,
            overrides={
                "max_minutes": str(skill.max_minutes),
                "max_tool_calls": str(skill.max_tool_calls),
                "max_cost_usd": str(skill.max_cost_usd),
                "approval_mode": skill.approval_mode,
                "approval_risk_threshold": str(skill.approval_risk_threshold),
            },
            ctx=ctx, router=router, state=state, console=console,
            fallback_clients=fallback_clients, stats=stats,
            tool_timeout_s=tool_timeout_s, session_store=session_store,
            session_id=session_id, active_workspace=active_workspace,
            memprov=memprov, loop_skill=skill,
        )
        return

    console.print("[yellow]Uso:[/yellow] /loop-skill list | /loop-skill run <nome> [texto livre]")
