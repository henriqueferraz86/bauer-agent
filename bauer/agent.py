"""Loop do agente com Tool Bridge (Fase 6) e Roteamento Inteligente.

Fluxo por turno do usuário:
  1. Usuário envia mensagem
  2. Roteador classifica: direct | code | reasoning | tool
  3. Redireciona para o modelo adequado
  4. Modelo responde (texto ou JSON de tool)
  5. Se JSON → valida e executa tool → resultado volta ao modelo → repete
  6. Se texto → exibe ao usuário → aguarda próxima mensagem

Proteção anti-loop: MAX_TOOL_TURNS por turno do usuário.
Nenhum tool call é silencioso — cada execução é exibida ao usuário.
"""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING, Any

from rich.console import Console
from rich.rule import Rule

from .context_manager import ContextManager
from .machine_id import machine_id as get_machine_id
from .model_router import ModelRouter, RouteKind
from .ollama_client import OllamaClient, OllamaError
from .openai_client import OpenAIClientError
from .performance_tracker import SessionStats
from .skill_registry import SkillRegistry
from .tool_router import SandboxError, ToolError, ToolRouter

if TYPE_CHECKING:
    from .orchestrator import AgentOrchestrator
    from .session_store import SessionStore

_EXIT_CMDS = {"/exit", "/quit", "/sair"}
_CLEAR_CMDS = {"/clear", "/limpar"}
_STATUS_CMDS = {"/status", "/stats"}
_MODEL_CMDS = {"/model", "/modelo"}
_SESSIONS_CMDS = {"/sessions", "/sessoes"}
_SPEC_CMDS = {"/spec", "/specs"}
_KANBAN_CMDS = {"/kanban", "/board", "/tasks", "/task"}   # bare /task → board
_PROJECT_CMDS = {"/project", "/proj", "/projeto"}
_AGENT_MGR_CMDS = {"/agents", "/agent list", "/agent create", "/agent delete"}  # gestão de agents

# Sub-comandos exibidos no menu de autocomplete
_SLASH_BASE = [
    "/exit",
    "/clear",
    "/status",
    "/model",
    "/sessions",
    "/spec",
    "/spec new",
    "/spec list",
    "/kanban",
    "/task",
    "/task add",
    "/task list",
    "/task start",
    "/task done",
    "/task block",
    "/memory",
    "/memory search",
    "/memory list",
    "/memory note",
    "/project",
    "/agents",
    "/agent list",
    "/agent create",
    "/agent delete",
]


# ─── Autocomplete (prompt_toolkit) ───────────────────────────────────────────

_SLASH_DESCRIPTIONS: dict[str, str] = {
    "/exit":           "encerra a sessão",
    "/clear":          "limpa o histórico",
    "/status":         "tokens usados / budget",
    "/model":          "trocar provider/modelo (abre seletor)",
    "/sessions":       "lista sessões salvas",
    "/spec":           "lista specs do projeto",
    "/spec new":       "cria novo spec (wizard)",
    "/spec list":      "lista todos os specs",
    "/kanban":         "exibe o Kanban board (TASKS.md)",
    "/task":           "Kanban board (sem args) ou sub-comandos",
    "/task add":       "adiciona tarefa: /task add <título>",
    "/task list":      "lista tarefas com status",
    "/task start":     "inicia tarefa: /task start <id>",
    "/task done":      "conclui tarefa: /task done <id>",
    "/task block":     "bloqueia tarefa: /task block <id>",
    "/memory":         "lista arquivos de memória",
    "/memory search":  "busca na memória: /memory search <query>",
    "/memory list":    "lista arquivos de memória",
    "/memory note":    "adiciona nota: /memory note <texto>",
    "/project":        "mostra PROJECT.md e resumo de tarefas",
    "/agents":         "lista agents criados",
    "/agent list":     "lista agents criados",
    "/agent create":   "cria novo agent (wizard interativo)",
    "/agent delete":   "remove agent: /agent delete <nome>",
}

try:
    from prompt_toolkit import PromptSession
    from prompt_toolkit.completion import CompleteEvent, Completer, Completion
    from prompt_toolkit.completion import ThreadedCompleter
    from prompt_toolkit.document import Document as PtDocument
    from prompt_toolkit.formatted_text import HTML
    from prompt_toolkit.history import InMemoryHistory
    from prompt_toolkit.key_binding import KeyBindings
    from prompt_toolkit.styles import Style as PtStyle

    class _SlashCompleter(Completer):
        """Completer que age apenas quando o input começa com '/'."""

        def get_completions(self, document: "PtDocument", complete_event: "CompleteEvent"):
            text = document.text_before_cursor
            if not text.startswith("/"):
                return

            candidates = list(_SLASH_BASE)
            # IDs dos specs dinamicamente
            try:
                from .spec_manager import SpecManager
                for s in SpecManager().list_specs():
                    candidates.append(f"/spec {s.id}")
            except Exception:
                pass

            for candidate in candidates:
                if candidate.startswith(text):
                    yield Completion(
                        candidate,                    # texto completo a inserir
                        start_position=-len(text),    # substitui o que foi digitado
                        display=candidate,
                        display_meta=_SLASH_DESCRIPTIONS.get(candidate, ""),
                    )

    _PT_STYLE = PtStyle.from_dict({
        "prompt":      "bold ansicyan",
        "completion-menu.completion":         "bg:#313244 #cdd6f4",
        "completion-menu.completion.current": "bg:#89b4fa #1e1e2e bold",
        "completion-menu.meta.completion":         "bg:#313244 #6c7086",
        "completion-menu.meta.completion.current": "bg:#89b4fa #1e1e2e",
    })

    def _make_slash_kb() -> "KeyBindings":
        """Key binding: '/' insere o caractere E abre o menu de completions."""
        kb = KeyBindings()

        @kb.add("/")
        def _on_slash(event):
            event.current_buffer.insert_text("/")
            event.current_buffer.start_completion(select_first=False)

        return kb

    def _make_prompt_session() -> "PromptSession":
        # Força saída VT100 (compatível com VSCode / Windows Terminal / xterm)
        try:
            import sys as _sys
            from prompt_toolkit.output import create_output
            _output = create_output(stdout=_sys.stdout)
        except Exception:
            _output = None  # deixa prompt_toolkit auto-detectar

        return PromptSession(
            completer=_SlashCompleter(),
            complete_while_typing=True,
            history=InMemoryHistory(),
            style=_PT_STYLE,
            mouse_support=False,
            key_bindings=_make_slash_kb(),
            output=_output,
        )

    _PT_AVAILABLE = True

