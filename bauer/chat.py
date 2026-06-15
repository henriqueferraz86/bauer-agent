"""Loop de chat interativo do Bauer Agent (Fase 2).

Sem tools, sem shell, sem RAG, sem memória persistente.
Conversa com modelo local via Ollama com contexto limitado pelo ContextManager.

Comandos internos do chat:
  /exit, /quit, /sair   — encerra sessão
  /clear, /limpar       — limpa histórico
  /status               — mostra uso de tokens
"""

from __future__ import annotations

import sys

from rich.console import Console
from .indicators import show_header, show_step, spinning
from .indicators import spinning
from rich.rule import Rule

from .context_manager import ContextManager
from .ollama_client import OllamaClient, OllamaError

_SYSTEM_PROMPT = (
    "Voce e o Bauer, um assistente tecnico local e direto. "
    "Responda em portugues quando o usuario escrever em portugues."
)

_EXIT_CMDS = {"/exit", "/quit", "/sair"}
_CLEAR_CMDS = {"/clear", "/limpar"}
_STATUS_CMDS = {"/status", "/stats"}


def run_chat_session(
    client: OllamaClient,
    model_name: str,
    applied_context: int,
    console: Console,
) -> None:
    """Loop REPL de chat. Encerra com /exit, /quit ou Ctrl-C."""
    ctx = ContextManager(applied_context=applied_context, system_prompt=_SYSTEM_PROMPT)

    console.print(Rule(f"[bold]Bauer Chat[/bold] — {model_name}"))
    console.print(
        f"[dim]Contexto: {applied_context} tokens | "
        f"Budget historico: {ctx.budget} tokens | "
        f"/exit para sair | /clear para limpar | /status para stats[/dim]\n"
    )

    while True:
        try:
            user_input = console.input("[bold cyan]voce>[/bold cyan] ").strip()
        except (KeyboardInterrupt, EOFError):
            console.print("\n[dim]Encerrando sessao.[/dim]")
            break

        if not user_input:
            continue

        if user_input.lower() in _EXIT_CMDS:
            console.print("[dim]Encerrando sessao.[/dim]")
            break

        if user_input.lower() in _CLEAR_CMDS:
            ctx.clear()
            console.print("[dim]Historico limpo.[/dim]")
            continue

        if user_input.lower() in _STATUS_CMDS:
            console.print(
                f"[dim]Historico: {len(ctx.messages)} mensagem(ns) | "
                f"~{ctx.used_tokens} tokens usados / {ctx.budget} budget[/dim]"
            )
            continue

        ctx.add_user(user_input)

        collected: list[str] = []
        error_occurred = False

        try:
            # Spinner APENAS enquanto coleta — sem sys.stdout.write durante
            with spinning(f"Consultando {model_name}...", console=console, transient=True) as sp:
                for chunk in client.chat_stream(model_name, ctx.get_payload()):
                    collected.append(chunk)
                    sp.update(f"Recebendo resposta... [{len(''.join(collected))} chars]")
        except OllamaError as exc:
            error_occurred = True
            console.print(f"\n[red]Erro Ollama:[/red] {exc}")
            console.print("[dim]Rode 'bauer doctor' para diagnostico completo.[/dim]")
            if ctx.messages and ctx.messages[-1]["role"] == "user":
                ctx.messages.pop()
        except KeyboardInterrupt:
            console.print("\n[dim]Interrompido pelo usuario.[/dim]")
        else:
            # Exibe resposta COMPLETA apos o spinner fechar (sem conflito)
            full_response = "".join(collected)
            sys.stdout.write("\033[32mbauer>\033[0m ")
            sys.stdout.write(full_response)
            sys.stdout.write("\n")
            sys.stdout.flush()

        if not error_occurred and collected:
            ctx.add_assistant("".join(collected))

        sys.stdout.flush()
