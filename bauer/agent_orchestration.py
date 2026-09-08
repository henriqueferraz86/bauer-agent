"""Fluxo de orquestração multi-etapa usado pela sessão interativa do agente."""

from __future__ import annotations

from typing import Any

from rich.console import Console
from rich.rule import Rule


def run_orchestrator_inline(user_input: str, orchestrator: Any, console: Console) -> str:
    """Executa o plano no chat, exibindo progresso e devolvendo a síntese final."""
    console.print(Rule("[bold dim]Orquestrador[/bold dim]"))
    console.print("[yellow dim]Planejando passos...[/yellow dim]")

    try:
        steps = orchestrator.plan(user_input)
        orchestrator.save_plan(user_input, steps)
    except Exception as exc:  # noqa: BLE001 - limite de interação CLI
        console.print(f"[red]Erro no planejamento: {exc}[/red]")
        return ""

    if not steps:
        return ""

    batches = orchestrator._topological_batches(steps)
    total_waves = len(batches)
    console.print(f"[dim]{len(steps)} passo(s) em {total_waves} onda(s)[/dim]\n")

    all_results: list[Any] = []
    done: dict[Any, Any] = {}
    for wave_idx, batch in enumerate(batches):
        pending = [step for step in batch if step["id"] not in done]
        if not pending:
            continue

        if len(pending) > 1:
            ids = ", ".join(str(step["id"]) for step in pending)
            console.print(
                f"[dim]Onda {wave_idx + 1}/{total_waves} — passos {ids} (paralelo)[/dim]"
            )
        else:
            step = pending[0]
            console.print(f"[dim]Passo {step['id']}/{len(steps)}: {step['goal']}[/dim]")

        try:
            batch_results = orchestrator.execute_parallel_steps(pending, all_results)
        except KeyboardInterrupt:
            console.print("\n[dim][orquestrador interrompido][/dim]")
            orchestrator.clear_progress(user_input)
            return ""
        except Exception as exc:  # noqa: BLE001 - uma onda não invalida as demais
            console.print(f"[red]Erro no passo {wave_idx + 1}: {exc}[/red]")
            continue

        all_results.extend(batch_results)
        orchestrator.save_progress(user_input, batch_results)
        for result in batch_results:
            done[result.id] = result
            if result.tool_log:
                tools_used = ", ".join(tool["tool"] for tool in result.tool_log)
                console.print(f"  [dim]tools: {tools_used}[/dim]")

    if not all_results:
        orchestrator.clear_progress(user_input)
        return ""

    console.print("[dim]Sintetizando...[/dim]")
    try:
        objective = steps[0].get("goal", user_input)
        final = orchestrator.synthesize(objective, all_results)
    except Exception as exc:  # noqa: BLE001 - resposta parcial é útil no CLI
        console.print(f"[red]Erro na sintese: {exc}[/red]")
        final = "\n".join(result.response for result in all_results)

    orchestrator.clear_progress(user_input)
    console.print(Rule())
    return final