except ImportError:
    _PT_AVAILABLE = False
    _make_prompt_session = None  # type: ignore[assignment]
    _PT_STYLE = None             # type: ignore[assignment]

MAX_TOOL_TURNS = 10


_SPEC_FORMAT_HINT = """
# SPEC-DRIVEN DEVELOPMENT
Quando o usuario pedir para criar um spec, gere um arquivo YAML em specs/<id>.yaml com este formato:

id: nome-do-feature
title: Título Descritivo
version: "1.0.0"
status: draft
created: <data-hoje>
purpose: |
  O que este feature faz e por que existe (1-3 frases).
behavior:
  - Regra 1 que a implementação DEVE respeitar
  - Regra 2
interface:
  inputs:
    - name: param
      type: str
      required: true
      description: descrição
  outputs:
    - name: resultado
      type: str
      description: descrição
acceptance_criteria:
  - Given X, when Y, then Z
linked_files:
  - bauer/arquivo.py
  - tests/test_arquivo.py

Status válidos: draft | review | approved | implemented | deprecated
Use write_file para salvar em specs/<id>.yaml
Diga ao usuario para rodar "bauer spec status <id> approved" quando o spec estiver pronto para implementar."""


def _specs_section(specs_dir: str = "specs") -> str:
    """Retorna seção de specs para injeção no system prompt.

    Carrega specs aprovados/implementados de `specs/` e formata como
    contratos do projeto. Inclui instruções de criação para que o agente
    saiba gerar specs quando solicitado em linguagem natural.
    Falhas silenciosas — specs são contexto adicional, não bloqueantes.
    """
    try:
        from .spec_manager import SpecManager
        mgr = SpecManager(specs_dir)
        ctx = mgr.specs_context(compact=True)
        # Sempre inclui o hint de formato (para criar specs via linguagem natural)
        # e os specs existentes se houver
        result = _SPEC_FORMAT_HINT
        if ctx:
            result += f"\n\n{ctx}"
        return result
    except Exception:
        return _SPEC_FORMAT_HINT


def _build_system_prompt(router: ToolRouter) -> str:
    """Monta o system prompt com a lista de tools disponíveis."""
    from datetime import datetime, timezone
    now = datetime.now()
    now_utc = datetime.now(timezone.utc)
    timestamp = (
        f"{now.strftime('%A, %d de %B de %Y')} — "
        f"{now.strftime('%H:%M')} (local) / "
        f"{now_utc.strftime('%H:%M')} UTC"
    )

    tool_infos = []
    for name in router.available_tools():
        info = router.tool_info(name)
        args_str = ", ".join(f"{k}" for k in info["args"])
        tool_infos.append(f'  {name}({args_str}) — {info["description"]}')
    tools_section = "\n".join(tool_infos)

    tool_names = ", ".join(router.available_tools())

    return (
        "Voce e o Bauer Agent, assistente de desenvolvimento local.\n\n"
        f"Data e hora atual: {timestamp}\n\n"
        "# REGRA PRINCIPAL\n"
        "Responda SEMPRE em texto normal (portugues). NUNCA use JSON para respostas de conversa.\n\n"
        "# FERRAMENTAS DISPONIVEIS\n"
        f"Voce pode usar estas ferramentas: {tool_names}\n"
        f"{tools_section}\n\n"
        "# QUANDO USAR FERRAMENTA\n"
        "Use UMA ferramenta SOMENTE se a pergunta exigir ler/escrever arquivos ou listar diretorios.\n"
        "Nesse caso, responda SOMENTE com o JSON abaixo (sem texto antes ou depois):\n"
        '{"action": "NOME_DA_TOOL", "args": {"parametro": "valor"}}\n\n'
        "# QUANDO NAO USAR FERRAMENTA (maioria dos casos)\n"
        "Para saudacoes, perguntas, explicacoes, codigo, matematica, conversas — responda em TEXTO PURO.\n\n"
        "EXEMPLOS CORRETOS:\n"
        "  Pergunta: 'oi'                  -> resposta: 'Ola! Como posso ajudar?'\n"
        "  Pergunta: 'que horas sao?'       -> resposta: 'Sao X horas.'\n"
        "  Pergunta: 'explique docker'      -> resposta em texto explicando docker\n"
        "  Pergunta: 'liste os arquivos'    -> {\"action\": \"list_dir\", \"args\": {\"path\": \".\"}}\n"
        "  Pergunta: 'leia o config.yaml'   -> {\"action\": \"read_file\", \"args\": {\"path\": \"config.yaml\"}}\n\n"
        "ERRADO (nunca faca isso):\n"
        "  Pergunta: 'oi' -> {\"action\": \"resposta\", ...}  <- ERRADO, use texto puro\n\n"
        "Depois de executar uma ferramenta, resuma o resultado em texto normal.\n"
        "Responda sempre em portugues."
        + _specs_section()
    )


def _extract_text_from_pseudo_json(response: str) -> str | None:
    """Se o modelo respondeu com {"action": "resposta/text", "args": {"conteudo": "..."}}
    extrai apenas o texto. Fallback para modelos pequenos que abusam do formato JSON."""
    import json as _json
    try:
        obj = _json.loads(response.strip())
        if not isinstance(obj, dict):
            return None
        # Ação que não é uma tool real = o modelo está respondendo em texto via JSON
        args = obj.get("args", {})
        for key in ("conteudo", "content", "text", "resposta", "message", "mensagem", "response"):
            if key in args and isinstance(args[key], str):
                return args[key]
    except Exception:
        pass
    return None


def _try_parse_tool(response: str, router: ToolRouter) -> dict | None:
    """Tenta parsear a resposta como tool action. Retorna dict ou None.

    Estratégias (em ordem):
    1. JSON puro ou bloco markdown — resposta inteira é a action
    2. JSON no início da resposta (modelo misturou JSON + texto) — extrai só o JSON
    Em ambos os casos, só retorna se a action for uma tool conhecida.
    """
    import json as _json

    available = set(router.available_tools())
    stripped = response.strip()

    # Estratégia 1: resposta inteira é JSON (ou bloco markdown)
    try:
        parsed = router._parse(stripped)
        if isinstance(parsed, dict) and parsed.get("action") in available:
            return parsed
    except Exception:
        pass

    # Estratégia 2: JSON válido no início seguido de texto extra
    if stripped.startswith("{"):
        try:
            decoder = _json.JSONDecoder()
            obj, _ = decoder.raw_decode(stripped)
            if isinstance(obj, dict) and obj.get("action") in available:
                return obj
        except Exception:
            pass

    return None


