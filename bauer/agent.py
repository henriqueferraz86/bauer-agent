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

import hashlib
import json
import os
import platform
import re
import sys
import time
from contextlib import contextmanager, nullcontext
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal

from rich.console import Console
from rich.rule import Rule

from . import theme as _theme_mod
from .context_manager import ContextManager
from .machine_id import machine_id as get_machine_id
from .model_router import ModelRouter
from .ollama_client import OllamaClient, OllamaError
from .openai_client import OpenAIClientError
from .performance_tracker import SessionStats
from .skill_registry import SkillRegistry
from .tool_router import SandboxError, ToolError, ToolRouter
from .agent_slash_commands import (
    _handle_agent_cmd,
    _handle_dispatch_cmd,
    _handle_kanban_cmd,
    _handle_memory_cmd,
    _handle_ops_cmd,
    _handle_project_cmd,
    _handle_spec_cmd,
    _handle_task_cmd,
)

if TYPE_CHECKING:
    from .orchestrator import AgentOrchestrator
    from .session_store import SessionStore
    from .app_verify import VerifyResult
    from .config_loader import LoopSection
    from .loop_skills import LoopSkill

_EXIT_CMDS = {"/exit", "/quit", "/sair"}
_CLEAR_CMDS = {"/clear", "/limpar"}
_STATUS_CMDS = {"/status", "/stats"}
_MODEL_CMDS = {"/model", "/modelo"}
_THEME_CMDS = {"/theme", "/tema", "/cor"}
_SESSIONS_CMDS = {"/sessions", "/sessoes"}
_SPEC_CMDS = {"/spec", "/specs"}
_KANBAN_CMDS = {"/kanban", "/board", "/tasks", "/task"}   # bare /task → board
_LOOP_SKILL_CMDS = {"/loop-skill", "/loop-skills"}
_DISPATCH_CMDS = {"/dispatch"}
_OPS_CMDS = {"/ops"}
_PROJECT_CMDS = {"/project", "/proj", "/projeto"}
_AGENT_MGR_CMDS = {"/agents", "/agent list", "/agent create", "/agent delete"}  # gestão de agents
_LISTEN_CMDS = {"/listen", "/ouvir"}
_LISTEN_LOOP_CMDS = {"/listen loop", "/listen auto", "/ouvir loop", "/ouvir auto"}
_LISTEN_LOOP_STOP_WORDS = {
    "parar",
    "para",
    "cancelar",
    "sair",
    "encerrar",
    "stop",
    "cancel",
    "exit",
}
_THUMBSUP_CMDS = {"/thumbsup", "/bom", "/positivo", "/like"}
_THUMBSDOWN_CMDS = {"/thumbsdown", "/ruim", "/negativo", "/dislike"}

# Sub-comandos exibidos no menu de autocomplete
_SLASH_BASE = [
    "/exit",
    "/clear",
    "/status",
    "/model",
    "/theme",
    "/listen",
    "/listen loop",
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
    "/thumbsup",
    "/thumbsdown",
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
    "/theme":          "troca a cor de acento (ou Ctrl+T p/ ciclar)",
    "/listen":         "fala com o Bauer pelo microfone",
    "/listen loop":    "conversa por voz ate cancelar",
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
    from prompt_toolkit.cursor_shapes import CursorShape
    from prompt_toolkit.document import Document as PtDocument
    from prompt_toolkit.formatted_text import HTML
    from prompt_toolkit.history import FileHistory, InMemoryHistory
    from prompt_toolkit.key_binding import KeyBindings
    from prompt_toolkit.styles import DynamicStyle
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

    def _build_pt_style() -> "PtStyle":
        """Estilo do prompt_toolkit DERIVADO do tema.

        Até o plano 028 isto era um dicionário de cores literais — Catppuccin
        (#313244/#cdd6f4/#89b4fa) no menu de completions, o teal antigo
        (#00d4aa) na marca e `ansicyan` no `❯`. Ou seja: o prompt onde o
        usuário digita e o popup de autocomplete NUNCA migraram no F0, porque o
        teste que proíbe cor literal cobria só `ui.py`, `ascii_intro.py` e
        `indicators.py`. Reconstruído a cada troca de acento (ver `/theme`).
        """
        t = _theme_mod
        return PtStyle.from_dict({
            "prompt": f"bold {t.ACCENT_TEXT}",
            "completion-menu.completion":         f"bg:{t.SURFACE} {t.WHITE}",
            "completion-menu.completion.current": f"bg:{t.ACCENT} {t.VOID} bold",
            "completion-menu.meta.completion":         f"bg:{t.SURFACE} {t.DIM}",
            "completion-menu.meta.completion.current": f"bg:{t.ACCENT} {t.VOID}",
            # Barra de status fixa (bottom toolbar) — identidade BAUER + modelo
            # + tokens, sempre visível enquanto o prompt está ativo.
            "bottom-toolbar":       f"noreverse bg:{t.SURFACE} {t.DIM}",
            "bottom-toolbar.brand": f"bold {t.ACCENT_TEXT}",
            "bottom-toolbar.model": t.CLOUD,
        })

    #: Estilo DINÂMICO: `DynamicStyle` chama a fábrica a cada render, então a
    #: troca de acento (Ctrl+T) aparece no prompt e no menu de completions
    #: imediatamente, sem recriar a PromptSession.
    _PT_STYLE = DynamicStyle(_build_pt_style)

    _PROMPT_FRAGMENTS = [("class:prompt", "bauer ❯ ")]

    def _make_slash_kb(ao_trocar_tema=None) -> "KeyBindings":
        """Key bindings do prompt: '/' abre completions, Ctrl+T cicla o acento.

        `ao_trocar_tema` (opcional) é chamado depois da troca, com o terminal
        já devolvido — a sessão passa um redesenho do cabeçalho. Sem ele o
        atalho só troca a cor dos elementos vivos (prompt e barra de status),
        porque o resto da tela é scrollback e não pode ser recolorido.
        """
        kb = KeyBindings()

        @kb.add("/")
        def _on_slash(event):
            event.current_buffer.insert_text("/")
            event.current_buffer.start_completion(select_first=False)

        def _ciclar_tema(event) -> None:
            try:
                from prompt_toolkit.application import run_in_terminal

                from . import theme as _t
                # Nome capturado AQUI, no momento da tecla. `run_in_terminal`
                # agenda a função para rodar depois; lendo `_t.ACCENT_NAME`
                # lá dentro, duas teclas rápidas anunciavam o mesmo acento (o
                # final) duas vezes — observado no teste real.
                _aplicado = _t.next_accent()
                _persistir_acento(_aplicado)

                # CONFIRMAÇÃO EXPLÍCITA. Sem ela o atalho é indistinguível de
                # não funcionar: o que muda na tela é a cor do `❯` e da barra
                # de status — dois elementos pequenos. Reportado em uso real
                # ("ctrl+t não funciona no Linux"); o mecanismo estava certo,
                # a troca é que era imperceptível.
                def _anunciar() -> None:
                    # Console novo, não o da sessão: `_make_slash_kb` vive no
                    # escopo de MÓDULO, onde não há `console` — a primeira
                    # versão referenciava um nome inexistente e o NameError
                    # era engolido pelo `except` abaixo, deixando o atalho
                    # mudo. `run_in_terminal` já devolveu o terminal, então
                    # escrever em stdout aqui é seguro.
                    if ao_trocar_tema is not None:
                        ao_trocar_tema()          # limpa e reimprime o cabeçalho
                    from .ui import accent_swatch_line
                    Console().print(accent_swatch_line(_aplicado))

                run_in_terminal(_anunciar)
                event.app.invalidate()  # redesenha prompt/toolbar na cor nova
            except Exception as _exc:  # noqa: BLE001 — atalho nunca derruba o prompt
                from .logging_config import log_suppressed
                log_suppressed("theme.atalho_ciclar", _exc)

        # Ctrl+T cicla a cor de acento — o atalho PORTÁVEL.
        #
        # Ctrl+T é ASCII 20 (DC4): todo terminal transmite, em qualquer SO.
        # Ctrl+DÍGITO não tem código ASCII (só Ctrl+A..Z = 1..26 e
        # Ctrl+] [ ^ _ = 27..31), então num terminal comum Ctrl+0 chega como o
        # caractere "0" — medido. Os dois protocolos que codificariam a tecla
        # (`modifyOtherKeys` do xterm, `CSI 27;5;48~`, e o formato kitty,
        # `CSI 48;5u`) NÃO são entendidos pelo parser do prompt_toolkit, que
        # espera `\x1b[1;5p` — uma convenção própria que quase nenhum terminal
        # emite. Num terminal com esses protocolos ligados, Ctrl+0 injetaria
        # lixo no input em vez de trocar a cor.
        #
        # Custo do Ctrl+T: perde o `transpose-chars` do emacs mode — irrelevante
        # num prompt de conversa.
        kb.add("c-t")(_ciclar_tema)

        # Ctrl+0 segue registrado como bônus: no Windows funciona, porque o
        # console Win32 reporta virtual key + modificador direto para a API,
        # sem passar por sequência de escape.
        kb.add("c-0")(_ciclar_tema)

        return kb

    def _make_prompt_session(bottom_toolbar=None, ao_trocar_tema=None) -> "PromptSession":
        # Histórico persistido entre sessões em ~/.bauer/.cli_history
        import os as _os
        from pathlib import Path as _Path
        _bauer_home = _Path(_os.environ.get("BAUER_HOME", str(_Path.home() / ".bauer")))
        _bauer_home.mkdir(parents=True, exist_ok=True)
        _hist_path = _bauer_home / ".cli_history"
        try:
            _history = FileHistory(str(_hist_path))
        except Exception:
            _history = InMemoryHistory()  # fallback se o arquivo não puder ser criado

        # create_output detecta Win32Console / VT100 e habilita o popup de completions.
        # Sem isso, em certos terminais Windows o menu de autocomplete não renderiza.
        # Se falhar (ex: Git Bash sem pty), usa output=None (auto-detect do prompt_toolkit).
        import sys as _sys
        try:
            from prompt_toolkit.output import create_output as _create_output
            _output = _create_output(stdout=_sys.stdout)
        except Exception:
            _output = None

        return PromptSession(
            completer=_SlashCompleter(),
            complete_while_typing=True,
            history=_history,
            style=_PT_STYLE,
            mouse_support=False,
            key_bindings=_make_slash_kb(ao_trocar_tema),
            output=_output,
            cursor=CursorShape.BLINKING_UNDERLINE,
            bottom_toolbar=bottom_toolbar,
        )

    _PT_AVAILABLE = True

except ImportError:
    _PT_AVAILABLE = False
    _make_prompt_session = None  # type: ignore[assignment]
    _PT_STYLE = None             # type: ignore[assignment]


def _capture_listen_input(console: Console) -> str | None:
    """Capture microphone input and return the transcribed text for a chat turn."""
    try:
        from .audio_capture import capture_voice_input

        text = capture_voice_input(console=console)
    except KeyboardInterrupt:
        console.print("[yellow]Audio cancelado.[/yellow]")
        return None
    except ImportError as exc:
        console.print(f"[red]{exc}[/red]")
        return None
    except Exception as exc:
        console.print(f"[red]Erro ao ouvir: {exc}[/red]")
        return None

    text = (text or "").strip()
    if not _is_meaningful_voice_text(text):
        console.print("[yellow]Nada util foi transcrito; tente falar novamente.[/yellow]")
        return None
    return text


def _is_meaningful_voice_text(text: str) -> bool:
    compact = text.strip()
    if len(compact) < 2:
        return False
    return bool(re.search(r"[\wÀ-ÿ]", compact, flags=re.UNICODE))


def _is_listen_loop_stop(text: str) -> bool:
    normalized = re.sub(r"[^\wÀ-ÿ\s-]", "", text.strip().lower(), flags=re.UNICODE)
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return normalized in _LISTEN_LOOP_STOP_WORDS


def _set_blink_underline() -> None:
    """Pede ao terminal um cursor sublinhado piscante (DECSCUSR `ESC[3 q`).

    Usado no fallback console.input (sem prompt_toolkit). Silencioso se o
    terminal não suportar a sequência.
    """
    try:
        sys.stdout.write("\x1b[3 q")
        sys.stdout.flush()
    except Exception:
        pass


MAX_TOOL_TURNS = 150

# ─── Loop detection ────────────────────────────────────────────────────────────
# Protege contra modelos que ficam chamando a mesma tool repetidamente.
# Fingerprint = tool_name + primeiros 100 chars do resultado; para chamadas
# BEM-SUCEDIDAS, inclui também o hash dos args (evita falso-positivo em
# list_dir("a"), list_dir("b")...). Para chamadas com ERRO, ignora os args —
# um modelo preso tentando args diferentes pro mesmo erro ainda conta como
# repetição (ver _loop_fp).
_LOOP_REPEAT_HARD  = 5   # N° de repetições consecutivas → hard stop imediato
_LOOP_OSCIL_WINDOW = 6   # Janela de calls para detectar padrão A→B→A→B


def _args_sig(args: object) -> str:
    """Hash curto dos args — distingue chamadas com args diferentes (evita falso-positivo no loop)."""
    try:
        raw = json.dumps(args, sort_keys=True, default=str, ensure_ascii=False)
        return hashlib.md5(raw.encode(), usedforsecurity=False).hexdigest()[:8]
    except Exception:
        return ""


def _is_failed_result(result: str) -> bool:
    """True se o resultado de uma tool indica erro/bloqueio — mesma convenção
    de prefixo usada em toda a base ("[Erro: ...]", "[BLOCKED] ...")."""
    return result.startswith("[Erro:") or result.startswith("[BLOCKED]")


def _loop_fp(entry: dict) -> str:
    """Fingerprint de loop: nome + primeiros 100 chars do resultado, com
    args_sig incluído SÓ quando o resultado foi bem-sucedido.

    Motivo da distinção (bug real, 2026-07-02): um modelo pequeno preso
    tentando a mesma tool sem conseguir montar os args certos varia os args
    a cada tentativa (às vezes {}, às vezes um nome de campo errado) — mas o
    ERRO é sempre o mesmo ("run_command requer 'command'."). Incluir
    args_sig incondicionalmente (P2.1, commit 5047d34) fazia cada tentativa
    malformada gerar uma fingerprint DIFERENTE, nunca acumulando as 5
    repetições consecutivas necessárias pro hard-stop — o modelo repetia o
    mesmo erro indefinidamente sem nenhuma proteção.
    Para resultados de SUCESSO, args_sig continua incluído — é o que evita o
    falso-positivo original que motivou adicioná-lo (list_dir("a"),
    list_dir("b"), list_dir("c") são legítimos mesmo com resultado inicial
    parecido; aí SIM os args diferentes devem contar como tentativas distintas).
    """
    result = entry.get("result", "")
    # Replays do deduper vêm prefixados com um aviso que ESCALA a cada
    # repetição ("[dedup] 2ª repetição…", "3ª…") — sem normalizar, cada replay
    # gera uma fingerprint diferente e o hard-stop nunca acumula as 5
    # repetições (bug real: modelo repetiu a mesma call 150x até estourar o
    # budget). O resultado subjacente vem após o \n do aviso.
    if result.startswith("[dedup]") and "\n" in result:
        result = result.split("\n", 1)[1]
    if _is_failed_result(result):
        return f"{entry['tool']}:{result[:100]}"
    sig = entry.get("args_sig", "")
    return f"{entry['tool']}:{sig}:{result[:100]}"


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
        # Telemetria: loops hard-stop viram incidentes → testes de regressão
        try:
            from .incidents import record_incident
            record_incident(
                "tool_loop_hard_stop",
                tool=last_tool,
                consecutive=consecutive,
                tool_log_size=len(tool_log),
                recent_tools=[e["tool"] for e in tool_log[-8:]],
            )
        except Exception:
            pass
        return None, True  # hard stop silencioso

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


# Enforcement universal: o agente DEVE executar, não narrar. Portado do
# TOOL_USE_ENFORCEMENT do Hermes — fecha o sintoma "vou fazer X" sem fazer.
TOOL_USE_ENFORCEMENT = (
    "# EXECUTE, NAO NARRE\n"
    "Voce DEVE usar suas ferramentas para AGIR — nunca apenas descreva o que 'vai fazer'.\n"
    "Quando disser que vai fazer algo (rodar testes, criar/editar arquivo, checar algo),\n"
    "faca a tool call correspondente NO MESMO turno. NUNCA termine o turno com uma promessa\n"
    "de acao futura ('vou criar...', 'em seguida farei...') — execute AGORA.\n"
    "Continue trabalhando ate a tarefa estar REALMENTE concluida (arquivo escrito, comando\n"
    "rodado, resultado verificado), nao ate ter apenas um plano do que fazer.\n"
    "Uma lista de 'proximos passos' NAO e entrega — a entrega e o artefato concreto.\n"
)

# Escada de decisão "código mínimo" — inspirada no projeto Ponytail (MIT):
# https://github.com/DietrichGebert/ponytail. Adaptada (não copiada verbatim);
# controlada por config.agent.minimal_code_mode (default True — ver
# _build_system_prompt).
MINIMAL_CODE_LADDER = (
    "# ESCADA DE DECISAO — CODIGO MINIMO NECESSARIO\n"
    "Antes de escrever codigo novo, suba esta escada NA ORDEM e pare no PRIMEIRO\n"
    "degrau que resolve o problema — isso roda DEPOIS de entender o problema\n"
    "(leia o codigo ao redor primeiro), nao no lugar disso:\n"
    "  1. Isso PRECISA existir agora? (YAGNI — nao construa para um caso\n"
    "     hipotetico futuro que ninguem pediu.)\n"
    "  2. Ja existe algo assim NESTE codebase? Reuse em vez de duplicar.\n"
    "  3. Resolve com a biblioteca padrao da linguagem, sem dependencia nova?\n"
    "  4. E um recurso nativo da plataforma/framework que ja esta em uso?\n"
    "  5. Uma dependencia ja instalada no projeto ja resolve isso?\n"
    "  6. Da para fazer em uma linha, sem criar abstracao nova?\n"
    "  7. So entao: a solucao MINIMA viavel — sem generalizar para casos que\n"
    "     ninguem pediu.\n"
    "PREGUICOSO, NAO NEGLIGENTE: validacao de entrada, tratamento de erro em\n"
    "fronteira de confianca, seguranca e acessibilidade NUNCA saem de escopo por\n"
    "causa desta escada — cortar essas coisas nao e 'codigo minimo', e bug.\n"
)

# Protocolo de execução de task — injetado SOMENTE quando o agent roda como
# worker do kanban (env BAUER_KANBAN_TASK). Espelha o KANBAN_GUIDANCE do Hermes,
# adaptado às tools reais do Bauer (kanban_show/complete/block/comment/create).
KANBAN_WORKER_GUIDANCE = (
    "# PROTOCOLO DE EXECUCAO DE TASK (KANBAN WORKER)\n"
    "Voce foi spawnado como worker de UMA task do board. O ID esta em\n"
    "$BAUER_KANBAN_TASK; seu workspace em $BAUER_KANBAN_WORKSPACE.\n"
    "\n"
    "1. ORIENTE-SE. Chame kanban_show (sua task) para ler titulo, corpo, os\n"
    "   handoffs das tasks-pai (resumo + artefatos) e o thread de comentarios.\n"
    "2. TRABALHE produzindo ARTEFATOS CONCRETOS dentro do workspace: arquivos\n"
    "   escritos, codigo, um relatorio .md com o conteudo real. Um 'proximo\n"
    "   passo' nao e entrega — a entrega e o arquivo/diff/relatorio em si.\n"
    "3. CONCLUA com handoff util: kanban_complete(task_id, result=...) onde\n"
    "   result NOMEIA os artefatos concretos (caminhos de arquivo, contagem de\n"
    "   testes, decisoes tomadas). Quem pega a proxima task le esse resultado.\n"
    "4. BLOQUEIE em ambiguidade real: kanban_block(task_id, reason=...). Voce\n"
    "   roda headless — NAO use clarify (vai dar timeout sem ninguem responder).\n"
    "   Comente o contexto antes com kanban_comment.\n"
    "5. FOLLOW-UP: se surgir trabalho extra, CRIE, nao faca — kanban_create(\n"
    "   title=..., parent_id=<sua task>) para o especialista certo. Nao estoure\n"
    "   o escopo desta task.\n"
    "\n"
    "NUNCA marque como done uma task que voce nao terminou de verdade — bloqueie.\n"
)


def _build_system_prompt(
    router: ToolRouter,
    tool_mode: str | None = None,
    *,
    client=None,
) -> str:
    """Monta o system prompt com a lista de tools disponíveis.

    tool_mode="native" omite as instruções de emitir tool call como JSON de
    texto (function calling nativo executa as tools pela API). tool_mode="bridge"
    mantém o protocolo JSON histórico. Ver plans/023.

    Passe ``client`` e deixe ``tool_mode=None`` para DERIVAR o modo do mesmo
    gate que o runtime usa (`_client_supports_native_tools`). Isso existe porque
    o default antigo era o literal "bridge", e 5 dos 7 call sites simplesmente
    não passavam nada — mandavam "responda SOMENTE com o JSON {...}" enquanto o
    cliente executava tool calling NATIVO. Instrução em conflito direto: o
    modelo ora chamava a tool, ora devolvia o JSON como texto, ora prosa. Para
    provider cloud (sempre native) isso valia desde sempre; medido no Beelink,
    o `bauer run` dava 0 tools por causa disso.

    Sem `client` e sem `tool_mode`, mantém "bridge" — comportamento antigo.
    """
    if tool_mode is None:
        tool_mode = "native" if (
            client is not None and _client_supports_native_tools(client)
        ) else "bridge"
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

    if tool_mode == "native":
        # Function calling nativo: as tools são invocadas pela API. NÃO ensinar
        # o formato JSON-de-texto (isso fazia modelos fracos emitir a tool call
        # como texto, que o caminho nativo não executa — "0 tools"). Ver plans/023.
        #
        # E NÃO repetir a assinatura das tools aqui: o `tools=` da requisição já
        # carrega nome, descrição e JSON Schema de cada uma. Inlinar o
        # `tools_section` duplicava tudo — medido no Beelink com 83 tools, o
        # prompt ia a ~19.9k tokens contra ~13.6k só dos schemas, ~6k a mais
        # (19% de uma janela de 32k) gastos dizendo duas vezes a mesma coisa.
        # Só os NOMES ficam, como índice barato para o modelo se orientar.
        tool_protocol_block = (
            "# FERRAMENTAS DISPONIVEIS\n"
            f"Voce tem estas ferramentas via function calling nativo: {tool_names}\n"
            "A assinatura completa de cada uma chega pela API — nao peca ao usuario.\n\n"
            "# COMO USAR FERRAMENTAS\n"
            "Quando a tarefa exigir ler/escrever arquivos, rodar comandos ou buscar na web,\n"
            "CHAME a ferramenta apropriada de verdade (tool call nativo). NAO escreva o comando\n"
            "em texto nem em JSON no corpo da resposta — invoque a tool.\n"
            "# REGRA DE PRECEDENCIA — ACAO VENCE EXPLICACAO\n"
            "Se o pedido contem um VERBO DE ACAO (crie, escreva, salve, edite, rode, execute,\n"
            "instale, apague), a PRIMEIRA coisa da sua resposta e a tool call. Nada antes dela.\n"
            "Entregar o codigo em bloco markdown em vez de chamar write_file NAO cumpre o\n"
            "pedido — o arquivo nao existe no disco, e a tarefa FALHOU.\n"
            "  'crie fib.py com uma funcao fib(n)'  -> tool call write_file(path, content)\n"
            "  'como se escreve um fibonacci?'      -> texto puro com o codigo em bloco\n"
            "So responda em TEXTO PURO quando o pedido for saudacao, pergunta, explicacao ou\n"
            "conversa — ou seja, quando NAO houver nada para mudar no disco nem executar.\n\n"
            "# VOCE TEM ACESSO REAL AO SHELL — NAO NEGUE ISSO\n"
            "Se o usuario pedir um comando shell (ex: 'pytest tests/', 'git status', 'python script.py'),\n"
            "VOCE PODE E DEVE executar via a tool run_command (arg `command`). NUNCA responda\n"
            "'nao tenho acesso ao terminal' — voce TEM. Se o comando exigir argumentos, rode com\n"
            "--help primeiro para descobrir, depois com os argumentos certos.\n"
            "Depois de executar uma ferramenta, resuma o resultado em texto normal.\n"
            "NUNCA responda 'O que prefere?' / 'Deseja que eu...' / 'sou apenas seu assistente' — AGE.\n"
            "Responda sempre em portugues.\n\n"
        )
    else:
        tool_protocol_block = (
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
            "Responda sempre em portugues.\n\n"
        )

    _platform_name = platform.system()
    if _platform_name == "Windows":
        _environment_line = (
            "- Voce roda em **Windows** com Python no venv. Subprocess usa `shell=False`.\n"
        )
        _absolute_path_examples = "`C:/...` ou `/Users/...`"
        _avoid_shell_commands = (
            "- Em run_command NAO use: `dir` (use tool list_dir), "
            "`cat`/`head`/`tail` (use read_file).\n"
        )
    else:
        _environment_line = (
            f"- Voce roda em **{_platform_name}** (Unix) com Python no venv. "
            "Subprocess usa `shell=False`.\n"
        )
        _absolute_path_examples = "`/tmp/...` ou `/home/...`"
        _avoid_shell_commands = (
            "- Em run_command NAO use: `ls` (use tool list_dir), "
            "`cat`/`head`/`tail` (use read_file).\n"
        )

    _environment_constraints = (
        "# CONSTRAINTS DO AMBIENTE (LEIA — evita erros recorrentes)\n"
        + _environment_line
        + "- TODAS as tools de arquivo (read_file, write_file, list_dir, etc) trabalham\n"
        "  em paths RELATIVOS ao workspace. Nunca passe paths absolutos do tipo\n"
        f"  {_absolute_path_examples}. Use `.`, `subdir/arquivo.py`, etc.\n"
        "- `..` e permitido se o path resolvido ficar dentro do workspace. `../fora` e BLOQUEADO.\n"
        + _avoid_shell_commands
        + "- `pip install`, `npm install`, `git push`, `rm` precisam `confirm: true` no args.\n"
        "- Antes de `python script.py`, LEIA o script para descobrir se exige argumentos.\n"
        "  Muitos scripts saem com 'Uso: python X.py <arg>' — read_file primeiro evita isso.\n\n"
    )

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
        + _environment_constraints
        + f"{tool_protocol_block}"
        + TOOL_USE_ENFORCEMENT
        + (("\n" + MINIMAL_CODE_LADDER) if _minimal_code_mode_enabled() else "")
        + (("\n" + KANBAN_WORKER_GUIDANCE) if os.environ.get("BAUER_KANBAN_TASK") else "")
        + _specialists_block()
        + _specs_section()
    )


