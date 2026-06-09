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
_DISPATCH_CMDS = {"/dispatch"}
_OPS_CMDS = {"/ops"}
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
    "/task ready",
    "/task start",
    "/task done",
    "/task block",
    "/task fail",
    "/dispatch",
    "/dispatch once",
    "/dispatch once --dry-run",
    "/dispatch status",
    "/ops",
    "/ops status",
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
    "/task ready":     "coloca na fila do dispatcher: /task ready <id>",
    "/task start":     "inicia tarefa: /task start <id>",
    "/task done":      "conclui tarefa: /task done <id>",
    "/task block":     "bloqueia tarefa: /task block <id>",
    "/task fail":      "marca tarefa como FAILED: /task fail <id>",
    "/dispatch":       "executa um tick do dispatcher hibrido",
    "/dispatch once":  "despacha tasks READY uma vez",
    "/dispatch status": "mostra fila/claims do dispatcher",
    "/ops":            "status operacional: lanes, claims, runs e eventos",
    "/ops status":     "status operacional detalhado",
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

MAX_TOOL_TURNS = 150

# ─── Loop detection ────────────────────────────────────────────────────────────
# Protege contra modelos que ficam chamando a mesma tool repetidamente.
# Usa fingerprint = tool_name + primeiros 100 chars do resultado para detectar
# chamadas idênticas consecutivas, independente dos args (que não ficam no log).
_LOOP_REPEAT_WARN  = 3   # N° de repetições consecutivas → soft warning no contexto
_LOOP_REPEAT_HARD  = 5   # N° de repetições consecutivas → hard stop imediato
_LOOP_OSCIL_WINDOW = 6   # Janela de calls para detectar padrão A→B→A→B


def _loop_fp(entry: dict) -> str:
    """Fingerprint de uma entrada do tool_log: nome + primeiros 100 chars do resultado."""
    return f"{entry['tool']}:{entry['result'][:100]}"


def _detect_loop(tool_log: list[dict]) -> tuple[str | None, bool]:
    """Analisa tool_log em busca de loops e oscilações.

    Returns:
        (warning_msg, is_hard_stop)
        warning_msg: None se sem loop; string de alerta se loop detectado.
        is_hard_stop: True → interromper o loop imediatamente.
    """
    if not tool_log:
        return None, False

    # ── 1. Repetição consecutiva (mesma fingerprint N vezes seguidas) ──────────
    last_fp = _loop_fp(tool_log[-1])
    consecutive = 0
    for entry in reversed(tool_log):
        if _loop_fp(entry) == last_fp:
            consecutive += 1
        else:
            break

    last_tool = tool_log[-1]["tool"]

    if consecutive >= _LOOP_REPEAT_HARD:
        msg = (
            f"[AVISO DO SISTEMA — LOOP DETECTADO] Você chamou '{last_tool}' "
            f"{consecutive} vezes consecutivas com resultado idêntico. "
            "PARE IMEDIATAMENTE. Não chame mais nenhuma tool agora. "
            "Analise o que já foi obtido e responda diretamente ao usuário "
            "com o resultado atual ou indique o que está impedindo o progresso."
        )
        return msg, True  # hard stop

    if consecutive >= _LOOP_REPEAT_WARN:
        msg = (
            f"[AVISO DO SISTEMA] Você chamou '{last_tool}' {consecutive} vezes "
            "consecutivas com o mesmo resultado. O resultado não vai mudar. "
            "Considere uma abordagem diferente ou conclua com os dados já obtidos."
        )
        return msg, False  # soft warning

    # ── 2. Oscilação A→B→A→B (últimas N calls alternam entre 2 tools) ─────────
    if len(tool_log) >= _LOOP_OSCIL_WINDOW:
        recent_names = [e["tool"] for e in tool_log[-_LOOP_OSCIL_WINDOW:]]
        evens = set(recent_names[::2])
        odds  = set(recent_names[1::2])
        if len(evens) == 1 and len(odds) == 1 and evens != odds:
            tool_a, tool_b = next(iter(evens)), next(iter(odds))
            msg = (
                f"[AVISO DO SISTEMA — OSCILAÇÃO DETECTADA] Você está alternando "
                f"entre '{tool_a}' e '{tool_b}' repetidamente ({_LOOP_OSCIL_WINDOW} calls). "
                "Isso indica um ciclo sem progresso. Mude de estratégia, "
                "combine os resultados já obtidos ou conclua a tarefa."
            )
            return msg, False  # soft warning (pode ser legítimo em alguns casos)

    return None, False


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
        "Voce e o Bauer Agent, assistente de desenvolvimento local e agente autonomo.\n\n"
        f"Data e hora atual: {timestamp}\n\n"
        "# REGRA PRINCIPAL\n"
        "Responda SEMPRE em texto normal (portugues). NUNCA use JSON para respostas de conversa.\n\n"
        "# AUTONOMIA — ACAO SEM PERGUNTAR\n"
        "Voce e um agente AUTONOMO. Quando tiver contexto suficiente para agir:\n"
        "- EXECUTE a acao mais logica DIRETAMENTE, sem pedir confirmacao.\n"
        "- NAO apresente listas de opcoes (1, 2, 3...) e pergunte 'O que prefere?'\n"
        "- NAO pergunte 'Posso prosseguir?', 'Deseja que eu...', 'Se quiser, podemos:'\n"
        "- NAO espere aprovacao para tarefas tecnicas rotineiras.\n"
        "- Escolha a acao mais sensata e execute. Informe o que fez DEPOIS de fazer.\n"
        "Interrompa para perguntar SOMENTE se:\n"
        "  a) Falta informacao critica impossivel de inferir (ex: credencial, nome de usuario).\n"
        "  b) A acao e DESTRUTIVA e irreversivel (ex: deletar dados de producao).\n"
        "  c) O usuario pediu explicitamente para voce confirmar antes.\n"
        "Em todos os outros casos: AGE. Nao pergunta.\n\n"
        "# CONSTRAINTS DO AMBIENTE (LEIA — evita erros recorrentes)\n"
        "- Voce roda em **Windows** com Python no venv. Subprocess usa `shell=False`.\n"
        "- TODAS as tools de arquivo (read_file, write_file, list_dir, etc) trabalham\n"
        "  em paths RELATIVOS ao workspace. Nunca passe paths absolutos do tipo\n"
        "  `C:/...` ou `/Users/...`. Use `.`, `subdir/arquivo.py`, etc.\n"
        "- `..` e permitido se o path resolvido ficar dentro do workspace. `../fora` e BLOQUEADO.\n"
        "- Em run_command NAO use: `dir` (use tool list_dir), `cat`/`head`/`tail` (use read_file).\n"
        "- `pip install`, `npm install`, `git push`, `rm` precisam `confirm: true` no args.\n"
        "- Antes de `python script.py`, LEIA o script para descobrir se exige argumentos.\n"
        "  Muitos scripts saem com 'Uso: python X.py <arg>' — read_file primeiro evita isso.\n\n"
        "# FERRAMENTAS DISPONIVEIS\n"
        f"Voce pode usar estas ferramentas: {tool_names}\n"
        f"{tools_section}\n\n"
        "# QUANDO USAR FERRAMENTA\n"
        "Use UMA ferramenta SOMENTE se a pergunta exigir ler/escrever arquivos ou listar diretorios.\n"
        "Nesse caso, responda SOMENTE com o JSON abaixo (sem texto antes ou depois):\n"
        '{"action": "NOME_DA_TOOL", "args": {"parametro": "valor"}}\n\n'
        "# QUANDO NAO USAR FERRAMENTA (maioria dos casos)\n"
        "Para saudacoes, perguntas, explicacoes, codigo, matematica, conversas — responda em TEXTO PURO.\n\n"
        "# VOCE TEM ACESSO REAL AO SHELL — NAO NEGUE ISSO\n"
        "Se o usuario digitar uma linha de comando shell (ex: 'bauer orchestrate run',\n"
        "'pytest tests/', 'git status', 'python script.py'), VOCE PODE E DEVE executar\n"
        "via a tool `run_command`. NUNCA responda 'nao tenho acesso ao terminal' — voce TEM.\n"
        "Use run_command com o comando EXATO que o usuario pediu (passe arg `command`).\n"
        "Exemplo: usuario diz 'bauer orchestrate run' -> {\"action\":\"run_command\",\"args\":{\"command\":\"bauer orchestrate run\"}}\n"
        "Se o comando exigir argumentos extras, execute primeiro com --help para descobrir,\n"
        "depois rode com os argumentos certos.\n\n"
        "EXEMPLOS CORRETOS:\n"
        "  Pergunta: 'oi'                  -> resposta: 'Ola! Como posso ajudar?'\n"
        "  Pergunta: 'que horas sao?'       -> resposta: 'Sao X horas.'\n"
        "  Pergunta: 'explique docker'      -> resposta em texto explicando docker\n"
        "  Pergunta: 'liste os arquivos'    -> {\"action\": \"list_dir\", \"args\": {\"path\": \".\"}}\n"
        "  Pergunta: 'leia o config.yaml'   -> {\"action\": \"read_file\", \"args\": {\"path\": \"config.yaml\"}}\n"
        "  Pergunta: 'rode os testes'       -> {\"action\": \"run_command\", \"args\": {\"command\": \"pytest tests/ -v\"}}\n"
        "  Pergunta: 'git status'           -> {\"action\": \"run_command\", \"args\": {\"command\": \"git status\"}}\n"
        "  Pergunta: 'bauer doctor'         -> {\"action\": \"run_command\", \"args\": {\"command\": \"bauer doctor\"}}\n\n"
        "ERRADO (nunca faca isso):\n"
        "  Pergunta: 'oi' -> {\"action\": \"resposta\", ...}  <- ERRADO, use texto puro\n"
        "  Qualquer resposta: 'O que prefere?' / 'Se quiser posso...' / 'Deseja que eu...' <- ERRADO\n"
        "  Qualquer resposta: 'nao tenho acesso ao terminal' / 'sou apenas seu assistente' <- ERRADO, voce TEM run_command\n\n"
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