def _try_parse_tools_batch(response: str, router: ToolRouter) -> list[dict] | None:
    """Extrai TODOS os tool calls de uma resposta (pode conter múltiplos JSONs por linha).

    Quando o modelo emite vários JSONs em linhas separadas, retorna todos de uma vez
    para evitar que o contexto cresça a cada round-trip individual.
    Retorna lista com ao menos 1 item, ou None se não houver tool call válido.
    """
    import json as _json

    available = set(router.available_tools())
    stripped = response.strip()
    actions: list[dict] = []

    # Tenta extrair um JSON por linha (modelo batch-tool-call)
    for line in stripped.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            obj = _json.loads(line)
            if isinstance(obj, dict) and obj.get("action") in available:
                actions.append(obj)
        except _json.JSONDecodeError:
            # Linha pode ser parte de um JSON multi-linha — ignora
            pass

    if actions:
        return actions

    # Fallback: lógica original (único JSON, possivelmente com texto ao redor)
    single = _try_parse_tool(response, router)
    return [single] if single is not None else None


# Limite de chars do resultado de uma tool que vai para o contexto.
# Evita overflow de contexto quando o modelo lê muitos arquivos grandes.
_MAX_TOOL_RESULT_IN_CTX = 3000


def _collect_response(
    client: OllamaClient,
    model_name: str,
    payload: list[dict],
) -> str:
    """Coleta a resposta completa do modelo (sem streaming ao usuário)."""
    parts: list[str] = []
    for chunk in client.chat_stream(model_name, payload):
        parts.append(chunk)
    response = "".join(parts)

    # Escanear resposta do modelo por segredos antes de processar/logar
    try:
        from .secrets_scanner import scan as _scan
        sr = _scan(response, redact=True)
        if sr.found:
            import logging as _log
            names = ", ".join(set(m["name"] for m in sr.matches))
            _log.getLogger(__name__).warning(
                "[secrets_scanner] Segredos na resposta do modelo: %s. Redagidos.", names
            )
            response = sr.redacted_text
    except Exception:
        pass

    return response


def _run_native_tool_turn(
    ctx,
    router: ToolRouter,
    client,
    model_name: str,
    tool_log: list[dict],
) -> str | None:
    """Executa um turno usando native function calling (OpenAI format).

    Retorna a resposta final de texto quando o modelo para de chamar tools,
    ou None se deve continuar no loop.
    Modifica ctx e tool_log in-place.
    """
    import json as _json
    schemas = router.get_tool_schemas()
    messages = ctx.get_payload()

    try:
        msg = client.chat_with_tools(model_name, messages, tools=schemas)
    except Exception:
        return None  # fallback para Tool Bridge

    tool_calls = msg.get("tool_calls") or []
    content = msg.get("content") or ""

    if not tool_calls:
        # Modelo respondeu sem chamar tools — retorna o texto
        ctx.add_assistant(content)
        return content

    if len(tool_log) >= MAX_TOOL_TURNS:
        ctx.add_assistant(content or "[Limite de tool calls atingido]")
        return content or "[Limite de tool calls atingido]"

    # Adiciona resposta do assistant com tool_calls ao contexto
    ctx.messages.append({"role": "assistant", "content": content, "tool_calls": tool_calls})

    # Executa cada tool call e adiciona resultados ao contexto
    for tc in tool_calls:
        tc_id = tc.get("id", "call_0")
        fn = tc.get("function", {})
        name = fn.get("name", "?")
        try:
            args = _json.loads(fn.get("arguments", "{}"))
        except _json.JSONDecodeError:
            args = {}

        try:
            result = router.execute_native_call(name, args)
        except (ToolError, SandboxError) as exc:
            result = f"[Erro: {exc}]"

        tool_log.append({"tool": name, "result": result[:300]})
        ctx.messages.append({
            "role": "tool",
            "tool_call_id": tc_id,
            "content": result,
        })

    return None  # continua o loop


def run_one_turn(
    ctx,
    router: ToolRouter,
    client: OllamaClient,
    model_name: str,
) -> tuple[str, list[dict]]:
    """Executa um turno completo do agente, incluindo tool calls encadeados.

    Se o cliente suporta native tool calling (OpenAI function calling), usa esse modo.
    Caso contrário, usa o Tool Bridge (JSON parsing da resposta do modelo).

    Pode levantar OllamaError se o modelo falhar.
    Retorna (resposta_final_em_texto, log_de_tool_calls).
    Usado tanto pelo CLI quanto pelo bauer serve.
    """
    tool_log: list[dict] = []

    # Native tool calling: disponível em OpenAIClient mas não em OllamaClient.
    # Checa a classe concreta para evitar falsos positivos com MagicMock em testes.
    from .openai_client import OpenAIClient as _OpenAIClient
    use_native = isinstance(client, _OpenAIClient) and getattr(client, "supports_native_tools", False)

    if use_native:
        for _ in range(MAX_TOOL_TURNS + 1):
            result = _run_native_tool_turn(ctx, router, client, model_name, tool_log)
            if result is not None:
                return result, tool_log
        # Fallthrough: atingiu limite de turns sem resposta final
        return "[Limite de iterações atingido]", tool_log

    # Tool Bridge (fallback para Ollama e modelos sem native tool calling)
    for _ in range(MAX_TOOL_TURNS + 1):
        response = _collect_response(client, model_name, ctx.get_payload())

        # Resposta vazia: contexto sobrecarregado ou timeout do modelo
        if not response.strip():
            return "[Modelo retornou resposta vazia — contexto pode estar sobrecarregado]", tool_log

        ctx.add_assistant(response)

        # Suporte a batch: modelo pode emitir múltiplos tool calls por resposta
        actions = _try_parse_tools_batch(response, router)

        if actions is not None and len(tool_log) < MAX_TOOL_TURNS:
            combined_parts: list[str] = []
            for action_dict in actions:
                if len(tool_log) >= MAX_TOOL_TURNS:
                    break
                action_name = action_dict.get("action", "?")
                try:
                    tool_result = router.execute(action_dict)
                except (ToolError, SandboxError) as exc:
                    tool_result = f"[Erro: {exc}]"

                tool_log.append({"tool": action_name, "result": tool_result[:300]})

                ctx_result = tool_result
                if len(tool_result) > _MAX_TOOL_RESULT_IN_CTX:
                    ctx_result = (
                        tool_result[:_MAX_TOOL_RESULT_IN_CTX]
                        + f"\n[... +{len(tool_result) - _MAX_TOOL_RESULT_IN_CTX} chars omitidos ...]"
                    )
                combined_parts.append(f"[Resultado de {action_name}]\n{ctx_result}")

            if combined_parts:
                ctx.add_user("\n\n".join(combined_parts))
        else:
            return response, tool_log

    return response, tool_log