def _minimal_code_mode_enabled() -> bool:
    """Lê config.agent.minimal_code_mode — best-effort, nunca bloqueia o chat.

    Default True (mesmo valor default de AgentSection) se a config não
    carregar, mesma filosofia de _resolve_loop_config.
    """
    try:
        from .config_loader import load_config
        return load_config().agent.minimal_code_mode
    except Exception:
        return True


def _specialist_delegation_enabled() -> bool:
    """Lê config.agent.specialist_delegation — mesma filosofia de
    _minimal_code_mode_enabled (best-effort, default True)."""
    try:
        from .config_loader import load_config
        return load_config().agent.specialist_delegation
    except Exception:
        return True


def _specialists_section() -> str:
    """Lista os agents especialistas (embutidos no pacote + agents.yaml do
    usuário) para o system prompt, instruindo o modelo a delegar via
    `delegate_task` quando a tarefa combinar com um deles.

    Retorna "" (sem seção) quando o pool está vazio — não faz sentido
    instruir sobre delegação sem nenhum especialista cadastrado. Best-effort:
    qualquer falha de leitura degrada para "" silenciosamente, igual ao
    padrão de _specs_section.
    """
    try:
        from .agent_registry import merged_specialist_pool, resolve_user_agents_path

        _agents_file = str(resolve_user_agents_path())
        agents = merged_specialist_pool(_agents_file)
        # Só lista agents locais (sem url) — remotos exigem bauer serve rodando
        # à parte e já são endereçáveis por agent_name sem precisar de aviso
        # prévio no prompt (o modelo não precisa "descobrir" um agent remoto
        # específico, isso é decisão de infra do usuário, não de tarefa).
        local_agents = [a for a in agents if not a.url]
        if not local_agents:
            return ""

        lines = [
            "# ESPECIALISTAS DISPONIVEIS\n"
            "Estes agents tem system prompt ajustado para suas areas, mas "
            "delegate_task(agent_name=\"<nome>\", task=\"...\") pra eles e uma "
            "consulta de UMA RESPOSTA EM TEXTO — SEM tools, sem acesso a "
            "arquivos/shell/docker, sem multiplas rodadas. Use SOMENTE para "
            "pedir uma opiniao/analise pontual (revisar um trecho, explicar um "
            "conceito, comparar opcoes, redigir um texto) que cabe numa resposta "
            "unica.\n"
            "NUNCA delegue tarefas que precisam EXECUTAR algo (rodar comando, "
            "subir/parar servico, ler logs, editar arquivo, navegar pasta) — "
            "mesmo que a tarefa seja da area de um especialista (ex.: 'suba o "
            "docker e arrume o dashboard' e DevOps mas exige tools reais: faca "
            "voce mesmo com run_command/read_file/write_file, NAO delegue). "
            "Na duvida entre delegar ou executar, execute voce mesmo."
        ]
        for a in local_agents:
            lines.append(f"  - {a.name}: {a.description}")
        return "\n".join(lines)
    except Exception:
        return ""


def _specialists_block() -> str:
    """Wrapper de `_specialists_section()` para `_build_system_prompt`: aplica
    o toggle de config e só prefixa a quebra de linha quando há conteúdo real
    (registry vazio não deve deixar um "\\n" solto no prompt)."""
    if not _specialist_delegation_enabled():
        return ""
    section = _specialists_section()
    return f"\n{section}" if section else ""


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


def _extract_embedded_json_action(text: str, available: set[str]) -> dict | None:
    """Acha o primeiro objeto JSON `{"action": ..., ...}` embutido em qualquer
    posição do texto — não só no início.

    Modelos sem tool calling nativo (bridge) às vezes ignoram a instrução de
    responder SOMENTE com o JSON e escrevem uma frase de narração antes,
    colado sem quebra de linha (ex.: "Vou verificar o diretório.{\"action\":
    ...}"). ``JSONDecoder.raw_decode`` a partir de cada ``{`` encontrado lida
    com chaves aninhadas corretamente, ao contrário de um regex ganancioso.
    """
    import json as _json

    decoder = _json.JSONDecoder()
    idx = text.find("{")
    while idx != -1:
        try:
            obj, _ = decoder.raw_decode(text, idx)
            if isinstance(obj, dict) and obj.get("action") in available:
                return obj
        except _json.JSONDecodeError:
            pass
        idx = text.find("{", idx + 1)
    return None



def _e_resposta_degenerada(texto: str) -> bool:
    """A resposta e um loop de repeticao do modelo, nao um conteudo real?

    Caso real (qwen3.6:27b em modo bridge, contexto apertado): o modelo tentou
    emitir um tool call e degenerou em `{"{"{"{"{"...` por dezenas de repeticoes.
    Como nao e JSON valido, `_try_parse_tool` devolvia None e o agente tratava a
    string inteira como RESPOSTA FINAL — imprimindo o lixo para o usuario como
    se fosse a answer do modelo.

    Heuristica deliberadamente conservadora (falso positivo aqui censura uma
    resposta legitima, o que e pior que deixar passar um lixo):

      - texto minimamente longo (respostas curtas repetitivas sao normais:
        "ok ok", tabelas, listas);
      - poucos caracteres distintos, indicando alfabeto colapsado;
      - um fragmento curto que, repetido, cobre quase todo o texto.
    """
    t = (texto or "").strip()
    if len(t) < 40:
        return False

    # Alfabeto colapsado: `{"{"{"...` usa 3 caracteres distintos em 100+ chars.
    if len(set(t)) <= 4:
        return True

    # Fragmento curto que se repete cobrindo a maior parte do texto.
    for tam in range(1, 13):
        if len(t) < tam * 6:          # precisa de repeticao suficiente p/ concluir
            break
        frag = t[:tam]
        repeticoes = t.count(frag)
        if repeticoes >= 6 and repeticoes * tam >= len(t) * 0.85:
            return True
    return False


def _try_parse_tool(response: str, router: ToolRouter) -> dict | None:
    """Tenta parsear a resposta como tool action. Retorna dict ou None.

    Estratégias (em ordem):
    1. JSON puro ou bloco markdown — resposta inteira é a action
    2. JSON no início da resposta (modelo misturou JSON + texto) — extrai só o JSON
    3. JSON embutido em qualquer posição (modelo narrou antes de chamar) — ver
       _extract_embedded_json_action
    Em todos os casos, só retorna se a action for uma tool conhecida.
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

    # Estratégia 3: JSON embutido após texto de narração
    return _extract_embedded_json_action(stripped, available)


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

# Tools de conteúdo → preserva linhas do início E do fim (ver _head_tail).
# run_command/process entram aqui: antes caíam no ramo genérico e chegavam ao
# modelo como os 300 primeiros chars, ou seja, só o banner do comando.
_CONTENT_TOOLS = frozenset({
    "read_file", "execute_code", "delegate_task", "http_request",
    "run_command", "process",
})

# Fração do orçamento reservada ao FIM do resultado. Exceção Python, mensagem
# de erro de comando e o tally do pytest ficam sempre nas últimas linhas —
# cortar só pela cabeça entregava ao modelo o preâmbulo e descartava a causa.
# Medido em 2026-08-16 no ramo genérico anterior: `pytest` com 2 falhas chegava
# como 350 chars de "bringing up nodes" + dots, cortado em "FAI", sem FAILED /
# assert / tally; e um script que imprime progresso e quebra no fim chegava como
# 6 linhas de "... ok", sem o RuntimeError. Ambos os casos: 0 de 5 sinais.
_TAIL_SHARE = 0.6


def _head_tail(text: str, budget: int) -> tuple[list[str], list[str], int]:
    """Seleciona linhas do início e do fim de `text` cabendo em ~`budget` chars.

    O fim é preenchido primeiro (recebe `_TAIL_SHARE` do orçamento) porque é
    onde mora o erro; o que sobra vai para o início.

    Returns:
        (linhas_do_inicio, linhas_do_fim, n_linhas_omitidas)
    """
    lines = [l for l in text.splitlines() if l.strip()]
    tail_budget = int(budget * _TAIL_SHARE)
    head_budget = budget - tail_budget

    tail: list[str] = []
    used = 0
    for line in reversed(lines):
        if used + len(line) + 1 > tail_budget:
            break
        tail.insert(0, line)
        used += len(line) + 1

    head: list[str] = []
    used = 0
    for line in lines[: len(lines) - len(tail)]:
        if used + len(line) + 1 > head_budget:
            break
        head.append(line)
        used += len(line) + 1

    return head, tail, len(lines) - len(head) - len(tail)


def _compress_tool_result_inline(action: str, result: str) -> str:
    """Comprime um resultado de tool grande para caber no contexto sem desperdiçar tokens.

    Estratégia por tipo de tool:
    - Listagem (list_dir, glob_files): contagem + primeiros N itens (itens são
      homogêneos, o fim não carrega sinal extra)
    - Conteúdo (read_file, execute_code, run_command): contagem de linhas +
      início E fim, porque a causa da falha fica nas últimas linhas
    - Genérico: mesma política de início+fim, em chars

    Só deve ser chamada quando len(result) > _TOOL_RESULT_COMPRESS_THRESHOLD.

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
    else:
        # Conteúdo e genérico: início + fim. O cabeçalho de metadados consome
        # parte do orçamento, então é descontado antes de dividir head/tail.
        header = f"[{n_chars} chars — {n_lines} linhas]"
        budget = max(_TOOL_RESULT_COMPRESSED_PREVIEW - len(header) - 40, 80)
        head, tail, omitted = _head_tail(result, budget)

        if not head and not tail:
            # Linha única gigante (JSON de uma linha, log sem quebras): não há
            # como fatiar por linha — fatia por char, preservando as duas pontas.
            half = budget // 2
            summary = f"{header} {result[:half].rstrip()}"
            summary += f"\n... [{max(n_chars - budget, 0)} chars omitidos] ...\n"
            summary += result[-half:].lstrip()
        else:
            parts = [header]
            parts.extend(head)
            if omitted > 0:
                parts.append(f"... +{omitted} linhas omitidas ...")
            parts.extend(tail)
            summary = "\n".join(parts)

    # Garante que não ultrapassa o limite mesmo após formatação. O corte final
    # tira do MEIO, não do fim, para nunca sacrificar as últimas linhas.
    if len(summary) > _TOOL_RESULT_COMPRESSED_PREVIEW:
        keep = _TOOL_RESULT_COMPRESSED_PREVIEW - 3
        head_keep = keep // 2
        summary = summary[:head_keep] + "..." + summary[-(keep - head_keep):]
    return summary


def _ctx_result_for_context(action: str, result: str) -> tuple[str, bool]:
    """Decide como um resultado de tool deve entrar no contexto.

    Acima do threshold há um único caminho: _compress_tool_result_inline.
    Havia aqui um segundo ramo, com truncamento em _MAX_TOOL_RESULT_IN_CTX
    (3000 chars), inalcançável desde sempre — qualquer resultado > 3000 também
    é > 2000 e já tinha retornado acima. Removido em 2026-08-16 junto com o fix
    de head+tail: ele truncava pela cabeça, o mesmo defeito que o fix corrigiu,
    e reintroduziria o bug se alguém o tornasse alcançável.

    Returns:
        (ctx_result, was_compressed)
        ctx_result: string a ser adicionada ao contexto
        was_compressed: True se o resultado foi comprimido
    """
    if len(result) > _TOOL_RESULT_COMPRESS_THRESHOLD:
        return _compress_tool_result_inline(action, result), True
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



#: Marca no host → nome do provider. Sem isto, TODO provider de nuvem virava
#: "openai": a versão anterior só separava Ollama do resto. Na tela isso ficou
#: como uma contradição visível — o boot dizia `openrouter` (do config) e o
#: painel de sessão dizia `openai` (desta inferência), sobre a mesma sessão.
_MARCAS_DE_HOST: "tuple[tuple[str, str], ...]" = (
    (":11434", "ollama"), ("ollama", "ollama"),
    ("openrouter", "openrouter"),
    ("anthropic", "anthropic"),
    ("deepseek", "deepseek"),
    ("groq", "groq"),
    ("mistral", "mistral"),
    ("together", "together"),
    ("cerebras", "cerebras"),
    ("sambanova", "sambanova"),
    ("perplexity", "perplexity"),
    ("fireworks", "fireworks"),
    ("moonshot", "moonshot"),
    ("dashscope", "alibaba"),
    ("opencode", "opencode"),
    ("x.ai", "xai"),
    ("googleapis", "gemini"),
    ("azure", "azure"),
    ("openai", "openai"),
)
# Só marcas de DOMÍNIO. Porta não entra, com uma exceção: `:11434` é convenção
# fixa do Ollama e de mais nada. A porta 1234 (default do LM Studio) foi
# testada e removida — é porta genérica, usada por qualquer servidor
# OpenAI-compat local, e chutar "lmstudio" a partir dela seria o mesmo tipo de
# palpite que esta função existe para consertar. Endpoint local desconhecido
# é honestamente "openai" (OpenAI-compat de origem indeterminada).


def _detectar_provider_por_host(host: str) -> str:
    """Infere o provider a partir da URL do client, quando ele nao se identifica.

    A porta 11434 e o sinal forte: e a porta do Ollama, fixa por convencao. A
    heuristica antiga olhava so o NOME do host ("ollama" in host), que falha em
    localhost/127.0.0.1 — ou seja, em praticamente toda instalacao real.

    Ultimo recurso: quando nem o client nem o config dizem quem sao. O nome
    generico "openai" so sai quando o host nao casa com nada conhecido — e ai
    e honesto, porque o endpoint e mesmo OpenAI-compat de origem desconhecida.
    """
    h = (host or "").lower()
    if not h:
        return "openai"
    for marca, nome in _MARCAS_DE_HOST:
        if marca in h:
            return nome
    return "openai"


def _provider_declarado() -> str:
    """O provider que o config DECLARA — a fonte que não precisa adivinhar.

    O boot usa exatamente isto (`state["configured_provider"]`); o painel de
    sessão inferia pelo host e discordava na tela. Vale como verdade no boot,
    quando o client ainda é o configurado; turno roteado é marcado à parte,
    pelo `→` do rodapé.
    """
    try:
        from .config_loader import load_config
        return str(load_config().model.provider or "")
    except Exception as exc:  # noqa: BLE001 — sem config, cai na inferência
        from .logging_config import log_suppressed
        log_suppressed("provider.declarado", exc)
        return ""


def _stream_to_sink(
    client: OllamaClient,
    model_name: str,
    api_payload: list[dict],
    *,
    max_retries: int = 2,
    on_retry=None,
) -> "list[str]":
    """Consome `chat_stream` emitindo cada chunk ao sink, com retry A FRIO.

    "A frio" é a regra que não pode ser afrouxada: assim que UM chunk saiu, a
    resposta já está na tela do usuário (ou na mensagem do canal). Retentar
    depois disso reimprimiria a resposta inteira grudada na anterior. Então a
    falha pós-emissão sobe — só a falha antes do primeiro token é retentada.

    Antes deste helper, o ramo de sink não tinha retry nenhum: quem instalava
    sink (o gateway) trocava, sem saber, o backoff de 429/5xx por uma falha
    seca. Com o terminal virando consumidor de sink (plano 028 F1), isso
    valeria também para todo `bauer agent` em provider de nuvem.
    """
    from .delta_stream import emit_delta as _emit_delta
    from .delta_stream import emit_round_start as _emit_round
    from .error_classifier import classify_api_error
    from .retry_utils import jittered_backoff

    parts: "list[str]" = []
    for attempt in range(max_retries + 1):
        _emit_round()
        parts = []
        try:
            for chunk in client.chat_stream(model_name, api_payload):
                parts.append(chunk)
                _emit_delta(chunk)
            return parts
        except Exception as exc:  # noqa: BLE001 — reclassificado logo abaixo
            if parts:
                raise  # já saiu texto: retry duplicaria a resposta
            classified = classify_api_error(exc)
            if not classified.retryable or attempt >= max_retries:
                raise
            wait = jittered_backoff(attempt, base_delay=5.0, max_delay=60.0)
            if on_retry is not None:
                try:
                    on_retry(attempt + 1, classified, wait)
                except Exception as _exc:  # noqa: BLE001 — aviso não derruba o turno
                    from .logging_config import log_suppressed
                    log_suppressed("stream.on_retry", _exc)
            time.sleep(wait)
    return parts


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

    # Delta sink: quando instalado, consome o stream token a token emitindo
    # cada chunk — a mensagem do canal (gateway) ou a resposta no terminal
    # (plano 028 F1) cresce ao vivo. Agora com retry a frio, ver _stream_to_sink.
    from .delta_stream import get_sink as _get_sink

    # Usa retry automático apenas no OpenAIClient (que tem implementação real).
    # Checar apenas hasattr() seria insuficiente pois MagicMock retorna True para tudo.
    from .openai_client import OpenAIClient as _OpenAIClientClass
    if _get_sink() is not None:
        parts = _stream_to_sink(client, model_name, api_payload)
    elif isinstance(client, _OpenAIClientClass) and hasattr(client, "chat_with_retry"):
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

    # Cost meter — entrega o custo real desta call ao sink ativo (daemon,
    # goal tracker, benchmark). No-op quando ninguém está medindo.
    try:
        from .cost_meter import provider_from_client, report_llm_cost
        report_llm_cost(
            provider_from_client(client),
            model_name,
            getattr(client, "last_usage", None),
        )
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


def _recover_empty_response(
    client: OllamaClient,
    model_name: str,
    ctx: ContextManager,
    console: Console | None = None,
) -> tuple[str, str]:
    """Recuperação em camadas quando o modelo retorna resposta vazia.

    Camadas (param na primeira que produzir resposta):
      1. Retry com backoff 2s   — rate-limit silencioso / transiente
      2. force_compress + retry — contexto sobrecarregado (causa mais comum
                                  observada em uso real; antes o usuário era
                                  instruído a dar /clear manualmente)

    Returns:
        (response, diagnostico):
        - response != ""  → recuperado; diagnostico é vazio
        - response == ""  → falha definitiva; diagnostico tem mensagem acionável.
          O incidente é gravado em logs/incidents/ para virar teste de regressão.
    """
    import time as _time

    # Camada 1: retry simples
    _time.sleep(2.0)
    response = _collect_response(client, model_name, ctx.get_payload())
    if response.strip():
        return response, ""

    # Camada 2: compressão forçada + retry
    compressed = False
    try:
        compressed = ctx.force_compress()
    except Exception:
        compressed = False
    if compressed:
        if console is not None:
            console.print("[dim][recovery] contexto comprimido — tentando novamente...[/dim]")
        response = _collect_response(client, model_name, ctx.get_payload())
        if response.strip():
            return response, ""

    # Falha definitiva: grava incidente (sem conteúdo de mensagens) + diagnóstico
    payload = ctx.get_payload()
    approx_chars = sum(
        len(m.get("content", "") if isinstance(m.get("content"), str) else str(m.get("content", "")))
        for m in payload
    )
    approx_tokens = approx_chars // 4
    applied = getattr(ctx, "applied_context", 0) or 0
    pct = f" (~{approx_tokens * 100 // applied}% do contexto)" if applied else ""

    try:
        from .incidents import record_incident
        record_incident(
            "empty_response",
            model=model_name,
            provider=getattr(ctx, "provider", "?"),
            messages_count=len(payload),
            approx_tokens=approx_tokens,
            applied_context=applied,
            compressed_before_final_retry=compressed,
        )
    except Exception:
        pass

    diagnostico = (
        f"[Modelo retornou resposta vazia mesmo após retry + compressão]\n"
        f"  Modelo: {model_name}\n"
        f"  Contexto: {len(payload)} mensagens, ~{approx_tokens:,} tokens{pct}\n"
        f"  Prováveis causas:\n"
        f"    1. Rate-limit silencioso do provider (comum em free tier)\n"
        f"    2. Filtro de conteúdo bloqueando a resposta\n"
        f"    3. Modelo sobrecarregado no servidor\n"
        f"  Soluções:\n"
        f"    Aguarde 30s — pode ser rate-limit transiente\n"
        f"    /model      — troca de provider/modelo\n"
        f"    /clear      — última opção: limpa todo o histórico"
    )
    return "", diagnostico