# ─── Compressão imediata de tool results grandes ───────────────────────────────
# Resultados maiores que este limite são comprimidos na ingestão (antes de entrar
# no contexto), não apenas no momento da compressão do histórico.
# Isso reduz o impacto de tool calls exploratórias (list_dir, execute_code,
# glob_files) que geram muitos chars mas pouco valor marginal por token.
_TOOL_RESULT_COMPRESS_THRESHOLD = 2000   # chars — acima disso, comprime imediatamente
_TOOL_RESULT_COMPRESSED_PREVIEW  = 500   # chars — alvo após compressão

# Percentual de uso do budget a partir do qual avisa o usuário no CLI
_CTX_WARN_THRESHOLD = 0.85


# Tools de listagem/exploração → compressão mais agressiva (itens, não linhas)
_LISTING_TOOLS = frozenset({
    "list_dir", "glob_files", "regex_search", "session_search",
    "list_tasks", "skills_list", "memory",
})

# Tools de conteúdo → preserva mais linhas no preview
_CONTENT_TOOLS = frozenset({
    "read_file", "execute_code", "delegate_task", "http_request",
})


def _compress_tool_result_inline(action: str, result: str) -> str:
    """Comprime um resultado de tool grande para caber no contexto sem desperdiçar tokens.

    Estratégia por tipo de tool:
    - Listagem (list_dir, glob_files): mostra contagem + primeiros N itens
    - Conteúdo (read_file, execute_code): mostra contagem de linhas + primeiras N linhas
    - Genérico: contagem de chars/linhas + preview do início

    Só deve ser chamada quando len(result) > _TOOL_RESULT_COMPRESS_THRESHOLD.
    Preserva sempre a primeira linha (quase sempre a mais importante).

    Returns:
        String comprimida com ≤ _TOOL_RESULT_COMPRESSED_PREVIEW chars.
    """
    lines = [l for l in result.splitlines() if l.strip()]
    n_lines = len(lines)
    n_chars = len(result)

    if action in _LISTING_TOOLS:
        # Para listagens: contagem + primeiros 8 itens
        n_preview = 8
        preview_items = lines[:n_preview]
        extra = n_lines - n_preview
        summary = f"[{n_chars} chars — {n_lines} itens] " + ", ".join(preview_items)
        if extra > 0:
            summary += f" ... +{extra} mais"
    elif action in _CONTENT_TOOLS:
        # Para conteúdo: contagem de linhas + primeiras 6 linhas
        n_preview = 6
        preview_lines = lines[:n_preview]
        extra = n_lines - n_preview
        summary = f"[{n_chars} chars — {n_lines} linhas]\n" + "\n".join(preview_lines)
        if extra > 0:
            summary += f"\n... +{extra} linhas omitidas"
    else:
        # Genérico: primeiros 300 chars + metadados
        preview = result[:300].rstrip()
        summary = f"[{n_chars} chars — {n_lines} linhas] {preview}"
        if n_chars > 300:
            summary += f"... [{n_chars - 300} chars omitidos]"

    # Garante que não ultrapassa o limite mesmo após formatação
    if len(summary) > _TOOL_RESULT_COMPRESSED_PREVIEW:
        summary = summary[:_TOOL_RESULT_COMPRESSED_PREVIEW - 1] + "…"
    return summary


def _ctx_result_for_context(action: str, result: str) -> tuple[str, bool]:
    """Decide como um resultado de tool deve entrar no contexto.

    Returns:
        (ctx_result, was_compressed)
        ctx_result: string a ser adicionada ao contexto
        was_compressed: True se o resultado foi comprimido
    """
    if len(result) > _TOOL_RESULT_COMPRESS_THRESHOLD:
        return _compress_tool_result_inline(action, result), True
    if len(result) > _MAX_TOOL_RESULT_IN_CTX:
        truncated = result[:_MAX_TOOL_RESULT_IN_CTX]
        return (
            truncated + f"\n[... +{len(result) - _MAX_TOOL_RESULT_IN_CTX} chars omitidos]",
            False,
        )
    return result, False