def _run_orchestrator_inline(
    user_input: str,
    orchestrator: "AgentOrchestrator",
    console: Console,
) -> str:
    """Executa o orquestrador inline no chat e exibe progresso passo a passo.

    Retorna a resposta final sintetizada para adicionar ao contexto do chat.
    """
    console.print(Rule("[bold dim]Orquestrador[/bold dim]"))
    console.print("[yellow dim]Planejando passos...[/yellow dim]")

    try:
        steps = orchestrator.plan(user_input)
        orchestrator.save_plan(user_input, steps)
    except Exception as exc:
        console.print(f"[red]Erro no planejamento: {exc}[/red]")
        return ""

    if not steps:
        return ""

    batches = orchestrator._topological_batches(steps)
    total_waves = len(batches)
    console.print(f"[dim]{len(steps)} passo(s) em {total_waves} onda(s)[/dim]\n")

    all_results = []
    done: dict = {}

    for wave_idx, batch in enumerate(batches):
        pending = [s for s in batch if s["id"] not in done]
        if not pending:
            continue

        if len(pending) > 1:
            ids = ", ".join(str(s["id"]) for s in pending)
            console.print(f"[dim]Onda {wave_idx + 1}/{total_waves} — passos {ids} (paralelo)[/dim]")
        else:
            s = pending[0]
            console.print(f"[dim]Passo {s['id']}/{len(steps)}: {s['goal']}[/dim]")

        try:
            batch_results = orchestrator.execute_parallel_steps(pending, all_results)
        except KeyboardInterrupt:
            console.print("\n[dim][orquestrador interrompido][/dim]")
            orchestrator.clear_progress(user_input)
            return ""
        except Exception as exc:
            console.print(f"[red]Erro no passo {wave_idx + 1}: {exc}[/red]")
            continue

        all_results.extend(batch_results)
        orchestrator.save_progress(user_input, batch_results)
        for r in batch_results:
            done[r.id] = r
            if r.tool_log:
                tools_used = ", ".join(t["tool"] for t in r.tool_log)
                console.print(f"  [dim]tools: {tools_used}[/dim]")

    if not all_results:
        orchestrator.clear_progress(user_input)
        return ""

    console.print("[dim]Sintetizando...[/dim]")
    try:
        objective = steps[0].get("goal", user_input)
        final = orchestrator.synthesize(objective, all_results)
    except Exception as exc:
        console.print(f"[red]Erro na sintese: {exc}[/red]")
        final = "\n".join(r.response for r in all_results)

    orchestrator.clear_progress(user_input)
    console.print(Rule())
    return final


# ─── /kanban handler ─────────────────────────────────────────────────────────