def _parse_provider_context_cap(error_text: str) -> int | None:
    """Extrai a janela de contexto REAL reportada pelo provider num erro 400/413.

    Formatos conhecidos:
      OpenRouter: "This endpoint's maximum context length is 65536 tokens."
      OpenAI:     "This model's maximum context length is 128000 tokens"
      Groq 413:   "... tokens per minute (TPM): Limit 12000, ..."  (TPM, não janela — ignorado)
    Retorna None quando não há um cap de CONTEXTO claro no texto.
    """
    import re as _re
    m = _re.search(r"maximum context length is (\d{3,})", error_text)
    if m:
        try:
            return int(m.group(1))
        except ValueError:
            return None
    m = _re.search(r"context length of only (\d{3,})|context window of (\d{3,})", error_text)
    if m:
        try:
            return int(m.group(1) or m.group(2))
        except ValueError:
            return None
    return None


@contextmanager
def _busy_spinner(console: Console, text: str):
    """Spinner genérico para qualquer trecho "mudo" do terminal (chamada de
    LLM, execução de tool). A barra de status fixa do prompt_toolkit
    (bottom_toolbar) só existe enquanto o prompt está esperando input — some
    assim que o Enter é pressionado, porque é renderizada pelo mecanismo de
    prompt, não por um layout persistente. Sem NENHUM indicador nesse meio
    tempo, um `run_command` demorado (docker build, etc.) parece travado.
    Este spinner cobre esse vão: sempre há algo visivelmente rodando entre
    o Enter e a próxima barra de status.

    Best-effort: se o console não suportar live display (outro Live ativo,
    output capturado), segue sem spinner em vez de quebrar o turno.

    Registra o spinner em `ui_frame` (plano 028 F2) — QUALQUER `input()` que
    rodar dentro do bloco, em qualquer profundidade, pode envolver-se com
    `ui_frame.suspend()` e pausar este spinner sem precisar saber que ele
    existe. É isto que substitui a antiga allowlist de tools "interativas":
    não é mais preciso listar de antemão quem chama input() — quem chama só
    precisa suspender.
    """
    from .ui_frame import current_frame as _current_frame
    from .ui_frame import register as _register

    # Com o quadro do turno ativo (plano 028 F2), a atividade vira uma linha do
    # TRILHO em vez de um spinner próprio: o Rich admite um único display ao
    # vivo por vez, e abrir um `console.status()` aqui falharia em silêncio,
    # deixando o usuário sem indicador — justamente o vão que o spinner cobre.
    _frame = _current_frame()
    if _frame is not None:
        from rich.text import Text as _Text

        _rotulo = _Text.from_markup(text).plain.strip()
        _frame.set_atividade(_rotulo)
        try:
            yield
        finally:
            _frame.set_atividade("")
        return

    _status = None
    try:
        _status = console.status(text, spinner="dots")
        _status.__enter__()
    except Exception:
        _status = None
    if _status is None:
        yield
        return
    with _register(_status):
        try:
            yield
        finally:
            try:
                _status.__exit__(None, None, None)
            except Exception:
                pass


def _thinking_status(console: Console, model_name: str):
    """Spinner enquanto o modelo gera a resposta completa (sem streaming)."""
    return _busy_spinner(console, f"[dim]{model_name} pensando… (Ctrl+C interrompe)[/dim]")


def _tool_exec_status(console: Console, name: str):
    """_busy_spinner para execução de tool.

    Até o F2 (plano 028) isto consultava uma allowlist de nomes de tool
    "interativas" (clarify, run_command sob confirmação…) para decidir se
    abria spinner — esquecer uma tool nova na lista reintroduzia o bug do
    "totodo" (Live + input() corrompendo stdin, incidente 2026-07-02). Agora
    o spinner SEMPRE abre, registrado em `ui_frame`; a tool que precisar de
    input() suspende sozinha via `ui_frame.suspend()` (ver `_clarify` em
    `bauer/tools/agent_misc.py` e `_prompt_cmd_decision` abaixo). Não há mais
    lista para manter."""
    return _busy_spinner(console, f"[dim]executando {name}… (Ctrl+C interrompe)[/dim]")