def _format_tool_display(action: str, result: str) -> str:
    """Formata o resultado de uma tool para exibição no terminal.

    Filtra ruído técnico (headers de formato, paths temporários) e
    mostra apenas o que é relevante para o usuário:
    - execute_code: ✓/✗ + stdout útil ou resumo de erro
    - read_file / write_file / edit_file: confirmação + linha count
    - list_dir / glob_files: contagem + primeiros itens
    - http_request: status + primeiros 100 chars do body
    - Genérico: primeiros 150 chars limpos

    Returns uma Rich markup string (uma linha, raramente duas).
    """
    r = result.strip()
    lines = r.splitlines()

    # ── execute_code ──────────────────────────────────────────────────────────
    if action == "execute_code":
        # Extrai exit code
        exit_code = 0
        exit_line = next((l for l in lines if l.startswith("exit:")), None)
        if exit_line:
            try:
                exit_code = int(exit_line.split(":", 1)[1].strip())
            except (ValueError, IndexError):
                pass

        # Separa blocos stdout / stderr (ignora headers "--- stdout ---" etc.)
        stdout_lines: list[str] = []
        stderr_lines: list[str] = []
        section = None
        for l in lines:
            if l.startswith("exit:"):
                continue
            if l.strip() in ("--- stdout ---", "-- stdout --"):
                section = "out"
                continue
            if l.strip() in ("--- stderr ---", "-- stderr --"):
                section = "err"
                continue
            if section == "out" and l.strip():
                stdout_lines.append(l)
            elif section == "err" and l.strip():
                stderr_lines.append(l)
            elif section is None and l.strip() and not l.startswith("---"):
                # resultado sem seções separadas
                stdout_lines.append(l)

        if exit_code == 0:
            if stdout_lines:
                first = stdout_lines[0][:120]
                extra = len(stdout_lines) - 1
                suffix = f" [dim](+{extra} linhas)[/dim]" if extra > 0 else ""
                return f"[green]✓[/green] [dim]{first}[/dim]{suffix}"
            return "[green]✓[/green]"
        else:
            # Erro: mostra stderr resumido (limpa paths temporários)
            err_clean = [
                l for l in stderr_lines
                if "Temp\\" not in l and "tmp" not in l.lower()[:20]
            ] or stderr_lines
            if err_clean:
                first_err = err_clean[0][:120]
                extra_err = len(err_clean) - 1
                suffix = f" [dim](+{extra_err} linhas)[/dim]" if extra_err > 0 else ""
                return f"[red]✗ exit {exit_code}[/red] [dim]{first_err}[/dim]{suffix}"
            return f"[red]✗ exit {exit_code}[/red]"

    # ── read_file ─────────────────────────────────────────────────────────────
    if action == "read_file":
        n = len([l for l in lines if l.strip()])
        # Mostra a primeira linha de conteúdo se tiver
        first = lines[0][:80].strip() if lines else ""
        return f"[dim]{n} linhas — {first}{'…' if len(lines[0]) > 80 else ''}[/dim]" if first else f"[dim]{n} linhas[/dim]"

    # ── write_file / edit_file / patch_file ───────────────────────────────────
    if action in ("write_file", "edit_file", "patch_file", "create_file"):
        first = lines[0][:120] if lines else r[:120]
        ok = "✗" if ("erro" in first.lower() or "error" in first.lower()) else "✓"
        color = "red" if ok == "✗" else "green"
        return f"[{color}]{ok}[/{color}] [dim]{first}[/dim]"

    # ── list_dir / glob_files ─────────────────────────────────────────────────
    if action in ("list_dir", "glob_files", "regex_search"):
        items = [l.strip() for l in lines if l.strip()]
        n = len(items)
        if n == 0:
            return "[dim](vazio)[/dim]"
        show = ", ".join(items[:4])
        suffix = f" … +{n-4}" if n > 4 else ""
        return f"[dim]{n} itens — {show}{suffix}[/dim]"

    # ── http_request ──────────────────────────────────────────────────────────
    if action == "http_request":
        first = lines[0][:120] if lines else r[:120]
        return f"[dim]{first}[/dim]"

    # ── delegate_task ─────────────────────────────────────────────────────────
    if action == "delegate_task":
        first = lines[0][:120] if lines else r[:120]
        return f"[cyan]⇢[/cyan] [dim]{first}[/dim]"

    # ── genérico ──────────────────────────────────────────────────────────────
    short = r[:150]
    return f"[dim]{short}{'…' if len(r) > 150 else ''}[/dim]"


def _collect_response(
    client: OllamaClient,
    model_name: str,
    payload: list[dict],
) -> str:
    """Coleta a resposta completa do modelo (sem streaming ao usuário).

    Usa chat_with_retry (com exponential backoff) quando disponível — OpenAIClient.
    Fallback para chat_stream direto (OllamaClient e qualquer client sem retry).

    Wave 1 integrations (provider-aware, opt-in):
      - Anthropic prompt caching: applies `cache_control` to system + last 3
        non-system messages so the next turn enjoys cache hits (~75% input cost
        reduction). `apply_anthropic_cache_control()` deep-copies — the input
        `payload` (and the persisted history) is never mutated.
      - Usage capture: after each LLM call, the client's `last_usage` attribute
        holds the raw provider usage dict. Normalised + accumulated outside
        this function via `bauer.account_usage.normalize_usage()`.
    """
    # Plugin hooks — pre_llm_call
    try:
        from .plugin_hooks import hooks as _phooks
        _phooks.ensure_plugins_loaded()
        _phooks.emit("pre_llm_call", model=model_name, messages=payload)
    except Exception:
        pass

    # Anthropic prompt caching — provider-aware no-op for everything else.
    # Caching only kicks in when the system prompt is byte-stable across turns;
    # the deep-copy here ensures we never persist cache_control markers into
    # the conversation history (which would invalidate cache hits).
    api_payload = payload
    try:
        from .prompt_caching import (
            apply_anthropic_cache_control,
            should_apply_cache_control,
        )
        if should_apply_cache_control(client):
            api_payload = apply_anthropic_cache_control(payload)
    except Exception:
        # If caching scaffolding fails for any reason, fall back to the raw
        # payload — better to ship the request than to crash on a cost
        # optimisation.
        api_payload = payload

    # Usa retry automático apenas no OpenAIClient (que tem implementação real).
    # Checar apenas hasattr() seria insuficiente pois MagicMock retorna True para tudo.
    from .openai_client import OpenAIClient as _OpenAIClientClass
    if isinstance(client, _OpenAIClientClass) and hasattr(client, "chat_with_retry"):
        parts = client.chat_with_retry(model_name, api_payload)
    else:
        parts = list(client.chat_stream(model_name, api_payload))
    response = "".join(parts)

    # Sanitiza lone surrogates (U+D800–U+DFFF) que provocam UnicodeEncodeError
    # ao salvar a sessão (SQLite, logging, JSON dump). Origem comum: streaming
    # SSE quebrando um caractere multi-byte UTF-8 entre chunks. Round-trip
    # encode/decode com errors='replace' substitui surrogates por U+FFFD.
    try:
        from .unicode_utils import sanitize_surrogates as _sanitize
        response = _sanitize(response)
    except Exception:
        # Fallback inline se import falhar — encode-decode direto
        response = response.encode("utf-8", errors="replace").decode("utf-8")

    # Plugin hooks — post_llm_call
    try:
        from .plugin_hooks import hooks as _phooks
        _phooks.emit("post_llm_call", model=model_name, messages=payload, response=response)
    except Exception:
        pass

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