def _handle_kanban_cmd(console) -> None:  # type: ignore[type-arg]
    """Exibe o Kanban board (workspace/TASKS.md) dentro da sessao do agente."""
    import sys as _sys
    from rich.columns import Columns
    from rich.panel import Panel as _Panel
    from rich.text import Text as _Text

    try:
        from .workspace_manager import WorkspaceManager
    except ImportError:
        console.print("[dim]WorkspaceManager nao disponivel.[/dim]")
        return

    wm = WorkspaceManager("workspace")
    tasks = wm.list_tasks()

    if not tasks:
        console.print("[dim]Nenhuma tarefa. Adicione com: [bold]bauer task add 'titulo'[/bold][/dim]")
        return

    _utf8 = _sys.platform != "win32" or (
        hasattr(_sys.stdout, "encoding") and
        (_sys.stdout.encoding or "").lower().replace("-", "") == "utf8"
    )
    _ICONS = {
        "TODO":        "📋" if _utf8 else "[ ]",
        "IN_PROGRESS": "🔄" if _utf8 else "[~]",
        "DONE":        "✅" if _utf8 else "[x]",
        "BLOCKED":     "🚫" if _utf8 else "[!]",
    }
    _BAR_FULL  = "█" if _utf8 else "#"
    _BAR_EMPTY = "░" if _utf8 else "."

    COLUMNS = [
        ("TODO",        "TODO",        "bright_white"),
        ("IN_PROGRESS", "IN PROGRESS", "yellow"),
        ("DONE",        "DONE",        "green"),
        ("BLOCKED",     "BLOCKED",     "red"),
    ]
    by_status: dict[str, list] = {s: [] for s, *_ in COLUMNS}
    for t in tasks:
        if t.status in by_status:
            by_status[t.status].append(t)

    panels = []
    for status, label, color in COLUMNS:
        col = by_status[status]
        lines = _Text()
        if not col:
            lines.append("  (vazio)\n", style="dim")
        else:
            for t in col:
                lines.append(f" [{t.id}] ", style="dim")
                lines.append(t.title + "\n", style=color)
        from rich.markup import escape as _esc
        panels.append(_Panel(
            lines,
            title=f"[bold {color}]{_esc(_ICONS[status])} {label} ({len(col)})[/bold {color}]",
            border_style=color,
            expand=True,
            padding=(0, 1),
        ))

    total = len(tasks)
    done = len(by_status["DONE"])
    pct = int(done / total * 100) if total else 0
    bar = _BAR_FULL * (pct // 5) + _BAR_EMPTY * (20 - pct // 5)

    console.print()
    console.print(Columns(panels, equal=True, expand=True))
    console.print(f"[dim]  Progresso: {bar} {pct}%  ({done}/{total} concluidas)[/dim]\n")


# ─── /spec handler ───────────────────────────────────────────────────────────


def _handle_spec_cmd(user_input: str, console) -> None:  # type: ignore[type-arg]
    """Processa comandos /spec digitados dentro da sessão do agente.

    Subcomandos:
      /spec          → lista specs existentes
      /spec list     → lista specs existentes
      /spec new      → wizard interativo para criar novo spec
      /spec new <id> → wizard com ID pré-preenchido
      /spec <id>     → exibe spec completo
    """
    from rich.panel import Panel
    from rich.table import Table

    try:
        from .spec_manager import SpecManager
        from .spec_wizard import wizard_create_spec
    except ImportError:
        console.print("[red]SpecManager nao disponivel.[/red]")
        return

    mgr = SpecManager()
    parts = user_input.strip().split()
    # parts[0] = "/spec", parts[1] = subcomando (opcional), parts[2] = id (opcional)
    sub = parts[1].lower() if len(parts) > 1 else "list"

    if sub in ("list", "ls"):
        specs = mgr.list_specs()
        if not specs:
            console.print("[dim]Nenhum spec encontrado. Use [bold]/spec new[/bold] para criar.[/dim]")
            return
        table = Table(show_lines=False, box=None, title=f"Specs ({len(specs)})")
        table.add_column("id", style="cyan", no_wrap=True)
        table.add_column("status")
        table.add_column("ACs", style="dim", width=4)
        table.add_column("purpose", style="dim")
        _colors = {"draft":"dim","review":"yellow","approved":"blue","implemented":"green","deprecated":"red"}
        for s in specs:
            c = _colors.get(s.status, "white")
            purpose = s.purpose.split("\n")[0][:55] + ("…" if len(s.purpose) > 55 else "")
            table.add_row(s.id, f"[{c}]{s.status}[/{c}]", str(len(s.acceptance_criteria)), purpose)
        console.print(table)
        console.print("[dim]Use [bold]/spec <id>[/bold] para ver detalhes, [bold]/spec new[/bold] para criar.[/dim]")
        return

    if sub == "new":
        spec_id_hint = parts[2] if len(parts) > 2 else ""
        if spec_id_hint:
            console.print(f"[dim]Criando spec '[cyan]{spec_id_hint}[/cyan]'...[/dim]")
        wizard_create_spec(mgr)
        return

    # /spec <id> — exibe o spec
    spec = mgr.get(sub)
    if not spec:
        console.print(f"[yellow]Spec '[cyan]{sub}[/cyan]' nao encontrado.[/yellow]")
        console.print(f"[dim]Crie com: [bold]/spec new {sub}[/bold][/dim]")
        if mgr.list_specs():
            console.print(f"[dim]Specs existentes: {', '.join(s.id for s in mgr.list_specs())}[/dim]")
        return

    console.print(Panel(
        spec.to_context(compact=False),
        title=f"[bold cyan]{spec.id}[/bold cyan] — {spec.title}",
        border_style="cyan",
    ))


def _handle_agent_cmd(user_input: str, console) -> None:  # type: ignore[type-arg]
    """Processa comandos /agent digitados dentro da sessao do agente.

    Subcomandos:
      /agents              → lista agents (alias)
      /agent list          → lista agents criados
      /agent create        → wizard interativo para criar agent
      /agent delete <nome> → remove agent do registry
    """
    from rich.table import Table
    from rich.prompt import Confirm

    try:
        from .agent_registry import AgentRegistry
        from .agent_wizard import wizard_create_agent
    except ImportError:
        console.print("[red]AgentRegistry nao disponivel.[/red]")
        return

    parts = user_input.strip().split(maxsplit=2)
    # "/agents" (sem espaço) → list
    cmd0 = parts[0].lower()
    sub = parts[1].lower() if len(parts) > 1 else "list"
    if cmd0 == "/agents":
        sub = "list"

    registry = AgentRegistry("agents.yaml")

    if sub in ("list", "ls"):
        agents = registry.list_agents()
        if not agents:
            console.print(
                "[yellow]Nenhum agent criado ainda.[/yellow]\n"
                "Crie um com: [bold]/agent create[/bold]"
            )
            return
        table = Table(title=f"Agents ({len(agents)})", show_lines=True)
        table.add_column("nome",     style="cyan", no_wrap=True)
        table.add_column("descricao")
        table.add_column("modelo",   style="dim")
        table.add_column("tools",    style="dim")
        table.add_column("criado em", style="dim")
        for ag in agents:
            model_str = f"{ag.provider}/{ag.model}" if ag.model else "[dim]config.yaml[/dim]"
            tools_str = ", ".join(ag.tools) if ag.tools else "—"
            created   = ag.created_at[:10] if ag.created_at else "—"
            table.add_row(ag.name, ag.description, model_str, tools_str, created)
        console.print(table)
        console.print("[dim]Para rodar: [bold]bauer agent run <nome>[/bold] | Para criar: [bold]/agent create[/bold][/dim]")
        return

    if sub == "create":
        wizard_create_agent(registry)
        return

    if sub == "delete":
        nome = parts[2].strip() if len(parts) > 2 else ""
        if not nome:
            console.print("[yellow]Uso: [bold]/agent delete <nome>[/bold][/yellow]")
            return
        ag = registry.get(nome)
        if ag is None:
            console.print(f"[red]Agent '[cyan]{nome}[/cyan]' nao encontrado.[/red]")
            agents = registry.list_agents()
            if agents:
                console.print(f"[dim]Agents existentes: {', '.join(a.name for a in agents)}[/dim]")
            return
        try:
            if not Confirm.ask(f"[yellow]Remover agent '[cyan]{nome}[/cyan]'?[/yellow]", default=False):
                console.print("[dim]Cancelado.[/dim]")
                return
        except Exception:
            # fallback se Confirm nao estiver disponivel (ex: pipe)
            pass
        registry.delete(nome)
        console.print(f"[green]✓[/green] Agent [cyan]{nome}[/cyan] removido.")
        return

    console.print(f"[yellow]Subcomando desconhecido: [bold]/agent {sub}[/bold][/yellow]")
    console.print("[dim]Disponiveis: list | create | delete <nome>[/dim]")


# ─── /task handler ───────────────────────────────────────────────────────────


def _handle_task_cmd(user_input: str, console) -> None:  # type: ignore[type-arg]
    """Processa comandos /task digitados dentro da sessao do agente.

    Subcomandos:
      /task               → exibe Kanban board (delega a _handle_kanban_cmd)
      /task list          → lista tarefas com status
      /task add <titulo>  → adiciona nova tarefa
      /task start <id>    → muda status para IN_PROGRESS
      /task done <id>     → muda status para DONE
      /task block <id>    → muda status para BLOCKED
    """
    from rich.table import Table

    try:
        from .workspace_manager import WorkspaceManager, WorkspaceError
    except ImportError:
        console.print("[red]WorkspaceManager nao disponivel.[/red]")
        return

    parts = user_input.strip().split(maxsplit=2)
    sub = parts[1].lower() if len(parts) > 1 else ""

    # bare /task → board
    if not sub:
        _handle_kanban_cmd(console)
        return

    wm = WorkspaceManager("workspace")

    if sub in ("list", "ls"):
        tasks = wm.list_tasks()
        if not tasks:
            console.print("[dim]Nenhuma tarefa. Use [bold]/task add <titulo>[/bold] para criar.[/dim]")
            return
        _STATUS_COLORS = {
            "TODO": "bright_white", "IN_PROGRESS": "yellow",
            "DONE": "green",        "BLOCKED": "red",
        }
        table = Table(show_lines=False, box=None)
        table.add_column("ID",     style="dim",    width=4, no_wrap=True)
        table.add_column("Status", width=12)
        table.add_column("Titulo")
        for t in tasks:
            c = _STATUS_COLORS.get(t.status, "white")
            table.add_row(t.id, f"[{c}]{t.status}[/{c}]", t.title)
        console.print(table)
        return

    if sub == "add":
        titulo = parts[2].strip() if len(parts) > 2 else ""
        if not titulo:
            console.print("[yellow]Uso: [bold]/task add <titulo>[/bold][/yellow]")
            return
        task = wm.add_task(titulo)
        console.print(f"[green]Tarefa adicionada:[/green] [[dim]{task.id}[/dim]] {task.title}")
        return

    # start / done / block → precisam de <id>
    _STATUS_MAP = {"start": "IN_PROGRESS", "done": "DONE", "block": "BLOCKED"}
    if sub in _STATUS_MAP:
        task_id = parts[2].strip() if len(parts) > 2 else ""
        if not task_id:
            console.print(f"[yellow]Uso: [bold]/task {sub} <id>[/bold][/yellow]")
            return
        new_status = _STATUS_MAP[sub]
        try:
            task = wm.update_task_status(task_id, new_status)
            _VERBS = {"IN_PROGRESS": "iniciada", "DONE": "concluida", "BLOCKED": "bloqueada"}
            console.print(
                f"[green]Tarefa {_VERBS[new_status]}:[/green] "
                f"[[dim]{task.id}[/dim]] {task.title} → [{new_status}]"
            )
        except Exception as exc:
            console.print(f"[red]Erro:[/red] {exc}")
        return

    console.print(f"[yellow]Subcomando desconhecido: [bold]/task {sub}[/bold][/yellow]")
    console.print("[dim]Disponiveis: add | list | start | done | block[/dim]")


# ─── /memory handler ─────────────────────────────────────────────────────────


def _handle_memory_cmd(user_input: str, console) -> None:  # type: ignore[type-arg]
    """Processa comandos /memory digitados dentro da sessao do agente.

    Subcomandos:
      /memory                  → lista arquivos de memoria
      /memory list             → lista arquivos de memoria
      /memory search <query>   → busca TF-IDF nos arquivos Markdown
      /memory note <texto>     → adiciona nota rapida
    """
    from pathlib import Path as _Path
    from rich.table import Table

    parts = user_input.strip().split(maxsplit=2)
    sub = parts[1].lower() if len(parts) > 1 else "list"

    try:
        from .memory_manager import MemoryManager
    except ImportError:
        console.print("[red]MemoryManager nao disponivel.[/red]")
        return

    mm = MemoryManager()

    if sub in ("list", "ls", ""):
        mem_dir = _Path("memory")
        if not mem_dir.exists():
            console.print("[dim]Diretorio memory/ nao encontrado.[/dim]")
            return
        files = sorted(mem_dir.glob("*.md"))
        if not files:
            console.print("[dim]Nenhum arquivo de memoria encontrado.[/dim]")
            return
        table = Table(show_lines=False, box=None, title=f"Memoria ({len(files)} arquivos)")
        table.add_column("Arquivo",  style="cyan")
        table.add_column("Tamanho",  style="dim", justify="right")
        for f in files:
            size = f.stat().st_size
            table.add_row(f.name, f"{size:,} B")
        console.print(table)
        console.print("[dim]Use [bold]/memory search <query>[/bold] para buscar.[/dim]")
        return

    if sub == "search":
        query = parts[2].strip() if len(parts) > 2 else ""
        if not query:
            console.print("[yellow]Uso: [bold]/memory search <query>[/bold][/yellow]")
            return
        try:
            results = mm.search(query, top_k=5)
        except Exception as exc:
            console.print(f"[red]Erro na busca:[/red] {exc}")
            return
        if not results:
            console.print(f"[dim]Nenhum resultado para '[cyan]{query}[/cyan]'.[/dim]")
            return
        table = Table(show_lines=True, box=None, title=f"Resultados: '{query}'")
        table.add_column("Score", style="dim", width=7, justify="right")
        table.add_column("Arquivo", style="cyan", no_wrap=True)
        table.add_column("Secao")
        table.add_column("Trecho", style="dim")
        for r in results:
            score_str = f"{r['score']:.3f}"
            snippet = (r.get("snippet", "") or "")[:70].replace("\n", " ")
            table.add_row(score_str, r["file"], r.get("title", ""), snippet)
        console.print(table)
        return

    if sub == "note":
        note_text = parts[2].strip() if len(parts) > 2 else ""
        if not note_text:
            console.print("[yellow]Uso: [bold]/memory note <texto>[/bold][/yellow]")
            return
        try:
            # Usa as primeiras 60 chars como titulo e o texto completo como corpo
            title = note_text[:60] + ("..." if len(note_text) > 60 else "")
            mm.add_note(title, note_text)
            console.print(f"[green]Nota adicionada:[/green] {title}")
        except Exception as exc:
            console.print(f"[red]Erro ao salvar nota:[/red] {exc}")
        return

    console.print(f"[yellow]Subcomando desconhecido: [bold]/memory {sub}[/bold][/yellow]")
    console.print("[dim]Disponiveis: list | search <query> | note <texto>[/dim]")


# ─── /project handler ────────────────────────────────────────────────────────


def _handle_project_cmd(console) -> None:  # type: ignore[type-arg]
    """Exibe PROJECT.md e um resumo das tarefas do workspace."""
    from pathlib import Path as _Path
    from rich.panel import Panel as _Panel
    from rich.rule import Rule as _Rule

    project_file = _Path("workspace") / "PROJECT.md"
    tasks_summary_parts: list[str] = []

    # Tenta carregar resumo de tarefas
    try:
        from .workspace_manager import WorkspaceManager
        wm = WorkspaceManager("workspace")
        tasks = wm.list_tasks()
        if tasks:
            from collections import Counter
            counts = Counter(t.status for t in tasks)
            total = len(tasks)
            done = counts.get("DONE", 0)
            pct = int(done / total * 100) if total else 0
            tasks_summary_parts.append(
                f"[dim]Tarefas: {total} total | "
                f"[green]{counts.get('DONE', 0)} DONE[/green] | "
                f"[yellow]{counts.get('IN_PROGRESS', 0)} IN_PROGRESS[/yellow] | "
                f"[white]{counts.get('TODO', 0)} TODO[/white] | "
                f"[red]{counts.get('BLOCKED', 0)} BLOCKED[/red] | "
                f"{pct}% concluido[/dim]"
            )
    except Exception:
        pass

    console.print()
    if project_file.exists():
        content = project_file.read_text(encoding="utf-8")
        # Exibe primeiros 50 linhas para nao inundar o terminal
        lines = content.splitlines()
        preview = "\n".join(lines[:50])
        if len(lines) > 50:
            preview += f"\n\n[dim]... (+{len(lines) - 50} linhas — abra workspace/PROJECT.md para ver tudo)[/dim]"
        console.print(_Panel(preview, title="[bold cyan]PROJECT.md[/bold cyan]", border_style="cyan"))
    else:
        console.print("[dim]PROJECT.md nao encontrado. Use: [bold]bauer project init <nome>[/bold][/dim]")

    for line in tasks_summary_parts:
        console.print(line)

    console.print()


def run_agent_session(
    client: OllamaClient,
    model_name: str,
    applied_context: int,
    console: Console,
    router: ToolRouter,
    model_router: ModelRouter | None = None,
    orchestrator: "AgentOrchestrator | None" = None,
    session_store: "SessionStore | None" = None,
    session_id: str | None = None,
    rebuild_client_fn: "Any | None" = None,
) -> None:
    """Loop do agente com Tool Bridge, roteamento inteligente e sessao persistente.

    Args:
        client: Cliente principal (Ollama, OpenAI, etc.).
        model_name: Nome do modelo ativo (padrao).
        applied_context: Contexto aplicado (em tokens).
        console: Console Rich para output.
        router: ToolRouter com as tools disponiveis.
        model_router: Roteador de modelos (opcional).
        orchestrator: AgentOrchestrator para tarefas complexas (opcional).
        session_store: SessionStore para persistencia de sessao (opcional).
        session_id: ID da sessao ativa — carrega historico se existir (opcional).
        rebuild_client_fn: Callable() -> (client, model_name) — reconstrói o cliente
            lendo config.yaml atualizado. Permite live model switch via /model.
    """
    system_prompt = _build_system_prompt(router)
    # Determina provider a partir do tipo do client para context budget correto
    _provider = getattr(client, "_provider", None) or (
        "ollama" if hasattr(client, "host") and "ollama" in getattr(client, "host", "").lower()
        else "openai"
    )
    ctx = ContextManager(
        applied_context=applied_context,
        system_prompt=system_prompt,
        provider=_provider,
    )
    # Habilita compressão semântica via LLM quando o cliente está disponível
    ctx.set_llm(client, model_name)

    # Carrega historico da sessao se existir
    if session_store is not None and session_id is not None:
        saved = session_store.load(session_id)
        if saved:
            ctx.messages = saved

    tool_names = ", ".join(router.available_tools())
    stats = SessionStats(model=model_name, context_tokens=applied_context, machine_id=get_machine_id())
    skills = SkillRegistry()
    routing = model_router is not None and model_router.config.enabled
    orch_enabled = orchestrator is not None and routing

    console.print(Rule(f"[bold]Bauer Agent[/bold] — {model_name}"))
    console.print(
        f"[dim]Contexto: {applied_context} tokens | Tokens usados: 0[/dim]\n"
    )

    # Cria sessão prompt_toolkit (autocomplete de /) apenas em terminal interativo real.
    # Tenta criar; se o terminal não suportar (ex: pipe, CI), cai para console.input().
    _is_interactive = sys.stdin.isatty() and sys.stdout.isatty()
    _pt_session = None
    if _PT_AVAILABLE and _is_interactive:
        try:
            _pt_session = _make_prompt_session()
        except Exception:
            _pt_session = None  # terminal incompatível — usa fallback

    while True:
        # --- entrada do usuário ---
        try:
            if _pt_session is not None:
                try:
                    user_input = _pt_session.prompt(
                        HTML("<bold><ansicyan>voce></ansicyan></bold> "),
                    ).strip()
                except Exception:
                    # Falha em runtime (ex: terminal redimensionado abruptamente)
                    # — tenta uma vez com console.input fallback
                    _pt_session = None
                    user_input = console.input("[bold cyan]voce>[/bold cyan] ").strip()
            else:
                user_input = console.input("[bold cyan]voce>[/bold cyan] ").strip()
        except (KeyboardInterrupt, EOFError):
            console.print("\n[dim]Sessao encerrada.[/dim]")
            return

        if not user_input:
            continue
        if user_input.lower() in _EXIT_CMDS:
            console.print("[dim]Ate logo.[/dim]")
            stats.save()
            if session_store is not None and session_id:
                session_store.save(session_id, ctx.messages)
                console.print(f"[dim]Sessao salva: {session_id}[/dim]")
            return
        if user_input.lower() in _CLEAR_CMDS:
            ctx.clear()
            if session_store is not None and session_id:
                session_store.save(session_id, [])  # limpa no disco tambem
            console.print("[dim]Historico limpo.[/dim]")
            continue
        if user_input.lower() in _STATUS_CMDS:
            console.print(
                f"[dim]Historico: {len(ctx.messages)} mensagem(ns) | "
                f"~{ctx.used_tokens} tokens usados / {ctx.budget} budget"
                + (f" | Sessao: {session_id}" if session_id else "")
                + "[/dim]"
            )
            continue
        if user_input.lower() in _MODEL_CMDS:
            # Live model switch: abre seletor, salva config.yaml, reconstrói client ao vivo.
            console.print(
                f"[dim]Modelo atual: [cyan]{model_name}[/cyan] | "
                f"Contexto: {applied_context} tokens[/dim]"
            )
            try:
                from pathlib import Path as _Path
                from .model_switcher import run_model_switcher as _rms
                _rms(_Path("config.yaml"))

                if rebuild_client_fn is not None:
                    _new_client, _new_model = rebuild_client_fn()
                    # Rebind ao vivo — próximo turno já usa o novo modelo
                    client = _new_client
                    model_name = _new_model
                    ctx.set_llm(client, model_name)
                    console.print(
                        f"[green]✓ Modelo trocado para [cyan]{model_name}[/cyan] — "
                        f"próxima mensagem já usa o novo modelo.[/green]"
                    )
                else:
                    console.print(
                        "[yellow]config.yaml atualizado. Reinicie para aplicar.[/yellow]"
                    )
            except Exception as _e:
                console.print(f"[red]Erro ao trocar modelo:[/red] {_e}")
            continue
        if user_input.lower() in _SESSIONS_CMDS:
            if session_store is not None:
                sessions = session_store.list_sessions()
                if sessions:
                    console.print(f"[dim]Sessoes salvas ({len(sessions)}): {', '.join(sessions[-10:])}[/dim]")
                    console.print(f"[dim]Para retomar: bauer agent --resume --session-id ID[/dim]")
                else:
                    console.print("[dim]Nenhuma sessao salva.[/dim]")
            else:
                console.print("[dim]Persistencia de sessao nao configurada.[/dim]")
            continue
        if user_input.lower().startswith(tuple(_SPEC_CMDS)):
            _handle_spec_cmd(user_input, console)
            continue
        if user_input.lower() in _KANBAN_CMDS:
            _handle_kanban_cmd(console)
            continue
        if user_input.lower() == "/agents" or user_input.lower().startswith("/agent "):
            _handle_agent_cmd(user_input, console)
            continue
        if user_input.lower().startswith("/task "):
            _handle_task_cmd(user_input, console)
            continue
        if user_input.lower().startswith("/memory"):
            _handle_memory_cmd(user_input, console)
            continue
        if user_input.lower() in _PROJECT_CMDS:
            _handle_project_cmd(console)
            continue

        suggested = skills.observe(user_input)
        if suggested:
            console.print(f"[dim]Skill sugerida: '{suggested}' — veja 'bauer memory show skills'[/dim]")

        stats.start_turn()

        # --- roteamento (se ativo) ---
        active_model = model_name
        route_kind = "direct"
        if routing and model_router is not None:
            try:
                selected_model, route = model_router.select_model(user_input)
                route_kind = route.kind
                if route_kind == "orchestrate" and orch_enabled:
                    console.print(f"[dim]  -> [orquestrar] tarefa complexa detectada[/dim]")
                elif selected_model != model_name:
                    active_model = selected_model
                    console.print(f"[dim]  -> [{route.label}] {selected_model}[/dim]")
            except Exception:
                pass

        # --- escalada para orquestrador ---
        if route_kind == "orchestrate" and orch_enabled and orchestrator is not None:
            final = _run_orchestrator_inline(user_input, orchestrator, console)
            if final:
                # Adiciona ao contexto como turno normal para manter histórico
                ctx.add_user(user_input)
                ctx.add_assistant(final)
                stats.end_turn(len(final))
                # Persiste sessao apos orquestracao
                if session_store is not None and session_id:
                    session_store.save(session_id, ctx.messages)
                sys.stdout.write("\033[32mbauer>\033[0m ")
                sys.stdout.write(final)
                sys.stdout.write("\n\n")
                sys.stdout.flush()
            continue

        ctx.add_user(user_input)

        # --- loop de tool turns (um turno do usuário pode ter N tool calls) ---
        tool_turns = 0
        while True:
            try:
                response = _collect_response(client, active_model, ctx.get_payload())
            except (OllamaError, OpenAIClientError) as exc:
                _err_type = "Ollama" if isinstance(exc, OllamaError) else "Provider"
                console.print(f"\n[red]Erro do {_err_type}:[/red] {exc}")
                stats.record_error(str(exc))
                if ctx.messages and ctx.messages[-1]["role"] == "user":
                    ctx.messages.pop()
                break
            except KeyboardInterrupt:
                console.print("\n[dim][interrompido][/dim]")
                if ctx.messages and ctx.messages[-1]["role"] == "user":
                    ctx.messages.pop()
                break

            # Resposta vazia: contexto provavelmente sobrecarregado
            if not response.strip():
                console.print(
                    "[yellow]Modelo retornou resposta vazia. "
                    "O contexto pode estar sobrecarregado — use [bold]/clear[/bold] para reiniciar "
                    "ou faça uma pergunta mais curta.[/yellow]"
                )
                if ctx.messages and ctx.messages[-1]["role"] == "user":
                    ctx.messages.pop()
                break

            ctx.add_assistant(response)

            # Tenta parsear como tool action(s) — suporta batch (múltiplos JSONs por resposta)
            actions = _try_parse_tools_batch(response, router)

            if actions is not None and tool_turns < MAX_TOOL_TURNS:
                combined_parts: list[str] = []

                for action_dict in actions:
                    if tool_turns >= MAX_TOOL_TURNS:
                        console.print(
                            f"[yellow]Limite de {MAX_TOOL_TURNS} tool calls atingido "
                            "neste turno.[/yellow]"
                        )
                        break

                    action_name = action_dict.get("action", "?")
                    console.print(f"[dim]  -> {action_name}[/dim]")

                    try:
                        tool_result = router.execute(action_dict)
                    except (ToolError, SandboxError) as exc:
                        tool_result = f"[Erro: {exc}]"

                    # Preview no terminal (300 chars)
                    preview = tool_result[:300] + ("..." if len(tool_result) > 300 else "")
                    console.print(f"[dim]{preview}[/dim]")

                    # Trunca resultado para o contexto — evita overflow em leituras massivas
                    ctx_result = tool_result
                    if len(tool_result) > _MAX_TOOL_RESULT_IN_CTX:
                        ctx_result = (
                            tool_result[:_MAX_TOOL_RESULT_IN_CTX]
                            + f"\n[... resultado truncado — "
                            f"{len(tool_result) - _MAX_TOOL_RESULT_IN_CTX} chars omitidos ...]"
                        )

                    combined_parts.append(f"[Resultado de {action_name}]\n{ctx_result}")
                    tool_turns += 1

                if combined_parts:
                    console.print()
                    ctx.add_user("\n\n".join(combined_parts))
                continue

            if tool_turns >= MAX_TOOL_TURNS:
                console.print(
                    f"[yellow]Limite de {MAX_TOOL_TURNS} tool calls atingido "
                    "neste turno.[/yellow]"
                )

            # Resposta final em texto — extrai se o modelo usou JSON de conversa
            display = _extract_text_from_pseudo_json(response) or response
            stats.end_turn(len(display))
            # Persiste sessao apos cada turno completo
            if session_store is not None and session_id:
                try:
                    session_store.save(session_id, ctx.messages)
                except Exception:
                    pass  # nao interrompe o agente por falha de persistencia
            sys.stdout.write("\033[32mbauer>\033[0m ")
            sys.stdout.write(display)
            sys.stdout.write("\n\n")
            sys.stdout.flush()
            break