def _prompt_cmd_decision(console: Console, title: str, body: str) -> str:
    """Prompt compartilhado de decisão de comando. Devolve
    "once" | "session" | "always" | "deny". Usado pelos dois gates (padrão
    perigoso e allowlist). "always" é o que ENSINA — pergunta uma vez, nunca mais.
    """
    from .ui import approval_card as _card
    from .ui import approval_options as _options
    from .ui_frame import suspend as _suspend

    console.print()
    console.print(_card(title, body))
    console.print(_options())
    try:
        with _suspend():
            raw = input("  > ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        return "deny"
    mapping = {
        "e": "once", "o": "once", "1": "once",
        "s": "session", "a": "always",
        "n": "deny", "d": "deny", "": "deny",
    }
    decision = mapping.get(raw[:1], "deny")
    _label = {"once": "executando uma vez", "session": "liberado nesta sessão",
              "always": "aprendido (liberado sempre)", "deny": "negado"}[decision]
    console.print(f"  [{'green' if decision != 'deny' else 'red'}]{_label}[/]")
    return decision


#: Cor de aviso do tema, para markup Rich dentro das mensagens de gate. Função
#: e não constante: o acento (e a paleta) mudam em tempo de execução, e um
#: valor capturado no import congelaria a cor — foi assim que a `bottom_toolbar`
#: e o `_PT_STYLE` escaparam do F0.
def _warn() -> str:
    return _theme_mod.WARN


def _glifo_aviso() -> str:
    """Sinal de aviso no conjunto ativo. `⚠` cru estoura em cp1252 — e o
    card onde ele aparece é o do comando perigoso (achado do F6)."""
    from .ui import active_glyphs
    return active_glyphs().warn


def _amostra_acentos(destaque: str = ""):
    """Catálogo de acentos para o `/theme` (cada nome na própria cor)."""
    from .ui import accent_swatches
    return accent_swatches(destaque)


def _persistir_acento(nome: str) -> None:
    """Grava o acento escolhido em `agent.accent` de um config EXISTENTE.

    Duas regras, ambas aprendidas quebrando a máquina de um usuário:

    1. **Escreve no config do diretório atual só se ele JÁ EXISTIR.** A versão
       anterior chamava `set_config_value` com o default relativo
       `"config.yaml"`. Rodando de dentro de um projeto que não tem config
       próprio, `set_config_value` CRIAVA o arquivo — com uma única chave,
       `agent.accent`. Esse arquivo passava a ofuscar o `~/.bauer/config.yaml`
       e TODO comando `bauer` naquela pasta morria em
       "Config inválida: model: Field required". Uma troca de cor derrubou o
       agente inteiro.

    2. **Nunca cria config.** Se não há config nenhum, o acento vale para a
       sessão e pronto. Criar um arquivo de configuração como efeito colateral
       de apertar Ctrl+T é surpresa demais para o benefício.

    Best-effort no resto: falhar ao gravar não pode desfazer o que o usuário
    acabou de ver na tela.
    """
    try:
        from pathlib import Path as _Path

        from .config_admin import set_config_value
        from .paths import config_path as _home_cfg

        # 1º o config do diretório atual, se houver; 2º o canônico do ~/.bauer.
        # Nesta ordem porque é a mesma que o carregamento usa — gravar num
        # arquivo que a sessão não leu seria escrever no vazio.
        local = _Path("config.yaml")
        alvo = local if local.is_file() else _home_cfg()
        if not alvo.is_file():
            return  # sem config para atualizar: a troca vale só nesta sessão
        set_config_value("agent.accent", nome, config_path=alvo)
    except Exception as exc:  # noqa: BLE001
        from .logging_config import log_suppressed
        log_suppressed("theme.persistir_acento", exc)


def _aplicar_acento_do_config() -> None:
    """Aplica o acento salvo no config, no boot da sessão."""
    try:
        from .config_loader import load_config
        nome = str(getattr(load_config().agent, "accent", "") or "").strip()
        if nome:
            _theme_mod.set_accent(nome)
    except KeyError as _exc:
        # nome inválido no config (renomeamos uma paleta, usuário digitou
        # errado): mantém o padrão em vez de derrubar o boot por causa de cor
        from .logging_config import log_suppressed
        log_suppressed("theme.acento_invalido", _exc)
    except Exception as exc:  # noqa: BLE001
        from .logging_config import log_suppressed
        log_suppressed("theme.aplicar_acento", exc)


def _make_cli_approval_callback(console: Console):
    """Gate 1 — padrões PERIGOSOS (rm -rf, fork bomb…). Compatível com
    check_all_command_guards: (command, description) -> decisão. "always" grava
    no ~/.bauer/approvals.yaml (approve_permanent) pelo próprio pipeline."""
    def _cb(command: str, description: str) -> str:
        # O título vai como TEXTO puro: quem estiliza é o `approval_card`
        # (plano 028 F3). Markup aqui competiria com o tema e voltaria a
        # espalhar cor solta pelo código.
        return _prompt_cmd_decision(
            console, f"{_glifo_aviso()} confirmar comando",
            f"[{_warn()}]{description}[/]\n\n[bold]$[/bold] [white]{command[:200]}[/white]",
        )
    return _cb


def _make_cli_allowlist_callback(console: Console):
    """Gate 2 — allowlist do shell (o que engessa: docker/pip/kubectl…).
    Recebe o binário `base` e devolve a decisão; "always" persiste em
    ~/.bauer/allowed_commands.yaml (via ShellRunner.add_learned_command)."""
    def _cb(base: str) -> str:
        return _prompt_cmd_decision(
            console, f"{_glifo_aviso()} comando fora da allowlist",
            f"[white]'{base}'[/white] [{_warn()}]não está na allowlist[/].\n"
            f"Liberar para o Bauer executar comandos [bold]{base}[/bold]?",
        )
    return _cb


def _make_streamer(console: Console):
    """Cria o renderizador de streaming do turno, ou None se não fizer sentido.

    Fica desligado fora de terminal interativo: sem TTY (pipe, CI, `bauer agent
    < arquivo`) o texto ainda sai — pelo caminho normal, no fim do turno — e
    sem sequência de controle de cursor no meio. Também respeita
    `agent.stream_response=false` no config, para quem preferir a resposta
    inteira de uma vez.
    """
    try:
        from .config_loader import load_config
        if not bool(getattr(load_config().agent, "stream_response", True)):
            return None
    except Exception as _exc:  # noqa: BLE001 — sem config legível, streaming é o padrão
        from .logging_config import log_suppressed
        log_suppressed("stream.config", _exc)
    try:
        if not sys.stdin.isatty():
            return None
        from .ui_stream import ConsoleStreamRenderer
        return ConsoleStreamRenderer(console)
    except Exception:  # noqa: BLE001 — nunca impedir o turno por causa do render
        return None


@contextmanager
def _quadro_do_turno(console: Console):
    """Abre o quadro vivo do turno (HUD fixo + trilho), ou não abre nada.

    Devolve o `TurnFrame` ou `None` — `None` fora de terminal interativo, com
    o streaming desligado no config, ou se o terminal não suportar Live. Quem
    chama trata os dois casos com o mesmo `with`, sem `if` espalhado.

    O quadro é do TURNO, não da sessão: entre um prompt e outro quem desenha o
    rodapé é a `bottom_toolbar` do prompt_toolkit (que precisa do terminal
    livre para ler input). Os dois nunca coexistem — é essa alternância que
    mantém o estado sempre visível sem os dois brigarem pelo terminal.
    """
    from .ui_frame import TurnFrame

    try:
        if not sys.stdin.isatty():
            yield None
            return
    except Exception:  # noqa: BLE001
        yield None
        return

    try:
        frame = TurnFrame(console)
    except Exception:  # noqa: BLE001
        yield None
        return

    with frame:
        yield frame if frame.is_live else None


def _ja_exibido(streamer, texto: str) -> bool:
    """A resposta já apareceu na tela via streaming?

    Compara o TEXTO, não só o "escreveu algo": entre o que o modelo emitiu e o
    que o turno resolve exibir há pós-processamento (gate de qualidade, replan,
    complemento). Quando os dois divergem, o certo é imprimir — repetir é feio,
    esconder é perder resposta.
    """
    if streamer is None or not getattr(streamer, "escreveu_algo", False):
        return False
    return streamer.text.strip() == (texto or "").strip()


def _print_assistant_response(console: Console, text: str, cost_line: str = "") -> None:
    """Render da resposta final: cabeçalho `● bauer` + corpo em Markdown.

    Markdown dá formatação real no terminal (código com syntax highlight,
    listas, tabelas, negrito) — antes a resposta saía como texto cru via
    sys.stdout. Fallback para texto puro se o corpo quebrar o parser.
    """
    from rich.text import Text as _Text

    console.print()
    try:
        from .ui import response_header as _rh
        # cost_line é uma linha pronta (pode ter markup) — extrai texto p/ meta.
        _meta = _Text.from_markup(cost_line).plain.strip() if cost_line else ""
        console.print(_rh(cost=_meta))
    except Exception:
        console.print(_Text("● bauer", style="bold #00d4aa"))
    try:
        from rich.markdown import Markdown as _Markdown
        console.print(_Markdown(text))
    except Exception:
        console.print(text, markup=False)
    console.print()


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
    # Derive provider name from client for circuit breaker tracking
    try:
        from .cost_meter import provider_from_client as _pfn
        _primary_provider = _pfn(client)
    except Exception:
        _primary_provider = getattr(client, "_provider", None) or "openai"

    # --- Circuit breaker: skip primary if already OPEN ---
    try:
        from .circuit_breaker import global_cb, CircuitOpenError
        _cb_available = True
    except Exception:
        _cb_available = False
        global_cb = None  # type: ignore[assignment]
        CircuitOpenError = Exception  # type: ignore[assignment, misc]

    if _cb_available and global_cb is not None and global_cb.is_open(_primary_provider):
        console.print(
            f"[yellow]⚡ Circuit OPEN para '{_primary_provider}' — saltando para fallback[/yellow]"
        )
    else:
        try:
            with _thinking_status(console, model_name):
                resp = _collect_response(client, model_name, payload)
            if _cb_available and global_cb is not None:
                global_cb.record_success(_primary_provider)
            return resp, client, model_name
        except (OllamaError, OpenAIClientError) as primary_exc:
            if _cb_available and global_cb is not None:
                global_cb.record_failure(_primary_provider, primary_exc)
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

            # Fall through to try fallback_clients below
            _primary_failed_exc = primary_exc
        else:
            _primary_failed_exc = None  # type: ignore[assignment]

    if not fallback_clients:
        if "_primary_failed_exc" in dir():
            raise _primary_failed_exc  # type: ignore[name-defined]
        raise OllamaError("Circuit OPEN e sem fallback configurado")

    for fb_entry in fallback_clients:
        fb_client, fb_model = fb_entry[0], fb_entry[1]
        _fb_label = fb_entry[2] if len(fb_entry) > 2 else getattr(fb_client, "default_model", fb_model)
        try:
            from .cost_meter import provider_from_client as _pfn
            _fb_provider = _pfn(fb_client)
        except Exception:
            _fb_provider = getattr(fb_client, "_provider", None) or "openai"
        if _cb_available and global_cb is not None and global_cb.is_open(_fb_provider):
            console.print(f"[dim]  Fallback {_fb_label}: circuit OPEN, pulando[/dim]")
            continue
        console.print(
            f"[yellow]⚡ Provider falhou — tentando fallback: [bold]{_fb_label}[/bold][/yellow]"
        )
        try:
            with _thinking_status(console, fb_model):
                resp = _collect_response(fb_client, fb_model, payload)
            if _cb_available and global_cb is not None:
                global_cb.record_success(_fb_provider)
            return resp, fb_client, fb_model
        except Exception as fb_exc:
            if _cb_available and global_cb is not None:
                global_cb.record_failure(_fb_provider, fb_exc)
            # Overflow de contexto num fallback: o payload não vai caber em
            # NENHUM provider desta varredura (mesmo payload em todos). Para
            # a cadeia aqui e propaga — o chamador comprime o contexto e
            # re-tenta, em vez de queimar dezenas de providers à toa.
            _stop_chain = False
            try:
                from .error_classifier import classify_api_error as _classify_fb
                _stop_chain = _classify_fb(fb_exc).should_compress
            except Exception:
                _stop_chain = False  # classifier indisponível — segue como antes
            if _stop_chain:
                console.print(
                    "[yellow]⚠ Payload grande demais para os providers — "
                    "interrompendo a varredura de fallbacks para comprimir o contexto.[/yellow]"
                )
                raise fb_exc
            console.print(f"[dim]  Fallback {_fb_label} também falhou: {fb_exc}[/dim]")
            continue

    # All fallbacks exhausted
    if "_primary_failed_exc" in dir() and "_primary_failed_exc" in locals():
        raise _primary_failed_exc  # type: ignore[name-defined]
    raise OllamaError("Todos os providers estão com circuit OPEN ou falharam")


class _NativeToolsUnsupported(Exception):
    """Provider rejeitou o parâmetro tools= — downgrade definitivo para bridge."""


# Códigos HTTP que indicam "provider não suporta native tools" (downgrade).
# 429 (rate limit) e 5xx (transiente) NÃO entram — retry native é o correto.
_NATIVE_UNSUPPORTED_CODES = {400, 404, 405, 422, 501}


def _client_supports_native_tools(client) -> bool:
    """True se este cliente sabe fazer function calling nativo.

    Checa as CLASSES CONCRETAS (não duck typing) porque um MagicMock de teste
    responderia True para qualquer getattr e ligaria o caminho native onde o
    teste espera o bridge.

    O OllamaClient entrou aqui junto do `chat_with_tools` dele: antes o gate
    era `isinstance(client, OpenAIClient)`, então `provider: ollama` caía SEMPRE
    no Tool Bridge por prompt — inclusive nos modelos que anunciam a capability
    "tools" — e o `supports_tools: true` do models.yaml só mudava o que o
    `bauer doctor` imprimia, nunca o caminho de execução.
    """
    from .ollama_client import OllamaClient as _OllamaClientCls
    from .openai_client import OpenAIClient as _OpenAIClientCls

    if not isinstance(client, _OpenAIClientCls | _OllamaClientCls):
        return False
    return bool(getattr(client, "supports_native_tools", False))


def _is_native_unsupported_error(exc: Exception) -> bool:
    """True se o erro indica que o provider não aceita o parâmetro tools=."""
    import re as _re
    m = _re.search(r"HTTP (\d{3})", str(exc))
    return bool(m) and int(m.group(1)) in _NATIVE_UNSUPPORTED_CODES


# Reflexão forçada: a cada N tool calls sem resposta final, injeta um nudge
# pedindo ao modelo para resumir progresso e decidir o próximo passo. Evita
# que o modelo "vagueie" por dezenas de calls sem convergir.
_REFLECT_EVERY = 6

_REFLECT_NUDGE = (
    "[SISTEMA — ponto de reflexão] Você já executou {n} tool calls neste turno "
    "sem dar uma resposta final. Pare e avalie: (1) resuma em 1 frase o que já "
    "descobriu; (2) decida se falta UM passo concreto — se sim, execute apenas "
    "ele; (3) caso contrário, responda ao usuário agora com o que tem."
)


def _maybe_reflect(ctx, n_calls: int) -> None:
    """Injeta nudge de reflexão a cada _REFLECT_EVERY tool calls."""
    if n_calls > 0 and n_calls % _REFLECT_EVERY == 0:
        ctx.add_user(_REFLECT_NUDGE.format(n=n_calls))


def _run_native_tool_turn(
    ctx,
    router: ToolRouter,
    client,
    model_name: str,
    tool_log: list[dict],
    _guardrail=None,
    _deduper=None,
    *,
    tool_timeout_s: float = 0.0,
    _trace=None,
) -> str | None:
    """Executa um turno usando native function calling (OpenAI format).

    Retorna a resposta final de texto quando o modelo para de chamar tools,
    ou None se deve continuar no loop.
    Modifica ctx e tool_log in-place.

    Raises:
        _NativeToolsUnsupported: provider rejeitou tools= (HTTP 400/404/405/
        422/501) — o caller deve fazer downgrade definitivo para o bridge.
        Antes este caso era engolido com `return None`, o que fazia o loop
        re-tentar native até estourar o budget inteiro.
    """
    import json as _json
    schemas = router.get_tool_schemas()
    messages = ctx.get_payload()

    try:
        msg = client.chat_with_tools(model_name, messages, tools=schemas)
    except Exception as exc:
        if _is_native_unsupported_error(exc):
            raise _NativeToolsUnsupported(str(exc)) from exc
        return None  # transiente (timeout, 5xx, rede): tenta de novo no loop

    # Cost meter — mesma medição que _collect_response faz no bridge; sem isto
    # os turnos native (o caminho comum com OpenAI-compat) nunca reportavam
    # custo/tokens a quem estivesse medindo (serve, daemon, goal tracker).
    try:
        from .cost_meter import provider_from_client, report_llm_cost
        report_llm_cost(
            provider_from_client(client),
            model_name,
            getattr(client, "last_usage", None),
        )
    except Exception:
        pass

    tool_calls = msg.get("tool_calls") or []
    content = msg.get("content") or ""

    if not tool_calls:
        # Modelo respondeu sem chamar tools — retorna o texto.
        # Conteúdo vazio NÃO entra no contexto: o caller trata isso como
        # "resposta vazia" e aciona o recovery, e um turno assistant em branco
        # no histórico só atrapalharia a retentativa.
        if content.strip():
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
                    tool_log.append({"tool": name, "args_sig": _args_sig(args), "result": result[:300]})
                    ctx.messages.append({
                        "role": "tool",
                        "tool_call_id": tc_id,
                        "content": result,
                    })
                    # Signal caller by returning None; run_one_turn will halt.
                    return _pre_n.message

        if not _native_guardrail_blocked:
            _native_failed = False
            # Dedup: chamada idêntica bem-sucedida → replay sem re-executar
            _replayed_n = _deduper.check(name, args) if _deduper is not None else None
            if _replayed_n is not None:
                result = _replayed_n
            else:
                # Gateway streaming: mostra "🔧 executando: <tool>…" no canal
                try:
                    from .delta_stream import emit_tool as _emit_tool
                    _emit_tool(name)
                except Exception:
                    pass
                _tool_span = _trace.span(f"tool:{name}", input={"args": args}) if _trace is not None else None
                try:
                    from .tool_timeout import call_with_timeout as _call_to
                    result, _timed_out = _call_to(
                        lambda: router.execute_native_call(name, args),
                        tool_timeout_s,
                        name,
                    )
                    if _timed_out:
                        _native_failed = True
                    if _tool_span is not None:
                        _tool_span.end(output=str(result)[:500], level="WARNING" if _timed_out else "DEFAULT")
                except (ToolError, SandboxError) as exc:
                    result = f"[Erro: {exc}]"
                    _native_failed = True
                    if _tool_span is not None:
                        _tool_span.end(output=result, level="ERROR")
                if _deduper is not None:
                    _deduper.record(name, args, result, failed=_native_failed)

            # Wave 4.5: post-call guardrail update (native path)
            if _guardrail is not None:
                _post_n = _guardrail.after_call(name, args, result, failed=_native_failed)
                if _post_n.action == "warn":
                    ctx.add_user(_post_n.message)

        ctx_result, _ = _ctx_result_for_context(name, result)
        tool_log.append({"tool": name, "args_sig": _args_sig(args), "result": result[:300]})
        ctx.messages.append({
            "role": "tool",
            "tool_call_id": tc_id,
            "content": ctx_result,
        })

    return None  # continua o loop


def _native_turn_interactive(
    ctx,
    router: ToolRouter,
    client,
    model_name: str,
    console: Console,
    cli_tool_log: list[dict],
    deduper,
    calls_left: int,
    guardrail=None,
    streamer=None,
    budget=None,
) -> tuple[str, str | None]:
    """Um turno de native function calling no chat interativo.

    Espelha o fluxo bridge do run_agent_session (display por tool, dedup,
    cli_tool_log) mas usa tool_calls nativos do provider em vez de parsing
    JSON da resposta — mais confiável em modelos que suportam.

    ``guardrail`` (ToolCallGuardrailController opcional, usado pelo /loop —
    ver run_one_turn/_run_native_tool_turn para o mesmo padrão) acumula
    falhas entre chamadas; None preserva o comportamento atual (sem guarda).

    ``streamer`` (ConsoleStreamRenderer opcional, plano 028 F1) faz o texto
    aparecer enquanto o modelo escreve. Quando presente, o spinner "pensando…"
    é dispensado — os dois disputariam o mesmo terminal, e o Rich só admite um
    display ao vivo por vez. Sem ele, tudo segue como antes.

    Returns:
        ("final", texto)          — modelo respondeu sem tools; exibir e encerrar turno
        ("continue", None)        — tools executadas; voltar ao loop para nova chamada
        ("guardrail_halt", texto) — guardrail mandou parar (só quando guardrail != None)

    Raises:
        _NativeToolsUnsupported: downgrade definitivo para bridge (sessão).
        OpenAIClientError: erros de rede/HTTP — tratados pelo handler do loop.
    """
    import json as _json

    schemas = router.get_tool_schemas()
    try:
        if streamer is not None:
            # Sem spinner: o texto aparecendo JÁ é o indicador de atividade, e
            # dois displays ao vivo no mesmo console se atropelam.
            msg = client.chat_with_tools(
                model_name, ctx.get_payload(), tools=schemas,
                on_delta=streamer.on_delta,
            )
            streamer.on_round()  # sela o que veio antes das tools/da resposta
        else:
            with _thinking_status(console, model_name):
                msg = client.chat_with_tools(model_name, ctx.get_payload(), tools=schemas)
    except TypeError as exc:
        # Client de terceiro/dublê sem o parâmetro `on_delta`: streaming é
        # opcional, a resposta não — repete sem ele. A checagem do nome é
        # deliberada: um TypeError vindo de DENTRO do provider dispararia uma
        # segunda chamada (e uma segunda cobrança) sem ela.
        if "on_delta" not in str(exc):
            raise
        with _thinking_status(console, model_name):
            msg = client.chat_with_tools(model_name, ctx.get_payload(), tools=schemas)
    except Exception as exc:
        if _is_native_unsupported_error(exc):
            raise _NativeToolsUnsupported(str(exc)) from exc
        raise

    tool_calls = msg.get("tool_calls") or []
    content = msg.get("content") or ""

    if not tool_calls:
        return "final", content

    ctx.messages.append({"role": "assistant", "content": content, "tool_calls": tool_calls})

    # Executa TODAS as tool_calls do batch: cada `tool_calls` da mensagem assistant
    # PRECISA de uma resposta `tool` correspondente, senão o provider rejeita o
    # próximo request (400 "tool_call_id sem resposta"). O cap de rounds é feito
    # pelo loop externo (MAX_TOOL_TURNS) — não truncamos o batch aqui.
    for tc in tool_calls:
        tc_id = tc.get("id", "call_0")
        fn = tc.get("function", {})
        name = fn.get("name", "?")
        try:
            args = _json.loads(fn.get("arguments", "{}"))
        except _json.JSONDecodeError:
            args = {}

        # Reserve the call before dispatch. A provider may emit a large native
        # batch, but the hard budget controls actual execution, not accounting
        # after the side effect has already happened.
        if budget is not None:
            if budget.remaining_tool_calls() <= 0:
                result = "[BLOCKED] tool call budget exhausted"
                cli_tool_log.append({"tool": name, "args_sig": _args_sig(args),
                                     "result": result, "budget_accounted": True})
                ctx.messages.append({"role": "tool", "tool_call_id": tc_id, "content": result})
                continue
            budget.consume_tool_call()

        # Guardrail pre-call (mesmo padrão de _run_native_tool_turn) — só ativo
        # quando o caller passa uma instância (ex.: /loop).
        _guard_blocked = False
        if guardrail is not None:
            _pre = guardrail.before_call(name, args)
            if _pre.should_halt:
                ctx.add_user(_pre.message)
                result = f"[BLOCKED] {_pre.message}"
                _guard_blocked = True
                cli_tool_log.append({"tool": name, "args_sig": _args_sig(args), "result": result[:300],
                                     "budget_accounted": budget is not None})
                ctx.messages.append({"role": "tool", "tool_call_id": tc_id, "content": result})
                if _pre.action == "halt":
                    console.print(f"  [yellow]⛔ guardrail:[/yellow] {_pre.message}")
                    return "guardrail_halt", _pre.message

        if not _guard_blocked:
            _failed = False
            _elapsed_ms: "int | None" = None
            _cached = deduper.check(name, args) if deduper is not None else None
            if _cached is not None:
                result = _cached
            else:
                # Cronômetro real (plano 028 F3). `tool_line` aceita
                # `elapsed_ms` desde sempre, mas NENHUM call site o passava — o
                # componente sabia exibir uma duração que ninguém media.
                # `perf_counter` e não `time()`: medir intervalo com relógio de
                # parede erra se o relógio do sistema for ajustado no meio.
                _t0 = time.perf_counter()
                try:
                    with _tool_exec_status(console, name):
                        result = router.execute_native_call(name, args)
                except (ToolError, SandboxError) as exc:
                    result = f"[Erro: {exc}]"
                    _failed = True
                _elapsed_ms = int((time.perf_counter() - _t0) * 1000)
                if deduper is not None:
                    deduper.record(name, args, result, failed=_failed)

            if guardrail is not None:
                _post = guardrail.after_call(name, args, result, failed=_failed)
                if _post.action == "warn":
                    ctx.add_user(_post.message)
                elif _post.should_halt:
                    ctx.add_user(_post.message)
                    ctx_result, _ = _ctx_result_for_context(name, result)
                    cli_tool_log.append({"tool": name, "args_sig": _args_sig(args), "result": result[:300],
                                         "budget_accounted": budget is not None})
                    ctx.messages.append({"role": "tool", "tool_call_id": tc_id, "content": ctx_result})
                    console.print(f"  [yellow]⛔ guardrail:[/yellow] {_post.message}")
                    return "guardrail_halt", _post.message

            display_line = _format_tool_display(name, result)
            try:
                from .ui import tool_block as _tool_block
                console.print(_tool_block(
                    name, display_line,
                    status=("fail" if _failed else "ok"),
                    elapsed_ms=_elapsed_ms,
                    result=result,
                ))
            except Exception:
                console.print(f"  [dim]→[/dim] [cyan]{name}[/cyan]  {display_line}")

            ctx_result, _ = _ctx_result_for_context(name, result)
            cli_tool_log.append({"tool": name, "args_sig": _args_sig(args), "result": result[:300],
                                 "budget_accounted": budget is not None})
            ctx.messages.append({
                "role": "tool",
                "tool_call_id": tc_id,
                "content": ctx_result,
            })

    console.print()
    return "continue", None


def run_one_turn(
    ctx,
    router: ToolRouter,
    client: OllamaClient,
    model_name: str,
    *,
    budget: "IterationBudget | None" = None,  # noqa: F821
    tool_timeout_s: float = 30.0,
    tracer: "Any | None" = None,
    session_id: str | None = None,
    memory_provider: "Any | None" = None,
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
    from .tracing import _NoopTrace as _TraceNoop

    _memprov = memory_provider  # G25: lifecycle hooks
    tool_log: list[dict] = []
    if budget is None:
        # `MAX_TOOL_TURNS + 1`: até MAX rodadas de tool call, +1 turno final para
        # o modelo emitir resposta de texto após a última tool.
        budget = _IterBudget(max_total=MAX_TOOL_TURNS + 1)

    # ``run_one_turn`` predates AutonomousBudget and used IterationBudget's
    # ``consume/exhausted`` names. /loop shares its hard budget with this
    # engine, so accept both interfaces without creating a shadow counter.
    def _budget_exhausted() -> bool:
        return bool(getattr(budget, "is_exhausted", getattr(budget, "exhausted", False)))

    def _consume_llm() -> None:
        if hasattr(budget, "consume_llm_call"):
            budget.consume_llm_call()
        else:
            budget.consume()

    _trace: Any = _TraceNoop()
    if tracer is not None:
        try:
            _trace = tracer.trace(
                "run_one_turn",
                session_id=session_id,
                metadata={"model": model_name},
            )
        except Exception:
            _trace = _TraceNoop()

    # Wave 4.5: per-turn guardrail controller (tracks cumulative failures /
    # no-progress across all tool calls in this turn).
    _guardrail = None
    try:
        from .tool_guardrails import ToolCallGuardrailController as _GuardrailCtrl
        _guardrail = _GuardrailCtrl()
    except ImportError:
        pass

    # Dedup de chamadas idênticas bem-sucedidas (replay em vez de re-executar)
    from .tool_dedup import ToolCallDeduper as _Deduper
    _deduper = _Deduper()

    use_native = _client_supports_native_tools(client)

    if use_native:
        _native_empty_recovered = False
        while not _budget_exhausted():
            _consume_llm()
            try:
                result = _run_native_tool_turn(ctx, router, client, model_name, tool_log,
                                               _guardrail=_guardrail, _deduper=_deduper,
                                               tool_timeout_s=tool_timeout_s, _trace=_trace)
            except _NativeToolsUnsupported:
                # Provider não aceita tools= — downgrade definitivo para o
                # bridge (abaixo) sem queimar o restante do budget.
                use_native = False
                if hasattr(budget, "refund"):
                    budget.refund()
                break

            # Resposta vazia sem tool call: o bridge já recuperava disso desde
            # sempre (retry → force_compress → retry); o caminho native devolvia
            # a string vazia como se fosse a resposta final e o turno morria com
            # "Resposta vazia — parei". Nunca doeu porque só provider cloud caía
            # aqui; com o Ollama usando native, dói.
            #
            # A camada de compressão é o tratamento certo: medido no Beelink,
            # o qwen3-coder:30b devolve vazio (eval_count=1) a partir de ~13k
            # tokens de prompt mesmo com num_ctx=32768 — encolher o payload é
            # literalmente a saída.
            if result is not None and not str(result).strip():
                if not _native_empty_recovered:
                    _native_empty_recovered = True
                    # A recuperação chama o provider de novo; se ELA falhar, o
                    # turno vira "Erro de provider" e o usuário perde tudo que
                    # as tools já fizeram nas rodadas anteriores. Falha de
                    # recuperação é diagnóstico, não erro de transporte.
                    try:
                        recovered, diagnostico = _recover_empty_response(client, model_name, ctx)
                    except Exception as _rec_exc:  # noqa: BLE001
                        recovered, diagnostico = "", (
                            f"[Modelo retornou resposta vazia e a recuperação também "
                            f"falhou: {_rec_exc}]"
                        )
                    if recovered.strip():
                        ctx.add_assistant(recovered)
                        return recovered, tool_log
                    return diagnostico or (
                        "[Modelo retornou resposta vazia e não se recuperou. Causa "
                        "provável: prompt grande demais para este modelo — reduza "
                        "tools.tool_allowlist ou use /clear.]"
                    ), tool_log
                return (
                    "[Modelo retornou resposta vazia repetidamente — sessão instável]"
                ), tool_log

            if result is not None:
                return result, tool_log
            _maybe_reflect(ctx, len(tool_log))
            # Detecção de loop após cada rodada de tool calls (native path)
            loop_warn, hard_stop = _detect_loop(tool_log)
            if loop_warn:
                ctx.add_user(loop_warn)
            if hard_stop:
                return "[Loop detectado — tarefa interrompida automaticamente]", tool_log
        if use_native:
            # Budget esgotado sem resposta final (ainda em native)
            return "[Limite de iterações atingido]", tool_log

    # Tool Bridge (fallback para Ollama e modelos sem native tool calling)
    _empty_recovered = False
    while not _budget_exhausted():
        _consume_llm()
        response = _collect_response(client, model_name, ctx.get_payload())

        # Resposta vazia: recovery em camadas (retry → compress+retry).
        # Só tenta o recovery completo uma vez por run para não mascarar
        # um provider genuinamente fora do ar.
        # Resposta DEGENERADA conta como vazia para efeito de recuperação: um
        # `{"{"{"{"...` não é conteúdo, é o modelo em loop de repetição. Sem
        # isto o texto caía direto no caminho de "resposta final" e era impresso
        # cru para o usuário — o pior desfecho possível, porque parece resposta.
        # A causa mais comum é a mesma da resposta vazia (contexto sobrecarregado
        # / prompt truncado), então o recovery em camadas — que inclui
        # force_compress — é exatamente o tratamento certo.
        _degenerada = _e_resposta_degenerada(response)
        if not response.strip() or _degenerada:
            if _degenerada:
                import logging as _lg
                _lg.getLogger("bauer.agent").warning(
                    "resposta degenerada do modelo (%d chars, %d distintos) — acionando recovery",
                    len(response), len(set(response)),
                )
            if not _empty_recovered:
                _empty_recovered = True
                response, diagnostico = _recover_empty_response(client, model_name, ctx)
                if not response or _e_resposta_degenerada(response):
                    return diagnostico or (
                        "[Modelo entrou em loop de repetição e não se recuperou. "
                        "Causa provável: contexto pequeno demais para o prompt — "
                        "aumente model.requested_context ou use /clear.]"
                    ), tool_log
            else:
                return "[Modelo retornou resposta vazia ou degenerada repetidamente — sessão instável]", tool_log

        ctx.add_assistant(response)

        # Suporte a batch: modelo pode emitir múltiplos tool calls por resposta
        actions = _try_parse_tools_batch(response, router)

        if actions is not None and len(tool_log) < MAX_TOOL_TURNS:
            combined_parts: list[str] = []
            for action_dict in actions:
                if len(tool_log) >= MAX_TOOL_TURNS:
                    break
                if hasattr(budget, "remaining_tool_calls"):
                    if budget.remaining_tool_calls() <= 0:
                        return "[Limite de ferramentas atingido]", tool_log
                    # Reserve before router.execute: a large batch cannot
                    # side-step max_tool_calls while waiting for post-accounting.
                    budget.consume_tool_call()
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
                        tool_log.append({"tool": action_name, "args_sig": _args_sig(action_args), "result": tool_result[:300],
                                         "budget_accounted": hasattr(budget, "remaining_tool_calls")})
                        combined_parts.append(f"[Resultado de {action_name}]\n{tool_result}")
                        continue

                # Dedup: chamada idêntica bem-sucedida → replay sem re-executar
                _replayed = _deduper.check(action_name, action_args)
                _tool_failed = False
                if _replayed is not None:
                    tool_result = _replayed
                else:
                    try:
                        from .delta_stream import emit_tool as _emit_tool
                        _emit_tool(action_name)
                    except Exception:
                        pass
                    _bridge_span = _trace.span(f"tool:{action_name}", input={"args": action_args})
                    try:
                        from .tool_timeout import call_with_timeout as _call_to
                        tool_result, _timed_out = _call_to(
                            lambda: router.execute(action_dict),
                            tool_timeout_s,
                            action_name,
                        )
                        if _timed_out:
                            _tool_failed = True
                        _bridge_span.end(
                            output=str(tool_result)[:500],
                            level="WARNING" if _timed_out else "DEFAULT",
                        )
                    except (ToolError, SandboxError) as exc:
                        tool_result = f"[Erro: {exc}]"
                        _tool_failed = True
                        _bridge_span.end(output=tool_result, level="ERROR")
                    _deduper.record(action_name, action_args, tool_result, failed=_tool_failed)

                # G25: memory lifecycle — handle_tool_call + on_delegation
                if _memprov is not None and not _tool_failed:
                    try:
                        _memprov.handle_tool_call(action_name, action_args, str(tool_result))
                        if action_name == "delegate_task":
                            sub = action_args.get("task", "") or action_args.get("context", "")
                            _memprov.on_delegation(str(sub), str(tool_result))
                    except Exception:
                        pass

                # Wave 4.5: post-call guardrail update
                if _guardrail is not None:
                    _post = _guardrail.after_call(
                        action_name, action_args, tool_result, failed=_tool_failed
                    )
                    if _post.action == "warn":
                        ctx.add_user(_post.message)
                    elif _post.should_halt:
                        ctx.add_user(_post.message)
                        tool_log.append({"tool": action_name, "args_sig": _args_sig(action_args), "result": tool_result[:300],
                                         "budget_accounted": hasattr(budget, "remaining_tool_calls")})
                        return _post.message, tool_log

                tool_log.append({"tool": action_name, "args_sig": _args_sig(action_args), "result": tool_result[:300],
                                 "budget_accounted": hasattr(budget, "remaining_tool_calls")})

                ctx_result, _ = _ctx_result_for_context(action_name, tool_result)
                combined_parts.append(f"[Resultado de {action_name}]\n{ctx_result}")

            if combined_parts:
                ctx.add_user("\n\n".join(combined_parts))
            _maybe_reflect(ctx, len(tool_log))
            # Detecção de loop após cada batch de tool calls (Tool Bridge path)
            loop_warn, hard_stop = _detect_loop(tool_log)
            if loop_warn:
                ctx.add_user(loop_warn)
            if hard_stop:
                return "[Loop detectado — tarefa interrompida automaticamente]", tool_log
        else:
            return response, tool_log

    return response, tool_log


def run_one_turn_with_fallback(
    ctx,
    router: ToolRouter,
    client: OllamaClient,
    model_name: str,
    fallback_clients: "list | None" = None,
    **kwargs,
) -> tuple[str, list[dict]]:
    """run_one_turn + cascata de fallback de provider em erro retryável (429/5xx).

    Sem isto, o gateway e o ``bauer serve`` (que chamam run_one_turn direto)
    devolvem o 429/5xx cru quando o provider primário está rate-limited — ao
    contrário do CLI ``bauer agent``, que já cascateia. Aqui replicamos essa
    resiliência para os dois.

    ``fallback_clients``: lista de ``(client, model_name)`` (o formato de
    ``_build_fallback_clients``). Em falha classificada como ``should_fallback``
    no primário, tenta o próximo; restaura ``ctx.messages`` ao estado pré-turno
    entre tentativas para não acumular lixo (um 429 estoura na 1ª chamada LLM,
    antes de qualquer tool rodar, então a restauração costuma ser no-op).

    Levanta a exceção do último provider quando todos falham.
    """
    attempts: list[tuple[Any, str]] = [(client, model_name)]
    for fb in (fallback_clients or []):
        if isinstance(fb, (list, tuple)) and len(fb) >= 2:
            attempts.append((fb[0], fb[1]))
        else:
            attempts.append((fb, model_name))

    _snapshot = list(ctx.messages)
    last_exc: Exception | None = None
    for idx, (_client, _model) in enumerate(attempts):
        if idx > 0:
            # Desfaz mutações parciais do turno que falhou antes de re-tentar.
            ctx.messages = list(_snapshot)
        try:
            return run_one_turn(ctx, router, _client, _model, **kwargs)
        except (OllamaError, OpenAIClientError) as exc:
            last_exc = exc
            # Última tentativa ou erro não-retryável → propaga.
            if idx >= len(attempts) - 1:
                raise
            try:
                from .error_classifier import classify_api_error
                if not classify_api_error(exc).should_fallback:
                    raise
            except ImportError:
                pass  # sem classifier: assume retryável e tenta o próximo
            import logging as _logging
            _logging.getLogger("bauer.agent").info(
                "run_one_turn: provider falhou (%s) — fallback %d/%d",
                exc.__class__.__name__, idx + 1, len(attempts) - 1,
            )
    if last_exc is not None:
        raise last_exc
    return "", []  # inalcançável (attempts sempre tem ≥1), satisfaz o type checker


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




# ─── /spec handler ───────────────────────────────────────────────────────────






# ─── /task handler ───────────────────────────────────────────────────────────








# ─── /memory handler ─────────────────────────────────────────────────────────




# ─── /project handler ────────────────────────────────────────────────────────




# ─── Checkpoint de planejamento (App Factory → /loop) ────────────────────────


def _resolve_planning_checkpoint() -> bool:
    """Lê agent.planning_checkpoint (best-effort, default True se falhar)."""
    try:
        from .config_loader import load_config
        return bool(load_config().agent.planning_checkpoint)
    except Exception:
        return True


def _af_active_gate(active_workspace) -> "tuple[Any, int | None]":  # type: ignore[type-arg]
    """(project_dir, gate_int) do projeto App Factory ativo — (None, None) se não há."""
    try:
        from . import app_factory as af
        proj = af.get_active_project(active_workspace)
        if proj is None:
            return None, None
        g = af.current_gate(proj)
        return proj, (int(g) if g is not None else None)
    except Exception:
        return None, None


_BACKLOG_ITEM_RE = re.compile(r"^-\s*\[[ xX]?\]\s+(.+)$")
_BACKLOG_STORY_RE = re.compile(r"^###\s+(.+)$")


def _parse_backlog_tasks(text: str, *, cap: int = 40) -> "list[dict]":
    """Extrai tarefas de topo ('- [ ] ...') do BACKLOG.md, agrupadas por '## Fase'.

    Só itens de nível 0-1 viram card (sub-bullets de prioridade/critério e
    checkboxes aninhados são ignorados). Prioridade lida do sub-bullet
    'Prioridade: alta/média/baixa' logo abaixo, se houver.

    Se nenhum checkbox for encontrado, tenta um fallback lendo headings
    '### ...' como itens (ex.: '### Story 1.1: Título'). Visto na prática
    (financeos-pme): o modelo às vezes foge do template
    (data/app_factory/templates/BACKLOG.md, que usa checkbox) e descreve o
    backlog como stories em heading — sem o fallback, um BACKLOG.md real e
    completo semeava ZERO cards, silenciosamente.
    """
    tasks = _extract_backlog_items(text, _BACKLOG_ITEM_RE, cap=cap)
    if tasks:
        return tasks
    return _extract_backlog_items(text, _BACKLOG_STORY_RE, cap=cap)


def _extract_backlog_items(text: str, item_re: "re.Pattern[str]", *, cap: int) -> "list[dict]":
    tasks: list[dict] = []
    phase = ""
    lines = text.splitlines()
    for i, raw in enumerate(lines):
        stripped = raw.strip()
        if stripped.startswith("## "):
            phase = stripped[3:].strip()
            continue
        if (len(raw) - len(raw.lstrip())) > 1:  # sub-bullet — ignora
            continue
        m = item_re.match(stripped)
        if not m:
            continue
        title = m.group(1).strip()
        if not title:
            continue
        prio = "medium"
        for j in range(i + 1, min(i + 5, len(lines))):
            sub = lines[j].strip().lower()
            if not sub:
                continue
            if sub.startswith("## ") or item_re.match(lines[j].strip()):
                break
            if "priorid" in sub or "priority" in sub:
                if "alta" in sub or "high" in sub:
                    prio = "high"
                elif "baixa" in sub or "low" in sub:
                    prio = "low"
                elif "crit" in sub:
                    prio = "critical"
                break
        tasks.append({"title": title[:200], "phase": phase, "priority": prio})
        if len(tasks) >= cap:
            break
    return tasks


def _seed_kanban_from_backlog(router, project_dir, console) -> int:
    """Cria cards no kanban a partir do BACKLOG.md do projeto. Retorna nº criados.

    Usa router._kanban_create (mesmo store do ledger que o /loop lê), então os
    cards aparecem tanto no board (/task) quanto no contexto de início do loop.
    """
    from pathlib import Path as _P
    backlog = _P(project_dir) / "docs" / "BACKLOG.md"
    if not backlog.is_file():
        console.print("[dim]BACKLOG.md não encontrado — nada para semear.[/dim]")
        return 0
    try:
        text = backlog.read_text(encoding="utf-8")
    except OSError:
        return 0
    tasks = _parse_backlog_tasks(text)
    if not tasks:
        console.print("[dim]BACKLOG.md sem itens '- [ ]' reconhecíveis — nada semeado.[/dim]")
        return 0
    _name = _P(project_dir).name
    created = 0
    for t in tasks:
        try:
            router._kanban_create({
                "title": t["title"],
                "priority": t["priority"],
                "description": f"[{_name}] {t['phase']}".strip(),
            })
            created += 1
        except Exception:
            continue
    if created:
        console.print(f"[green]✓[/green] {created} card(s) criados no kanban a partir do BACKLOG.md.")
    return created


def _ensure_kanban_seeded(router, proj, console) -> int:
    """Kanban = fonte única quando `task_backend=sqlite` — semeia sem perguntar
    e sem depender de "acabou de cruzar o gate nesta sessão".

    Antes disto, semear só acontecia via `Confirm.ask` no instante exato em
    que os 7 docs terminavam de ser preenchidos NA MESMA SESSÃO — e
    `_gate_before` é capturado uma vez no início da sessão (linha ~5713). Um
    projeto que cruzou o gate numa sessão anterior, ou via `/loop` (que nem
    passa por TTY), nunca era ofertado de novo: o kanban ficava vazio para
    sempre, mesmo com IMPLEMENTATION alcançado e BACKLOG.md cheio de itens.
    Foi exatamente o sintoma relatado ("por que o kanban está vazio?").

    Idempotente por construção: só semeia se o board estiver vazio. Chamado
    tanto no boot da sessão (pega projetos que já cruzaram antes) quanto no
    checkpoint (pega o cruzamento ao vivo) — os dois caminhos convergem aqui.

    Em `task_backend=markdown` é no-op: ali "semear" segue com o fluxo antigo
    (Confirm interativo, ver `_maybe_planning_checkpoint`), porque as tarefas
    já vão para o MESMO TASKS.md que o resto do workspace usa — não há board
    separado para unificar.
    """
    try:
        from .workspace_manager_factory import get_workspace_manager, resolve_task_backend

        if resolve_task_backend() != "sqlite":
            return 0

        from . import app_factory as _af
        gate = _af.current_gate(proj)
        if gate is None or int(gate) < int(_af.Gate.IMPLEMENTATION):
            return 0

        wm = get_workspace_manager(router.workspace)
        if wm.list_tasks():
            return 0  # já tem cards — não duplica

        seeded = _seed_kanban_from_backlog(router, proj, console)
        if seeded:
            console.print(
                f"[dim]  (kanban é a fonte única — {seeded} card(s) "
                "semeados automaticamente do BACKLOG.md)[/dim]"
            )
        return seeded
    except Exception as exc:  # noqa: BLE001 — nunca bloqueia o boot/checkpoint
        from .logging_config import log_suppressed
        log_suppressed("kanban.ensure_seeded", exc)
        return 0


def _maybe_planning_checkpoint(
    console, router, active_workspace, gate_before, enabled: bool
) -> "tuple[str, str] | None":
    """Se o projeto ativo acabou de cruzar p/ IMPLEMENTATION, oferece o checkpoint.

    Retorna None (sem checkpoint / desabilitado / não-interativo / declinou),
    ou ('develop', task) / ('revisar', '') / ('continuar', ''). Só 'develop'
    exige ação do caller (disparar _run_loop_mode com a task).
    """
    if not enabled:
        return None
    import sys as _sys
    from . import app_factory as _af

    proj, gate_after = _af_active_gate(active_workspace)
    _IMPLEMENTATION = int(_af.Gate.IMPLEMENTATION)
    crossed = (
        proj is not None and gate_after is not None
        and gate_after >= _IMPLEMENTATION
        and (gate_before is None or gate_before < _IMPLEMENTATION)
    )
    if not crossed:
        return None
    # Não-interativo (canal/CI/pipe): não dá pra perguntar — segue sem interromper.
    if not _sys.stdin.isatty():
        return None

    from pathlib import Path as _P
    from rich.panel import Panel as _Panel
    _name = _P(proj).name
    try:
        _score = f"{_af.delivery_score(proj).get('score', 0)}/10"
    except Exception:
        _score = "?"

    console.print()
    console.print(_Panel(
        f"[bold green]Planejamento completo[/bold green] — [cyan]{_name}[/cyan] "
        f"(gate: implementation, score {_score})\n\n"
        "Como seguir?\n"
        f"  [bold]R[/bold] Revisar antes    — pausa p/ você editar os docs (ex.: {_name}/docs/SPEC.md)\n"
        "  [bold]D[/bold] Desenvolver      — dispara /loop autônomo agora (implementa a V1, verify a cada fatia)\n"
        "  [bold]C[/bold] Continuar manual — sem loop; você conduz turno a turno",
        title="[bold]Checkpoint — App Factory[/bold]", border_style="green",
    ))
    try:
        from rich.prompt import Prompt
        choice = Prompt.ask(
            "Escolha", choices=["R", "D", "C", "r", "d", "c"], default="R"
        ).strip().lower()
    except Exception:
        return None

    if choice == "r":
        console.print(
            f"[dim]Revise/edite os docs em [cyan]{_name}/docs/[/cyan]. Quando estiver "
            "pronto, peça a implementação ou rode [bold]/loop[/bold]. "
            "Veja [bold]/spec list[/bold] e [bold]/project[/bold].[/dim]"
        )
        return ("revisar", "")
    if choice == "c":
        console.print("[dim]Ok — modo manual. Você conduz cada passo.[/dim]")
        return ("continuar", "")

    # Desenvolver: semeia o kanban, depois monta a task do loop.
    #
    # Com task_backend=sqlite o kanban É a fonte única de tarefas — semear é
    # automático (_ensure_kanban_seeded), sem perguntar. O Confirm só faz
    # sentido em task_backend=markdown, onde "semear" quer dizer "criar
    # entradas no MESMO TASKS.md que o resto do workspace usa" — aí a escolha
    # é do usuário porque mistura o BACKLOG.md deste projeto com o ledger
    # geral do workspace.
    from .workspace_manager_factory import resolve_task_backend as _rtb
    if _rtb() == "sqlite":
        _seeded = _ensure_kanban_seeded(router, proj, console)
    else:
        _seeded = 0
        try:
            from rich.prompt import Confirm
            if Confirm.ask("Semear o kanban a partir do BACKLOG.md?", default=True):
                _seeded = _seed_kanban_from_backlog(router, proj, console)
        except Exception:
            pass
    _kanban_hint = (
        "Trabalhe pelos cards do kanban: kanban_list para ver o board e "
        "kanban_complete(task_id, result=...) ao terminar cada card. "
        if _seeded else ""
    )
    task = (
        f"Implemente a V1 do projeto '{_name}' seguindo os documentos em "
        f"{_name}/docs/ (SPEC.md, ARCHITECTURE.md, BACKLOG.md). Trabalhe uma fatia "
        "vertical por vez (UI -> API -> persistência), rode verify_app a cada fatia "
        "e só avance quando passar. " + _kanban_hint +
        "Atualize PROGRESS.md ao concluir cada item. NÃO use clarify — assuma "
        "premissas razoáveis e registre em DECISIONS.md."
    )
    console.print(f"[cyan]Iniciando desenvolvimento autônomo de '{_name}'…[/cyan]")
    return ("develop", task)


def _ledger_block(workspace_dir: str | None) -> str:
    """Retorna bloco markdown com tarefas pendentes do TASKS.md do workspace.

    Retorna string vazia se não há workspace, TASKS.md não existe ou não há
    tarefas pendentes (TODO/READY/IN_PROGRESS/BLOCKED).
    """
    if not workspace_dir:
        return ""
    try:
        from pathlib import Path as _Path
        tasks_file = _Path(str(workspace_dir)) / "TASKS.md"
        if not tasks_file.is_file():
            return ""
        from .workspace_manager import WorkspaceManager as _WM
        from .workspace_manager_factory import get_workspace_manager
        wm = _WM(str(workspace_dir))
        _PENDING = {"TODO", "READY", "IN_PROGRESS", "BLOCKED"}
        pending = [t for t in wm.list_tasks() if t.status in _PENDING]
        if not pending:
            return ""
        lines = ["# Task Ledger (TASKS.md — tarefas pendentes)\n"]
        for t in pending:
            lines.append(f"- [{t.status}] #{t.id} {t.title}")
        lines.append(
            "\nAtualize o status das tarefas via tools kanban_update_task / "
            "kanban_mark_done à medida que completar cada item."
        )
        return "\n".join(lines)
    except Exception:
        return ""


@dataclass
class _TurnState:
    """Estado mutável de run_agent_session que atravessa múltiplas chamadas a
    _run_tool_loop_body (client/modelo podem trocar por fallback; native_session_ok
    é downgrade definitivo; fb_idx avança sem reiniciar; mem_turn_idx é contador
    de sessão). Mutado in-place pela função — não precisa ser retornado."""
    client: Any
    active_model: str
    native_session_ok: bool
    fb_idx: int = 0
    mem_turn_idx: int = 0


@dataclass
class _TurnOutcome:
    """Resultado de UMA chamada a _run_tool_loop_body — por que o "turno"
    (uma rodada de tool calls até resposta só-texto, ou uma condição de
    parada) terminou, e o que exibir/registrar em seguida."""
    kind: Literal[
        "final",            # resposta de texto, sem mais tool calls — fim natural
        "loop_hard_stop",   # _detect_loop bateu repetição consecutiva
        "guardrail_halt",   # ToolCallGuardrailController mandou parar (só com guardrail != None)
        "tool_limit",       # MAX_TOOL_TURNS atingido no meio do loop nativo
        "provider_error",   # todos os fallbacks falharam
        "empty_response",   # recovery de resposta vazia falhou
        "interrupted",      # KeyboardInterrupt
        "budget_exhausted", # AutonomousBudget esgotado (só com budget != None)
    ]
    display: str = ""
    tool_log: list = field(default_factory=list)
    turn_cost_line: str = ""


def _run_tool_loop_body(
    *,
    ctx,
    router: ToolRouter,
    state: _TurnState,
    console: Console,
    fallback_clients,
    stats,
    tool_timeout_s: float,
    session_store,
    session_id,
    active_workspace,
    turn_input_text: str,
    memprov,
    budget=None,
    guardrail=None,
    streamer=None,
) -> _TurnOutcome:
    """Roda uma rodada de chamadas LLM + tool calls até o modelo responder só
    texto (fim natural) ou uma condição de parada disparar. Extração pura do
    corpo do while True que existia inline em run_agent_session — usada tanto
    pelo fluxo manual (uma chamada por input do usuário) quanto pelo /loop
    (várias chamadas em sequência, sem esperar input humano).

    ``budget``/``guardrail`` são opcionais; ``None`` preserva o comportamento
    de hoje (sem teto de orçamento, sem guarda de falha acumulada) — usado
    pelo fluxo manual. O /loop passa instâncias reais.
    """
    tool_turns = 0
    cli_tool_log: list[dict] = []
    _overflow_compress_attempted = False
    from .tool_dedup import ToolCallDeduper as _CliDeduper
    _cli_deduper = _CliDeduper()

    while True:
        # Checa ANTES de mais uma chamada LLM — consume_llm_call()/consume_tool_call()
        # levantam BudgetExhaustedError se já esgotado, então este é o único ponto
        # que precisa checar (nada consome budget entre uma volta e outra do while).
        if budget is not None and budget.is_exhausted:
            return _TurnOutcome(kind="budget_exhausted", tool_log=cli_tool_log)

        # Aviso precoce de contexto cheio — antes de travar silenciosamente
        usage = ctx.usage_pct
        if usage >= _CTX_WARN_THRESHOLD:
            pct = int(usage * 100)
            console.print(
                f"[yellow]⚠ Contexto em {pct}% do budget "
                f"({ctx.used_tokens}/{ctx.budget} tokens). "
                "Use [bold]/clear[/bold] se o modelo ficar lento.[/yellow]"
            )
        # MemoryProvider: on_turn_start antes da chamada LLM
        if memprov is not None:
            try:
                memprov.on_turn_start(state.mem_turn_idx, ctx.messages)
            except Exception:
                pass

        _native_final = False
        try:
            if state.native_session_ok:
                try:
                    _nkind, _ntext = _native_turn_interactive(
                        ctx, router, state.client, state.active_model, console,
                        cli_tool_log, _cli_deduper,
                        MAX_TOOL_TURNS - tool_turns,
                        guardrail=guardrail,
                        streamer=streamer,
                        budget=budget,
                    )
                except _NativeToolsUnsupported:
                    state.native_session_ok = False
                    console.print(
                        "[dim][native→bridge] provider sem suporte a tools "
                        "nativas — usando Tool Bridge JSON nesta sessão.[/dim]"
                    )
                else:
                    if budget is not None:
                        budget.consume_llm_call()  # client.chat_with_tools() acabou de suceder
                    if _nkind == "guardrail_halt":
                        return _TurnOutcome(kind="guardrail_halt", display=_ntext or "", tool_log=cli_tool_log)
                    if _nkind == "continue":
                        tool_turns = len(cli_tool_log)
                        _maybe_reflect(ctx, tool_turns)
                        loop_warn, hard_stop = _detect_loop(cli_tool_log)
                        if loop_warn:
                            ctx.add_user(loop_warn)
                        if hard_stop:
                            console.print(
                                f"[yellow]⚠ Loop detectado — '{cli_tool_log[-1]['tool']}' "
                                f"repetiu o mesmo resultado {_LOOP_REPEAT_HARD}x seguidas. "
                                "Turno interrompido.[/yellow]"
                            )
                            return _TurnOutcome(kind="loop_hard_stop", tool_log=cli_tool_log)
                        # Cap de rounds (antes feito pelo slice em
                        # _native_turn_interactive, removido p/ não quebrar
                        # o pareamento assistant↔tool).
                        if tool_turns >= MAX_TOOL_TURNS:
                            console.print(
                                f"[yellow]Limite de {MAX_TOOL_TURNS} tool calls "
                                "atingido neste turno.[/yellow]"
                            )
                            return _TurnOutcome(kind="tool_limit", tool_log=cli_tool_log)
                        continue
                    # _nkind == "final": resposta de texto sem tools
                    response = _ntext or ""
                    _native_final = True
            if not _native_final:
                response, state.client, state.active_model = _collect_with_fallback(
                    state.client, state.active_model, ctx.get_payload(), fallback_clients, console
                )
                if budget is not None:
                    budget.consume_llm_call()  # cobre o path bridge — native já contou acima
        except (OllamaError, OpenAIClientError) as exc:
            # Context overflow: o payload não cabe na janela REAL do provider.
            # Fallback com o MESMO payload é inútil (caso real: 62 providers
            # tentados em sequência com 66k tokens, todos falhando). O único
            # recovery que funciona é comprimir o contexto e re-tentar. Se o
            # erro traz o cap real (ex.: "maximum context length is 65536"),
            # encolhe o budget para a compressão automática voltar a disparar
            # nos próximos turnos. Uma tentativa por turno — se comprimir não
            # resolver, segue para a lógica de fallback normal abaixo.
            if not _overflow_compress_attempted:
                try:
                    from .error_classifier import classify_api_error as _classify_ov
                    _cls_ov = _classify_ov(exc)
                except Exception:
                    _cls_ov = None
                if _cls_ov is not None and _cls_ov.should_compress:
                    _overflow_compress_attempted = True
                    _cap = _parse_provider_context_cap(str(exc))
                    if _cap:
                        try:
                            if ctx.shrink_budget(_cap):
                                console.print(
                                    f"[dim]Janela real do provider: {_cap} tokens — "
                                    "budget de contexto ajustado.[/dim]"
                                )
                        except Exception:
                            pass
                    _compressed_ov = False
                    try:
                        _compressed_ov = ctx.force_compress()
                    except Exception:
                        _compressed_ov = False
                    if _compressed_ov:
                        console.print(
                            "[yellow]⚠ Payload excedeu a janela do provider — "
                            "contexto comprimido, tentando novamente…[/yellow]"
                        )
                        continue

            # Tenta fallback automático para erros retryáveis (429, 5xx).
            # Avança em fallback_clients (não reinicia no item 0) para
            # percorrer a lista quando vários providers falham em sequência.
            _did_fallback = False
            if fallback_clients and state.fb_idx < len(fallback_clients):
                try:
                    from .error_classifier import classify_api_error
                    _cls = classify_api_error(exc)
                    _should_fb = _cls.should_fallback
                except Exception:
                    _should_fb = True  # sem classifier: assume retryável
                if _should_fb:
                    _fb = fallback_clients[state.fb_idx]
                    state.fb_idx += 1
                    _fb_client, _fb_model = (
                        (_fb[0], _fb[1]) if isinstance(_fb, (list, tuple))
                        else (_fb, state.active_model)
                    )
                    console.print(
                        f"[yellow]⚡ Provider falhou ({exc.__class__.__name__}) — "
                        f"trocando para fallback {state.fb_idx}/{len(fallback_clients)}: "
                        f"[bold]{_fb_model}[/bold][/yellow]"
                    )
                    state.client = _fb_client
                    state.active_model = _fb_model
                    state.native_session_ok = _client_supports_native_tools(_fb_client)
                    _did_fallback = True
            if not _did_fallback:
                _err_type = "Ollama" if isinstance(exc, OllamaError) else "Provider"
                console.print(f"\n[red]Erro do {_err_type}:[/red] {exc}")
                if fallback_clients and state.fb_idx >= len(fallback_clients):
                    console.print(
                        f"[dim]Todos os {len(fallback_clients)} fallbacks foram tentados.[/dim]"
                    )
                stats.record_error(str(exc))
                if ctx.messages and ctx.messages[-1]["role"] == "user":
                    ctx.messages.pop()
                return _TurnOutcome(kind="provider_error", display=str(exc), tool_log=cli_tool_log)
            # Fallback ativado: re-tenta o turno com o novo client (mesma
            # mensagem do usuário ainda no contexto). Atualiza stats/provider.
            stats.model = state.active_model
            _fb_provider = getattr(state.client, "_provider", None) or "openai"
            stats.provider = _fb_provider
            ctx._provider = _fb_provider
            continue
        except KeyboardInterrupt:
            console.print("\n[dim][interrompido][/dim]")
            if ctx.messages and ctx.messages[-1]["role"] == "user":
                ctx.messages.pop()
            return _TurnOutcome(kind="interrupted", tool_log=cli_tool_log)

        # Resposta vazia: recovery automático (retry → compress+retry)
        # antes de devolver o problema ao usuário.
        if not response.strip():
            console.print("[dim][recovery] resposta vazia — tentando recuperar...[/dim]")
            response, diagnostico = _recover_empty_response(
                state.client, state.active_model, ctx, console=console
            )
            if not response:
                console.print(f"[yellow]{diagnostico}[/yellow]")
                if ctx.messages and ctx.messages[-1]["role"] == "user":
                    ctx.messages.pop()
                return _TurnOutcome(kind="empty_response", display=diagnostico, tool_log=cli_tool_log)

        ctx.add_assistant(response)

        # Tenta parsear como tool action(s) — suporta batch (múltiplos JSONs por resposta).
        # Resposta final do native NÃO é re-parseada: texto que por acaso
        # contenha JSON não deve re-disparar tools.
        actions = None if _native_final else _try_parse_tools_batch(response, router)

        if actions is not None and tool_turns < MAX_TOOL_TURNS:
            combined_parts: list[str] = []

            # Só processa o que cabe dentro do limite de tool turns
            pending_actions = [a for a in actions if tool_turns < MAX_TOOL_TURNS]
            if len(actions) > len(pending_actions):
                console.print(
                    f"[yellow]Limite de {MAX_TOOL_TURNS} tool calls atingido "
                    "neste turno.[/yellow]"
                )

            # Guardrail pre-call (sequencial, antes de disparar a execução —
            # inclusive a paralela). Só ativo quando guardrail != None (/loop).
            _to_execute = pending_actions
            _guard_halted = False
            if guardrail is not None:
                _to_execute = []
                for _a in pending_actions:
                    _aname = _a.get("action", "?")
                    _aargs = _a.get("args", {}) or {}
                    _pre = guardrail.before_call(_aname, _aargs)
                    if _pre.should_halt:
                        ctx.add_user(_pre.message)
                        _blocked = f"[BLOCKED] {_pre.message}"
                        combined_parts.append(f"[Resultado de {_aname}]\n{_blocked}")
                        cli_tool_log.append({"tool": _aname, "args_sig": _args_sig(_aargs), "result": _blocked[:300]})
                        tool_turns += 1
                        if _pre.action == "halt":
                            _guard_halted = True
                            break  # não dispara o resto do batch
                    else:
                        _to_execute.append(_a)

            def _exec_action(action_dict: dict) -> tuple[str, str, str, "int | None"]:
                """Executa 1 action com dedup, timeout e cronômetro.

                O 4º item é `elapsed_ms` (plano 028 F3) — None quando o
                resultado veio do dedup, porque aí não houve execução para
                cronometrar e exibir "0ms" mentiria sobre o que aconteceu.
                """
                _name = action_dict.get("action", "?")
                _args = action_dict.get("args", {}) or {}
                _cached = _cli_deduper.check(_name, _args)
                if _cached is not None:
                    return _name, _cached, _args_sig(_args), None
                _failed = False
                _t0 = time.perf_counter()
                try:
                    from .tool_timeout import call_with_timeout as _call_to
                    _result, _timed_out = _call_to(
                        lambda: router.execute(action_dict),
                        tool_timeout_s,
                        _name,
                    )
                    if _timed_out:
                        _failed = True
                except (ToolError, SandboxError) as _exc:
                    _result = f"[Erro: {_exc}]"
                    _failed = True
                _elapsed = int((time.perf_counter() - _t0) * 1000)
                _cli_deduper.record(_name, _args, _result, failed=_failed)
                return _name, _result, _args_sig(_args), _elapsed

            # Um único spinner cobre o batch inteiro (serial ou paralelo) — não
            # dá pra abrir um spinner por tool no caminho paralelo, Rich só
            # permite um Live display ativo por vez (as threads do
            # ThreadPoolExecutor rodam em background, só a thread principal
            # mexe no console). Antes do F2 (plano 028) uma tool "interativa"
            # no lote (ex.: clarify) fazia o batch inteiro pular o spinner —
            # allowlist mantida à mão, ver _tool_exec_status acima. Agora o
            # spinner sempre abre; a tool suspende sozinha quando chama
            # input() (ver ui_frame.suspend()).
            if not _to_execute:
                _busy_label = ""
            elif len(_to_execute) == 1:
                _busy_label = f"[dim]executando {_to_execute[0].get('action', '?')}… (Ctrl+C interrompe)[/dim]"
            else:
                _names = ", ".join(a.get("action", "?") for a in _to_execute)
                _busy_label = f"[dim]executando {len(_to_execute)} tools ({_names})… (Ctrl+C interrompe)[/dim]"

            with _busy_spinner(console, _busy_label) if _busy_label else nullcontext():
                # Execução paralela quando o modelo emitiu múltiplos tool calls de uma vez
                if len(_to_execute) > 1:
                    import contextvars
                    from concurrent.futures import ThreadPoolExecutor
                    from concurrent.futures import as_completed as _as_completed

                    # ThreadPoolExecutor NÃO herda o Context da thread chamadora
                    # por conta própria — cada worker começa com um ContextVar
                    # limpo. Sem isto, a pilha de displays registrada em
                    # ui_frame (o spinner acima, aberto na thread principal)
                    # fica invisível para uma tool com input() rodando no pool:
                    # suspend() veria a pilha vazia e não pausaria nada, trazendo
                    # de volta o bug do "totodo" — só que agora no caminho
                    # paralelo.
                    #
                    # Um `Context` NÃO pode rodar em duas threads ao mesmo tempo
                    # (`.run()` levanta RuntimeError "already entered" — testado
                    # na prática, não é só doc) — por isso cada submit leva o
                    # SEU PRÓPRIO `copy_context()`, tirado na thread principal
                    # (onde o snapshot ainda enxerga o que foi registrado) antes
                    # do despacho, nunca um snapshot único reaproveitado entre
                    # as tasks.
                    ordered_results: list[tuple[str, str, str, "int | None"]] = [
                        ("", "", "", None)
                    ] * len(_to_execute)
                    with ThreadPoolExecutor(max_workers=min(len(_to_execute), 8)) as _ex:
                        _fmap = {
                            _ex.submit(contextvars.copy_context().run, _exec_action, a): i
                            for i, a in enumerate(_to_execute)
                        }
                        for _fut in _as_completed(_fmap):
                            ordered_results[_fmap[_fut]] = _fut.result()
                else:
                    ordered_results = [_exec_action(a) for a in _to_execute]

            for _exec_dict, (action_name, tool_result, _asig, _elapsed_ms) in zip(
                _to_execute, ordered_results
            ):
                if budget is not None and not budget.is_exhausted:
                    budget.consume_tool_call()
                # Guardrail post-call
                if guardrail is not None:
                    _post = guardrail.after_call(
                        action_name, _exec_dict.get("args", {}) or {}, tool_result,
                        failed=tool_result.startswith("[Erro:"),
                    )
                    if _post.action == "warn":
                        ctx.add_user(_post.message)
                    elif _post.should_halt:
                        ctx.add_user(_post.message)
                        _guard_halted = True

                # Display inteligente — filtra ruído, mostra apenas o relevante
                display_line = _format_tool_display(action_name, tool_result)
                try:
                    from .ui import tool_block as _tool_block
                    console.print(_tool_block(
                        action_name, display_line,
                        status=("fail" if tool_result.startswith("[Erro:") else "ok"),
                        elapsed_ms=_elapsed_ms,
                        result=tool_result,
                    ))
                except Exception:
                    console.print(f"  [dim]→[/dim] [cyan]{action_name}[/cyan]  {display_line}")

                # Comprime resultado para o contexto — reduz impacto de resultados grandes
                ctx_result, was_compressed = _ctx_result_for_context(action_name, tool_result)

                combined_parts.append(f"[Resultado de {action_name}]\n{ctx_result}")
                cli_tool_log.append({"tool": action_name, "args_sig": _asig, "result": tool_result[:300]})
                tool_turns += 1

            if combined_parts:
                console.print()
                ctx.add_user("\n\n".join(combined_parts))

            if _guard_halted:
                return _TurnOutcome(kind="guardrail_halt", tool_log=cli_tool_log)

            _maybe_reflect(ctx, tool_turns)
            # Detecção de loop após cada batch de tool calls (CLI path)
            loop_warn, hard_stop = _detect_loop(cli_tool_log)
            if loop_warn:
                ctx.add_user(loop_warn)
            if hard_stop:
                console.print(
                    f"[yellow]⚠ Loop detectado — '{cli_tool_log[-1]['tool']}' "
                    f"repetiu o mesmo resultado {_LOOP_REPEAT_HARD}x seguidas. "
                    "Turno interrompido.[/yellow]"
                )
                return _TurnOutcome(kind="loop_hard_stop", tool_log=cli_tool_log)
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
            _cost_before = stats.cost_usd_total
            _raw_usage = getattr(state.client, "last_usage", None) or {}
            _turn_usage = stats.record_turn_usage(_raw_usage)
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
                if budget is not None and not budget.is_exhausted:
                    # A chamada em si já foi contada em budget.consume_llm_call()
                    # (native/bridge, acima) — aqui só soma o custo real, que só
                    # fica disponível depois que record_turn_usage roda.
                    budget.consume_cost(max(0.0, stats.cost_usd_total - _cost_before))
        except Exception:
            pass  # never block the chat on a cost-display failure

        # Persiste sessao apos cada turno completo
        if session_store is not None and session_id:
            try:
                session_store.save(session_id, ctx.messages)
            except Exception:
                pass  # nao interrompe o agente por falha de persistencia

        # Sync memory: grava turno em DecisionMemory (background)
        try:
            from .memory_context import sync_memory_after_turn as _sync_mem
            _sync_mem(
                turn_input_text, display, cli_tool_log,
                workspace=active_workspace,
                session_id=session_id or "",
            )
        except Exception:
            pass

        # G10: background quality review — daemon thread, never blocks
        try:
            from .background_review import review_turn as _bg_review
            _bg_review(
                turn_input_text, display, cli_tool_log,
                workspace=active_workspace,
                session_id=session_id or "",
            )
        except Exception:
            pass

        # G4: feed recent messages to ToolRouter for LLM approval context
        try:
            router.set_context(ctx.messages)
        except Exception:
            pass

        # MemoryProvider hooks: sync_turn + nudge
        state.mem_turn_idx += 1
        if memprov is not None:
            try:
                memprov.sync_turn(state.mem_turn_idx, ctx.messages)
                if memprov.should_nudge(state.mem_turn_idx):
                    ctx.add_ephemeral_system(memprov.nudge_message())
            except Exception:
                pass

        return _TurnOutcome(kind="final", display=display, tool_log=cli_tool_log, turn_cost_line=_turn_cost_line)


# ---------------------------------------------------------------------------
# /loop — modo autônomo (turno após turno, sem confirmação humana)
# ---------------------------------------------------------------------------

_LOOP_CONFIRM_NUDGE = (
    "Você respondeu sem chamar nenhuma tool, o que normalmente indica que a "
    "tarefa terminou. Se ela está REALMENTE completa, responda apenas "
    "confirmando isso (sem chamar tools). Se ainda falta trabalho, continue "
    "chamando as tools necessárias."
)

_LOOP_STOP_REASON_LABELS = {
    "completed": "tarefa concluída (confirmado pelo modelo)",
    "budget_exhausted": "orçamento do /loop esgotado",
    "loop_hard_stop": "loop de tool calls repetidas detectado",
    "guardrail_halt": "guardrail de falhas acumuladas interrompeu",
    "provider_error": "erro de provider sem fallback disponível",
    "empty_response": "resposta vazia persistente",
    "interrupted": "interrompido pelo usuário (Ctrl+C)",
    "verification_failed": "verificação de loop-skill falhou após tentativa de correção",
}

_LOOP_SKILL_COOLDOWN_S = 60


def _parse_loop_args(rest: str) -> tuple[str, dict]:
    """Extrai flags conhecidas de `/loop <tarefa> [--max-minutes N] ...`.

    Retorna (descrição_da_tarefa, overrides) — overrides tem só as chaves
    que o usuário passou explicitamente (valores ainda como string bruta,
    convertidos/validados por `_resolve_loop_config`).

    A tarefa é texto livre e fica VERBATIM (só as flags são removidas).
    Nada de shlex aqui: em modo POSIX ele consome as barras invertidas de
    caminhos Windows — `/loop suba o docker em C:\\Users\\x` chegava ao
    modelo como "C:Usersx", um caminho inexistente (incidente real
    autonomous_loop_stopped de 2026-07-02).
    """
    import re as _re

    _FLAG_TO_KEY = {
        "max-minutes": "max_minutes",
        "max-tool-calls": "max_tool_calls",
        "max-cost": "max_cost_usd",
        "approval": "approval_mode",
    }
    overrides: dict = {}

    def _grab(m: "_re.Match") -> str:
        overrides[_FLAG_TO_KEY[m.group(1)]] = m.group(2)
        return ""  # remove a flag + valor + whitespace à direita

    task = _re.sub(
        r"(?:^|(?<=\s))--(max-minutes|max-tool-calls|max-cost|approval)\s+(\S+)\s*",
        _grab,
        rest,
    )

    def _grab_yolo(_m: "_re.Match") -> str:
        overrides["approval_mode"] = "yolo"
        return ""

    task = _re.sub(r"(?:^|(?<=\s))--yolo(?:\s+|$)", _grab_yolo, task)
    return task.strip(), overrides


def _resolve_loop_config(overrides: dict) -> "LoopSection":
    """Resolve limites do /loop: flag > config.yaml (`loop:`) > defaults.

    Nunca lança — falha ao carregar config.yaml cai silenciosamente para os
    defaults de `LoopSection`. Overrides de flag inválidos (ex.: --max-cost
    abc) levantam ValueError com mensagem amigável para o chamador tratar.
    """
    from .config_loader import LoopSection

    try:
        from .config_loader import load_config
        base = load_config().loop
    except Exception:
        base = LoopSection()

    data = base.model_dump()
    if "max_minutes" in overrides:
        try:
            data["max_minutes"] = int(overrides["max_minutes"])
        except ValueError:
            raise ValueError(f"--max-minutes inválido: {overrides['max_minutes']!r}") from None
    if "max_tool_calls" in overrides:
        try:
            data["max_tool_calls"] = int(overrides["max_tool_calls"])
        except ValueError:
            raise ValueError(f"--max-tool-calls inválido: {overrides['max_tool_calls']!r}") from None
    if "max_cost_usd" in overrides:
        try:
            data["max_cost_usd"] = float(overrides["max_cost_usd"])
        except ValueError:
            raise ValueError(f"--max-cost inválido: {overrides['max_cost_usd']!r}") from None
    if "approval_mode" in overrides:
        data["approval_mode"] = overrides["approval_mode"]
    if "approval_risk_threshold" in overrides:
        try:
            data["approval_risk_threshold"] = float(overrides["approval_risk_threshold"])
        except ValueError:
            raise ValueError(
                f"approval_risk_threshold inválido: {overrides['approval_risk_threshold']!r}"
            ) from None

    return LoopSection(**data)


def _run_loop_mode(
    *,
    task_description: str,
    overrides: dict,
    ctx,
    router: ToolRouter,
    state: _TurnState,
    console: Console,
    fallback_clients,
    stats,
    tool_timeout_s: float,
    session_store,
    session_id,
    active_workspace,
    memprov,
    loop_skill: "LoopSkill | None" = None,
    bus=None,
) -> None:
    """Roda o agente sozinho, turno após turno, sem confirmação humana a cada
    passo — até concluir a tarefa (sinal natural + nudge de confirmação),
    estourar o orçamento de segurança, um guardrail mandar parar, ou o
    usuário interromper com Ctrl+C.

    Muta `state`/`ctx`/`stats` in-place, igual ao call site do fluxo manual —
    quem chama sincroniza `client`/`active_model`/etc. de volta depois.

    `loop_skill`: quando setado, ao final de um stop_reason=="completed" roda
    um gate de verificação obrigatório (`_run_loop_skill_verification`) e
    grava o resultado em `DecisionMemory` — comportamento extra só ativo
    para loop-skills; um `/loop` manual (`loop_skill=None`) continua
    idêntico ao de sempre.
    """
    try:
        loop_cfg = _resolve_loop_config(overrides)
    except ValueError as exc:
        console.print(f"[red]{exc}[/red]")
        return

    from .autonomous_budget import AutonomousBudget
    from .headless_approval import HeadlessApprovalEngine, HeadlessApprovalConfig
    from .tool_guardrails import ToolCallGuardrailController
    from .usage_pricing import format_cost as _fmt_cost

    budget = AutonomousBudget(
        max_cost_usd=loop_cfg.max_cost_usd,
        max_wall_seconds=loop_cfg.max_minutes * 60,
        max_tool_calls=loop_cfg.max_tool_calls,
    )
    # `bus` (o do Kernel, quando o turno é governado): os limiares de estagnação
    # deixam de morrer no console e viram `run.progress.warning` auditável. Sem
    # bus, o guardrail é idêntico ao de sempre.
    guardrail = ToolCallGuardrailController(bus=bus, session_id=str(session_id or ""))
    engine = HeadlessApprovalEngine(
        HeadlessApprovalConfig(mode=loop_cfg.approval_mode, risk_threshold=loop_cfg.approval_risk_threshold)
    )

    console.print(
        f"[cyan]▶ /loop iniciado[/cyan] [dim]| aprovação: {loop_cfg.approval_mode} | "
        f"até {loop_cfg.max_minutes}m / {loop_cfg.max_tool_calls} tool calls / "
        f"{_fmt_cost(loop_cfg.max_cost_usd)}. Ctrl+C interrompe.[/dim]"
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
            outcome = _run_tool_loop_body(
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
            _mins, _secs = divmod(int(snap.elapsed_seconds), 60)
            console.print(
                f"[dim][loop] rodada {round_num} | {snap.tool_calls} tool calls "
                f"({snap.tool_calls}/{snap.max_tool_calls}) | {_mins}m{_secs:02d}s/"
                f"{loop_cfg.max_minutes}m | {_fmt_cost(snap.cost_usd)}/{_fmt_cost(snap.max_cost_usd)}[/dim]"
            )
            if not warned_80 and budget.is_warning:
                console.print("[yellow]⚠ /loop perto do limite de orçamento (≥80%).[/yellow]")
                warned_80 = True

            if outcome.kind == "final":
                _print_assistant_response(console, outcome.display, outcome.turn_cost_line)

                if not outcome.tool_log:
                    # Sinal natural genuíno: a resposta já veio em texto puro,
                    # SEM nenhuma tool call nesta rodada — candidato real a
                    # "terminei". Uma rodada que chamou tools e só por acaso
                    # terminou em texto não conta (o modelo ainda trabalhou).
                    if confirm_pending:
                        stop_reason = "completed"
                        break
                    confirm_pending = True
                    ctx.add_user(_LOOP_CONFIRM_NUDGE)
                else:
                    confirm_pending = False
                continue

            confirm_pending = False

            if outcome.kind == "tool_limit":
                # Cap por-turno (MAX_TOOL_TURNS) — não é o orçamento do /loop,
                # só reinicia a "conversa" pro próximo round continuar.
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
        verify_result = _run_loop_skill_verification(
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

    _print_loop_summary(console, stop_reason, round_num, budget, all_tool_log, task_description)

    # Conclusão normal não é incidente — gravar (e logar em INFO) um
    # "autonomous_loop_stopped: completed" fazia sucesso parecer erro no
    # terminal do usuário. Só paradas anômalas viram incidente.
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
        _ks = KanbanStore(active_workspace)
        _ks.append_event(
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
            from pathlib import Path as _Path_dm
            from .decision_memory import DecisionMemory
            snap = budget.snapshot()
            _dm = DecisionMemory(db_path=_Path_dm(active_workspace) / "decisions.db")

            if verify_result is not None:
                _outcome = "good" if verify_result.ok else "bad"
                _score = 1.0 if verify_result.ok else 0.0
                _verify_line = f" | verificação: {verify_result.summary}"
            elif stop_reason == "completed":
                _outcome, _score, _verify_line = "good", 0.5, " | sem verificação configurada"
            elif stop_reason in ("provider_error", "empty_response", "guardrail_halt", "verification_failed"):
                _outcome, _score, _verify_line = "bad", 0.0, ""
            else:  # budget_exhausted, loop_hard_stop, interrupted — inconclusivo
                _outcome, _score, _verify_line = "neutral", 0.5, ""

            _tool_names = sorted({t.get("tool", "?") for t in all_tool_log})
            _dm.record(
                context=task_description[:2000],
                decision=(
                    f"loop-skill '{loop_skill.name}': "
                    f"{_LOOP_STOP_REASON_LABELS.get(stop_reason, stop_reason)} "
                    f"em {round_num} rodada(s), {snap.tool_calls} tool calls, "
                    f"{_fmt_cost(snap.cost_usd)}{_verify_line}"
                ),
                outcome=_outcome,
                tags=[loop_skill.name, "loop-skill"] + _tool_names,
                score=_score,
            )
        except Exception:
            pass  # observabilidade nunca derruba o /loop


def _run_loop_skill_verification(
    *, loop_skill: "LoopSkill", active_workspace, ctx, router, state, console,
    fallback_clients, stats, tool_timeout_s, session_store, session_id,
    memprov, budget, guardrail,
) -> "VerifyResult | None":
    """Gate de verificação obrigatório de um loop-skill.

    Roda 1x; se falhar, injeta o erro no contexto e dá EXATAMENTE UMA rodada
    extra de correção (`_run_tool_loop_body` direto, sem loop de confirmação
    aninhado), depois reverifica UMA vez. Bounded por construção — só existem
    2 pontos de chamada de `_run_once()` neste código, nunca um loop.

    `None` = loop-skill não configurou verificação (nem verify_command nem
    verify_auto) — não é uma falha, é ausência de gate.
    """
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
        return verify_project(active_workspace)  # verify_auto

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
        _run_tool_loop_body(
            ctx=ctx, router=router, state=state, console=console,
            fallback_clients=fallback_clients, stats=stats,
            tool_timeout_s=tool_timeout_s, session_store=session_store,
            session_id=session_id, active_workspace=active_workspace,
            turn_input_text="", memprov=memprov, budget=budget, guardrail=guardrail,
        )
    except Exception:
        pass  # mesmo se a rodada de correção falhar/estourar, tenta verificar de novo

    console.print(f"[cyan]reverificando '{loop_skill.name}'...[/cyan]")
    result2 = _run_once()
    if result2.ok:
        console.print(f"[green]verificação passou após correção:[/green] {result2.summary}")
    else:
        console.print(
            f"[red]verificação falhou de novo — encerrando (sem mais tentativas):[/red] {result2.summary}"
        )
    return result2


def _print_loop_summary(console, stop_reason, round_num, budget, all_tool_log, task_description) -> None:
    """Painel resumo ao final do /loop — motivo, orçamento, tool calls por tipo."""
    try:
        from collections import Counter

        from rich.panel import Panel

        from .usage_pricing import format_cost as _fmt_cost

        snap = budget.snapshot()
        label = _LOOP_STOP_REASON_LABELS.get(stop_reason, stop_reason)
        lines = [
            f"[bold]Motivo:[/bold] {label}",
            f"[bold]Rodadas:[/bold] {round_num}",
            f"[bold]Duração:[/bold] {int(snap.elapsed_seconds)}s",
            f"[bold]Tool calls:[/bold] {snap.tool_calls}/{snap.max_tool_calls}",
            f"[bold]LLM calls:[/bold] {snap.llm_calls}/{snap.max_llm_calls}",
            f"[bold]Custo:[/bold] {_fmt_cost(snap.cost_usd)}/{_fmt_cost(snap.max_cost_usd)}",
        ]
        tool_counts = Counter(t.get("tool", "?") for t in all_tool_log)
        if tool_counts:
            lines.append(
                "[bold]Por tool:[/bold] "
                + ", ".join(f"{k}={v}" for k, v in tool_counts.most_common())
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


def _handle_loop_skill_cmd(
    user_input: str, console, *, ctx, router, state: _TurnState,
    fallback_clients, stats, tool_timeout_s, session_store, session_id,
    active_workspace, memprov,
) -> None:
    """`/loop-skill list` e `/loop-skill run <nome> [texto livre]` — uso
    manual/debug. O disparo automático (ver dispatch em run_agent_session)
    é o fluxo principal."""
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
        for s in loop_skills:
            console.print(f"[cyan]{s.name}[/cyan] — {s.description} [dim]({s.trigger_pattern})[/dim]")
        return

    if sub.lower().startswith("run "):
        name_and_rest = sub[4:].strip()
        name, _, free_text = name_and_rest.partition(" ")
        try:
            skill = registry.get(name)
        except LoopSkillNotFound as exc:
            console.print(f"[red]{exc}[/red]")
            return
        # Uso manual: sem regex real pra casar, então usa free_text como a
        # tarefa literal se fornecido, senão usa o task_template como está.
        task = free_text.strip() or skill.task_template
        console.print(f"[cyan]rodando loop-skill '{skill.name}' manualmente...[/cyan]")
        _run_loop_mode(
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


def _resolve_max_tool_turns() -> int:
    """Lê config.tools.max_tool_turns — best-effort, default 150 (mesmo
    valor default de ToolsSection) se a config não carregar, mesma
    filosofia de _minimal_code_mode_enabled/_resolve_loop_config."""
    try:
        from .config_loader import load_config
        return load_config().tools.max_tool_turns
    except Exception:
        return 150


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
    tool_timeout_s: float = 30.0,
    memory_provider: "Any | None" = None,
    learning_hints: "str | None" = None,
    route_profiles: "dict | None" = None,
    route_client_fn: "Any | None" = None,
    route_profiles_by_provider: "dict[str, dict] | None" = None,
    kernel: "Any | None" = None,
    render_header: "Any | None" = None,
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
        route_profiles: Profiles do roteamento heurístico (Fase 12) — quando
            presentes, cada turno escolhe o modelo do tier via classify_task,
            igual ao serve. Tem precedência sobre o ModelRouter legado (LLM).
        route_client_fn: Callable(provider) -> client|None p/ providers ≠ do
            principal (com cache). None/falha → o turno usa o modelo padrão.
        route_profiles_by_provider: dict[provider] -> profiles — conjunto de
            tiers dedicado por provider ATIVO (`model.profiles_by_provider`).
            Quando o provider corrente (que `/model` troca ao vivo) tem uma
            entrada aqui, ela substitui `route_profiles` só naquele turno; sem
            entrada, cai em `route_profiles` como sempre. Sem isto, trocar de
            provider via `/model` não mudava os tiers — a sessão continuava
            roteando turnos para o provider antigo, mesmo sem credencial nele.
        kernel: BauerKernel (Sprint 6c) — quando presente, cada turno roda
            via kernel.execute() com o corpo do turno como executor: run
            auditável + kill-switch/policy/budget antes da chamada LLM.
            None (default) = comportamento de sempre, sem kernel.
    """
    # MAX_TOOL_TURNS é lido por dezenas de call sites como global do módulo
    # (inclusive dentro de funções aninhadas em _run_tool_loop_body) — em vez
    # de threadar um parâmetro por toda essa cadeia, resolve uma vez aqui e
    # muta o global; toda leitura subsequente nesta sessão já pega o valor
    # configurado (Python resolve nomes de módulo em tempo de chamada, não
    # de definição).
    global MAX_TOOL_TURNS
    MAX_TOOL_TURNS = _resolve_max_tool_turns()

    system_prompt = _build_system_prompt(router, client=client)
    if learning_hints:
        system_prompt += f"\n\n# Aprendizados desta sessão\n{learning_hints}"
    # Determina provider a partir do client — usado no painel E no context budget.
    #
    # A heurística anterior era `"ollama" in host`, que falha em TODA URL real
    # do Ollama: `http://localhost:11434` e `http://127.0.0.1:11434` não contêm
    # a substring "ollama" (só acerta se o host for literalmente `ollama`, nome
    # de serviço em Docker Compose). Consequências, ambas vistas em uso real:
    #   1. o painel exibia "openai" numa sessão local — contradizendo a própria
    #      mensagem "Modelo local + contexto 8192" impressa logo acima;
    #   2. este `_provider` vai para o ContextManager, que o usa para escolher a
    #      janela de fallback — um Ollama seria orçado com a janela da OpenAI.
    #
    # A porta 11434 é o sinal confiável (é a do Ollama, fixa por convenção);
    # o nome só como reforço.
    # Ordem: o que o client afirma > o que o config DECLARA > inferência pelo
    # host. O config entrou no meio porque o painel dizia "openai" para uma
    # sessão OpenRouter enquanto o boot, lendo o config, dizia "openrouter" —
    # duas afirmações contraditórias sobre a mesma sessão, na mesma tela.
    _provider = (
        getattr(client, "_provider", None)
        or _provider_declarado()
        or _detectar_provider_por_host(getattr(client, "host", ""))
    )
    # Uma porta só para o que entra na janela (S9). O prompt do sistema é
    # INSTRUÇÃO — e é a única coisa aqui que é; tudo que vier de arquivo, web ou
    # tool entra por `conteudo()` e nunca vira ordem.
    from .core.context import ContextBuilder

    # `montar` já faz o set_llm — a compressão semântica via LLM continua ligada.
    ctx, _ctx_pacote = (
        ContextBuilder(applied_context=applied_context, provider=_provider,
                       bus=getattr(kernel, "bus", None))
        .instrucao("seguranca", system_prompt)
        .montar(llm_client=client, llm_model=model_name))

    # MemoryProvider — inicializa e injeta bloco de contexto no system prompt
    _mem_workspace = getattr(router, "workspace", "workspace")
    _memprov = memory_provider
    if _memprov is None:
        try:
            from .memory_provider import get_memory_provider as _gmp
            _memprov = _gmp()
        except Exception:
            _memprov = None
    if _memprov is not None:
        try:
            _memprov.initialize(_mem_workspace)
            _memprov.prefetch()
            _memprov.queue_prefetch()  # dispara background refresh logo após o sync inicial
            _mem_block = _memprov.system_prompt_block()
            if _mem_block:
                ctx.add_ephemeral_system(_mem_block)
        except Exception:
            pass
    _mem_turn_idx = 0

    # P2.2: Task Ledger — injeta tarefas pendentes do TASKS.md no contexto de início de sessão
    _ledger = _ledger_block(getattr(router, "workspace", None))
    if _ledger:
        ctx.add_ephemeral_system(_ledger)

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
    from .loop_skills import LoopSkillRegistry
    _loop_skill_registry = LoopSkillRegistry()
    _loop_skill_last_run: dict[str, float] = {}
    routing = model_router is not None and model_router.config.enabled
    orch_enabled = orchestrator is not None and routing

    #: modelo que serviu o ÚLTIMO turno — lido pela barra de status. Começa como
    #: o configurado; passa a divergir assim que o roteamento manda um turno
    #: para outro tier.
    _modelo_do_turno = model_name

    # Com roteamento ligado, `model_name` NÃO é o modelo que vai responder — ele
    # só entra se o router cair nele. Anunciá-lo sem ressalva foi o que fez um
    # `hf.co/...:Q5_K_M` local aparecer como "Modelo" enquanto todo turno ia
    # para o `minimax` no OpenRouter, acumulando custo.
    # Gate em `route_profiles` SOZINHO — é ele que liga o router heurístico no
    # laço do turno. `routing` é o ModelRouter legado, outro caminho: usá-lo
    # aqui deixaria o banner mudo justamente na configuração do usuário.
    # E os perfis são `ModelProfile` (objetos), não dicts — `.get()` quebraria.
    def _resolve_tier_profiles(provider: str) -> "dict | None":
        """`route_profiles_by_provider[provider]` se DECLARADO (mesmo vazio —
        vazio é a forma explícita de dizer "sem tiers dedicados para este
        provider, nunca desviar dele"), senão o `route_profiles` fixo do
        boot. `in` em vez de `.get(...) or fallback`: um provider com `{}`
        declarado não pode cair no fallback silenciosamente, ou declarar
        "nenhum tier para openai" teria o mesmo efeito que não declarar nada
        — e o turno voltaria a arriscar o provider errado."""
        if route_profiles_by_provider is not None and provider in route_profiles_by_provider:
            return route_profiles_by_provider[provider]
        return route_profiles

    def _fmt_tiers(profiles: "dict | None") -> str:
        return ", ".join(f"{k}: {getattr(v, 'model', '?')}" for k, v in sorted((profiles or {}).items()))

    def _atualizar_linha_de_roteamento() -> "dict | None":
        """Recalcula `_boot_tier_profiles`/`_linhas_extra` para o provider AO
        VIVO. Chamada no boot e de novo a cada troca de `/model` — sem isto o
        banner "Roteando" e o "(padrão — o turno pode ir p/ outro tier)"
        ficavam presos ao provider do boot até reiniciar a sessão, mesmo o
        roteamento em si (laço do turno) já seguindo o provider certo."""
        nonlocal _boot_tier_profiles
        _boot_tier_profiles = _resolve_tier_profiles(_provider)
        _linhas_extra[:] = [("Roteando", _fmt_tiers(_boot_tier_profiles))] if _boot_tier_profiles else []
        return _boot_tier_profiles

    _linhas_extra: list[tuple[str, str]] = []
    _boot_tier_profiles: "dict | None" = None
    _atualizar_linha_de_roteamento()

    # Acento salvo pelo usuário (Ctrl+T / `/theme`) — aplicado ANTES do
    # primeiro render, senão o logo e o painel de sessão sairiam no padrão e
    # só o resto da tela obedeceria a escolha.
    _aplicar_acento_do_config()

    # Resolve o conjunto de glifos UMA vez, olhando o console real desta
    # sessão. Sem isto, um terminal cp1252 (cmd legado) estoura com
    # UnicodeEncodeError no primeiro ❯/▰ — o mecanismo de queda para ASCII
    # existe em theme.py mas só vale se alguém o acionar (plano 028 F0).
    try:
        from . import ui as _ui_kit
        _ui_kit.use_glyphs(stream=getattr(console, "file", None) or sys.stdout)
    except Exception as _exc:  # noqa: BLE001 — na dúvida segue com o padrão Unicode
        from .logging_config import log_suppressed
        log_suppressed("ui.deteccao_glifos", _exc)

    from .ascii_intro import session_panel
    def _montar_cabecalho():
        """O painel de sessão. Função e não bloco solto porque a troca de tema
        precisa REDESENHÁ-LO — e redesenhar por um caminho diferente do boot
        acabaria divergindo dele (foi assim que as três paletas nasceram)."""
        return session_panel(
            "Bauer Agent",
            f"{model_name}  (padrão — o turno pode ir p/ outro tier)"
            if _boot_tier_profiles else model_name,
            applied_context,
            provider=_provider or None,
            extra_rows=_linhas_extra,
            commands=[
                ("/model", "trocar"),
                ("/listen", "falar"),
                ("/status", "stats"),
                ("/clear", "limpar"),
                ("/memory", "memoria"),
                ("/loop", "autonomo"),
                ("/exit", "sair"),
            ],
        )

    def _redesenhar_cabecalho() -> None:
        """Limpa a tela e reimprime logo + painel no acento atual.

        Texto já impresso não pode ser recolorido — é scrollback, bytes que já
        saíram. A única forma de o cabeçalho acompanhar a troca de tema é
        reimprimi-lo. `clear()` usa ESC[2J, que empurra o conteúdo anterior
        para o histórico de rolagem em vez de destruí-lo: a conversa continua
        acessível rolando para cima.
        """
        try:
            console.clear()
            if render_header is not None:
                # O MESMO renderizador do boot (logo + checagens), passado pelo
                # agent_cmd. Reconstruir aqui daria um segundo caminho que
                # divergiria do primeiro com o tempo.
                render_header()
            else:
                console.print(_montar_cabecalho())
            console.print()
        except Exception as _exc:  # noqa: BLE001 — redesenho nunca derruba a sessão
            from .logging_config import log_suppressed
            log_suppressed("theme.redesenhar_cabecalho", _exc)

    if render_header is None:
        console.print(_montar_cabecalho())
        console.print()

    # Plugin hooks — session_start
    try:
        from .plugin_hooks import hooks as _phooks
        _phooks.ensure_plugins_loaded()
        _phooks.emit("session_start", session_id=session_id or "local", model=model_name)
    except Exception as _exc:
        from .logging_config import log_suppressed
        log_suppressed("plugin_hooks.session_start", _exc)

    # Estado do HUD (plano 028 F2) — UMA fonte para as duas superfícies que
    # desenham o rodapé: a `bottom_toolbar` do prompt_toolkit (enquanto o
    # prompt espera) e o `Live` do Rich (durante o turno). Antes do F2 a
    # toolbar montava o HTML na mão, com a paleta antiga hardcoded — e por isso
    # escapou inteira do F0.
    _hud_tokens_por_segundo = 0.0
    _hud_kernel_estado: "str | None" = None

    # Esteira do Kernel: assina o bus e acende os estados conforme o turno
    # governado avança. Só existe quando o Kernel está ligado — sem ele
    # `_hud_kernel_estado` fica None e a esteira nem aparece (regra do plano
    # 028: não inventa estado).
    #
    # Assina o WILDCARD "*", não `run.state.changed`: o Kernel publica estado
    # por cinco tópicos diferentes (planning.started, state.changed,
    # replanning, validation.started, progress.warning). Assinar só um — como
    # o plano dizia — perderia planning e evaluating.
    if kernel is not None:
        from . import ui_frame as _ui_frame_mod
        from .ui_hud import estado_do_evento as _estado_do_evento

        def _on_kernel_event(evt) -> None:
            nonlocal _hud_kernel_estado
            try:
                novo = _estado_do_evento(
                    getattr(evt, "event_type", ""), getattr(evt, "status", None)
                )
                if novo is None:
                    return  # evento sem estado de run: mantém o que já havia
                _hud_kernel_estado = novo
                _frame_atual = _ui_frame_mod.current_frame()
                if _frame_atual is not None:
                    _frame_atual.set_hud(_hud_state_atual())
            except Exception as _exc:  # noqa: BLE001 — HUD nunca derruba o run
                from .logging_config import log_suppressed
                log_suppressed("hud.kernel_event", _exc)

        try:
            _kernel_bus = getattr(kernel, "bus", None)
            if _kernel_bus is not None:
                _kernel_bus.subscribe("*", _on_kernel_event)
        except Exception as _exc:  # noqa: BLE001
            from .logging_config import log_suppressed
            log_suppressed("hud.kernel_subscribe", _exc)

    def _hud_state_atual():
        from .ui_hud import HudState
        from .usage_pricing import provider_e_local as _e_local

        # O modelo do ÚLTIMO TURNO, não o configurado. Com roteamento ligado os
        # dois divergem, e a barra afirmava o configurado enquanto o custo subia
        # no outro. Marca com `→` quando o turno não rodou no modelo escolhido.
        _m = _modelo_do_turno or model_name
        _rotulo = str(_m) if _m == model_name else f"→ {_m}"

        # O selo segue o provider do TURNO (mesma razão do modelo acima): o
        # roteamento pode mandar um turno para a nuvem numa sessão que começou
        # local, e o selo tem que contar isso — é o dado que ele existe para dar.
        try:
            from .cost_meter import provider_from_client as _pfc
            _local = _e_local(_pfc(client))
        except Exception:  # noqa: BLE001
            _local = False

        return HudState(
            local=_local,
            modelo=_rotulo,
            ctx_pct=max(0.0, min(1.0, ctx.usage_pct)),
            custo_usd=float(getattr(stats, "cost_usd_total", 0.0) or 0.0),
            tokens_por_segundo=_hud_tokens_por_segundo,
            kernel_estado=_hud_kernel_estado,
            comandos=(("/loop", "autonomo"), ("/model", "modelo"), ("/exit", "sair")),
        )

    # Cria sessão prompt_toolkit (autocomplete de /) apenas em terminal interativo real.
    # Só exige stdin como tty — stdout pode estar capturado pelo Rich em alguns terminais.
    # Tenta criar; se o terminal não suportar (ex: pipe, CI), cai para console.input().
    _is_interactive = sys.stdin.isatty()
    _pt_session = None
    if _PT_AVAILABLE and _is_interactive:
        # Barra de status fixa no rodapé — o logo/painel do topo rola junto
        # com o histórico, então a identidade + estado da sessão vivem aqui,
        # sempre visíveis. Closure lê as variáveis locais atuais: /model e
        # consumo de tokens/custo refletem na barra a cada prompt.
        def _bottom_toolbar():
            try:
                from .ui_hud import render_hud_html as _render_html
                return HTML(_render_html(_hud_state_atual()))
            except Exception:
                return " BAUER "
        try:
            _pt_session = _make_prompt_session(
                bottom_toolbar=_bottom_toolbar,
                ao_trocar_tema=_redesenhar_cabecalho,
            )
        except Exception as _pt_exc:
            console.print(f"[dim](autocomplete indisponível: {_pt_exc})[/dim]")
            _pt_session = None  # terminal incompatível — usa fallback

    # Native tool calling no chat interativo (antes só o run_one_turn usava).
    # Flag de sessão: 1º HTTP 400/404/405/422/501 em tools= → downgrade
    # definitivo para o bridge JSON pelo resto da sessão.
    _native_session_ok = _client_supports_native_tools(client)
    # Índice do próximo fallback a tentar — avança a cada falha, não reinicia
    # no primeiro item (senão ficaria preso no mesmo provider quebrado).
    _fb_idx = 0

    # Checkpoint de planejamento (App Factory → /loop): resolvido uma vez.
    _planning_checkpoint_enabled = _resolve_planning_checkpoint()

    # Kanban = fonte única (task_backend=sqlite): semeia AQUI, no boot, não só
    # no instante em que o gate é cruzado. Cobre o caso real que motivou isto —
    # projeto que cruzou IMPLEMENTATION numa sessão anterior (ou via /loop, sem
    # TTY) e nunca foi ofertado de novo, ficando com o board vazio para sempre.
    if _planning_checkpoint_enabled:
        try:
            # active_workspace só é atribuída dentro do loop de turnos (mais
            # abaixo) — aqui, no boot, antes do loop começar, deriva do
            # router com o mesmo fallback que o loop usa.
            _af_proj_boot, _ = _af_active_gate(getattr(router, "workspace", "workspace"))
            if _af_proj_boot is not None:
                _ensure_kanban_seeded(router, _af_proj_boot, console)
        except Exception as _exc:
            from .logging_config import log_suppressed
            log_suppressed("kanban.ensure_seeded_boot", _exc)
    # Auto-injeção de skill relevante por turno: resolvido uma vez.
    try:
        from .config_loader import load_config as _lc_sk
        _skill_auto_inject_enabled = bool(_lc_sk().agent.skill_auto_inject)
    except Exception:
        _skill_auto_inject_enabled = True
    # Skill injetada no último turno — p/ atribuir o feedback 👍/👎 a ela.
    _last_injected_skill = None

    # Confirmação interativa de comando perigoso (só em TTY real). Em vez de
    # bloquear em silêncio, pergunta e o "always" ENSINA o allowlist. Resolvido
    # uma vez; instala o callback por turno (o /loop usa motor próprio).
    #
    # Até o F2 (plano 028) havia aqui uma segunda variável global,
    # `_CONFIRM_EXEC_ACTIVE`, só para avisar `_tool_exec_status` que não podia
    # abrir spinner em volta de tools de execução. Não é mais necessária: o
    # spinner sempre abre e `_prompt_cmd_decision` (chamado pelos callbacks
    # abaixo) suspende sozinho via `ui_frame.suspend()` quando pede input().
    _confirm_cb = None
    try:
        from .config_loader import load_config as _lc_cc
        _confirm_on = bool(_lc_cc().tools.confirm_commands)
    except Exception:
        _confirm_on = True
    if _confirm_on and sys.stdin.isatty():
        _confirm_cb = _make_cli_approval_callback(console)
        # Gate 2 — allowlist do shell (docker/pip/…): instala no ShellRunner do
        # router, se houver. É o gate que mais engessa; "always" aprende.
        _sr = getattr(router, "_shell_runner", None)
        if _sr is not None:
            try:
                _sr.allowlist_callback = _make_cli_allowlist_callback(console)
            except Exception:
                pass
    _listen_loop_active = False

    while True:
        # --- entrada do usuário ---
        if _listen_loop_active:
            listened = _capture_listen_input(console)
            if not listened:
                _listen_loop_active = False
                console.print("[dim]Modo /listen loop pausado. Digite /listen loop para retomar.[/dim]")
                continue
            if _is_listen_loop_stop(listened):
                _listen_loop_active = False
                console.print("[dim]Modo /listen loop encerrado.[/dim]")
                continue
            console.print(f"[dim]Voce disse:[/dim] {listened}")
            user_input = listened
        else:
            try:
                if _pt_session is not None:
                    try:
                        user_input = _pt_session.prompt(_PROMPT_FRAGMENTS).strip()
                    except (KeyboardInterrupt, EOFError):
                        raise
                    except Exception:
                        _pt_session = None
                        _set_blink_underline()
                        _prompt = _ui_kit.active_glyphs().prompt
                        user_input = console.input(
                            f"[bold {_theme_mod.ACCENT_TEXT}]bauer {_prompt}[/] "
                        ).strip()
                else:
                    _set_blink_underline()
                    _prompt = _ui_kit.active_glyphs().prompt
                    user_input = console.input(
                        f"[bold {_theme_mod.ACCENT_TEXT}]bauer {_prompt}[/] "
                    ).strip()
            except (KeyboardInterrupt, EOFError):
                console.print("\n[dim]Sessao encerrada.[/dim]")
                try:
                    from .plugin_hooks import hooks as _phooks
                    _phooks.emit("session_end", session_id=session_id or "local", model=model_name)
                except Exception:
                    pass
                if _memprov is not None:
                    try:
                        _memprov.on_session_end(ctx.messages)
                    except Exception:
                        pass
                return

        if not user_input:
            continue
        if user_input.lower() in _LISTEN_LOOP_CMDS:
            _listen_loop_active = True
            console.print("[dim]Modo /listen loop iniciado. Fale 'parar' ou pressione Ctrl+C na escuta para pausar.[/dim]")
            continue
        if user_input.lower() in _LISTEN_CMDS:
            listened = _capture_listen_input(console)
            if not listened:
                continue
            console.print(f"[dim]Voce disse:[/dim] {listened}")
            user_input = listened
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
            if _memprov is not None:
                try:
                    _memprov.on_session_end(ctx.messages)
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
        if user_input.lower().split()[0] in _THEME_CMDS:
            _partes = user_input.split(maxsplit=1)
            _pedido = _partes[1].strip().lower() if len(_partes) > 1 else ""
            if _pedido:
                try:
                    _theme_mod.set_accent(_pedido)
                    _persistir_acento(_pedido)
                    # Mesmo redesenho do Ctrl+T: o cabeçalho já impresso não
                    # muda de cor sozinho, e deixar metade da tela no acento
                    # antigo parece defeito.
                    _redesenhar_cabecalho()
                    console.print(_amostra_acentos(destaque=_pedido))
                except KeyError as _exc:
                    console.print(f"[{_warn()}]{_exc.args[0]}[/]")
            else:
                # Sem argumento: mostra o catálogo com o atual em destaque. É
                # também a resposta para "quais cores existem?" — perguntar e
                # ver é melhor que decorar nome de paleta.
                console.print(_amostra_acentos(destaque=_theme_mod.ACCENT_NAME))
                console.print(
                    "[dim]  /theme <nome> para trocar · [bold]Ctrl+T[/bold] cicla[/dim]"
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
                from .paths import config_path as _cfg_path
                _cfg = _Path("config.yaml")
                if not _cfg.exists():
                    _cfg = _cfg_path()
                _rms(_cfg)

                if rebuild_client_fn is not None:
                    _rebuilt = rebuild_client_fn()
                    # Aceita 2-tupla (legado) ou 3-tupla (com fallbacks renovados).
                    if isinstance(_rebuilt, tuple) and len(_rebuilt) == 3:
                        _new_client, _new_model, _new_fallbacks = _rebuilt
                        fallback_clients = _new_fallbacks  # renova cadeia do novo modelo
                    else:
                        _new_client, _new_model = _rebuilt
                    # Novo modelo escolhido → recomeça a cadeia de fallback do topo
                    _fb_idx = 0
                    # Rebind ao vivo — próximo turno já usa o novo modelo
                    client = _new_client
                    model_name = _new_model
                    ctx.set_llm(client, model_name)
                    # Fix 2 & 3: atualiza stats e provider após switch
                    stats.model = model_name
                    _provider = getattr(_new_client, "_provider", None) or "openai"
                    stats.provider = _provider
                    ctx._provider = _provider
                    # Recalcula o banner/tiers para o provider NOVO — sem isto
                    # "Roteando" ficava mostrando o conjunto do provider antigo
                    # até reiniciar a sessão, mesmo o roteamento em si (laço do
                    # turno) já respeitando a troca a partir da mensagem seguinte.
                    _novos_tiers = _atualizar_linha_de_roteamento()
                    if route_profiles_by_provider is not None or route_profiles:
                        console.print(
                            f"[dim]  Roteando ({_provider}): "
                            + (_fmt_tiers(_novos_tiers) if _novos_tiers
                               else "sem tiers dedicados — turno sempre usa o modelo ativo")
                            + "[/dim]"
                        )
                    # L8: persiste preferência explícita do usuário (sobrepõe SelfTuner)
                    try:
                        import json as _json_l8
                        from datetime import datetime as _dt_l8
                        from pathlib import Path as _Path_l8
                        _pref_dir = _Path_l8("memory")
                        _pref_dir.mkdir(parents=True, exist_ok=True)
                        (_pref_dir / "model_preference.json").write_text(
                            _json_l8.dumps({
                                "model": model_name,
                                "provider": _provider,
                                "set_by": "user",
                                "set_at": _dt_l8.utcnow().isoformat(),
                            }, indent=2),
                            encoding="utf-8",
                        )
                    except Exception:
                        pass
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
                    console.print("[dim]Para retomar: bauer agent --resume --session-id ID[/dim]")
                else:
                    console.print("[dim]Nenhuma sessao salva.[/dim]")
            else:
                console.print("[dim]Persistencia de sessao nao configurada.[/dim]")
            continue
        active_workspace = getattr(router, "workspace", "workspace")
        if user_input.lower().startswith(tuple(_SPEC_CMDS)):
            _handle_spec_cmd(user_input, console, active_workspace)
            continue

        if user_input.lower() == "/loop" or user_input.lower().startswith("/loop "):
            _rest = user_input[len("/loop"):].strip()
            if _rest.lower() == "status":
                console.print(
                    "[dim]Nenhum /loop em execução — o modo /loop roda de forma síncrona "
                    "(ocupa o prompt enquanto ativo). Use Ctrl+C para interromper um /loop "
                    "em andamento.[/dim]"
                )
                continue
            if _rest.lower() == "stop":
                console.print(
                    "[dim]Nenhum /loop em execução para interromper. Durante um /loop "
                    "ativo, use Ctrl+C.[/dim]"
                )
                continue
            if not _rest:
                console.print(
                    "[yellow]Uso:[/yellow] /loop <tarefa> [--max-minutes N] [--max-tool-calls N] "
                    "[--max-cost N] [--approval threshold|deny_all|yolo] [--yolo]\n"
                    "[dim]Ctrl+C interrompe o /loop em andamento (não a sessão).[/dim]"
                )
                continue
            _loop_task, _loop_overrides = _parse_loop_args(_rest)
            if not _loop_task.strip():
                console.print("[yellow]Descreva a tarefa: /loop <tarefa> ...[/yellow]")
                continue
            _loop_state = _TurnState(
                client=client,
                active_model=model_name,
                native_session_ok=_native_session_ok,
                fb_idx=_fb_idx,
                mem_turn_idx=_mem_turn_idx,
            )
            _run_loop_mode(
                task_description=_loop_task,
                overrides=_loop_overrides,
                ctx=ctx,
                router=router,
                state=_loop_state,
                console=console,
                fallback_clients=fallback_clients,
                stats=stats,
                tool_timeout_s=tool_timeout_s,
                session_store=session_store,
                session_id=session_id,
                active_workspace=active_workspace,
                memprov=_memprov,
                bus=getattr(kernel, "bus", None),
            )
            client = _loop_state.client
            _native_session_ok = _loop_state.native_session_ok
            _fb_idx = _loop_state.fb_idx
            _mem_turn_idx = _loop_state.mem_turn_idx
            continue

        # Auto-gatilho de loop-skills — nunca antes dos comandos explícitos
        # acima (built-ins sempre vencem), nunca visto por texto gerado
        # DENTRO de um /loop (user_input só é lido aqui, no topo do
        # while True — nunca re-entrado durante _run_loop_mode).
        if user_input.lower() != "/loop" and not user_input.lower().startswith("/loop "):
            _ls_match = _loop_skill_registry.match(user_input)
            if _ls_match is not None:
                _ls_skill, _ls_re = _ls_match
                _ls_now = time.monotonic()
                # Sentinela None (não 0.0): time.monotonic() tem época
                # arbitrária — no Linux é uptime, e em VM recém-bootada
                # (CI!) monotonic() < cooldown fazia o default 0.0 parecer
                # "rodou agora há pouco", bloqueando o PRIMEIRO disparo.
                _ls_last = _loop_skill_last_run.get(_ls_skill.name)
                if _ls_last is None or _ls_now - _ls_last >= _LOOP_SKILL_COOLDOWN_S:
                    _loop_skill_last_run[_ls_skill.name] = _ls_now
                    console.print(
                        f"[cyan]padrão '{_ls_skill.name}' reconhecido[/cyan] "
                        f"[dim]— disparando /loop autônomo automaticamente "
                        f"(sem confirmação; instalada localmente por você).[/dim]"
                    )
                    _ls_task = _ls_skill.render_task(_ls_re)
                    _ls_state = _TurnState(
                        client=client, active_model=model_name,
                        native_session_ok=_native_session_ok,
                        fb_idx=_fb_idx, mem_turn_idx=_mem_turn_idx,
                    )
                    _run_loop_mode(
                        task_description=_ls_task,
                        overrides={
                            "max_minutes": str(_ls_skill.max_minutes),
                            "max_tool_calls": str(_ls_skill.max_tool_calls),
                            "max_cost_usd": str(_ls_skill.max_cost_usd),
                            "approval_mode": _ls_skill.approval_mode,
                            "approval_risk_threshold": str(_ls_skill.approval_risk_threshold),
                        },
                        ctx=ctx, router=router, state=_ls_state, console=console,
                        fallback_clients=fallback_clients, stats=stats,
                        tool_timeout_s=tool_timeout_s, session_store=session_store,
                        session_id=session_id, active_workspace=active_workspace,
                        memprov=_memprov, loop_skill=_ls_skill,
                        bus=getattr(kernel, "bus", None),
                    )
                    client = _ls_state.client
                    _native_session_ok = _ls_state.native_session_ok
                    _fb_idx = _ls_state.fb_idx
                    _mem_turn_idx = _ls_state.mem_turn_idx
                    continue
                # em cooldown — trata como input normal, não dispara de novo

        if user_input.lower() in _LOOP_SKILL_CMDS or user_input.lower().startswith(
            ("/loop-skill ", "/loop-skills ")
        ):
            _ls_state2 = _TurnState(
                client=client, active_model=model_name, native_session_ok=_native_session_ok,
                fb_idx=_fb_idx, mem_turn_idx=_mem_turn_idx,
            )
            _handle_loop_skill_cmd(
                user_input, console, ctx=ctx, router=router, state=_ls_state2,
                fallback_clients=fallback_clients, stats=stats, tool_timeout_s=tool_timeout_s,
                session_store=session_store, session_id=session_id,
                active_workspace=active_workspace, memprov=_memprov,
            )
            client = _ls_state2.client
            _native_session_ok = _ls_state2.native_session_ok
            _fb_idx = _ls_state2.fb_idx
            _mem_turn_idx = _ls_state2.mem_turn_idx
            continue

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

        # L7: feedback de usuário — /thumbsup / /thumbsdown
        if user_input.lower() in _THUMBSUP_CMDS or user_input.lower() in _THUMBSDOWN_CMDS:
            _rating = "positivo" if user_input.lower() in _THUMBSUP_CMDS else "negativo"
            _last_msgs = ctx.messages
            _last_user = next(
                (m["content"] for m in reversed(_last_msgs) if m.get("role") == "user"),
                "",
            )
            _last_asst = next(
                (m["content"] for m in reversed(_last_msgs) if m.get("role") == "assistant"),
                "",
            )
            try:
                from .learning_engine import LearningEngine as _LE7
                _le7 = _LE7()
                _le7.mm.append_entry(
                    "FEEDBACK.md",
                    f"Feedback {_rating} — {model_name}",
                    fields={
                        "rating": _rating,
                        "model": model_name,
                        "machine_id": stats.machine_id,
                    },
                    body=(
                        f"**Pergunta:**\n{_last_user[:300]}\n\n"
                        f"**Resposta:**\n{_last_asst[:500]}"
                    ),
                )
                _icon = "👍" if _rating == "positivo" else "👎"
                console.print(f"[dim]{_icon} Feedback registrado.[/dim]")
            except Exception:
                console.print("[dim]Feedback não pôde ser salvo.[/dim]")

            # Fecha o loop de qualidade: o 👍/👎 vira o veredito humano da última
            # decisão da sessão (sobrescreve o score heurístico do gravador
            # por-turno). É o `update_outcome` que antes nunca era chamado.
            try:
                from pathlib import Path as _P_fb
                from .decision_memory import DecisionMemory as _DM_fb
                _ws_fb = getattr(router, "workspace", None)
                if _ws_fb and session_id:
                    _good = _rating == "positivo"
                    _DM_fb(db_path=_P_fb(_ws_fb) / "decisions.db").update_latest_outcome(
                        session_id, "good" if _good else "bad",
                        score=0.9 if _good else 0.1,
                    )
            except Exception:
                pass  # feedback de qualidade é best-effort

            # Telemetria: atribui o 👍/👎 à skill que estava ativa no último turno.
            if _last_injected_skill:
                try:
                    from .skill_stats import record_feedback as _rec_fb
                    _rec_fb(_last_injected_skill, _rating == "positivo")
                except Exception:
                    pass
            continue

        suggested = skills.observe(user_input)
        if suggested:
            console.print(f"[dim]Skill sugerida: '{suggested}' — veja 'bauer memory show skills'[/dim]")

        stats.start_turn()

        # --- roteamento (se ativo) ---
        active_model = model_name
        _turn_client = client
        _hr_routed = False  # turno roteado pelo router heurístico (Fase 12)
        route_kind = "direct"
        # `_provider` segue /model ao vivo — se o provider ativo tem um
        # conjunto de tiers dedicado, ele vale neste turno; senão cai no
        # `route_profiles` fixo do boot (comportamento de sempre).
        _tier_profiles = _resolve_tier_profiles(_provider)
        if _tier_profiles:
            # Router heurístico por tier (mesma decisão do serve) — precedência
            # sobre o ModelRouter legado. CONSERVADOR: tier sem profile, provider
            # sem client, ou falha → o turno segue no modelo padrão da sessão.
            try:
                from .model_router import decide as _hr_decide
                _d = _hr_decide(user_input, _tier_profiles)
                if _d.model:
                    if not _d.provider or _d.provider == _provider:
                        _c = client  # mesmo provider → reusa o client vivo da sessão
                    elif route_client_fn is not None:
                        _c = route_client_fn(_d.provider)
                    else:
                        _c = None
                    if _c is not None:
                        _turn_client, active_model, _hr_routed = _c, _d.model, True
                        # sem colchetes no chip — Rich interpretaria [fast]
                        # como tag de markup e o engoliria do output
                        console.print(
                            f"[dim]  → tier {_d.profile}: {_d.model} "
                            f"({_d.task_type}/{_d.complexity})[/dim]"
                        )
            except Exception as _hr_exc:  # noqa: BLE001 — routing é otimização; nunca bloqueia o turno
                from .logging_config import log_suppressed as _hr_log
                _hr_log("agent.heuristic_route", _hr_exc)
        elif routing and model_router is not None:
            try:
                selected_model, route = model_router.select_model(user_input)
                route_kind = route.kind
                if route_kind == "orchestrate" and orch_enabled:
                    console.print("[dim]  -> [orquestrar] tarefa complexa detectada[/dim]")
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
                _print_assistant_response(console, final)
            continue

        # Prefetch memory context (decisões passadas + sessões similares)
        try:
            from .memory_context import prefetch_memory_context as _prefetch
            _mem_ctx = _prefetch(user_input, workspace=active_workspace)
            if _mem_ctx:
                ctx.add_ephemeral_system(_mem_ctx)
        except Exception:
            pass  # nunca bloquear o chat por falha de memória

        # Auto-injeção de skill: casa o turno com as skills e injeta o conteúdo
        # da mais relevante SE o match for confiante (threshold medido). Faz as
        # skills dispararem sozinhas; na dúvida injeta nada (falha seguro).
        _injected_skill = None  # nome da skill injetada neste turno (p/ telemetria)
        if _skill_auto_inject_enabled:
            try:
                from .skill_match import match_skill, skill_injection_block
                _sk = match_skill(user_input)
                if _sk is not None:
                    ctx.add_ephemeral_system(skill_injection_block(_sk))
                    _injected_skill = _sk.name
                    try:
                        from .ui import skill_line as _skl
                        console.print(_skl(_sk.name, int(_sk.score * 100)))
                    except Exception:
                        console.print(
                            f"[dim]↳ skill '{_sk.name}' ({_sk.score:.0%})[/dim]"
                        )
            except Exception:
                pass  # skill é auxílio; nunca bloquear o turno

        ctx.add_user(user_input)

        # Confirmação interativa de comando: instala o callback por turno (o
        # /loop, quando roda, o sobrescreve com seu motor headless e depois
        # reseta p/ None — por isso re-aplicamos aqui a cada turno de chat).
        router._approval_callback = _confirm_cb

        # Gate do projeto App Factory ativo ANTES do turno — p/ detectar o
        # cruzamento p/ IMPLEMENTATION ao final (checkpoint de planejamento).
        _gate_before = None
        if _planning_checkpoint_enabled:
            _gate_before = _af_active_gate(active_workspace)[1]

        # --- loop de tool turns (extraído p/ _run_tool_loop_body — usado
        # também pelo /loop autônomo, que passa budget/guardrail reais) ---
        _state = _TurnState(
            client=_turn_client,
            active_model=active_model,
            native_session_ok=_native_session_ok,
            fb_idx=_fb_idx,
            mem_turn_idx=_mem_turn_idx,
        )

        # Streaming ao vivo da resposta (plano 028 F1). Um por INVOCAÇÃO, não
        # por turno: com o replan do Kernel o corpo roda de novo, e um streamer
        # reaproveitado acumularia a resposta reprovada junto com a boa.
        _stream_holder: dict = {}

        def _invoke_turn():
            _stream_holder["atual"] = _make_streamer(console)
            return _run_tool_loop_body(
                ctx=ctx,
                router=router,
                state=_state,
                console=console,
                fallback_clients=fallback_clients,
                stats=stats,
                tool_timeout_s=tool_timeout_s,
                session_store=session_store,
                session_id=session_id,
                active_workspace=active_workspace,
                turn_input_text=user_input,
                memprov=_memprov,
                streamer=_stream_holder["atual"],
            )

        def _invoke_turn_com_quadro():
            """O turno inteiro dentro do quadro vivo (plano 028 F2).

            É isto que resolve o D3: o HUD fica colado no rodapé durante a
            execução — antes o estado sumia no Enter e só voltava no prompt
            seguinte. Fora de TTY, `_quadro_do_turno` devolve um nullcontext e
            tudo segue pelo caminho antigo.
            """
            with _quadro_do_turno(console) as _frame:
                if _frame is not None:
                    _frame.set_hud(_hud_state_atual())
                return _invoke_turn()

        if kernel is not None:
            # ── Kernel 6c-3: turno governado (kernel.enabled=true) ────────────
            # O corpo do turno vira EXECUTOR do kernel (o streaming pro console
            # acontece dentro dele — execute() basta, não precisa de stream()).
            # Ganhos: cada turno interativo vira um Run auditável com estados
            # completos; kill-switch/policy/budget barram ANTES da chamada LLM.
            from .core.kernel import KernelRequest as _KReq
            _captured: dict = {}
            _base_msgs = len(ctx.messages)  # snapshot p/ replan (ver /chat)

            def _turn_executor(payload):
                if payload.get("replan_attempt"):
                    # descarta a tentativa reprovada e injeta o motivo do gate
                    del ctx.messages[_base_msgs:]
                    ctx.add_ephemeral_system(
                        "Sua resposta anterior foi reprovada pelo quality gate: "
                        f"{payload.get('replan_feedback', '')}. Responda novamente "
                        "corrigindo esse problema."
                    )
                o = _invoke_turn()
                _captured["outcome"] = o
                if o.kind == "final":
                    return {"status": "completed", "output": o.display,
                            "tool_calls_count": len(o.tool_log)}
                # kinds anormais (provider_error, interrupted, tool_limit…) já
                # imprimiram o necessário no console — o run registra o motivo
                return {"status": "failed", "error": f"turno terminou em {o.kind}"}

            # Esteira zerada no início do turno: sem isto o rodapé exibiria o
            # "completed" do turno ANTERIOR enquanto este ainda nem começou.
            _hud_kernel_estado = None
            # O quadro cobre o `execute()` INTEIRO, não cada invocação do
            # executor: com replan o corpo do turno roda de novo, e abrir/fechar
            # o quadro a cada tentativa faria o HUD piscar no meio do turno.
            with _quadro_do_turno(console) as _frame:
                if _frame is not None:
                    _frame.set_hud(_hud_state_atual())
                _kout = kernel.execute(_KReq(
                    task=user_input, session_id=session_id or "",
                    agent_id="cli.agent", input={"endpoint": "cli.agent"},
                ), executor=_turn_executor)
            outcome = _captured.get("outcome")
            if outcome is None:
                # governança barrou ANTES do turno (kill-switch/policy/budget) —
                # remove o input pendurado do contexto e volta ao prompt
                del ctx.messages[_base_msgs - 1:]
                _blocked = _kout.error or _kout.policy_reason or _kout.status
                from .ui import notice as _notice
                _hint = ""
                if _kout.status == "waiting_approval" and _kout.approval_id:
                    _hint = f"Aprove com: bauer kernel approve {_kout.approval_id}"
                console.print(_notice(
                    "Turno bloqueado pela governança",
                    str(_blocked),
                    kind="blocked",
                    hint=_hint,
                ))
                continue
        else:
            outcome = _invoke_turn_com_quadro()
        # Turno roteado (heurístico): o client do profile vale SÓ para este
        # turno — não adota na sessão, a menos que o fallback tenha trocado o
        # client no meio do turno (aí a troca é intencional e permanece).
        # O modelo que DE FATO serviu este turno. A barra de status mostrava
        # `model_name` — o configurado — mesmo quando o roteamento mandou o turno
        # para outro provider. Medido em uso real: cabeçalho e rodapé diziam
        # `hf.co/unsloth/Qwen3.6-27B-MTP-GGUF:Q5_K_M` / provider `ollama`
        # enquanto o turno rodava em `minimax/minimax-m2.5` e acumulava custo no
        # OpenRouter. É o mesmo defeito do `tool_mode`: o harness sabe e não
        # conta.
        _modelo_do_turno = active_model
        if not _hr_routed or _state.client is not _turn_client:
            client = _state.client
            active_model = _state.active_model
            _modelo_do_turno = _state.active_model
            # Downgrade native→bridge só é adotado na sessão quando o turno
            # rodou no client da sessão — um provider roteado sem tools nativas
            # não deve rebaixar o provider principal.
            _native_session_ok = _state.native_session_ok
        _fb_idx = _state.fb_idx
        _mem_turn_idx = _state.mem_turn_idx

        # tok/s do turno que acabou de rodar — o StreamDiag do renderer (F1) já
        # mede; aqui só é publicado no HUD para aparecer na barra do próximo
        # prompt. Fica o valor do ÚLTIMO turno (não zera): "18.4 tok/s" parado
        # na barra informa a velocidade real da máquina; zerar entre turnos só
        # apagaria o número que o usuário quer comparar.
        _streamer_do_turno = _stream_holder.get("atual")
        if _streamer_do_turno is not None:
            try:
                _tps = _streamer_do_turno.diag.tokens_per_second
                if _tps > 0:
                    _hud_tokens_por_segundo = _tps
            except Exception as _exc:  # noqa: BLE001 — métrica nunca derruba o turno
                from .logging_config import log_suppressed
                log_suppressed("hud.metrica", _exc)

        if outcome.kind == "final":
            if _ja_exibido(_stream_holder.get("atual"), outcome.display):
                # Já saiu ao vivo — reimprimir daria a resposta em dobro. Só a
                # linha de custo, que o streaming não tem como saber no meio.
                if outcome.turn_cost_line:
                    console.print(outcome.turn_cost_line)
                console.print()
            else:
                _print_assistant_response(console, outcome.display, outcome.turn_cost_line)
        # demais kinds (loop_hard_stop, provider_error, empty_response,
        # interrupted, tool_limit) já imprimiram o necessário dentro de
        # _run_tool_loop_body — nada mais a fazer, volta a ler o próximo input.

        # Telemetria de skill (Nível 1: só coleta). Se uma skill foi injetada,
        # atribui o DESFECHO grosseiro do turno a ela: "final" = ok; kinds de
        # erro = ruim; resto = neutro. Sinal fraco por turno (a skill é 1 fator
        # entre vários) — só o agregado significa algo. NÃO age.
        if _injected_skill:
            try:
                from .skill_stats import record_use as _rec_skill
                if outcome.kind == "final":
                    _sk_outcome = "good"
                elif outcome.kind in ("provider_error", "empty_response", "loop_hard_stop"):
                    _sk_outcome = "bad"
                else:
                    _sk_outcome = "neutral"
                _rec_skill(_injected_skill, _sk_outcome)
            except Exception:
                pass
            _last_injected_skill = _injected_skill  # p/ atribuir 👍/👎 no próximo /thumbs

        # Checkpoint: o projeto App Factory ativo acabou de completar o
        # planejamento (cruzou p/ IMPLEMENTATION)? Oferece Revisar/Desenvolver/
        # Continuar. Só 'develop' age aqui — dispara o /loop autônomo.
        _cp = _maybe_planning_checkpoint(
            console, router, active_workspace, _gate_before, _planning_checkpoint_enabled,
        )
        if _cp is not None and _cp[0] == "develop":
            _cp_state = _TurnState(
                client=client, active_model=model_name,
                native_session_ok=_native_session_ok, fb_idx=_fb_idx,
                mem_turn_idx=_mem_turn_idx,
            )
            _run_loop_mode(
                task_description=_cp[1], overrides={}, ctx=ctx, router=router,
                state=_cp_state, console=console, fallback_clients=fallback_clients,
                stats=stats, tool_timeout_s=tool_timeout_s, session_store=session_store,
                session_id=session_id, active_workspace=active_workspace, memprov=_memprov,
                bus=getattr(kernel, "bus", None),
            )
            client = _cp_state.client
            _native_session_ok = _cp_state.native_session_ok
            _fb_idx = _cp_state.fb_idx
            _mem_turn_idx = _cp_state.mem_turn_idx