def _collect_with_fallback(
    client: OllamaClient,
    model_name: str,
    payload: list[dict],
    fallback_clients: "list | None",
    console: Console,
) -> "tuple[str, Any, str]":
    """Tenta coletar resposta; em falha retryável tenta providers de fallback.

    Returns:
        (response, active_client, active_model_name)

    Raises:
        OllamaError | OpenAIClientError: quando todos os providers falham.
    """
    try:
        return _collect_response(client, model_name, payload), client, model_name
    except (OllamaError, OpenAIClientError) as primary_exc:
        if not fallback_clients:
            raise

        # Classifica o erro — só faz fallback para erros "de provider", não de auth
        _should_fallback = True
        try:
            from .error_classifier import classify_api_error
            classified = classify_api_error(primary_exc)
            _should_fallback = classified.should_fallback
        except Exception:
            pass  # sem classifier: assume que deve tentar fallback

        if not _should_fallback:
            raise

        for fb_client, fb_model in fallback_clients:
            _fb_label = getattr(fb_client, "default_model", fb_model)
            console.print(
                f"[yellow]⚡ Provider falhou — tentando fallback: [bold]{_fb_label}[/bold][/yellow]"
            )
            try:
                resp = _collect_response(fb_client, fb_model, payload)
                return resp, fb_client, fb_model
            except Exception as fb_exc:
                console.print(f"[dim]  Fallback {_fb_label} também falhou: {fb_exc}[/dim]")
                continue

        raise  # todos os fallbacks esgotados


def _run_native_tool_turn(
    ctx,
    router: ToolRouter,
    client,
    model_name: str,
    tool_log: list[dict],
    _guardrail=None,
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

        # Wave 4.5: pre-call guardrail check (native path)
        _native_guardrail_blocked = False
        if _guardrail is not None:
            _pre_n = _guardrail.before_call(name, args)
            if _pre_n.should_halt:
                ctx.add_user(_pre_n.message)
                result = f"[BLOCKED] {_pre_n.message}"
                _native_guardrail_blocked = True
                # For halt: add the blocked result and return immediately
                # after processing all pending tool_calls in this batch.
                if _pre_n.action == "halt":
                    tool_log.append({"tool": name, "result": result[:300]})
                    ctx.messages.append({
                        "role": "tool",
                        "tool_call_id": tc_id,
                        "content": result,
                    })
                    # Signal caller by returning None; run_one_turn will halt.
                    return _pre_n.message

        if not _native_guardrail_blocked:
            _native_failed = False
            try:
                result = router.execute_native_call(name, args)
            except (ToolError, SandboxError) as exc:
                result = f"[Erro: {exc}]"
                _native_failed = True

            # Wave 4.5: post-call guardrail update (native path)
            if _guardrail is not None:
                _post_n = _guardrail.after_call(name, args, result, failed=_native_failed)
                if _post_n.action == "warn":
                    ctx.add_user(_post_n.message)

        ctx_result, _ = _ctx_result_for_context(name, result)
        tool_log.append({"tool": name, "result": result[:300]})
        ctx.messages.append({
            "role": "tool",
            "tool_call_id": tc_id,
            "content": ctx_result,
        })

    return None  # continua o loop


def run_one_turn(
    ctx,
    router: ToolRouter,
    client: OllamaClient,
    model_name: str,
    *,
    budget: "IterationBudget | None" = None,
) -> tuple[str, list[dict]]:
    """Executa um turno completo do agente, incluindo tool calls encadeados.

    Se o cliente suporta native tool calling (OpenAI function calling), usa esse modo.
    Caso contrário, usa o Tool Bridge (JSON parsing da resposta do modelo).

    Args:
        ctx: ContextManager com histórico e payload do prompt.
        router: ToolRouter para dispatch de tools.
        client: cliente LLM (OllamaClient / OpenAIClient / AnthropicClient).
        model_name: nome do modelo a usar nesta chamada.
        budget: IterationBudget opcional. Se None, constrói um com
            `MAX_TOOL_TURNS + 1` como cap. Subagents (delegate_task) podem
            passar um budget próprio para isolar seu cap do parent.

    Pode levantar OllamaError se o modelo falhar.
    Retorna (resposta_final_em_texto, log_de_tool_calls).
    Usado tanto pelo CLI quanto pelo bauer serve.
    """
    from .iteration_budget import IterationBudget as _IterBudget

    tool_log: list[dict] = []
    if budget is None:
        # `MAX_TOOL_TURNS + 1`: até MAX rodadas de tool call, +1 turno final para
        # o modelo emitir resposta de texto após a última tool.
        budget = _IterBudget(max_total=MAX_TOOL_TURNS + 1)

    # Wave 4.5: per-turn guardrail controller (tracks cumulative failures /
    # no-progress across all tool calls in this turn).
    _guardrail = None
    try:
        from .tool_guardrails import ToolCallGuardrailController as _GuardrailCtrl
        _guardrail = _GuardrailCtrl()
    except ImportError:
        pass

    # Native tool calling: disponível em OpenAIClient mas não em OllamaClient.
    # Checa a classe concreta para evitar falsos positivos com MagicMock em testes.
    from .openai_client import OpenAIClient as _OpenAIClient
    use_native = isinstance(client, _OpenAIClient) and getattr(client, "supports_native_tools", False)

    if use_native:
        while not budget.exhausted:
            budget.consume()
            result = _run_native_tool_turn(ctx, router, client, model_name, tool_log,
                                           _guardrail=_guardrail)
            if result is not None:
                return result, tool_log
            # Detecção de loop após cada rodada de tool calls (native path)
            loop_warn, hard_stop = _detect_loop(tool_log)
            if loop_warn:
                ctx.add_user(loop_warn)
                if hard_stop:
                    return "[Loop detectado — tarefa interrompida automaticamente]", tool_log
        # Fallthrough: budget esgotado sem resposta final
        return "[Limite de iterações atingido]", tool_log

    # Tool Bridge (fallback para Ollama e modelos sem native tool calling)
    _empty_retried = False
    while not budget.exhausted:
        budget.consume()
        response = _collect_response(client, model_name, ctx.get_payload())

        # Resposta vazia: pode ser rate-limit silencioso (free tier), filtro
        # de conteudo ou contexto sobrecarregado. Faz 1 retry com backoff
        # antes de desistir — resolve a maioria dos casos transientes.
        if not response.strip():
            if not _empty_retried:
                _empty_retried = True
                import time as _time
                _time.sleep(2.0)  # pequeno backoff antes do retry
                response = _collect_response(client, model_name, ctx.get_payload())

            if not response.strip():
                # Diagnostico acionavel — calcula tamanho aproximado do contexto
                payload = ctx.get_payload()
                approx_chars = sum(
                    len(m.get("content", "") if isinstance(m.get("content"), str) else str(m.get("content", "")))
                    for m in payload
                )
                approx_tokens = approx_chars // 4
                ctx_used_pct = ""
                try:
                    budget = getattr(ctx, "applied_context", 0) or 0
                    if budget:
                        ctx_used_pct = f" (~{approx_tokens * 100 // budget}% do budget)"
                except Exception:
                    pass
                return (
                    f"[Modelo retornou resposta vazia mesmo apos retry]\n"
                    f"  Modelo: {model_name}\n"
                    f"  Contexto: {len(payload)} mensagens, ~{approx_tokens:,} tokens{ctx_used_pct}\n"
                    f"  Provaveis causas:\n"
                    f"    1. Rate-limit silencioso do provider (comum em free tier)\n"
                    f"    2. Filtro de conteudo bloqueando a resposta\n"
                    f"    3. Modelo sobrecarregado no servidor\n"
                    f"  Solucoes:\n"
                    f"    /clear      — limpa o historico e tenta de novo\n"
                    f"    /model      — troca de provider/modelo\n"
                    f"    Aguarde 30s — pode ser rate-limit transiente"
                ), tool_log

        ctx.add_assistant(response)

        # Suporte a batch: modelo pode emitir múltiplos tool calls por resposta
        actions = _try_parse_tools_batch(response, router)

        if actions is not None and len(tool_log) < MAX_TOOL_TURNS:
            combined_parts: list[str] = []
            for action_dict in actions:
                if len(tool_log) >= MAX_TOOL_TURNS:
                    break
                action_name = action_dict.get("action", "?")
                action_args = action_dict.get("args", {}) or {}

                # Wave 4.5: pre-call guardrail check
                if _guardrail is not None:
                    _pre = _guardrail.before_call(action_name, action_args)
                    if _pre.should_halt:
                        ctx.add_user(_pre.message)
                        if _pre.action == "halt":
                            return _pre.message, tool_log
                        tool_result = f"[BLOCKED] {_pre.message}"
                        tool_log.append({"tool": action_name, "result": tool_result[:300]})
                        combined_parts.append(f"[Resultado de {action_name}]\n{tool_result}")
                        continue

                _tool_failed = False
                try:
                    tool_result = router.execute(action_dict)
                except (ToolError, SandboxError) as exc:
                    tool_result = f"[Erro: {exc}]"
                    _tool_failed = True

                # Wave 4.5: post-call guardrail update
                if _guardrail is not None:
                    _post = _guardrail.after_call(
                        action_name, action_args, tool_result, failed=_tool_failed
                    )
                    if _post.action == "warn":
                        ctx.add_user(_post.message)
                    elif _post.should_halt:
                        ctx.add_user(_post.message)
                        tool_log.append({"tool": action_name, "result": tool_result[:300]})
                        return _post.message, tool_log

                tool_log.append({"tool": action_name, "result": tool_result[:300]})

                ctx_result, _ = _ctx_result_for_context(action_name, tool_result)
                combined_parts.append(f"[Resultado de {action_name}]\n{ctx_result}")

            if combined_parts:
                ctx.add_user("\n\n".join(combined_parts))
            # Detecção de loop após cada batch de tool calls (Tool Bridge path)
            loop_warn, hard_stop = _detect_loop(tool_log)
            if loop_warn:
                ctx.add_user(loop_warn)
                if hard_stop:
                    return "[Loop detectado — tarefa interrompida automaticamente]", tool_log
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


def _handle_kanban_cmd(console, workspace: Any = "workspace") -> None:  # type: ignore[type-arg]
    """Exibe o Kanban board (TASKS.md) do workspace ativo dentro da sessao."""
    import sys as _sys
    from rich.columns import Columns
    from rich.panel import Panel as _Panel
    from rich.text import Text as _Text

    try:
        from .workspace_manager import WorkspaceManager
    except ImportError:
        console.print("[dim]WorkspaceManager nao disponivel.[/dim]")
        return

    wm = WorkspaceManager(workspace)
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
        "READY":       "▶" if _utf8 else "[>]",
        "IN_PROGRESS": "🔄" if _utf8 else "[~]",
        "DONE":        "✅" if _utf8 else "[x]",
        "BLOCKED":     "🚫" if _utf8 else "[!]",
        "FAILED":      "✖" if _utf8 else "[x!]",
    }
    _BAR_FULL  = "█" if _utf8 else "#"
    _BAR_EMPTY = "░" if _utf8 else "."

    COLUMNS = [
        ("TODO",        "TODO",        "bright_white"),
        ("READY",       "READY",       "cyan"),
        ("IN_PROGRESS", "IN PROGRESS", "yellow"),
        ("BLOCKED",     "BLOCKED",     "red"),
        ("FAILED",      "FAILED",      "magenta"),
        ("DONE",        "DONE",        "green"),
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


def _handle_task_cmd(user_input: str, console, workspace: Any = "workspace") -> None:  # type: ignore[type-arg]
    """Processa comandos /task digitados dentro da sessao do agente.

    Subcomandos:
      /task               → exibe Kanban board (delega a _handle_kanban_cmd)
      /task list          → lista tarefas com status
      /task add <titulo>  → adiciona nova tarefa
      /task ready <id>    → muda status para READY e habilita dispatcher
      /task start <id>    → muda status para IN_PROGRESS
      /task done <id>     → muda status para DONE
      /task block <id>    → muda status para BLOCKED
      /task fail <id>     → muda status para FAILED
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
        _handle_kanban_cmd(console, workspace)
        return

    wm = WorkspaceManager(workspace)

    if sub in ("list", "ls"):
        tasks = wm.list_tasks()
        if not tasks:
            console.print("[dim]Nenhuma tarefa. Use [bold]/task add <titulo>[/bold] para criar.[/dim]")
            return
        _STATUS_COLORS = {
            "TODO": "bright_white", "READY": "cyan",
            "IN_PROGRESS": "yellow", "DONE": "green",
            "BLOCKED": "red", "FAILED": "magenta",
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

    if sub == "ready":
        task_id = parts[2].strip() if len(parts) > 2 else ""
        if not task_id:
            console.print("[yellow]Uso: [bold]/task ready <id>[/bold][/yellow]")
            return
        try:
            from .task_dispatcher import TaskDispatcher
            task = TaskDispatcher(workspace).mark_ready(task_id)
            console.print(
                f"[green]Tarefa pronta para dispatcher:[/green] "
                f"[[dim]{task.id}[/dim]] {task.title} → [READY]"
            )
        except Exception as exc:
            console.print(f"[red]Erro:[/red] {exc}")
        return

    # start / done / block / fail → precisam de <id>
    _STATUS_MAP = {"start": "IN_PROGRESS", "done": "DONE", "block": "BLOCKED", "fail": "FAILED"}
    if sub in _STATUS_MAP:
        task_id = parts[2].strip() if len(parts) > 2 else ""
        if not task_id:
            console.print(f"[yellow]Uso: [bold]/task {sub} <id>[/bold][/yellow]")
            return
        new_status = _STATUS_MAP[sub]
        try:
            task = wm.update_task_status(task_id, new_status)
            _VERBS = {"IN_PROGRESS": "iniciada", "DONE": "concluida", "BLOCKED": "bloqueada", "FAILED": "falhou"}
            console.print(
                f"[green]Tarefa {_VERBS[new_status]}:[/green] "
                f"[[dim]{task.id}[/dim]] {task.title} → [{new_status}]"
            )
        except Exception as exc:
            console.print(f"[red]Erro:[/red] {exc}")
        return

    console.print(f"[yellow]Subcomando desconhecido: [bold]/task {sub}[/bold][/yellow]")
    console.print("[dim]Disponiveis: add | list | ready | start | done | block | fail[/dim]")


def _handle_dispatch_cmd(user_input: str, console, workspace: Any = "workspace") -> None:  # type: ignore[type-arg]
    """Processa comandos /dispatch dentro da sessao do agente."""
    import shlex
    from collections import Counter

    from rich.table import Table

    try:
        parts = shlex.split(user_input)
    except ValueError as exc:
        console.print(f"[red]Erro ao ler comando:[/red] {exc}")
        return

    sub = parts[1].lower() if len(parts) > 1 else "once"
    args = parts[2:]

    def _int_option(names: tuple[str, ...], default: int) -> int:
        for idx, token in enumerate(args):
            for name in names:
                if token == name and idx + 1 < len(args):
                    try:
                        return max(1, int(args[idx + 1]))
                    except ValueError:
                        return default
                prefix = name + "="
                if token.startswith(prefix):
                    try:
                        return max(1, int(token[len(prefix):]))
                    except ValueError:
                        return default
        return default

    if sub in ("help", "-h", "--help"):
        console.print(
            "[bold]Uso:[/bold]\n"
            "  /dispatch                 # um tick em background\n"
            "  /dispatch once --dry-run  # mostra o que seria claimed\n"
            "  /dispatch once --foreground\n"
            "  /dispatch status\n"
            "  /dispatch reclaim\n"
            "  /dispatch cancel <id>\n"
            "  /dispatch retry <id>\n"
            "\n[dim]READY e fila. IN_PROGRESS e worker claimed. DONE/FAILED sao finais.[/dim]"
        )
        return

    try:
        from .task_dispatcher import TaskDispatcher
        from .workspace_manager import WorkspaceManager
    except ImportError as exc:
        console.print(f"[red]Dispatcher nao disponivel:[/red] {exc}")
        return

    if sub in ("status", "queue", "fila"):
        from .kanban_store import KanbanStore

        wm = WorkspaceManager(workspace)
        store = KanbanStore(workspace)
        tasks = wm.list_tasks()
        counts = Counter(t.status for t in tasks)
        table = Table(title=f"Dispatcher - {workspace}", show_lines=False)
        table.add_column("Status", style="cyan")
        table.add_column("Qtd", justify="right")
        for status in ("READY", "IN_PROGRESS", "FAILED", "DONE", "BLOCKED", "TODO"):
            table.add_row(status, str(counts.get(status, 0)))
        console.print(table)

        running = [t for t in tasks if t.status == "IN_PROGRESS" and t.metadata.get("dispatch") == "true"]
        if running:
            run_table = Table(title="Claims ativos", show_lines=False)
            run_table.add_column("ID", style="dim", no_wrap=True)
            run_table.add_column("Tentativas", justify="right")
            run_table.add_column("Worker")
            run_table.add_column("Heartbeat", style="dim")
            run_table.add_column("Titulo")
            for task in running:
                run_table.add_row(
                    task.id,
                    task.metadata.get("attempts", "0"),
                    task.metadata.get("worker_pid") or task.metadata.get("claimed_by", ""),
                    task.metadata.get("heartbeat_at", ""),
                    task.title,
                )
            console.print(run_table)
        else:
            console.print("[dim]Nenhum claim ativo do dispatcher.[/dim]")

        runs = store.list_runs(statuses=["claimed", "running", "retrying"], limit=10)
        if runs:
            runs_table = Table(title="Runs ativos/recentes", show_lines=False)
            runs_table.add_column("Run", style="dim")
            runs_table.add_column("Task")
            runs_table.add_column("Status")
            runs_table.add_column("Tent.", justify="right")
            runs_table.add_column("Heartbeat", style="dim")
            for run in runs:
                runs_table.add_row(run.run_id, run.task_id, run.status, str(run.attempt), run.heartbeat_at)
            console.print(runs_table)

        events = store.list_events(limit=5)
        if events:
            events_table = Table(title="Ultimos eventos", show_lines=False)
            events_table.add_column("Task")
            events_table.add_column("Evento")
            events_table.add_column("Ator")
            events_table.add_column("Mensagem")
            for event in events:
                events_table.add_row(event.task_id, event.event_type, event.actor, event.message[:80])
            console.print(events_table)
        return

    if sub in ("daemon", "loop"):
        console.print(
            "[yellow]/dispatch daemon nao roda dentro do chat para nao bloquear a sessao.[/yellow]\n"
            "[dim]Use no terminal: [bold]bauer dispatch daemon --workspace <workspace>[/bold][/dim]"
        )
        return

    if sub in ("reclaim", "recover"):
        dispatcher = TaskDispatcher(workspace)
        crashed = dispatcher.detect_crashed_workers()
        reclaimed = dispatcher.reclaim_stale()
        console.print(
            "[bold cyan]dispatch reclaim[/bold cyan] "
            f"crashed={len(crashed)} reclaimed={len(reclaimed)}"
        )
        if crashed:
            console.print(f"[dim]crashed:[/dim] {', '.join(crashed)}")
        if reclaimed:
            console.print(f"[dim]reclaimed:[/dim] {', '.join(reclaimed)}")
        return

    if sub in ("cancel", "retry"):
        if not args:
            console.print(f"[yellow]Uso: /dispatch {sub} <task_id>[/yellow]")
            return
        dispatcher = TaskDispatcher(workspace)
        try:
            if sub == "cancel":
                task = dispatcher.cancel_task(args[0], reason="cancelado via chat")
                console.print(f"[yellow]{task.id}[/yellow] -> [BLOCKED] {task.title}")
            else:
                task = dispatcher.retry_failed(args[0], reason="retry via chat")
                console.print(f"[cyan]{task.id}[/cyan] -> [READY] {task.title}")
        except Exception as exc:
            console.print(f"[red]Erro no dispatcher:[/red] {exc}")
        return

    if sub not in ("once", "run", "tick"):
        console.print(f"[yellow]Subcomando desconhecido: [bold]/dispatch {sub}[/bold][/yellow]")
        console.print("[dim]Disponiveis: once | status | reclaim | cancel | retry | help[/dim]")
        return

    dry_run = any(a in ("--dry-run", "--dry") for a in args)
    foreground = any(a in ("--foreground", "-f") for a in args)
    max_spawn = _int_option(("--max-spawn", "--max"), 1)
    max_in_progress = _int_option(("--max-in-progress", "--limit"), 1)

    dispatcher = TaskDispatcher(workspace)
    try:
        result = dispatcher.dispatch_once(
            dry_run=dry_run,
            max_spawn=max_spawn,
            max_in_progress=max_in_progress,
            spawn_background=not foreground,
        )
    except Exception as exc:
        console.print(f"[red]Erro no dispatcher:[/red] {exc}")
        return

    console.print(
        "[bold cyan]dispatch once[/bold cyan] "
        f"crashed={len(result.crashed)} reclaimed={len(result.reclaimed)} claimed={len(result.claimed)} "
        f"spawned={len(result.spawned)} completed={len(result.completed)} "
        f"failed={len(result.failed)} dry={len(result.dry_run)}"
    )
    any_activity = False
    for label, items in (
        ("crashed", result.crashed),
        ("reclaimed", result.reclaimed),
        ("claimed", result.claimed),
        ("spawned", result.spawned),
        ("completed", result.completed),
        ("failed", result.failed),
        ("dry", result.dry_run),
        ("skipped", result.skipped),
    ):
        if items:
            any_activity = True
            console.print(f"[dim]{label}:[/dim] {', '.join(items)}")

    if result.spawned and not foreground:
        console.print("[dim]Workers em background. Acompanhe com [bold]/task[/bold] ou [bold]/dispatch status[/bold].[/dim]")
    if not any_activity:
        console.print("[dim]Nenhuma task READY elegivel. Use [bold]/task ready <id>[/bold] para entrar na fila.[/dim]")


def _handle_ops_cmd(user_input: str, console, workspace: Any = "workspace") -> None:  # type: ignore[type-arg]
    """Processa /ops dentro da sessao do agente."""
    from rich.table import Table

    from .ops_status import build_ops_status

    parts = user_input.strip().split()
    sub = parts[1].lower() if len(parts) > 1 else "status"
    if sub not in ("status", "queue", "fila", "lanes"):
        console.print("[yellow]Uso: /ops status[/yellow]")
        return

    status = build_ops_status(workspace, limit=8)
    counts = status["status_counts"]
    summary = Table(title=f"Ops - {workspace}", show_lines=False)
    summary.add_column("Status", style="cyan")
    summary.add_column("Qtd", justify="right")
    for name in ("READY", "IN_PROGRESS", "FAILED", "BLOCKED", "TODO", "DONE"):
        summary.add_row(name, str(counts.get(name, 0)))
    console.print(summary)

    lanes = status.get("lanes", [])
    if lanes:
        lane_table = Table(title="Lanes", show_lines=False)
        lane_table.add_column("Lane", style="cyan")
        lane_table.add_column("Agent")
        lane_table.add_column("Cap.", justify="right")
        lane_table.add_column("Ready", justify="right")
        lane_table.add_column("Run", justify="right")
        lane_table.add_column("Fail", justify="right")
        for lane in lanes:
            lane_table.add_row(
                str(lane.get("lane", "")),
                str(lane.get("agent", "")),
                str(lane.get("max_concurrent", "")),
                str(lane.get("ready", 0)),
                str(lane.get("running", 0)),
                str(lane.get("failed", 0)),
            )
        console.print(lane_table)

    claims = status.get("active_claims", [])
    if claims:
        claim_table = Table(title="Claims ativos", show_lines=False)
        claim_table.add_column("Task", style="cyan")
        claim_table.add_column("Lane")
        claim_table.add_column("PID")
        claim_table.add_column("Alive")
        claim_table.add_column("Lease")
        for claim in claims:
            lease = claim.get("claim_seconds_left")
            claim_table.add_row(
                str(claim.get("public_id", "")),
                str(claim.get("lane", "")),
                str(claim.get("worker_pid") or ""),
                str(claim.get("worker_alive")),
                "" if lease is None else f"{lease}s",
            )
        console.print(claim_table)
    else:
        console.print("[dim]Nenhum claim ativo.[/dim]")

    events = status.get("recent_events", [])[:5]
    if events:
        events_table = Table(title="Eventos recentes", show_lines=False)
        events_table.add_column("Task")
        events_table.add_column("Evento")
        events_table.add_column("Mensagem")
        for event in events:
            events_table.add_row(
                str(event.get("task_id", "")),
                str(event.get("event_type", "")),
                str(event.get("message", ""))[:80],
            )
        console.print(events_table)


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


def _handle_project_cmd(console, workspace: Any = "workspace") -> None:  # type: ignore[type-arg]
    """Exibe PROJECT.md e um resumo das tarefas do workspace."""
    from pathlib import Path as _Path
    from rich.panel import Panel as _Panel
    from rich.rule import Rule as _Rule

    project_file = _Path(workspace) / "PROJECT.md"
    tasks_summary_parts: list[str] = []

    # Tenta carregar resumo de tarefas
    try:
        from .workspace_manager import WorkspaceManager
        wm = WorkspaceManager(workspace)
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
                f"[cyan]{counts.get('READY', 0)} READY[/cyan] | "
                f"[white]{counts.get('TODO', 0)} TODO[/white] | "
                f"[red]{counts.get('BLOCKED', 0)} BLOCKED[/red] | "
                f"[magenta]{counts.get('FAILED', 0)} FAILED[/magenta] | "
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
    fallback_clients: "list | None" = None,
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
        fallback_clients: Lista de (client, model_name) para tentar quando o provider
            principal falha com erro retryável (PROVIDER_DOWN / QUOTA_EXCEEDED).
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
    # `provider` is needed for cost lookup in usage_pricing. Derive from the
    # ContextManager's `_provider` (set above from cfg) — falls back to "" which
    # cleanly disables costing without errors.
    stats = SessionStats(
        model=model_name,
        context_tokens=applied_context,
        machine_id=get_machine_id(),
        provider=_provider or "",
    )
    skills = SkillRegistry()
    routing = model_router is not None and model_router.config.enabled
    orch_enabled = orchestrator is not None and routing

    console.print(Rule(f"[bold]Bauer Agent[/bold] — {model_name}"))
    console.print(
        f"[dim]Contexto: {applied_context} tokens | Tokens usados: 0[/dim]\n"
    )

    # Plugin hooks — session_start
    try:
        from .plugin_hooks import hooks as _phooks
        _phooks.ensure_plugins_loaded()
        _phooks.emit("session_start", session_id=session_id or "local", model=model_name)
    except Exception:
        pass

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
            try:
                from .plugin_hooks import hooks as _phooks
                _phooks.emit("session_end", session_id=session_id or "local", model=model_name)
            except Exception:
                pass
            return

        if not user_input:
            continue
        if user_input.lower() in _EXIT_CMDS:
            console.print("[dim]Ate logo.[/dim]")
            stats.save()
            if session_store is not None and session_id:
                session_store.save(session_id, ctx.messages)
                console.print(f"[dim]Sessao salva: {session_id}[/dim]")
            try:
                from .plugin_hooks import hooks as _phooks
                _phooks.emit("session_end", session_id=session_id or "local", model=model_name)
            except Exception:
                pass
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
        active_workspace = getattr(router, "workspace", "workspace")

        if user_input.lower() in _KANBAN_CMDS:
            _handle_kanban_cmd(console, active_workspace)
            continue
        if user_input.lower() == "/agents" or user_input.lower().startswith("/agent "):
            _handle_agent_cmd(user_input, console)
            continue
        if user_input.lower().startswith("/task "):
            _handle_task_cmd(user_input, console, active_workspace)
            continue
        if user_input.lower() in _DISPATCH_CMDS or user_input.lower().startswith("/dispatch "):
            _handle_dispatch_cmd(user_input, console, active_workspace)
            continue
        if user_input.lower() in _OPS_CMDS or user_input.lower().startswith("/ops "):
            _handle_ops_cmd(user_input, console, active_workspace)
            continue
        if user_input.lower().startswith("/memory"):
            _handle_memory_cmd(user_input, console)
            continue
        if user_input.lower() in _PROJECT_CMDS:
            _handle_project_cmd(console, active_workspace)
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
        cli_tool_log: list[dict] = []  # para detecção de loop no CLI path
        while True:
            # Aviso precoce de contexto cheio — antes de travar silenciosamente
            usage = ctx.usage_pct
            if usage >= _CTX_WARN_THRESHOLD:
                pct = int(usage * 100)
                console.print(
                    f"[yellow]⚠ Contexto em {pct}% do budget "
                    f"({ctx.used_tokens}/{ctx.budget} tokens). "
                    "Use [bold]/clear[/bold] se o modelo ficar lento.[/yellow]"
                )
            try:
                response, client, active_model = _collect_with_fallback(
                    client, active_model, ctx.get_payload(), fallback_clients, console
                )
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

                # Só processa o que cabe dentro do limite de tool turns
                pending_actions = [a for a in actions if tool_turns < MAX_TOOL_TURNS]
                if len(actions) > len(pending_actions):
                    console.print(
                        f"[yellow]Limite de {MAX_TOOL_TURNS} tool calls atingido "
                        "neste turno.[/yellow]"
                    )

                # Execução paralela quando o modelo emitiu múltiplos tool calls de uma vez
                if len(pending_actions) > 1:
                    from concurrent.futures import ThreadPoolExecutor
                    from concurrent.futures import as_completed as _as_completed

                    def _exec_action(action_dict: dict) -> tuple[str, str]:
                        _name = action_dict.get("action", "?")
                        try:
                            _result = router.execute(action_dict)
                        except (ToolError, SandboxError) as _exc:
                            _result = f"[Erro: {_exc}]"
                        return _name, _result

                    ordered_results: list[tuple[str, str]] = [("", "")] * len(pending_actions)
                    with ThreadPoolExecutor(max_workers=min(len(pending_actions), 8)) as _ex:
                        _fmap = {_ex.submit(_exec_action, a): i for i, a in enumerate(pending_actions)}
                        for _fut in _as_completed(_fmap):
                            ordered_results[_fmap[_fut]] = _fut.result()
                else:
                    ordered_results = []
                    for a in pending_actions:
                        _name = a.get("action", "?")
                        try:
                            _result = router.execute(a)
                        except (ToolError, SandboxError) as exc:
                            _result = f"[Erro: {exc}]"
                        ordered_results.append((_name, _result))

                for action_name, tool_result in ordered_results:
                    # Display inteligente — filtra ruído, mostra apenas o relevante
                    display_line = _format_tool_display(action_name, tool_result)
                    console.print(f"  [dim]→[/dim] [cyan]{action_name}[/cyan]  {display_line}")

                    # Comprime resultado para o contexto — reduz impacto de resultados grandes
                    ctx_result, was_compressed = _ctx_result_for_context(action_name, tool_result)

                    combined_parts.append(f"[Resultado de {action_name}]\n{ctx_result}")
                    cli_tool_log.append({"tool": action_name, "result": tool_result[:300]})
                    tool_turns += 1

                if combined_parts:
                    console.print()
                    ctx.add_user("\n\n".join(combined_parts))

                # Detecção de loop após cada batch de tool calls (CLI path)
                loop_warn, hard_stop = _detect_loop(cli_tool_log)
                if loop_warn:
                    console.print(f"[bold yellow]⚠ {loop_warn}[/bold yellow]")
                    ctx.add_user(loop_warn)
                    if hard_stop:
                        console.print("[red]Loop detectado — interrompendo turno automaticamente.[/red]")
                        break
                continue

            if tool_turns >= MAX_TOOL_TURNS:
                console.print(
                    f"[yellow]Limite de {MAX_TOOL_TURNS} tool calls atingido "
                    "neste turno.[/yellow]"
                )

            # Resposta final em texto — extrai se o modelo usou JSON de conversa
            display = _extract_text_from_pseudo_json(response) or response
            stats.end_turn(len(display))

            # Wave 1 — capture real token usage from the client (provider-agnostic).
            # `last_usage` is populated by openai_client / anthropic_client; if the
            # provider doesn't surface usage (e.g. Ollama local, some compat
            # backends), record_turn_usage gets {} and is a no-op.
            _turn_usage: dict = {}
            _turn_cost_line: str = ""
            try:
                _raw_usage = getattr(client, "last_usage", None) or {}
                _turn_usage = stats.record_turn_usage(_raw_usage)
                # Compose a one-line cost summary for the user. Only show when
                # we actually have token data — silent fallback to old behaviour
                # for providers without usage support.
                if _turn_usage and (_turn_usage.get("total_tokens", 0) > 0):
                    from .usage_pricing import format_cost as _fmt_cost
                    _in = _turn_usage.get("prompt_tokens", 0)
                    _out = _turn_usage.get("completion_tokens", 0)
                    _cache_read = _turn_usage.get("cache_read_input_tokens", 0)
                    _cache_part = ""
                    if _cache_read and _in:
                        _cache_part = f" cache:{_cache_read * 100 // _in}%"
                    _cost_now = _fmt_cost(stats.cost_usd_total)
                    _turn_cost_line = (
                        f"[dim]  ↳ {_in}→{_out} tok{_cache_part} | "
                        f"sess total: {_cost_now}[/dim]"
                    )
            except Exception:
                pass  # never block the chat on a cost-display failure

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
            if _turn_cost_line:
                console.print(_turn_cost_line)
            break
