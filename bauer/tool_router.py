"""Tool Bridge do Bauer Agent (Fase 4/5).

Permite usar ferramentas mesmo com modelos sem tool calling nativo.
O modelo escreve intenção em JSON; o Bauer valida, sandboxa e executa.

Premortem item 4 — Tool Bridge inseguro:
  Três camadas obrigatórias:
  1. Allowlist de tools (nenhuma fora da lista executa)
  2. Sandbox de diretório (nenhum path sai do workspace)
  3. Regra clara antes de sobrescrever arquivo

Tools de arquivo (sempre disponíveis):
  list_dir    — lista conteúdo de diretório
  read_file   — lê arquivo de texto (limite 100 KB)
  write_file  — grava arquivo (overwrite=false por padrão)
  search_text — busca padrão em arquivos
  create_dir  — cria diretório recursivo no workspace
  delete_file — remove arquivo (não diretório)
  append_file — acrescenta conteúdo ao final de arquivo
  move_file   — move/renomeia arquivo dentro do workspace
  diff_files  — diff unificado entre dois arquivos

Tools de busca:
  glob_files    — encontra arquivos por padrão glob
  regex_search  — busca com regex real (suporta flags i/m/s)

Tools de utilidade:
  calculate     — avalia expressão matemática segura
  datetime_now  — data/hora atual formatada
  json_query    — consulta JSON por path simples
  encode_decode — base64 / URL / hex encode e decode

Tools opcionais (requerem ShellRunner):
  run_command — executa comando controlado

Tools web (requerem web_enabled=true):
  web_search   — pesquisa na web (DuckDuckGo)
  web_fetch    — busca conteúdo de URL
  http_request — HTTP GET/POST genérico com headers e body

Tools de agente (sempre disponíveis):
  patch          — edição cirúrgica find-and-replace em arquivo
  todo           — lista de tarefas da sessão (in-memory)
  memory         — key-value persistente em .bauer_memory.json
  execute_code   — sandbox Python via subprocess (timeout configurável)
  clarify        — pergunta ao usuário mid-task
  delegate_task  — delega subtarefa a sub-agente isolado
  vision_analyze — análise de imagem via modelo multimodal (requer llm_client)
  mcp_call       — chama tool em servidor MCP via stdio (requer pip install mcp)
"""

from __future__ import annotations

import ast
import difflib
import json
import logging
import os
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

from .shell_runner import ShellError
from .tool_policy import load_tool_policy
from .tools.browser import BrowserToolsMixin
from .tools.channel import ChannelToolsMixin
from .tools.code_intel import CodeIntelToolsMixin
from .tools.cronjob import CronjobToolsMixin
from .tools.execution import ExecToolsMixin
from .tools.factory import FactoryToolsMixin
from .tools.fs import FsToolsMixin
from .tools.kanban import KanbanToolsMixin
from .tools.mcp import McpToolsMixin
from .tools.memory import MemoryToolsMixin
from .tools.session import SessionToolsMixin
from .tools.skills import SkillsToolsMixin
from .tools.utility import UtilityToolsMixin
from .tools.web import WebToolsMixin
from .unicode_utils import sanitize_surrogates as _sanitize_surrogates
from .workspace_manager import WorkspaceError, WorkspaceManager

# Wave 4.5: lazy imports so the tool_router stays importable even if the
# security modules are somehow unavailable (e.g. stripped install).
try:
    from .url_safety import UrlSafetyError, is_safe_url as _is_safe_url
    _URL_SAFETY_AVAILABLE = True
except ImportError:
    _URL_SAFETY_AVAILABLE = False

try:
    from .schema_sanitizer import sanitize_tool_schemas as _sanitize_schemas
    _SCHEMA_SANITIZER_AVAILABLE = True
except ImportError:
    _SCHEMA_SANITIZER_AVAILABLE = False

try:
    from .approval import check_all_command_guards as _check_command_guards
    _APPROVAL_AVAILABLE = True
except ImportError:
    _APPROVAL_AVAILABLE = False

def _package_available(name: str) -> bool:
    """Verifica se um pacote Python está disponível sem importá-lo."""
    import sys
    import importlib.util
    if name in sys.modules:
        return True
    try:
        return importlib.util.find_spec(name) is not None
    except (ValueError, ModuleNotFoundError):
        return False

# P4: exceções e tipos compartilhados moram em tools/base.py para evitar import
# circular com os mixins. Re-exportadas aqui — `from bauer.tool_router import
# ToolError, SandboxError, DryRunResult` continua funcionando.
from .tools.base import DryRunResult, SandboxError, ToolError  # noqa: E402

# Níveis de permissão: do menos ao mais privilegiado
_PERMISSION_LEVELS = ("read", "write", "execute", "network", "system")
_RISK_LEVELS = ("low", "medium", "high", "critical")

# Mapa de metadados de segurança por tool
# permission_level: impacto máximo possível da tool
# risk_level: probabilidade × severidade de dano
# requires_approval: se True, deve exigir confirmação humana em produção
_TOOL_SECURITY: dict[str, dict] = {
    # Leitura — sem side effects
    "list_dir":       {"permission": "read",    "risk": "low",    "approval": False},
    "read_file":      {"permission": "read",    "risk": "low",    "approval": False},
    "search_text":    {"permission": "read",    "risk": "low",    "approval": False},
    "glob_files":     {"permission": "read",    "risk": "low",    "approval": False},
    "regex_search":   {"permission": "read",    "risk": "low",    "approval": False},
    "diff_files":     {"permission": "read",    "risk": "low",    "approval": False},
    "calculate":      {"permission": "read",    "risk": "low",    "approval": False},
    "datetime_now":   {"permission": "read",    "risk": "low",    "approval": False},
    "json_query":     {"permission": "read",    "risk": "low",    "approval": False},
    "encode_decode":  {"permission": "read",    "risk": "low",    "approval": False},
    "todo":           {"permission": "read",    "risk": "low",    "approval": False},
    "skills_list":    {"permission": "read",    "risk": "low",    "approval": False},
    "skill_view":     {"permission": "read",    "risk": "low",    "approval": False},
    "app_factory_status": {"permission": "read", "risk": "low",   "approval": False},
    "app_factory_score":  {"permission": "read", "risk": "low",   "approval": False},
    "verify_app":     {"permission": "shell",   "risk": "medium", "approval": False},
    "memory":         {"permission": "read",    "risk": "low",    "approval": False},
    "session_search": {"permission": "read",    "risk": "low",    "approval": False},
    "kanban_list":    {"permission": "read",    "risk": "low",    "approval": False},
    "kanban_show":    {"permission": "read",    "risk": "low",    "approval": False},
    "process":        {"permission": "read",    "risk": "low",    "approval": False},
    "code_symbols":   {"permission": "read",    "risk": "low",    "approval": False},
    "find_definition":{"permission": "read",    "risk": "low",    "approval": False},
    "get_imports":    {"permission": "read",    "risk": "low",    "approval": False},
    "find_usages":    {"permission": "read",    "risk": "low",    "approval": False},
    # G15/G26: LSP tools
    "lsp_hover":             {"permission": "read",    "risk": "low",    "approval": False},
    "lsp_definitions":       {"permission": "read",    "risk": "low",    "approval": False},
    "lsp_references":        {"permission": "read",    "risk": "low",    "approval": False},
    "lsp_diagnostics":       {"permission": "read",    "risk": "low",    "approval": False},
    "lsp_workspace_symbols": {"permission": "read",    "risk": "low",    "approval": False},
    "lsp_completion":        {"permission": "read",    "risk": "low",    "approval": False},
    "lsp_code_actions":      {"permission": "read",    "risk": "low",    "approval": False},
    "lsp_format":            {"permission": "write",   "risk": "medium", "approval": False},
    "lsp_rename":            {"permission": "write",   "risk": "high",   "approval": True},
    # Escrita local — workspace-scoped
    "write_file":     {"permission": "write",   "risk": "medium", "approval": False},
    "append_file":    {"permission": "write",   "risk": "medium", "approval": False},
    "patch":          {"permission": "write",   "risk": "medium", "approval": False},
    "create_dir":     {"permission": "write",   "risk": "low",    "approval": False},
    "move_file":      {"permission": "write",   "risk": "medium", "approval": False},
    "delete_file":    {"permission": "write",   "risk": "high",   "approval": True},
    "skill_manage":   {"permission": "write",   "risk": "low",    "approval": False},
    "app_factory_init": {"permission": "write", "risk": "low",    "approval": False},
    "kanban_create":  {"permission": "write",   "risk": "low",    "approval": False},
    "kanban_complete":{"permission": "write",   "risk": "low",    "approval": False},
    "kanban_block":   {"permission": "write",   "risk": "low",    "approval": False},
    "kanban_unblock": {"permission": "write",   "risk": "low",    "approval": False},
    "kanban_heartbeat":{"permission":"write",   "risk": "low",    "approval": False},
    "kanban_comment": {"permission": "write",   "risk": "low",    "approval": False},
    "kanban_link":    {"permission": "write",   "risk": "low",    "approval": False},
    "cronjob":        {"permission": "write",   "risk": "medium", "approval": False},
    # Execução de código — isolada mas com efeitos
    "execute_code":   {"permission": "execute", "risk": "medium", "approval": False},
    "run_command":    {"permission": "execute", "risk": "high",   "approval": True},
    "clarify":        {"permission": "execute", "risk": "low",    "approval": False},
    "delegate_task":  {"permission": "execute", "risk": "medium", "approval": False},
    "mixture_of_agents":{"permission":"execute","risk": "medium", "approval": False},
    "image_generate": {"permission": "execute", "risk": "low",    "approval": False},
    "text_to_speech": {"permission": "execute", "risk": "low",    "approval": False},
    "vision_analyze": {"permission": "execute", "risk": "low",    "approval": False},
    "video_analyze":  {"permission": "execute", "risk": "low",    "approval": False},
    # Rede
    "web_search":     {"permission": "network", "risk": "low",    "approval": False},
    "web_fetch":      {"permission": "network", "risk": "low",    "approval": False},
    "http_request":   {"permission": "network", "risk": "medium", "approval": False},
    "browser_navigate":{"permission":"network", "risk": "medium", "approval": False},
    "browser_snapshot":{"permission":"network", "risk": "low",    "approval": False},
    "browser_click":  {"permission": "network", "risk": "medium", "approval": False},
    "browser_type":   {"permission": "network", "risk": "medium", "approval": False},
    "browser_scroll": {"permission": "network", "risk": "low",    "approval": False},
    "browser_back":   {"permission": "network", "risk": "low",    "approval": False},
    "browser_press":  {"permission": "network", "risk": "medium", "approval": False},
    "browser_console":{"permission": "network", "risk": "low",    "approval": False},
    "browser_get_images":{"permission":"network","risk": "low",   "approval": False},
    "browser_vision": {"permission": "network", "risk": "low",    "approval": False},
    "browser_dialog": {"permission": "network", "risk": "medium", "approval": False},
    "browser_cdp":    {"permission": "network", "risk": "high",   "approval": True},
    "mcp_call":       {"permission": "network", "risk": "medium", "approval": False},
    # Canais do Bauer Gateway — outbound para humanos (outbox durável)
    "channel_send":   {"permission": "network", "risk": "medium", "approval": False},
    "channel_list":   {"permission": "read",    "risk": "low",    "approval": False},
    "send_message":   {"permission": "network", "risk": "medium", "approval": False},
    "transcribe_audio": {"permission": "network", "risk": "low",  "approval": False},
    # Sistema
    "video_generate": {"permission": "network", "risk": "low",    "approval": False},
}

# Per-tool hard timeout in seconds (enforced via ThreadPoolExecutor).
# 0 = no timeout. Pulled from tools.yaml `timeout_seconds` key if present,
# otherwise defaults below are used.
_TOOL_TIMEOUTS: dict[str, int] = {
    # Execution tools — cap to avoid runaway processes
    "execute_code":   120,
    "run_command":    120,
    "delegate_task":  600,
    "mixture_of_agents": 300,
    # Network tools — prevent hung connections
    "web_fetch":       60,
    "web_search":      30,
    "http_request":    60,
    "browser_navigate": 60,
    "browser_snapshot": 30,
    "browser_click":    30,
    "browser_type":     30,
    "mcp_call":         60,
    # IO tools
    "image_generate": 120,
    "text_to_speech":  90,
    "vision_analyze":  60,
    "video_analyze":  180,
    "transcribe_audio": 150,
    "send_message":    60,
    # Default for everything else (0 = no timeout)
}

def _load_tool_timeouts_from_yaml() -> None:
    """Override _TOOL_TIMEOUTS from tools.yaml timeout_seconds keys (if present)."""
    try:
        from pathlib import Path as _Path
        import yaml as _yaml
        _p = _Path(__file__).parent.parent / "tools.yaml"
        if not _p.exists():
            return
        data = _yaml.safe_load(_p.read_text())
        for tool_name, cfg in (data.get("tools") or {}).items():
            if isinstance(cfg, dict) and "timeout_seconds" in cfg:
                _TOOL_TIMEOUTS[tool_name] = int(cfg["timeout_seconds"])
    except Exception:
        pass  # Never crash on config load

try:
    _load_tool_timeouts_from_yaml()
except Exception:
    pass

_TOOL_CONTEXTS = ("supervisor", "orchestrator", "chat", "worker")
_TOOL_CONTEXT_ALIASES = {
    "": "supervisor",
    "default": "supervisor",
    "operator": "supervisor",
    "agent": "orchestrator",
    "dag": "orchestrator",
    "durable-worker": "worker",
}
_CHAT_CONTEXT_DENYLIST = frozenset({
    "kanban_heartbeat",
    "kanban_complete",
    "kanban_block",
})
_WORKER_CONTEXT_ALLOWLIST = frozenset({
    # Read/inspect the workspace and current state.
    "list_dir",
    "read_file",
    "search_text",
    "glob_files",
    "regex_search",
    "diff_files",
    "calculate",
    "datetime_now",
    "json_query",
    "encode_decode",
    "todo",
    "skills_list",
    "skill_view",
    "memory",
    "session_search",
    "kanban_list",
    "kanban_show",
    "process",
    "code_symbols",
    "find_definition",
    "get_imports",
    "find_usages",
    "lsp_hover",
    "lsp_definitions",
    "lsp_references",
    "lsp_diagnostics",
    "lsp_workspace_symbols",
    "lsp_completion",
    "lsp_code_actions",
    # Mutate only the local workspace needed to complete the claimed task.
    "write_file",
    "append_file",
    "patch",
    "create_dir",
    "move_file",
    "delete_file",
    "execute_code",
    "run_command",
    "clarify",
    # Report lifecycle for the single claimed task.
    "kanban_heartbeat",
    "kanban_comment",
    "kanban_complete",
    "kanban_block",
})

# P4: limites de I/O movidos para tools/base.py (compartilhados com FsToolsMixin).
# Re-importados aqui — usados nos schemas do __init__ e por testes que fazem
# `from bauer.tool_router import _MAX_SEARCH_RESULTS`.
from .tools.base import (  # noqa: E402
    _DEFAULT_READ_LINES,
    _MAX_FILE_BYTES,
    _MAX_READ_BYTES,
    _MAX_SEARCH_RESULTS,
    _syntax_check,
)

# G18.4: padrões de nomes de modelos multimodais conhecidos (capability check
# das tools de visão). Lista generosa; é só um HINT — configurar
# auxiliary.vision_model sempre bypassa a checagem.
_MULTIMODAL_PATTERNS = (
    "gpt-4o", "gpt-4-vision", "gpt-4-turbo", "o1", "o3", "o4",
    "claude-3", "claude-4", "claude-opus", "claude-sonnet", "claude-haiku",
    "gemini", "llava", "bakllava", "moondream", "pixtral", "llama-3.2-vision",
    "llama3.2-vision", "qwen2-vl", "qwen2.5-vl", "qwen-vl", "minicpm-v",
    "internvl", "phi-3-vision", "phi-3.5-vision", "vision",
)

def _looks_multimodal(model_name: str) -> bool:
    """Heurística: o nome do modelo parece suportar visão? (G18.4)"""
    m = (model_name or "").lower()
    return any(p in m for p in _MULTIMODAL_PATTERNS)

def _normalize_tool_context(value: str | None) -> str:
    raw = (value if value is not None else os.environ.get("BAUER_TOOL_CONTEXT", "supervisor"))
    context = _TOOL_CONTEXT_ALIASES.get(str(raw).strip().lower(), str(raw).strip().lower())
    return context if context in _TOOL_CONTEXTS else "supervisor"

class ToolRouter(
    BrowserToolsMixin,
    ChannelToolsMixin,
    CodeIntelToolsMixin,
    CronjobToolsMixin,
    ExecToolsMixin,
    FactoryToolsMixin,
    FsToolsMixin,
    KanbanToolsMixin,
    McpToolsMixin,
    MemoryToolsMixin,
    SessionToolsMixin,
    SkillsToolsMixin,
    UtilityToolsMixin,
    WebToolsMixin,
):
    """Roteador central do Tool Bridge.

    Uso:
        router = ToolRouter(workspace=Path("workspace"))
        result = router.execute('{"action": "list_dir", "args": {"path": "."}}')

    P4: tools por categoria vivem em mixins (bauer/tools/*.py) herdados aqui.
    """

    def __init__(
        self,
        workspace: str | Path = "workspace",
        shell_runner=None,
        web_enabled: bool = False,
        web_config=None,
        llm_client=None,
        vision_client=None,
        model_name: str = "",
        dry_run: bool = False,
        max_tool_calls: int = 500,
        max_retries: int = 3,
        audit_enabled: bool = True,
        session_id: str = "",
        tool_context: str | None = None,
        tool_policy_path: str | Path | None = None,
    ):
        self.workspace = Path(workspace).resolve()
        self._llm_client = llm_client  # cliente LLM opcional (vision_analyze, delegate_task)
        self._model_name = model_name  # nome do modelo configurado (para delegate_task)
        # G18.4: cliente multimodal dedicado (auxiliary.vision_model). As tools de
        # visão usam ele se presente; senão caem no _llm_client principal.
        self._vision_client = vision_client
        self._dry_run = dry_run          # SAFETY-002: simula execução sem side effects
        self._max_tool_calls = max_tool_calls  # LIMITS-001: teto de chamadas por sessão
        self._max_retries = max_retries        # LIMITS-001: max tentativas por tool
        self._tool_call_count = 0              # contador redefenido por sessão
        self.tool_context = _normalize_tool_context(tool_context)
        self._tool_policy = load_tool_policy(self.workspace, explicit_path=tool_policy_path)
        # SEG-3: audit logger
        if audit_enabled:
            from .audit_logger import AuditLogger
            logs_dir = Path(workspace).resolve().parent / "logs"
            self._audit: "AuditLogger | None" = AuditLogger(logs_dir, session_id)
        else:
            self._audit = None
        self._recent_messages: list[dict] = []  # G4: context for LLM approval
        # G17.1: dedup anti-loop de read_file — resolved_path → {key, mtime, hits}
        self._read_tracker: dict[str, dict] = {}
        # G17.2: read-before-write — paths (resolvidos) já lidos nesta sessão
        self._read_paths: set[str] = set()
        self._tools: dict[str, dict] = {
            "list_dir": {
                "fn": self._list_dir,
                "description": "Lista conteudo de diretorio dentro do workspace.",
                "args": {"path": "str — caminho relativo ao workspace (default: '.')"},
            },
            "read_file": {
                "fn": self._read_file,
                "description": (
                    "Le arquivo de texto com numeracao de linha e paginacao. "
                    "Use offset+limit para ler trechos de arquivos grandes."
                ),
                "args": {
                    "path": "str — caminho relativo ao workspace (obrigatorio)",
                    "offset": f"int — linha inicial 1-indexed (default: 1)",
                    "limit": f"int — numero de linhas a ler (default: {_DEFAULT_READ_LINES})",
                },
            },
            "write_file": {
                "fn": self._write_file,
                "description": "Grava arquivo. overwrite=false por padrao.",
                "args": {
                    "path": "str — caminho relativo ao workspace (obrigatorio)",
                    "content": "str — conteudo do arquivo (obrigatorio)",
                    "overwrite": "bool — se true, sobrescreve arquivo existente (default: false)",
                },
            },
            "search_text": {
                "fn": self._search_text,
                "description": f"Busca padrao (case-insensitive) em arquivos. Max {_MAX_SEARCH_RESULTS} resultados.",
                "args": {
                    "path": "str — arquivo ou diretorio (default: '.')",
                    "pattern": "str — texto a buscar (obrigatorio)",
                },
            },
        }
        # ── Tools de arquivo avançadas ─────────────────────────────────────
        self._tools["create_dir"] = {
            "fn": self._create_dir,
            "description": "Cria diretorio (e pais) dentro do workspace.",
            "args": {"path": "str — caminho relativo ao workspace (obrigatorio)"},
        }
        self._tools["delete_file"] = {
            "fn": self._delete_file,
            "description": "Remove arquivo do workspace. Nao remove diretorios.",
            "args": {
                "path": "str — caminho relativo ao workspace (obrigatorio)",
                "confirm": "bool — deve ser true para confirmar exclusao (default: false)",
            },
        }
        self._tools["append_file"] = {
            "fn": self._append_file,
            "description": "Acrescenta texto ao final de um arquivo (cria se nao existir).",
            "args": {
                "path": "str — caminho relativo ao workspace (obrigatorio)",
                "content": "str — conteudo a acrescentar (obrigatorio)",
            },
        }
        self._tools["move_file"] = {
            "fn": self._move_file,
            "description": "Move ou renomeia arquivo dentro do workspace.",
            "args": {
                "src": "str — caminho de origem (obrigatorio)",
                "dst": "str — caminho de destino (obrigatorio)",
                "overwrite": "bool — sobrescreve destino se existir (default: false)",
            },
        }
        self._tools["diff_files"] = {
            "fn": self._diff_files,
            "description": "Mostra diff unificado entre dois arquivos do workspace.",
            "args": {
                "path_a": "str — primeiro arquivo (obrigatorio)",
                "path_b": "str — segundo arquivo (obrigatorio)",
                "context_lines": "int — linhas de contexto (default: 3)",
            },
        }

        # ── Tools de busca ─────────────────────────────────────────────────
        self._tools["glob_files"] = {
            "fn": self._glob_files,
            "description": "Encontra arquivos por padrao glob dentro do workspace.",
            "args": {
                "pattern": "str — padrao glob (ex: '**/*.py', 'src/*.ts') (obrigatorio)",
                "path": "str — subdiretorio base (default: '.')",
            },
        }
        self._tools["regex_search"] = {
            "fn": self._regex_search,
            "description": "Busca com regex em arquivos. Suporta flags: i (case-insensitive), m (multiline), s (dotall).",
            "args": {
                "pattern": "str — expressao regular (obrigatorio)",
                "path": "str — arquivo ou diretorio (default: '.')",
                "flags": "str — combinacao de i/m/s (default: '')",
            },
        }

        # ── Tools de utilidade ─────────────────────────────────────────────
        self._tools["calculate"] = {
            "fn": self._calculate,
            "description": "Avalia expressao matematica segura. Suporta +,-,*,/,**,%, abs, round, min, max, sum.",
            "args": {"expression": "str — expressao matematica (ex: '2 ** 10 + sqrt(144)') (obrigatorio)"},
        }
        self._tools["datetime_now"] = {
            "fn": self._datetime_now,
            "description": "Retorna data e hora atual.",
            "args": {
                "format": "str — 'iso' | 'br' | 'us' | 'timestamp' (default: 'iso')",
                "tz": "str — 'utc' ou 'local' (default: 'utc')",
            },
        }
        self._tools["json_query"] = {
            "fn": self._json_query,
            "description": "Parseia JSON e consulta por path simples (ex: '.users[0].name').",
            "args": {
                "data": "str — JSON string ou caminho de arquivo relativo ao workspace (obrigatorio)",
                "query": "str — path no formato '.chave.sub[0]' (obrigatorio)",
            },
        }
        self._tools["encode_decode"] = {
            "fn": self._encode_decode,
            "description": "Codifica/decodifica texto: base64_encode, base64_decode, url_encode, url_decode, hex_encode, hex_decode.",
            "args": {
                "input": "str — texto ou bytes (obrigatorio)",
                "operation": "str — uma de: base64_encode, base64_decode, url_encode, url_decode, hex_encode, hex_decode (obrigatorio)",
            },
        }

        # ── Tool patch — edição cirúrgica de arquivo ───────────────────────
        self._tools["patch"] = {
            "fn": self._patch_file,
            "description": (
                "Edita arquivo substituindo old_string por new_string. "
                "Falha se old_string nao for encontrado ou houver mais de uma ocorrencia."
            ),
            "args": {
                "path": "str — caminho relativo ao workspace (obrigatorio)",
                "old_string": "str — trecho exato a substituir (obrigatorio)",
                "new_string": "str — novo trecho (default: '' para apagar)",
            },
        }

        # ── Tool todo — lista de tarefas da sessão ─────────────────────────
        self._todo_items: list[dict] = []   # [{id, text, done}]
        self._todo_next_id: int = 1
        self._tools["todo"] = {
            "fn": self._todo,
            "description": (
                "Gerencia lista de tarefas da sessao. "
                "Acoes: add, list, done, remove, clear."
            ),
            "args": {
                "action": "str — add | list | done | remove | clear (obrigatorio)",
                "text": "str — texto da tarefa (obrigatorio para add)",
                "id": "int — ID da tarefa (obrigatorio para done/remove)",
            },
        }

        # ── Tool memory — key-value persistente entre sessões ──────────────
        self._tools["memory"] = {
            "fn": self._memory,
            "description": (
                "Armazena e recupera informacoes entre sessoes. "
                "Acoes: set, get, list, delete."
            ),
            "args": {
                "action": "str — set | get | list | delete (obrigatorio)",
                "key": "str — chave (obrigatorio para set/get/delete)",
                "value": "str — valor a armazenar (obrigatorio para set)",
            },
        }

        # ── Tool execute_code — sandbox Python via subprocess ──────────────
        self._tools["execute_code"] = {
            "fn": self._execute_code,
            "description": (
                "Executa codigo Python em subprocesso isolado. "
                "Captura stdout e stderr. Timeout configuravel."
            ),
            "args": {
                "code": "str — codigo Python a executar (obrigatorio)",
                "timeout": "int — timeout em segundos (default: 30, max: 120)",
            },
        }

        # ── Tool clarify — agente pergunta ao usuário ──────────────────────
        self._tools["clarify"] = {
            "fn": self._clarify,
            "description": (
                "Faz uma pergunta ao usuario e retorna a resposta. "
                "Suporta multipla escolha via choices."
            ),
            "args": {
                "question": "str — pergunta ao usuario (obrigatorio)",
                "choices": "str — opcoes separadas por | (opcional, ex: 'sim|nao|cancelar')",
            },
        }

        # ── Tool delegate_task — sub-agente ────────────────────────────────
        self._tools["delegate_task"] = {
            "fn": self._delegate_task,
            "description": (
                "Delega uma subtarefa a um sub-agente isolado e retorna o resultado. "
                "Use para tarefas independentes que nao precisam do contexto atual."
            ),
            "args": {
                "task": "str — descricao completa da tarefa a delegar (obrigatorio)",
                "context": "str — contexto adicional para o sub-agente (opcional)",
                "timeout": "int — timeout em segundos (default: 120, max: 600)",
            },
        }

        # ── Tool vision_analyze — análise de imagem ─────────────────────────
        self._tools["vision_analyze"] = {
            "fn": self._vision_analyze,
            "description": (
                "Analisa imagem (URL ou path local) usando modelo de visao. "
                "Requer provider com suporte multimodal (GPT-4o, Claude 3, Gemini)."
            ),
            "args": {
                "image": "str — URL https:// ou caminho relativo ao workspace (obrigatorio)",
                "query": "str — pergunta ou instrucao sobre a imagem (obrigatorio)",
            },
        }

        # ── Tool cronjob — tarefas agendadas ───────────────────────────────
        self._tools["cronjob"] = {
            "fn": self._cronjob,
            "description": (
                "Cria e gerencia tarefas agendadas persistentes. "
                "Acoes: create, list, delete, run, pause, resume."
            ),
            "args": {
                "action": "str — create | list | delete | run | pause | resume (obrigatorio)",
                "name": "str — nome unico do job (obrigatorio para create/delete/run/pause/resume)",
                "command": "str — codigo Python ou comando shell a executar (obrigatorio para create)",
                "schedule": (
                    "str — quando executar: 'every 30m' | 'every 2h' | 'every 1d' | "
                    "'daily 09:00' | 'cron: */5 * * * *' (obrigatorio para create)"
                ),
                "mode": "str — python | shell (default: python)",
            },
        }

        # ── Tool session_search — busca em memória e histórico ──────────────
        self._tools["session_search"] = {
            "fn": self._session_search,
            "description": (
                "Busca texto em memória persistente e histórico de sessões. "
                "Acoes: search(query), recent(n)."
            ),
            "args": {
                "action": "str — search | recent (obrigatorio)",
                "query": "str — texto ou regex a buscar (obrigatorio para search)",
                "source": "str — memory | sessions | all (default: all)",
                "n": "int — numero de entradas recentes (default: 10, para recent)",
            },
        }

        # ── Tool mixture_of_agents — múltiplos LLMs em paralelo ────────────
        self._tools["mixture_of_agents"] = {
            "fn": self._mixture_of_agents,
            "description": (
                "Consulta multiplos agentes em paralelo e sintetiza as respostas. "
                "Cada agente recebe uma perspectiva diferente do problema."
            ),
            "args": {
                "query": "str — problema ou pergunta a ser respondida (obrigatorio)",
                "perspectives": (
                    "str — perspectivas separadas por | "
                    "(default: 'analitico|critico|criativo|pragmatico')"
                ),
                "synthesize": "bool — se true, faz passada final de sintese (default: true)",
            },
        }

        # ── Tool video_analyze — análise de vídeo ──────────────────────────
        self._tools["video_analyze"] = {
            "fn": self._video_analyze,
            "description": (
                "Analisa video por URL (providers nativos) ou arquivo local via frames-chave. "
                "Requer llm_client com suporte a visao."
            ),
            "args": {
                "video": "str — URL https:// ou caminho relativo ao workspace (obrigatorio)",
                "query": "str — pergunta ou instrucao sobre o video (obrigatorio)",
                "max_frames": "int — max frames a analisar para video local (default: 5, max: 20)",
            },
        }

        # ── Skills system ────────────────────────────────────────────────────
        self._tools["skill_manage"] = {
            "fn": self._skill_manage,
            "description": (
                "Gerencia skills (memorias procedurais persistentes): create, update, delete. "
                "Skills sao procedimentos reutilizaveis salvos em .bauer_skills.json."
            ),
            "args": {
                "action": "str — create | update | delete (obrigatorio)",
                "name": "str — nome unico da skill (obrigatorio)",
                "description": "str — descricao do que a skill faz (obrigatorio para create/update)",
                "content": "str — corpo da skill: passos, codigo, notas (obrigatorio para create/update)",
                "tags": "list[str] — tags para categorizar (opcional)",
            },
        }
        self._tools["skill_view"] = {
            "fn": self._skill_view,
            "description": (
                "Carrega e exibe o conteudo completo de uma skill pelo nome. "
                "Retorna descricao, conteudo, tags e metadados."
            ),
            "args": {
                "name": "str — nome exato da skill (obrigatorio)",
            },
        }
        self._tools["skills_list"] = {
            "fn": self._skills_list,
            "description": (
                "Lista todas as skills disponíveis com nomes, descricoes e tags. "
                "Suporta filtro por tag ou substring do nome."
            ),
            "args": {
                "filter": "str — substring para filtrar por nome ou tag (opcional)",
            },
        }

        # ── App Factory (Spec-Driven Development) ────────────────────────────
        self._tools["app_factory_init"] = {
            "fn": self._app_factory_init,
            "description": (
                "Inicia a governanca da App Factory no projeto: cria docs/ com os "
                "esqueletos dos 7 docs de planejamento (SPEC, ARCHITECTURE, BACKLOG, "
                "TASKS, DECISIONS, PROJECT_CONTEXT, PROGRESS) + docs de entrega + "
                "README/.env.example/CI. Depois disso a escrita de codigo fica "
                "BLOQUEADA ate os 7 docs estarem preenchidos."
            ),
            "args": {
                "idea": "str — descricao da ideia/aplicacao (obrigatorio)",
                "stack": "str — stack preferida, ex: FastAPI+React (opcional)",
                "path": "str — subdiretorio do projeto (opcional, default = raiz do workspace)",
                "overwrite": "bool — sobrescrever docs existentes (opcional, default false)",
            },
        }
        self._tools["app_factory_status"] = {
            "fn": self._app_factory_status,
            "description": (
                "Mostra o estado da App Factory: gate atual (discovery/planning/"
                "implementation/delivery), docs de planejamento pendentes e o "
                "Delivery Score parcial."
            ),
            "args": {
                "path": "str — subdiretorio do projeto (opcional)",
            },
        }
        self._tools["app_factory_score"] = {
            "fn": self._app_factory_score,
            "description": (
                "Calcula o Delivery Score objetivo (0-10) da V1 a partir de sinais "
                "verificaveis: SPEC/ARCHITECTURE/BACKLOG preenchidos, README, "
                ".env.example, testes, seguranca, deploy, runbook."
            ),
            "args": {
                "path": "str — subdiretorio do projeto (opcional)",
            },
        }
        self._tools["verify_app"] = {
            "fn": self._verify_app,
            "description": (
                "Auto-verifica que o app GERADO realmente roda: detecta a stack "
                "(Node/Python/Go/Rust), instala deps, builda e roda testes/smoke, "
                "e reporta pass/fail com a cauda do erro. Use APÓS implementar uma "
                "fatia para confirmar que funciona (não só que os arquivos existem)."
            ),
            "args": {
                "path": "str — subdiretorio do projeto (opcional; default = projeto ativo da App Factory)",
                "install": "bool — instalar dependencias antes (opcional, default true)",
                "timeout": "int — timeout por passo em segundos (opcional, default 300)",
            },
        }

        # ── Process manager ──────────────────────────────────────────────────
        self._processes: dict[str, dict] = {}  # pid_str → {"proc": Popen, "label": str}
        self._tools["process"] = {
            "fn": self._process,
            "description": (
                "Gerencia processos em background: start (lanca), list (lista), "
                "poll (verifica status), log (stdout/stderr), kill (encerra), write (stdin)."
            ),
            "args": {
                "action": "str — start | list | poll | log | kill | write (obrigatorio)",
                "command": "str — comando a executar (obrigatorio para start)",
                "label": "str — nome amigavel do processo (opcional, para start)",
                "pid": "str — ID do processo retornado por start (obrigatorio para poll/log/kill/write)",
                "input": "str — texto a enviar para stdin (obrigatorio para write)",
                "max_lines": "int — maximo de linhas de log (default: 50)",
            },
        }

        # ── Geração de mídia ─────────────────────────────────────────────────
        self._tools["image_generate"] = {
            "fn": self._image_generate,
            "description": (
                "Gera imagem a partir de prompt de texto via OpenAI DALL-E. "
                "Requer llm_client com suporte a OpenAI Images API."
            ),
            "args": {
                "prompt": "str — descricao detalhada da imagem (obrigatorio)",
                "model": "str — dall-e-3 | dall-e-2 (default: dall-e-3)",
                "size": "str — 1024x1024 | 1792x1024 | 1024x1792 (default: 1024x1024)",
                "quality": "str — standard | hd (default: standard, so dall-e-3)",
                "output_file": "str — caminho relativo para salvar a imagem no workspace (opcional)",
            },
        }
        self._tools["text_to_speech"] = {
            "fn": self._text_to_speech,
            "description": (
                "Converte texto em audio via OpenAI TTS API. "
                "Salva arquivo mp3 no workspace. Requer llm_client com OpenAI API."
            ),
            "args": {
                "text": "str — texto para converter (obrigatorio, max 4096 chars)",
                "output_file": "str — caminho relativo no workspace para salvar o mp3 (obrigatorio)",
                "voice": "str — alloy | echo | fable | onyx | nova | shimmer (default: alloy)",
                "model": "str — tts-1 | tts-1-hd (default: tts-1)",
            },
        }

        # ── Kanban board ─────────────────────────────────────────────────────
        self._tools["kanban_create"] = {
            "fn": self._kanban_create,
            "description": "Cria nova tarefa no board Kanban. Retorna o ID da tarefa.",
            "args": {
                "title": "str — titulo da tarefa (obrigatorio)",
                "description": "str — detalhes da tarefa (opcional)",
                "assignee": "str — agente/usuario responsavel (opcional)",
                "priority": "str — low | medium | high | critical (default: medium)",
                "status": "str — todo | ready | in_progress | blocked | failed | done (default: todo)",
                "parent_id": "str — ID da tarefa pai para sub-tarefas (opcional)",
            },
        }
        self._tools["kanban_list"] = {
            "fn": self._kanban_list,
            "description": "Lista tarefas do board com filtros por status, assignee ou prioridade.",
            "args": {
                "status": "str — todo | ready | in_progress | blocked | failed | done | all (default: all)",
                "assignee": "str — filtrar por responsavel (opcional)",
                "priority": "str — low | medium | high | critical (opcional)",
            },
        }
        self._tools["kanban_show"] = {
            "fn": self._kanban_show,
            "description": "Exibe detalhes completos de uma tarefa: descricao, historico, comentarios.",
            "args": {
                "task_id": "str — ID da tarefa (obrigatorio)",
            },
        }
        self._tools["kanban_complete"] = {
            "fn": self._kanban_complete,
            "description": "Marca tarefa como concluida com payload de handoff opcional.",
            "args": {
                "task_id": "str — ID da tarefa (obrigatorio)",
                "result": "str — resumo do resultado/handoff (opcional)",
            },
        }
        self._tools["kanban_block"] = {
            "fn": self._kanban_block,
            "description": "Bloqueia tarefa registrando o motivo do bloqueio.",
            "args": {
                "task_id": "str — ID da tarefa (obrigatorio)",
                "reason": "str — motivo do bloqueio (obrigatorio)",
            },
        }
        self._tools["kanban_unblock"] = {
            "fn": self._kanban_unblock,
            "description": "Remove bloqueio de tarefa, retornando-a ao status anterior.",
            "args": {
                "task_id": "str — ID da tarefa (obrigatorio)",
                "note": "str — nota sobre como o bloqueio foi resolvido (opcional)",
            },
        }
        self._tools["kanban_heartbeat"] = {
            "fn": self._kanban_heartbeat,
            "description": "Envia update de progresso para tarefa em andamento (keep-alive).",
            "args": {
                "task_id": "str — ID da tarefa (obrigatorio)",
                "progress": "str — descricao do progresso atual (obrigatorio)",
            },
        }
        self._tools["kanban_comment"] = {
            "fn": self._kanban_comment,
            "description": "Adiciona comentario em tarefa sem alterar seu status.",
            "args": {
                "task_id": "str — ID da tarefa (obrigatorio)",
                "comment": "str — texto do comentario (obrigatorio)",
                "author": "str — autor do comentario (default: agent)",
            },
        }
        self._tools["kanban_link"] = {
            "fn": self._kanban_link,
            "description": "Cria dependencia parent-child entre duas tarefas.",
            "args": {
                "parent_id": "str — ID da tarefa pai (obrigatorio)",
                "child_id": "str — ID da tarefa filha (obrigatorio)",
            },
        }

        # ── Browser automation (Playwright) ───────────────────────────────────
        self._browser_page = None   # playwright page ativa
        self._browser_ctx = None    # playwright browser context
        self._browser_pw = None     # playwright instance
        # G18: Playwright sync e thread-affine. Todas as browser tools rodam
        # SEMPRE nesta unica thread persistente — senao a pagina criada num
        # browser_navigate fica presa numa thread morta na call seguinte
        # (erro "cannot switch to a different thread which happens to have exited").
        self._browser_executor = None
        self._tools["browser_navigate"] = {
            "fn": self._browser_navigate,
            "description": "Abre uma URL num browser real (Playwright) — PESADO e lento. Use SO quando precisar interagir com a pagina (clicar, preencher, JS). Para apenas LER ou BUSCAR informacao, prefira web_search/web_fetch. Requer: pip install playwright && playwright install chromium.",
            "args": {
                "url": "str — URL completa com https:// (obrigatorio)",
                "wait_until": "str — load | domcontentloaded | networkidle (default: load)",
            },
        }
        self._tools["browser_snapshot"] = {
            "fn": self._browser_snapshot,
            "description": "Retorna arvore de acessibilidade da pagina atual como texto estruturado.",
            "args": {
                "include_hidden": "bool — incluir elementos ocultos (default: false)",
            },
        }
        self._tools["browser_click"] = {
            "fn": self._browser_click,
            "description": "Clica em elemento da pagina por seletor CSS ou texto visivel.",
            "args": {
                "selector": "str — seletor CSS, XPath ou texto (obrigatorio)",
                "by": "str — css | xpath | text | role (default: css)",
            },
        }
        self._tools["browser_type"] = {
            "fn": self._browser_type,
            "description": "Digita texto em campo de input identificado por seletor.",
            "args": {
                "selector": "str — seletor CSS do campo (obrigatorio)",
                "text": "str — texto a digitar (obrigatorio)",
                "clear_first": "bool — limpar campo antes de digitar (default: true)",
            },
        }
        self._tools["browser_scroll"] = {
            "fn": self._browser_scroll,
            "description": "Rola a pagina ou elemento especifico.",
            "args": {
                "direction": "str — up | down | top | bottom (default: down)",
                "amount": "int — pixels a rolar (default: 500)",
                "selector": "str — seletor do elemento a rolar (opcional, rola pagina se omitido)",
            },
        }
        self._tools["browser_back"] = {
            "fn": self._browser_back,
            "description": "Navega para pagina anterior no historico do browser.",
            "args": {},
        }
        self._tools["browser_press"] = {
            "fn": self._browser_press,
            "description": "Pressiona tecla(s) de teclado (Enter, Tab, Escape, ArrowDown, etc).",
            "args": {
                "key": "str — tecla ou combinacao (ex: 'Enter', 'Control+A') (obrigatorio)",
                "selector": "str — seletor do elemento foco (opcional, usa foco atual se omitido)",
            },
        }
        self._tools["browser_console"] = {
            "fn": self._browser_console,
            "description": "Retorna mensagens do console JavaScript e erros da pagina atual.",
            "args": {
                "max_lines": "int — maximo de linhas (default: 50)",
            },
        }
        self._tools["browser_get_images"] = {
            "fn": self._browser_get_images,
            "description": "Lista todas as imagens da pagina com URL e alt text.",
            "args": {
                "include_data_urls": "bool — incluir imagens data:// (default: false)",
            },
        }
        self._tools["browser_vision"] = {
            "fn": self._browser_vision,
            "description": "Captura screenshot da pagina e analisa com modelo de visao. Requer llm_client.",
            "args": {
                "query": "str — pergunta ou instrucao sobre o que analisar (obrigatorio)",
            },
        }
        self._tools["browser_dialog"] = {
            "fn": self._browser_dialog,
            "description": "Responde a dialogo JavaScript (alert/confirm/prompt) pendente.",
            "args": {
                "action": "str — accept | dismiss (default: accept)",
                "prompt_text": "str — texto para dialogo prompt (opcional)",
            },
        }
        self._tools["browser_cdp"] = {
            "fn": self._browser_cdp,
            "description": "Envia comando raw do Chrome DevTools Protocol (CDP). Uso avancado.",
            "args": {
                "method": "str — metodo CDP ex: 'Page.captureScreenshot' (obrigatorio)",
                "params": "dict — parametros do comando (opcional)",
            },
        }

        # ── Tool mcp_call — cliente MCP ─────────────────────────────────────
        self._tools["mcp_call"] = {
            "fn": self._mcp_call,
            "description": (
                "Chama uma tool em servidor MCP (Model Context Protocol) via stdio. "
                "Requer: pip install mcp. Configure servidores em config.yaml: mcp.servers."
            ),
            "args": {
                "server": "str — nome do servidor MCP configurado (obrigatorio)",
                "tool": "str — nome da tool a chamar no servidor (obrigatorio)",
                "arguments": "dict — argumentos da tool (obrigatorio)",
            },
        }

        # ── Tools de canal — Bauer Gateway (Telegram, Discord, webhook…) ────
        self._tools["channel_send"] = {
            "fn": self._channel_send,
            "description": (
                "Envia mensagem a um canal configurado do Bauer Gateway "
                "(telegram, discord, slack, webhook…). A mensagem entra no outbox "
                "duravel e e entregue pelo gateway (retry automatico). "
                "Use channel_list para ver os canais disponiveis."
            ),
            "args": {
                "channel": "str — nome do canal registrado (obrigatorio)",
                "text": "str — texto da mensagem (obrigatorio)",
            },
        }
        self._tools["channel_list"] = {
            "fn": self._channel_list,
            "description": "Lista os canais de notificacao configurados no Bauer Gateway.",
            "args": {},
        }
        self._tools["send_message"] = {
            "fn": self._send_message,
            "description": (
                "Envia mensagem diretamente a um chat de um canal vivo do gateway "
                "(telegram, discord). Entrega IMEDIATA quando o gateway esta rodando; "
                "senao enfileira no outbox. Suporta arquivo/imagem opcional via media_path."
            ),
            "args": {
                "channel": "str — 'telegram' ou 'discord' (obrigatorio)",
                "chat_id": "str — id do chat/usuario destino (obrigatorio)",
                "text": "str — texto da mensagem",
                "media_path": "str — caminho de arquivo local para anexar (opcional)",
            },
        }
        self._tools["transcribe_audio"] = {
            "fn": self._transcribe_audio,
            "description": (
                "Transcreve um arquivo de audio para texto (Whisper via Groq/OpenAI). "
                "Formatos: ogg, mp3, m4a, wav, webm, flac. Max 25MB."
            ),
            "args": {
                "path": "str — caminho do arquivo de audio (obrigatorio)",
            },
        }

        if shell_runner is not None:
            self._tools["run_command"] = {
                "fn": self._make_run_command(shell_runner),
                "description": "Executa comando shell controlado (allowlist + denylist + safe_mode). Use background=true para processos longos (servidores, watch).",
                "args": {
                    "command": "str — linha de comando (obrigatorio)",
                    "confirm": "bool — bypass safe_mode para risco medio (default: false)",
                    "background": "bool — roda destacado e retorna PID; acompanhe via tool process (default: false)",
                },
            }

        if web_enabled:
            from .web.dispatcher import WebDispatcher
            self._web = WebDispatcher(web_config)

            self._tools["web_search"] = {
                "fn": self._web_search,
                "description": "PRIMEIRA ESCOLHA para buscar qualquer informacao/fato na web (noticias, datas, precos, documentacao). Rapido e leve — uma chamada resolve. Use web_fetch para ler uma URL especifica. NAO use browser_navigate para buscar fatos.",
                "args": {
                    "query": "str — termo de pesquisa (obrigatorio)",
                    "max_results": "int — maximo de resultados (default: 5, max: 10)",
                },
            }
            self._tools["web_fetch"] = {
                "fn": self._web_fetch,
                "description": "Busca o conteudo de uma URL e retorna como texto.",
                "args": {
                    "url": "str — URL completa (obrigatorio, com https://)",
                    "max_chars": "int — maximo de caracteres (default: 5000)",
                },
            }
            self._tools["http_request"] = {
                "fn": self._http_request,
                "description": "Realiza requisicao HTTP (GET/POST/PUT/PATCH/DELETE) com headers e body customizados.",
                "args": {
                    "url": "str — URL completa (obrigatorio)",
                    "method": "str — GET | POST | PUT | PATCH | DELETE (default: GET)",
                    "headers": "dict — headers adicionais (default: {})",
                    "body": "str | dict — corpo da requisicao (opcional, para POST/PUT/PATCH)",
                    "max_chars": "int — limite do corpo da resposta (default: 5000)",
                },
            }

        # ── G7: Code Intelligence Light ────────────────────────────────────────
        self._tools["code_symbols"] = {
            "fn": self._code_symbols,
            "description": "Lista simbolos (funcoes, classes, variaveis top-level) de um arquivo Python via AST.",
            "args": {
                "file": "str — caminho do arquivo .py relativo ao workspace (obrigatorio)",
            },
        }
        self._tools["find_definition"] = {
            "fn": self._find_definition,
            "description": "Encontra onde uma funcao ou classe e definida no workspace (busca AST/grep).",
            "args": {
                "symbol": "str — nome da funcao ou classe (obrigatorio)",
                "workspace": "str — diretorio de busca (default: '.')",
            },
        }
        self._tools["get_imports"] = {
            "fn": self._get_imports,
            "description": "Lista todos os imports de um arquivo Python.",
            "args": {
                "file": "str — caminho do arquivo .py relativo ao workspace (obrigatorio)",
            },
        }
        self._tools["find_usages"] = {
            "fn": self._find_usages,
            "description": "Encontra onde um simbolo e usado no workspace (busca grep, multi-linguagem).",
            "args": {
                "symbol": "str — nome do simbolo (obrigatorio)",
                "workspace": "str — diretorio de busca (default: '.')",
                "file_pattern": "str — glob de extensoes (default: '*.py')",
            },
        }

        # ── G15: LSP Tools ─────────────────────────────────────────────────────
        self._tools["lsp_hover"] = {
            "fn": self._lsp_hover,
            "description": "Retorna informacao hover (tipo, doc) para o simbolo na posicao linha:coluna via LSP.",
            "args": {
                "file": "str — caminho do arquivo (relativo ao workspace)",
                "line": "int — numero da linha (0-indexed)",
                "character": "int — numero da coluna (0-indexed)",
            },
        }
        self._tools["lsp_definitions"] = {
            "fn": self._lsp_definitions,
            "description": "Encontra onde um simbolo e definido via LSP (go-to-definition).",
            "args": {
                "file": "str — caminho do arquivo",
                "line": "int — linha do simbolo (0-indexed)",
                "character": "int — coluna do simbolo (0-indexed)",
            },
        }
        self._tools["lsp_references"] = {
            "fn": self._lsp_references,
            "description": "Lista todas as referencias ao simbolo na posicao dada via LSP.",
            "args": {
                "file": "str — caminho do arquivo",
                "line": "int — linha (0-indexed)",
                "character": "int — coluna (0-indexed)",
            },
        }
        self._tools["lsp_diagnostics"] = {
            "fn": self._lsp_diagnostics,
            "description": "Retorna erros e warnings do arquivo via LSP (type checking, lint).",
            "args": {
                "file": "str — caminho do arquivo para inspecionar",
            },
        }
        self._tools["lsp_workspace_symbols"] = {
            "fn": self._lsp_workspace_symbols,
            "description": "Busca símbolos (classes, funções, variáveis) em todo o workspace via LSP.",
            "args": {
                "query": "str — texto de busca parcial do símbolo",
            },
        }
        self._tools["lsp_completion"] = {
            "fn": self._lsp_completion,
            "description": "Retorna sugestões de autocompletar na posição dada via LSP.",
            "args": {
                "file": "str — caminho do arquivo",
                "line": "int — linha (0-indexed)",
                "character": "int — coluna (0-indexed)",
            },
        }
        self._tools["lsp_code_actions"] = {
            "fn": self._lsp_code_actions,
            "description": "Retorna ações de código (quick-fixes, refatorações) para um intervalo via LSP.",
            "args": {
                "file": "str — caminho do arquivo",
                "start_line": "int — linha inicial do intervalo (0-indexed)",
                "start_char": "int — coluna inicial (0-indexed)",
                "end_line": "int — linha final do intervalo (0-indexed)",
                "end_char": "int — coluna final (0-indexed)",
            },
        }
        self._tools["lsp_format"] = {
            "fn": self._lsp_format,
            "description": "Formata o arquivo usando o formatter do servidor LSP (textDocument/formatting).",
            "args": {
                "file": "str — caminho do arquivo a formatar",
                "tab_size": "int — tamanho do tab (padrão 4)",
                "insert_spaces": "bool — usar espaços ao invés de tabs (padrão true)",
            },
        }
        self._tools["lsp_rename"] = {
            "fn": self._lsp_rename,
            "description": "Renomeia um símbolo em todo o workspace via LSP (textDocument/rename).",
            "args": {
                "file": "str — caminho do arquivo onde o símbolo está",
                "line": "int — linha do símbolo (0-indexed)",
                "character": "int — coluna do símbolo (0-indexed)",
                "new_name": "str — novo nome para o símbolo",
            },
        }

    # --- API pública -----------------------------------------------------------

    def _is_tool_allowed_in_context(self, name: str, context: str | None = None) -> bool:
        context = _normalize_tool_context(context or self.tool_context)
        return self._tool_policy.allows(context, name)

    def _allowed_contexts_for_tool(self, name: str) -> list[str]:
        return self._tool_policy.allowed_contexts(name)

    def _record_tool_denied(self, name: str, args: dict) -> None:
        task_id = (
            os.environ.get("BAUER_KANBAN_TASK")
            or str(args.get("task_id") or args.get("id") or "000")
        )
        run_id = os.environ.get("BAUER_KANBAN_RUN_ID", "")
        claim_id = os.environ.get("BAUER_KANBAN_CLAIM_ID", "")
        try:
            from .kanban_store import KanbanStore

            store = KanbanStore(self.workspace)
            if run_id:
                store.update_run(
                    run_id,
                    metadata={
                        "last_denied_tool": name,
                        "last_denied_context": self.tool_context,
                    },
                )
            store.append_event(
                task_id,
                "tool.denied",
                actor=f"tool_router:{self.tool_context}",
                run_id=run_id,
                message=f"Tool '{name}' denied in context '{self.tool_context}'.",
                metadata={
                    "tool": name,
                    "context": self.tool_context,
                    "claim_id": claim_id,
                    "arg_keys": sorted(str(key) for key in args.keys()),
                },
            )
        except Exception:
            pass

    def available_tools(self) -> list[str]:
        """Retorna união de tools built-in e tools registradas externamente (ToolRegistry)."""
        built_in = set(self._tools.keys())
        try:
            from .tool_registry import ToolRegistry as _ToolRegistry
            external = set(_ToolRegistry.get().list_names())
        except ImportError:
            external = set()
        return sorted(name for name in (built_in | external) if self._is_tool_allowed_in_context(name))

    def tool_info(self, name: str) -> dict:
        # Verifica registry externo primeiro
        try:
            from .tool_registry import ToolRegistry as _ToolRegistry
            ext_def = _ToolRegistry.get().get_tool(name)
            if ext_def is not None:
                return {
                    "name": name,
                    "description": ext_def.description,
                    "args": ext_def.args,
                    "permission_level": ext_def.permission,
                    "risk_level": ext_def.risk,
                    "requires_approval": ext_def.requires_approval,
                    "source": "external",
                    "allowed_contexts": self._allowed_contexts_for_tool(name),
                    "context_allowed": self._is_tool_allowed_in_context(name),
                    "policy_source": self._tool_policy.source,
                }
        except ImportError:
            pass

        if name not in self._tools:
            raise ToolError(f"Tool desconhecida: '{name}'")
        info = self._tools[name]
        sec = _TOOL_SECURITY.get(name, {"permission": "read", "risk": "low", "approval": False})
        return {
            "name": name,
            "description": info["description"],
            "args": info["args"],
            "permission_level": sec["permission"],
            "risk_level": sec["risk"],
            "requires_approval": sec["approval"],
            "source": "builtin",
            "allowed_contexts": self._allowed_contexts_for_tool(name),
            "context_allowed": self._is_tool_allowed_in_context(name),
            "policy_source": self._tool_policy.source,
        }

    def tool_security(self, name: str) -> dict:
        """Retorna metadados de segurança de uma tool."""
        return _TOOL_SECURITY.get(name, {"permission": "read", "risk": "low", "approval": False})

    def reset_call_count(self) -> None:
        """Reseta o contador de chamadas (use no inicio de cada sessão de agent)."""
        self._tool_call_count = 0

    def set_context(self, messages: list[dict], max_messages: int = 6) -> None:
        """G4: Store recent conversation for LLM approval context."""
        self._recent_messages = messages[-max_messages:] if len(messages) > max_messages else list(messages)

    def _resolve_vision_client(self, tool: str):
        """Resolve o cliente para tools de visão (G18.4).

        Preferência: vision_client dedicado (auxiliary.vision_model) → confia
        na escolha explícita. Senão, usa o llm_client principal, mas só se o
        modelo parecer multimodal. Levanta ToolError claro e acionável quando
        não há cliente ou o modelo é text-only — em vez de mandar imagem pra um
        modelo de texto e receber lixo.
        """
        if self._vision_client is not None:
            return self._vision_client
        if self._llm_client is None:
            raise ToolError(
                f"{tool}: nenhum modelo de visao configurado.\n"
                "Configure auxiliary.vision_model no config.yaml "
                "(ex: ollama 'llava', ou gpt-4o/claude/gemini), ou rode num "
                "fluxo com llm_client."
            )
        model = getattr(self._llm_client, "model", "") or ""
        if not _looks_multimodal(model):
            raise ToolError(
                f"{tool}: o modelo ativo ('{model or 'desconhecido'}') nao parece "
                "suportar visao.\n"
                "Configure auxiliary.vision_model com um modelo multimodal "
                "(ex: ollama pull llava; ou gpt-4o/claude/gemini)."
            )
        return self._llm_client

    def _llm_single_turn(self, client, messages: list[dict]) -> str:
        """Chamada single-turn (sem tools) a um cliente LLM, via chat_stream.

        Helper central das tools que fazem UMA chamada direta ao modelo
        (vision_analyze, video_analyze, mixture_of_agents, browser_vision).

        IMPORTANTE: NÃO usar `run_one_turn` para isso. A assinatura dele é
        ``run_one_turn(ctx, router, client, model_name, ...)`` — não aceita uma
        lista de mensagens nem `tools=`. Chamá-lo como
        ``run_one_turn(client, messages, tools=None)`` quebra com TypeError, que
        era silenciosamente mascarado pelos `except Exception` dessas tools
        (deixando-as não-funcionais em produção, ainda que os testes passassem
        por mockar run_one_turn).
        """
        model = (
            getattr(client, "default_model", "")
            or getattr(client, "model", "")
            or self._model_name
            or ""
        )
        chunks = list(client.chat_stream(model, messages))
        return "".join(chunks)

    def get_tool_schemas(self) -> list[dict]:
        """Retorna schemas de tools no formato OpenAI function calling.

        Compatível com:
        - OpenAI (GPT-4o, GPT-4-turbo)
        - Groq, Mistral, Together AI, DeepSeek (OpenAI-compat)
        - GitHub Copilot / GitHub Models

        Uso:
            schemas = router.get_tool_schemas()
            # passar em chat_with_tools(model, messages, tools=schemas)
        """
        schemas: list[dict] = []
        for name, info in self._tools.items():
            if not self._is_tool_allowed_in_context(name):
                continue
            args_info = info.get("args", {})
            # Constrói properties do schema JSON
            properties: dict[str, dict] = {}
            required: list[str] = []
            for arg_name, arg_desc in args_info.items():
                desc_str = arg_desc if isinstance(arg_desc, str) else str(arg_desc)
                # Infere tipo a partir da descrição
                if desc_str.startswith("int") or "int —" in desc_str:
                    arg_type = "integer"
                elif desc_str.startswith("bool") or "bool —" in desc_str:
                    arg_type = "boolean"
                elif desc_str.startswith("dict") or "dict —" in desc_str:
                    arg_type = "object"
                else:
                    arg_type = "string"
                properties[arg_name] = {"type": arg_type, "description": desc_str}
                # Marca obrigatório se a descrição contém "(obrigatorio)"
                if "obrigatorio" in desc_str.lower():
                    required.append(arg_name)

            schemas.append({
                "type": "function",
                "function": {
                    "name": name,
                    "description": info.get("description", ""),
                    "parameters": {
                        "type": "object",
                        "properties": properties,
                        "required": required,
                    },
                },
            })
        # Wave 4.5: sanitise schemas before sending to LLM (fix nullable unions,
        # unwrap single-branch combinators, etc.)
        if _SCHEMA_SANITIZER_AVAILABLE:
            schemas = _sanitize_schemas(schemas)
        return schemas

    def execute_native_call(self, tool_name: str, tool_args: dict) -> str:
        """Executa uma tool call nativa (do native function calling).

        Aceita o nome da função e os argumentos já parseados (dict).
        Encaminha para execute() com o formato JSON padrão do Tool Bridge.
        """
        import json as _json
        action = {"action": tool_name, "args": tool_args}
        return self.execute(action)

    def execute(self, action_json: str | dict) -> str:
        """Parseia, valida e executa uma tool action.

        Aceita:
          - string JSON pura
          - string com bloco markdown ```json ... ```
          - dict já parsado

        Retorna resultado como string. Levanta ToolError ou SandboxError em falha.
        """
        action = self._parse(action_json)

        name = action.get("action", "")
        if not name:
            raise ToolError(
                "Campo 'action' ausente no JSON.\n"
                f"Exemplo: {{\"action\": \"list_dir\", \"args\": {{\"path\": \".\"}}}}"
            )

        # Resolve função — registry externo tem prioridade sobre built-ins
        _ext_fn = None
        try:
            from .tool_registry import ToolRegistry as _ToolRegistry
            _ext_def = _ToolRegistry.get().get_tool(name)
            if _ext_def is not None:
                _ext_fn = _ext_def.fn
        except ImportError:
            pass

        if _ext_fn is None and name not in self._tools:
            available = ", ".join(self.available_tools())
            raise ToolError(
                f"Tool desconhecida: '{name}'.\n"
                f"Disponiveis: {available}"
            )

        args = action.get("args", {})
        if not isinstance(args, dict):
            raise ToolError("Campo 'args' deve ser um objeto JSON.")

        if not self._is_tool_allowed_in_context(name):
            self._record_tool_denied(name, args)
            raise ToolError(
                f"tool denied: '{name}' nao permitido no contexto '{self.tool_context}'."
            )

        # LIMITS-001: enforça max_tool_calls por sessão
        self._tool_call_count += 1
        if self._tool_call_count > self._max_tool_calls:
            raise ToolError(
                f"Limite de {self._max_tool_calls} chamadas de tool por sessão atingido. "
                "Use reset_call_count() para iniciar nova sessão ou aumente max_tool_calls."
            )

        # Wave 4.5: command guards — HARDLINE always blocked; DANGEROUS denied
        # when no interactive approver is available (non-interactive mode).
        if _APPROVAL_AVAILABLE and name in {"run_command", "execute_code"}:
            _cmd = str(args.get("command", args.get("code", "")))
            _guard_dec = _check_command_guards(_cmd, yolo=False)
            if _guard_dec.action == "denied":
                raise ToolError(
                    f"[BLOCKED] {_guard_dec.scope.upper()}: {_guard_dec.reason}"
                )

        # SAFETY-002: modo dry_run — não executa side effects
        _DRY_RUN_SIDE_EFFECTS = frozenset({
            "write_file", "append_file", "patch", "delete_file", "move_file", "create_dir",
            "run_command", "execute_code", "web_fetch", "http_request",
            "browser_navigate", "browser_click", "browser_type", "browser_press",
            "browser_dialog", "browser_cdp",
            "image_generate", "text_to_speech", "delegate_task",
            "cronjob",  # apenas action=run
        })
        if self._dry_run and name in _DRY_RUN_SIDE_EFFECTS:
            sec = _TOOL_SECURITY.get(name, {})
            return str(DryRunResult(
                tool=name,
                summary=(
                    f"[{sec.get('permission','?')} / risco:{sec.get('risk','?')}] "
                    f"Teria executado com args: {json.dumps(args, ensure_ascii=False)[:200]}"
                ),
            ))

        # App Factory: gate de Spec-Driven Development + containment.
        #  - Containment: se há projeto ativo (app_factory_init com path), toda
        #    escrita deve ficar DENTRO da pasta da ideia — nada solto na raiz nem
        #    em pastas irmãs. Bloqueia com orientação de onde escrever.
        #  - Gate: em projetos governados, bloqueia CÓDIGO antes dos 7 docs de
        #    planejamento. Docs/, README e .env.example permanecem liberados.
        # Sem projeto ativo e sem governança, é no-op.
        _AF_GUARDED = {
            "write_file": "path", "append_file": "path", "patch": "path",
            "create_dir": "path", "move_file": "dst",
        }
        if name in _AF_GUARDED and not self._dry_run:
            _target = str(args.get(_AF_GUARDED[name], "") or "")
            if _target:
                try:
                    from pathlib import Path as _P
                    from . import app_factory as _af
                    # 1. Containment no projeto ativo (1 ideia = 1 pasta) —
                    #    usa o target cru (relativo ao workspace) p/ mensagem clara.
                    _ok, _why = _af.check_containment(self.workspace, _target)
                    # 2. Gate de planejamento, no projeto correto (ativo se houver,
                    #    senão a raiz). can_write_code resolve o target relativo ao
                    #    project_dir → passamos ABSOLUTO p/ evitar prefixo duplicado
                    #    quando o projeto é uma subpasta.
                    if _ok:
                        _active = _af.get_active_project(self.workspace)
                        _proj = _active if _active is not None else self.workspace
                        _tp = _P(_target)
                        _abs_target = _tp if _tp.is_absolute() else (_P(self.workspace) / _tp)
                        _ok, _why = _af.can_write_code(_proj, str(_abs_target))
                except Exception:  # noqa: BLE001 — nunca quebrar o fluxo por causa do gate
                    _ok, _why = True, ""
                if not _ok:
                    return f"[App Factory] {_why}"

        # G4: LLM approval for high-risk tools (fail-open if aux unavailable)
        _sec = _TOOL_SECURITY.get(name, {})
        if _sec.get("approval") and not self._dry_run:
            try:
                from .llm_approval import llm_evaluate_tool
                _approval = llm_evaluate_tool(name, args, self._recent_messages)
                if not _approval.approved:
                    return (
                        f"[LLM Approval Negado] A tool '{name}' foi rejeitada. "
                        f"Motivo: {_approval.reason}. "
                        + (f"Sugestão: {_approval.suggestion}" if _approval.suggestion else "")
                    )
            except Exception:
                pass  # approval nunca bloqueia por erro técnico

        # Plugin hooks — pre_tool_call
        try:
            from .plugin_hooks import hooks as _hooks
            _hooks.ensure_plugins_loaded()
            _hooks.emit("pre_tool_call", action=name, args=args)
        except Exception:
            pass  # hooks nunca bloqueiam execução

        # SEG-3: audit com medição de tempo
        from .audit_logger import audit_tool_call as _audit_ctx
        import time as _time
        _t0 = _time.monotonic()
        _audit_error: Exception | None = None

        try:
            # Per-tool timeout enforcement (Wave E)
            _timeout_sec = _TOOL_TIMEOUTS.get(name, 0)
            _fn = (_ext_fn if _ext_fn is not None else self._tools[name]["fn"])
            if name.startswith("browser_"):
                # G18: TODA browser tool roda na MESMA thread dedicada e persistente.
                # Playwright sync e thread-affine (greenlet): a pagina criada num
                # browser_navigate so pode ser dirigida pela thread que a criou.
                # Tools sem timeout configurado (browser_scroll/back/console/...)
                # NAO podem rodar inline na thread chamadora — senao da
                # "greenlet.error: Cannot switch to a different thread".
                import concurrent.futures as _cf
                _pool = self._get_browser_executor()
                _future = _pool.submit(_fn, args)
                _eff_timeout = _timeout_sec if _timeout_sec > 0 else 60
                try:
                    result = _future.result(timeout=_eff_timeout)
                except _cf.TimeoutError as _te:
                    raise ToolError(
                        f"Tool '{name}' excedeu o timeout de {_eff_timeout}s. "
                        "Tente novamente com uma operação mais simples ou aumente timeout_seconds em tools.yaml."
                    ) from _te
            elif _timeout_sec > 0:
                import concurrent.futures as _cf
                with _cf.ThreadPoolExecutor(max_workers=1) as _pool:
                    _future = _pool.submit(_fn, args)
                    try:
                        result = _future.result(timeout=_timeout_sec)
                    except _cf.TimeoutError as _te:
                        raise ToolError(
                            f"Tool '{name}' excedeu o timeout de {_timeout_sec}s. "
                            "Tente novamente com uma operação mais simples ou aumente timeout_seconds em tools.yaml."
                        ) from _te
            else:
                # Tool externa (ToolRegistry) tem prioridade sobre built-in
                if _ext_fn is not None:
                    result = _ext_fn(args)
                else:
                    result = self._tools[name]["fn"](args)
        except Exception as _exc:
            _audit_error = _exc
            _duration_ms = (_time.monotonic() - _t0) * 1000
            if self._audit is not None:
                self._audit.log_tool_call(
                    name, args,
                    status="error",
                    duration_ms=_duration_ms,
                    error_msg=str(_exc)[:300],
                )
            # Plugin hooks — post_tool_call (erro)
            try:
                from .plugin_hooks import hooks as _hooks
                _hooks.emit("post_tool_call", action=name, args=args, result=None, error=_exc)
            except Exception:
                pass
            raise

        _duration_ms = (_time.monotonic() - _t0) * 1000

        # Sanitiza surrogates antes de qualquer outra operação no resultado
        # (nomes de arquivo no Windows podem conter U+D800–U+DFFF via os.fsdecode)
        result = _sanitize_surrogates(result)

        # Escanear output de tools por segredos antes de retornar
        try:
            from .secrets_scanner import scan as _scan_secrets
            scan_result = _scan_secrets(result, redact=True)
            if scan_result.found:
                secrets_found = [m["name"] for m in scan_result.matches]
                logger.info(
                    "[secrets_scanner] Segredos detectados no output de '%s': %s. "
                    "Redagidos automaticamente.",
                    name, ", ".join(sorted(set(secrets_found))),
                )
                result = scan_result.redacted_text
        except Exception:
            pass  # scanner nunca bloqueia execução

        # Escanear output de tools por binários/shellcode suspeitos
        try:
            from .binary_scanner import scan as _scan_binary
            _bin_result = _scan_binary(result)
            if _bin_result.is_suspicious:
                logger.warning(
                    "[binary_scanner] Conteúdo suspeito no output de '%s': %s",
                    name, _bin_result.summary(),
                )
                if _bin_result.is_binary:
                    result = (
                        f"[binary_scanner] AVISO: output de '{name}' parece ser um executável binário "
                        f"({_bin_result.summary()}). Conteúdo suprimido por segurança."
                    )
        except Exception:
            pass  # scanner nunca bloqueia execução

        # SEG-3: audit de sucesso
        if self._audit is not None:
            self._audit.log_tool_call(
                name, args,
                status="ok",
                duration_ms=_duration_ms,
                result_preview=result[:200] if isinstance(result, str) else None,
            )

        # Plugin hooks — post_tool_call (sucesso)
        try:
            from .plugin_hooks import hooks as _hooks
            _hooks.emit("post_tool_call", action=name, args=args, result=result, error=None)
        except Exception:
            pass  # hooks nunca bloqueiam execução

        return result

    # --- sandbox ---------------------------------------------------------------

    @staticmethod
    def _check_within_workspace(resolved: Path, workspace_resolved: Path) -> None:
        """Verifica se resolved está dentro de workspace_resolved.
        Usa relative_to() (mais robusto que startswith em strings).
        """
        try:
            resolved.relative_to(workspace_resolved)
        except ValueError:
            raise SandboxError(
                f"Acesso negado: path resolve para fora do workspace.\n"
                f"  Workspace (raiz permitida): {workspace_resolved}\n"
                f"  Tentativa (fora do sandbox): {resolved}\n"
                f"Use apenas paths relativos DENTRO do workspace.\n"
                f"  Para listar o conteudo: list_dir com path='.'\n"
                f"  Para subdir: 'subdir' ou 'subdir/arquivo.py'\n"
                f"  '..' funciona se nao sair do workspace (ex: 'a/../b' → 'b')."
            )

    def _sandbox(self, path: str) -> Path:
        """Resolve path dentro do workspace. Bloqueia qualquer saída do sandbox.

        Proteção em duas camadas (inspirado em Hermes path_security.py):
          1. Normalização de paths absolutos gerados por modelos
          2. Verificação pós-resolve via relative_to() — bloqueio definitivo
             (cobre '..', symlinks, paths absolutos, e qualquer tentativa de fuga)

        Também normaliza paths absolutos que modelos frequentemente geram:
          /workspace/foo.txt  → foo.txt   (strip do prefixo workspace)
          /foo.txt            → foo.txt   (strip de / inicial — atalho de 1 componente)

        Note: `..` é PERMITIDO desde que o path resolvido fique dentro do workspace.
        Ex: 'subdir/../outro' → 'outro' (válido).
        Ex: '../fora_do_workspace' → BLOQUEADO pela Camada 2.
        """

        # --- Camada 2: normalização de paths absolutos --------------------------
        ws_name = self.workspace.name
        p_raw = Path(path)
        ws_resolved = self.workspace.resolve()

        if p_raw.is_absolute():
            non_root_parts = p_raw.parts[1:]  # remove '/' ou 'C:\' inicial

            if non_root_parts and non_root_parts[0] == ws_name:
                # Caso: /workspace_name/rest → tratar como caminho relativo 'rest'
                path = "/".join(non_root_parts[1:]) if len(non_root_parts) > 1 else "."
            elif len(non_root_parts) <= 1:
                # Caso: /filename.txt → strip '/' e tratar como relativo
                path = non_root_parts[0] if non_root_parts else "."
            else:
                # Caminho absoluto com múltiplos componentes fora do workspace
                try:
                    resolved = p_raw.resolve()
                except Exception as exc:
                    raise SandboxError(f"Path invalido: '{path}': {exc}") from exc
                self._check_within_workspace(resolved, ws_resolved)
                return resolved
        else:
            # Caminho relativo: normaliza /workspace/ ou \workspace\ que o modelo adiciona
            normalized = path.lstrip("/\\")
            if normalized == ws_name or normalized.startswith(ws_name + "/") or normalized.startswith(ws_name + "\\"):
                normalized = normalized[len(ws_name):].lstrip("/\\")
            path = normalized or "."

        # --- Camada 3: resolve + relative_to() ----------------------------------
        try:
            resolved = (self.workspace / path).resolve()
        except Exception as exc:
            raise SandboxError(f"Path invalido: '{path}': {exc}") from exc

        self._check_within_workspace(resolved, ws_resolved)
        return resolved

    # --- parser ----------------------------------------------------------------

    def _parse(self, action_json: str | dict) -> dict:
        if isinstance(action_json, dict):
            return action_json

        text = action_json.strip()

        # Extrai JSON de bloco markdown ```json ... ``` ou ``` ... ```
        m = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
        if m:
            text = m.group(1).strip()

        try:
            result = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ToolError(
                f"JSON invalido: {exc}\n"
                f"Entrada (primeiros 200 chars): {action_json[:200]}"
            ) from exc

        if not isinstance(result, dict):
            raise ToolError("A action JSON deve ser um objeto ({{...}}), nao lista ou valor simples.")

        return result

    # --- exec → bauer/tools/execution.py | fs → fs.py | web → web.py ------------

    # --- patch -----------------------------------------------------------------

    def _patch_file(self, args: dict) -> str:
        """Substituição cirúrgica: old_string → new_string.

        Boas práticas implementadas:
        - Falha se old_string não for encontrado (evita edição silenciosa errada)
        - Falha se houver mais de 1 ocorrência (ambíguo → exige especificidade)
        - Retorna diff compacto para rastreabilidade
        """
        path = args.get("path")
        old_string = args.get("old_string")
        new_string = args.get("new_string", "")

        if not path:
            raise ToolError("patch requer 'path'.")
        if old_string is None:
            raise ToolError("patch requer 'old_string'.")

        p = self._sandbox(str(path))
        if not p.exists():
            raise ToolError(f"Arquivo nao encontrado: '{path}'")
        if p.is_dir():
            raise ToolError(f"'{path}' e um diretorio — use em arquivos.")

        # Nota (G17.2): patch NAO exige read_file previo — o match exato e unico
        # de old_string ja e o gate (nao da pra editar as cegas: se nao bater,
        # falha). Read-before-write fica so no write_file overwrite (sem gate).

        try:
            original = p.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            raise ToolError(f"'{path}' parece ser arquivo binario — patch so funciona em texto.")

        count = original.count(old_string)
        if count == 0:
            raise ToolError(
                f"Trecho nao encontrado em '{path}'.\n"
                "Verifique espacos, indentacao e quebras de linha exatas."
            )
        if count > 1:
            raise ToolError(
                f"Trecho encontrado {count} vezes em '{path}' — ambiguo.\n"
                "Inclua mais contexto em 'old_string' para tornar a substituicao unica."
            )

        updated = original.replace(old_string, new_string, 1)
        p.write_text(updated, encoding="utf-8")
        self._mark_written(p)  # G17.2: conteudo mudou, modelo viu o diff

        # Diff compacto para rastreabilidade
        diff_lines = list(difflib.unified_diff(
            original.splitlines(keepends=True),
            updated.splitlines(keepends=True),
            fromfile=f"a/{path}",
            tofile=f"b/{path}",
            n=2,
        ))
        diff_str = "".join(diff_lines[:40])  # máx 40 linhas de diff
        if len(diff_lines) > 40:
            diff_str += f"\n... (+{len(diff_lines) - 40} linhas)"

        result = f"Arquivo '{path}' atualizado.\n{diff_str}"
        syntax_err = _syntax_check(p, updated)
        if syntax_err:
            result += (
                f"\n[ATENÇÃO — erro de sintaxe detectado] {syntax_err}\n"
                "O patch foi aplicado mas quebrou o arquivo — corrija antes de usar."
            )
        return result

    # --- todo ------------------------------------------------------------------

    def _todo(self, args: dict) -> str:
        """Lista de tarefas da sessão (in-memory, não persiste)."""
        action = str(args.get("action", "")).lower()
        if not action:
            raise ToolError("todo requer 'action': add | list | done | remove | clear.")

        if action == "add":
            text = args.get("text", "").strip()
            if not text:
                raise ToolError("todo add requer 'text'.")
            item = {"id": self._todo_next_id, "text": text, "done": False}
            self._todo_items.append(item)
            self._todo_next_id += 1
            return f"[{item['id']}] Adicionado: {text}"

        elif action == "list":
            if not self._todo_items:
                return "Lista de tarefas vazia."
            lines = ["Tarefas da sessao:"]
            for item in self._todo_items:
                mark = "✓" if item["done"] else "○"
                lines.append(f"  [{item['id']}] {mark} {item['text']}")
            done = sum(1 for i in self._todo_items if i["done"])
            lines.append(f"\n{done}/{len(self._todo_items)} concluidas.")
            return "\n".join(lines)

        elif action == "done":
            item_id = args.get("id")
            if item_id is None:
                raise ToolError("todo done requer 'id'.")
            try:
                item_id = int(item_id)
            except (ValueError, TypeError):
                raise ToolError("todo: 'id' deve ser um numero inteiro.")
            for item in self._todo_items:
                if item["id"] == item_id:
                    item["done"] = True
                    return f"[{item_id}] Marcado como concluido: {item['text']}"
            raise ToolError(f"Tarefa {item_id} nao encontrada.")

        elif action == "remove":
            item_id = args.get("id")
            if item_id is None:
                raise ToolError("todo remove requer 'id'.")
            try:
                item_id = int(item_id)
            except (ValueError, TypeError):
                raise ToolError("todo: 'id' deve ser um numero inteiro.")
            before = len(self._todo_items)
            self._todo_items = [i for i in self._todo_items if i["id"] != item_id]
            if len(self._todo_items) == before:
                raise ToolError(f"Tarefa {item_id} nao encontrada.")
            return f"Tarefa {item_id} removida."

        elif action == "clear":
            count = len(self._todo_items)
            self._todo_items = []
            self._todo_next_id = 1
            return f"Lista limpa. {count} tarefa(s) removida(s)."

        else:
            raise ToolError(f"Acao desconhecida: '{action}'. Use: add | list | done | remove | clear.")

    # --- memory/session/skills/mcp → bauer/tools/{memory,session,skills,mcp}.py -

    # --- clarify ---------------------------------------------------------------

    def _clarify(self, args: dict) -> str:
        """Pergunta ao usuário e retorna resposta.

        Em modo interativo: usa input() para ler do terminal.
        Em modo não-interativo (sem TTY): retorna placeholder com a pergunta.

        Boas práticas:
        - Não bloqueia indefinidamente (timeout de 300s)
        - Choices: valida que a resposta é uma das opções (se fornecidas)
        - Não-interativo: retorna a pergunta para que o caller decida
        """
        import sys

        question = args.get("question", "").strip()
        if not question:
            raise ToolError("clarify requer 'question'.")

        raw_choices = args.get("choices", "")
        choices: list[str] = []
        if raw_choices:
            choices = [c.strip() for c in str(raw_choices).split("|") if c.strip()]

        # Modo não-interativo (pipe, CI, etc.)
        if not sys.stdin.isatty():
            choices_hint = f" [{' / '.join(choices)}]" if choices else ""
            return (
                f"[clarify — aguardando input do usuario]\n"
                f"Pergunta: {question}{choices_hint}\n"
                f"(Forneça a resposta no proximo turno da conversa.)"
            )

        # Modo interativo
        choices_hint = f" [{' / '.join(choices)}]" if choices else ""
        prompt = f"\n🤔 {question}{choices_hint}\n> "

        try:
            import signal

            def _timeout_handler(signum, frame):
                raise TimeoutError

            # Timeout de 5 minutos para não bloquear indefinidamente
            try:
                signal.signal(signal.SIGALRM, _timeout_handler)
                signal.alarm(300)
                answer = input(prompt).strip()
                signal.alarm(0)
            except AttributeError:
                # Windows não tem SIGALRM — usa input sem timeout
                answer = input(prompt).strip()

        except (KeyboardInterrupt, TimeoutError, EOFError):
            return "[clarify] Sem resposta do usuario (timeout/cancelado)."

        if not answer:
            return "[clarify] Resposta vazia."

        if choices:
            choices_lower = [c.lower() for c in choices]
            if answer.lower() not in choices_lower:
                return (
                    f"[clarify] Resposta '{answer}' invalida. "
                    f"Esperado: {' | '.join(choices)}"
                )

        return answer

    # --- vision_analyze --------------------------------------------------------

    def _vision_analyze(self, args: dict) -> str:
        """Analisa imagem via modelo multimodal (OpenAI vision format).

        Boas práticas:
        - Suporta URL externa (passa diretamente) e path local (base64)
        - Detecta formato da imagem por extensão/magic bytes
        - Requer llm_client com suporte a chat multimodal
        - Fallback: usa httpx para chamar API OpenAI-compat diretamente
        """
        import base64

        image = args.get("image", "").strip()
        query = args.get("query", "").strip()

        if not image:
            raise ToolError("vision_analyze requer 'image' (URL ou path).")
        if not query:
            raise ToolError("vision_analyze requer 'query'.")

        # Determina se é URL ou path local
        if image.startswith(("http://", "https://")):
            image_content = {"type": "image_url", "image_url": {"url": image}}
        else:
            # Path local — lê e base64-encoda
            p = self._sandbox(image)
            if not p.exists():
                raise ToolError(f"Imagem nao encontrada: '{image}'")

            raw = p.read_bytes()
            ext = p.suffix.lower().lstrip(".")
            mime_map = {
                "jpg": "image/jpeg", "jpeg": "image/jpeg",
                "png": "image/png", "gif": "image/gif",
                "webp": "image/webp", "bmp": "image/bmp",
            }
            mime = mime_map.get(ext, "image/jpeg")
            b64 = base64.b64encode(raw).decode("ascii")
            data_url = f"data:{mime};base64,{b64}"
            image_content = {"type": "image_url", "image_url": {"url": data_url}}

        # Mensagem no formato OpenAI multimodal
        message = {
            "role": "user",
            "content": [
                {"type": "text", "text": query},
                image_content,
            ],
        }

        # G18.4: usa o cliente de visão resolvido (vision_model dedicado ou
        # llm_client principal se multimodal). Erro claro e acionável se nenhum.
        vision_client = self._resolve_vision_client("vision_analyze")
        try:
            return self._llm_single_turn(vision_client, [message])
        except ToolError:
            raise
        except Exception as exc:
            raise ToolError(f"vision_analyze: erro ao chamar modelo: {exc}")

    # --- channel_send / channel_list — Bauer Gateway ----------------------------

    def _send_message(self, args: dict) -> str:
        """Envia mensagem direto pelo bridge vivo do gateway (ou outbox).

        Diferença para channel_send: aqui o destino é um chat_id REAL de um
        canal inbound (telegram/discord). Com o gateway no mesmo processo a
        entrega é imediata, incluindo mídia. Sem gateway vivo, enfileira no
        outbox durável para o próximo `bauer gateway start`.
        """
        channel = str(args.get("channel", "")).strip().lower()
        chat_id = str(args.get("chat_id", "")).strip()
        text = str(args.get("text", "")).strip()
        media_path = str(args.get("media_path", "")).strip()
        if not channel:
            raise ToolError("send_message requer 'channel' (telegram/discord).")
        if not chat_id:
            raise ToolError("send_message requer 'chat_id' (id do chat destino).")
        if not text and not media_path:
            raise ToolError("send_message requer 'text' e/ou 'media_path'.")

        from . import live_bridges
        bridge = live_bridges.get(channel)
        if bridge is not None:
            sent: list[str] = []
            if text:
                bridge.send_text(chat_id, text)
                sent.append("texto")
            if media_path:
                send_media = getattr(bridge, "send_media", None)
                if send_media is None:
                    raise ToolError(f"Canal '{channel}' não suporta envio de mídia.")
                if not send_media(chat_id, media_path):
                    raise ToolError(f"Falha enviando mídia '{media_path}' via {channel}.")
                sent.append("mídia")
            return f"Mensagem ({' + '.join(sent)}) entregue em {channel}:{chat_id}."

        # Gateway não está neste processo — outbox durável
        from .gateway_outbox import GatewayOutbox
        payload: dict = {"text": text, "source": "send_message"}
        if media_path:
            payload["media_path"] = media_path
        message = GatewayOutbox(self.workspace).enqueue(
            channel=channel, target=chat_id, payload=payload, metadata={},
        )
        return (
            f"Gateway não está rodando neste processo — mensagem enfileirada "
            f"(id={message.message_id}); será entregue quando `bauer gateway start` subir."
        )

    def _transcribe_audio(self, args: dict) -> str:
        """Transcreve áudio para texto (Whisper Groq/OpenAI)."""
        path = str(args.get("path", "")).strip()
        if not path:
            raise ToolError("transcribe_audio requer 'path'.")
        from .transcription import transcribe_audio
        result = transcribe_audio(path)
        if not result.get("success"):
            raise ToolError(f"Transcrição falhou: {result.get('error')}")
        return f"[{result.get('provider')}] {result['transcript']}"

    # --- code-intel + LSP → bauer/tools/code_intel.py --------------------------

    # ==========================================================================
    # MIXTURE_OF_AGENTS
    # ==========================================================================

    def _mixture_of_agents(self, args: dict) -> str:
        """Consulta múltiplos agentes em paralelo com perspectivas diferentes.

        Arquitetura (Mixture of Agents — Li et al., 2024):
          1. Para cada perspectiva: cria prompt especializado + chama LLM em paralelo
          2. Coleta todas as respostas
          3. Se synthesize=true: passada final de síntese combinando os insights
          4. Retorna respostas individuais + síntese

        Sem llm_client: simula perspectivas via prompts diferentes no mesmo modelo.
        """
        from concurrent.futures import ThreadPoolExecutor, as_completed

        query = str(args.get("query", "")).strip()
        if not query:
            raise ToolError("mixture_of_agents requer 'query'.")

        if self._llm_client is None:
            raise ToolError(
                "mixture_of_agents requer llm_client configurado.\n"
                "O agente precisa estar rodando com um provider LLM ativo."
            )

        raw_perspectives = str(args.get("perspectives", "analitico|critico|criativo|pragmatico"))
        perspectives = [p.strip() for p in raw_perspectives.split("|") if p.strip()]
        synthesize = str(args.get("synthesize", "true")).lower() != "false"

        # Prompts de sistema por perspectiva
        persona_prompts = {
            "analitico": "Você é um analista sistemático. Decomponha o problema em partes, identifique causas e efeitos, use dados e lógica.",
            "critico": "Você é um crítico rigoroso. Identifique falhas, riscos, suposições incorretas e pontos fracos na situação.",
            "criativo": "Você é um pensador criativo. Proponha soluções inovadoras, faça conexões inesperadas, pense fora do padrão.",
            "pragmatico": "Você é um executor pragmático. Foque em ações concretas, priorize pelo impacto, considere recursos e tempo.",
            "especialista": "Você é um especialista de domínio. Aplique conhecimento técnico profundo e melhores práticas da área.",
            "cético": "Você é um questionador cético. Questione premissas, peça evidências, desafie conclusões.",
            "otimista": "Você é um estrategista otimista. Identifique oportunidades, vantagens e cenários positivos.",
            "sistemico": "Você pensa sistemicamente. Considere interdependências, efeitos de segunda ordem e o contexto maior.",
        }

        def _call_perspective(perspective: str) -> tuple[str, str]:
            system = persona_prompts.get(
                perspective.lower(),
                f"Você é um especialista com perspectiva '{perspective}'. Analise o problema sob esse ângulo.",
            )
            messages = [
                {"role": "system", "content": system},
                {"role": "user", "content": query},
            ]
            try:
                response = self._llm_single_turn(self._llm_client, messages)
                return perspective, str(response)
            except Exception as exc:
                return perspective, f"[erro: {exc}]"

        # Executa perspectivas em paralelo
        individual: list[tuple[str, str]] = []
        max_workers = min(len(perspectives), 6)
        with ThreadPoolExecutor(max_workers=max_workers) as ex:
            futures = {ex.submit(_call_perspective, p): p for p in perspectives}
            for f in as_completed(futures):
                individual.append(f.result())

        # Ordena pela ordem original das perspectivas
        order = {p: i for i, p in enumerate(perspectives)}
        individual.sort(key=lambda x: order.get(x[0], 99))

        # Monta output das respostas individuais
        lines = [f"[mixture_of_agents] Query: {query[:100]}\n"]
        lines.append(f"Perspectivas ({len(individual)}):")
        for perspective, response in individual:
            resp_preview = response[:400].strip()
            if len(response) > 400:
                resp_preview += "\n  ..."
            lines.append(f"\n── [{perspective.upper()}] ──")
            lines.append(resp_preview)

        # Passada de síntese
        if synthesize and len(individual) >= 2:
            synthesis_context = "\n\n".join(
                f"[{p.upper()}]: {r[:300]}" for p, r in individual
            )
            synthesis_prompt = (
                f"Você recebeu análises de {len(individual)} perspectivas diferentes "
                f"sobre a seguinte questão:\n\n{query}\n\n"
                f"Análises:\n{synthesis_context}\n\n"
                f"Sintetize os insights mais valiosos de cada perspectiva em uma "
                f"resposta integrada e acionável. Seja conciso e direto."
            )
            try:
                synthesis = self._llm_single_turn(
                    self._llm_client,
                    [{"role": "user", "content": synthesis_prompt}],
                )
                lines.append("\n── [SÍNTESE] ──")
                lines.append(str(synthesis)[:600])
            except Exception as exc:
                lines.append(f"\n[síntese falhou: {exc}]")

        return "\n".join(lines)

    # ==========================================================================
    # VIDEO_ANALYZE
    # ==========================================================================

    def _video_analyze(self, args: dict) -> str:
        """Analisa vídeo por URL ou arquivo local.

        Estratégias (em ordem de preferência):
          1. URL + provider nativo (Gemini, GPT-4o com video):
             passa a URL diretamente como conteúdo de mídia
          2. Arquivo local — extrai frames-chave via cv2 (se disponível)
             ou via iteração de bytes em formato simples
          3. Fallback — analisa apenas o primeiro frame como imagem

        Boas práticas:
          - max_frames limita custo (default 5)
          - Frames espaçados uniformemente ao longo do vídeo
          - Síntese final combina análises dos frames individuais
        """
        video = str(args.get("video", "")).strip()
        query = str(args.get("query", "")).strip()
        max_frames = int(args.get("max_frames", 5))
        max_frames = max(1, min(max_frames, 20))

        if not video:
            raise ToolError("video_analyze requer 'video' (URL ou path).")
        if not query:
            raise ToolError("video_analyze requer 'query'.")
        # G18.4: o gate de visão (modelo dedicado ou principal multimodal) é
        # aplicado nos helpers, no ponto da chamada ao modelo — depois da
        # validação de formato/dependências, para que erros de input venham
        # antes do erro de capability.

        # ── Estratégia 1: URL → provider nativo ─────────────────────────────
        if video.startswith(("http://", "https://")):
            return self._video_analyze_url(video, query)

        # ── Estratégia 2: arquivo local → extração de frames ────────────────
        p = self._sandbox(video)
        if not p.exists():
            raise ToolError(f"Video nao encontrado: '{video}'")

        ext = p.suffix.lower()
        if ext not in (".mp4", ".avi", ".mov", ".mkv", ".webm", ".gif", ".m4v"):
            raise ToolError(
                f"Formato '{ext}' nao suportado. "
                "Suportados: .mp4, .avi, .mov, .mkv, .webm, .gif"
            )

        # Tenta cv2 primeiro (mais preciso)
        if _package_available("cv2"):
            return self._video_analyze_cv2(p, query, max_frames)

        # Fallback: tenta PIL/Pillow para GIFs animados
        if ext == ".gif" and _package_available("PIL"):
            return self._video_analyze_gif_pil(p, query, max_frames)

        raise ToolError(
            "video_analyze para arquivos locais requer OpenCV ou PIL instalado.\n"
            "Instale com: pip install opencv-python\n"
            "Ou use uma URL pública para análise via provider."
        )

    def _video_analyze_url(self, url: str, query: str) -> str:
        """Passa URL de vídeo diretamente ao LLM (Gemini, GPT-4o vision)."""
        # Formato OpenAI-compat para vídeo via URL
        message = {
            "role": "user",
            "content": [
                {"type": "text", "text": query},
                {
                    "type": "image_url",
                    "image_url": {"url": url},
                },
            ],
        }
        try:
            result = self._llm_single_turn(self._resolve_vision_client("video_analyze"), [message])
            return f"[video_analyze — URL]\n{result}"
        except Exception as exc:
            raise ToolError(
                f"video_analyze: erro ao analisar URL via provider: {exc}\n"
                "Verifique se seu provider suporta análise de vídeo por URL "
                "(Gemini suporta, OpenAI gpt-4o ainda não)."
            )

    def _video_analyze_cv2(self, path: Path, query: str, max_frames: int) -> str:
        """Extrai frames-chave via cv2 e analisa cada um."""
        import cv2
        import base64
        import tempfile

        cap = cv2.VideoCapture(str(path))
        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        fps = cap.get(cv2.CAP_PROP_FPS) or 25
        duration_s = total_frames / fps if fps > 0 else 0

        if total_frames <= 0:
            cap.release()
            raise ToolError(f"Nao foi possivel ler frames de '{path.name}'.")

        # Índices uniformemente distribuídos
        indices = [int(i * total_frames / max_frames) for i in range(max_frames)]

        frame_analyses: list[str] = []
        for idx in indices:
            cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
            ret, frame = cap.read()
            if not ret:
                continue
            timestamp_s = idx / fps if fps > 0 else 0

            # Encode frame como JPEG em memória
            ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 85])
            if not ok:
                continue
            b64 = base64.b64encode(buf.tobytes()).decode("ascii")
            data_url = f"data:image/jpeg;base64,{b64}"

            try:
                from .agent import run_one_turn
                frame_query = (
                    f"Frame do vídeo '{path.name}' em {timestamp_s:.1f}s "
                    f"(de {duration_s:.1f}s total).\n{query}"
                )
                msg = {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": frame_query},
                        {"type": "image_url", "image_url": {"url": data_url}},
                    ],
                }
                resp = self._llm_single_turn(self._resolve_vision_client("video_analyze"), [msg])
                frame_analyses.append(f"[{timestamp_s:.1f}s] {str(resp)[:300]}")
            except Exception as exc:
                frame_analyses.append(f"[{timestamp_s:.1f}s] [erro: {exc}]")

        cap.release()

        if not frame_analyses:
            raise ToolError("Nao foi possivel extrair ou analisar nenhum frame.")

        # Síntese final
        lines = [
            f"[video_analyze] '{path.name}' — {duration_s:.1f}s, {max_frames} frames\n"
        ]
        lines.append("Análise por frame:")
        lines.extend(f"  {a}" for a in frame_analyses)

        if len(frame_analyses) > 1:
            try:
                synthesis_input = "\n".join(frame_analyses)
                synth_msg = {
                    "role": "user",
                    "content": (
                        f"Você analisou {len(frame_analyses)} frames do vídeo '{path.name}'. "
                        f"Pergunta original: {query}\n\n"
                        f"Análises dos frames:\n{synthesis_input}\n\n"
                        "Sintetize uma resposta final coerente sobre o vídeo completo."
                    ),
                }
                synthesis = self._llm_single_turn(self._resolve_vision_client("video_analyze"), [synth_msg])
                lines.append("\nSíntese:")
                lines.append(str(synthesis)[:600])
            except Exception:
                pass

        return "\n".join(lines)

    def _video_analyze_gif_pil(self, path: Path, query: str, max_frames: int) -> str:
        """Analisa GIF animado extraindo frames via PIL."""
        import base64
        from io import BytesIO
        from PIL import Image

        gif = Image.open(path)
        total = getattr(gif, "n_frames", 1)
        indices = [int(i * total / max_frames) for i in range(min(max_frames, total))]

        frame_analyses: list[str] = []
        for idx in indices:
            gif.seek(idx)
            buf = BytesIO()
            frame = gif.convert("RGB")
            frame.save(buf, format="JPEG", quality=85)
            b64 = base64.b64encode(buf.getvalue()).decode("ascii")
            data_url = f"data:image/jpeg;base64,{b64}"

            try:
                msg = {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": f"Frame {idx}/{total} do GIF. {query}"},
                        {"type": "image_url", "image_url": {"url": data_url}},
                    ],
                }
                resp = self._llm_single_turn(self._resolve_vision_client("video_analyze"), [msg])
                frame_analyses.append(f"[frame {idx}] {str(resp)[:300]}")
            except Exception as exc:
                frame_analyses.append(f"[frame {idx}] [erro: {exc}]")

        lines = [f"[video_analyze] GIF '{path.name}' — {total} frames\n"]
        lines.extend(f"  {a}" for a in frame_analyses)
        return "\n".join(lines)

    # =========================================================================
    # Wave 6 — Skills, Process, Media, Kanban, Browser
    # =========================================================================

    # ── Constantes ────────────────────────────────────────────────────────────

    _SKILLS_FILE = ".bauer_skills.json"
    _KANBAN_FILE = ".bauer_kanban.json"  # legacy file; TASKS.md is now authoritative.
    _KANBAN_TO_WORKSPACE_STATUS = {
        "todo": "TODO",
        "ready": "READY",
        "in_progress": "IN_PROGRESS",
        "blocked": "BLOCKED",
        "failed": "FAILED",
        "done": "DONE",
    }
    _WORKSPACE_TO_KANBAN_STATUS = {
        "TODO": "todo",
        "READY": "ready",
        "IN_PROGRESS": "in_progress",
        "BLOCKED": "blocked",
        "FAILED": "failed",
        "DONE": "done",
    }
    _KANBAN_PRIORITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3}

    # =========================================================================
    # Skills system
    # =========================================================================

    # =========================================================================
    # App Factory (Spec-Driven Development)
    # =========================================================================

    # =========================================================================
    # Process manager
    # =========================================================================

    def _process(self, args: dict) -> str:
        import subprocess
        import threading

        action = str(args.get("action", "")).strip().lower()
        if not action:
            raise ToolError("process: 'action' é obrigatório (start|list|poll|log|kill|write).")

        # ── start ─────────────────────────────────────────────────────────────
        if action == "start":
            command = args.get("command")
            if not command:
                raise ToolError("process: 'command' é obrigatório para action=start.")
            label = str(args.get("label", str(command)[:40]))
            try:
                proc = subprocess.Popen(
                    command,
                    shell=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    stdin=subprocess.PIPE,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    cwd=str(self.workspace),
                )
            except Exception as exc:
                raise ToolError(f"process start: falha ao iniciar — {exc}") from exc

            pid_str = str(proc.pid)
            stdout_buf: list[str] = []
            stderr_buf: list[str] = []

            def _reader(stream, buf):
                try:
                    for line in stream:
                        buf.append(line)
                except Exception:
                    pass

            threading.Thread(target=_reader, args=(proc.stdout, stdout_buf), daemon=True).start()
            threading.Thread(target=_reader, args=(proc.stderr, stderr_buf), daemon=True).start()

            self._processes[pid_str] = {
                "proc": proc,
                "label": label,
                "command": str(command),
                "stdout_buf": stdout_buf,
                "stderr_buf": stderr_buf,
            }
            return f"[process] Iniciado '{label}' — PID {pid_str}"

        # ── list ──────────────────────────────────────────────────────────────
        if action == "list":
            if not self._processes:
                return "[process] Nenhum processo em andamento."
            lines = [f"[process] {len(self._processes)} processo(s):"]
            for pid, info in self._processes.items():
                proc = info["proc"]
                rc = proc.poll()
                status = f"exit:{rc}" if rc is not None else "running"
                lines.append(f"  PID {pid} [{status}] {info['label']}")
            return "\n".join(lines)

        # ── operações por PID ─────────────────────────────────────────────────
        _valid_actions = ("start", "list", "poll", "log", "kill", "write")
        if action not in _valid_actions:
            raise ToolError(f"process: action '{action}' inválida. Use {' | '.join(_valid_actions)}.")

        pid = str(args.get("pid", "")).strip()
        if not pid:
            raise ToolError(f"process: 'pid' é obrigatório para action={action}.")
        if pid not in self._processes:
            raise ToolError(f"process: PID '{pid}' não encontrado. Use action=list para ver ativos.")

        info = self._processes[pid]
        proc = info["proc"]

        if action == "poll":
            rc = proc.poll()
            if rc is None:
                return f"[process] PID {pid} '{info['label']}' — running"
            del self._processes[pid]
            return f"[process] PID {pid} '{info['label']}' — finalizado com exit:{rc}"

        if action == "log":
            max_lines = int(args.get("max_lines", 50))
            stdout_lines = info["stdout_buf"][-max_lines:]
            stderr_lines = info["stderr_buf"][-max_lines:]
            out = "".join(stdout_lines) or "(vazio)"
            err = "".join(stderr_lines) or "(vazio)"
            return (
                f"[process] PID {pid} '{info['label']}'\n"
                f"─── stdout ───\n{out}\n"
                f"─── stderr ───\n{err}"
            )

        if action == "kill":
            try:
                proc.terminate()
                proc.wait(timeout=3)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
            del self._processes[pid]
            return f"[process] PID {pid} '{info['label']}' encerrado."

        if action == "write":
            text = args.get("input")
            if text is None:
                raise ToolError("process write: 'input' é obrigatório.")
            if proc.poll() is not None:
                raise ToolError(f"process write: PID {pid} já finalizou.")
            try:
                proc.stdin.write(str(text))
                proc.stdin.flush()
            except Exception as exc:
                raise ToolError(f"process write: falha — {exc}") from exc
            return f"[process] Enviado para PID {pid}: {str(text)[:80]}"

        raise ToolError(f"process: action '{action}' inválida. Use start|list|poll|log|kill|write.")

    # =========================================================================
    # Geração de mídia
    # =========================================================================

    def _image_generate(self, args: dict) -> str:
        prompt = str(args.get("prompt", "")).strip()
        if not prompt:
            raise ToolError("image_generate: 'prompt' é obrigatório.")
        if self._llm_client is None:
            raise ToolError("image_generate: llm_client não configurado.")

        model = str(args.get("model", "dall-e-3"))
        size = str(args.get("size", "1024x1024"))
        quality = str(args.get("quality", "standard"))
        output_file = args.get("output_file")

        valid_models = ("dall-e-3", "dall-e-2")
        if model not in valid_models:
            raise ToolError(f"image_generate: model deve ser {valid_models}.")
        valid_sizes = ("1024x1024", "1792x1024", "1024x1792", "512x512", "256x256")
        if size not in valid_sizes:
            raise ToolError(f"image_generate: size deve ser um de {valid_sizes}.")

        try:
            # Descobre qual objeto tem .images.generate:
            # 1) self._llm_client (caso mock direto — não precisa de openai instalado)
            # 2) self._llm_client._client (caso wrapper bauer sobre openai.OpenAI)
            # 3) cria openai.OpenAI com credenciais (exige openai instalado)
            _lc = self._llm_client
            if hasattr(_lc, "images") and callable(getattr(getattr(_lc, "images", None), "generate", None)):
                client_obj = _lc
            elif hasattr(getattr(_lc, "_client", None), "images"):
                client_obj = _lc._client
            else:
                try:
                    import openai
                except ImportError:
                    raise ToolError("image_generate: requer 'pip install openai'.")
                base_url = getattr(_lc, "base_url", None) or "https://api.openai.com/v1"
                api_key = getattr(_lc, "api_key", None) or ""
                client_obj = openai.OpenAI(api_key=api_key, base_url=base_url)

            kw: dict = {"model": model, "prompt": prompt, "size": size, "n": 1}
            if model == "dall-e-3":
                kw["quality"] = quality
            response = client_obj.images.generate(**kw)
            img_url = response.data[0].url
        except ToolError:
            raise
        except Exception as exc:
            raise ToolError(f"image_generate: falha na API — {exc}") from exc

        result = f"[image_generate] Imagem gerada:\n  URL: {img_url}"

        if output_file:
            try:
                import httpx
                dest = self._sandbox(output_file)
                dest.parent.mkdir(parents=True, exist_ok=True)
                r = httpx.get(img_url, timeout=30)
                r.raise_for_status()
                dest.write_bytes(r.content)
                result += f"\n  Salvo em: {dest.relative_to(self.workspace)}"
            except Exception as exc:
                result += f"\n  Aviso: falha ao salvar — {exc}"

        return result

    def _text_to_speech(self, args: dict) -> str:
        text = str(args.get("text", "")).strip()
        if not text:
            raise ToolError("text_to_speech: 'text' é obrigatório.")
        if len(text) > 4096:
            raise ToolError("text_to_speech: texto excede 4096 caracteres (limite da API).")
        output_file = str(args.get("output_file", "")).strip()
        if not output_file:
            raise ToolError("text_to_speech: 'output_file' é obrigatório.")
        if self._llm_client is None:
            raise ToolError("text_to_speech: llm_client não configurado.")

        voice = str(args.get("voice", "alloy"))
        model = str(args.get("model", "tts-1"))
        valid_voices = ("alloy", "echo", "fable", "onyx", "nova", "shimmer")
        if voice not in valid_voices:
            raise ToolError(f"text_to_speech: voice deve ser um de {valid_voices}.")
        valid_models = ("tts-1", "tts-1-hd")
        if model not in valid_models:
            raise ToolError(f"text_to_speech: model deve ser {valid_models}.")

        dest = self._sandbox(output_file)
        dest.parent.mkdir(parents=True, exist_ok=True)

        try:
            _lc = self._llm_client
            # Descobre o objeto com .audio.speech.create:
            # 1) self._llm_client direto (mocks e openai.OpenAI wrappers expostos)
            # 2) self._llm_client._client (wrapper bauer)
            # 3) cria openai.OpenAI com credenciais (exige openai instalado)
            if hasattr(_lc, "audio") and callable(getattr(getattr(_lc, "audio", None), "speech", None) and
                                                   getattr(getattr(getattr(_lc, "audio", None), "speech", None), "create", None) or None):
                client_obj = _lc
            elif hasattr(_lc, "audio"):
                client_obj = _lc
            elif hasattr(getattr(_lc, "_client", None), "audio"):
                client_obj = _lc._client
            else:
                try:
                    import openai
                except ImportError:
                    raise ToolError("text_to_speech: requer 'pip install openai'.")
                base_url = getattr(_lc, "base_url", None) or "https://api.openai.com/v1"
                api_key = getattr(_lc, "api_key", None) or ""
                client_obj = openai.OpenAI(api_key=api_key, base_url=base_url)

            response = client_obj.audio.speech.create(model=model, voice=voice, input=text)
            response.stream_to_file(str(dest))
        except ToolError:
            raise
        except Exception as exc:
            raise ToolError(f"text_to_speech: falha na API — {exc}") from exc

        size_kb = dest.stat().st_size // 1024 if dest.exists() else 0
        return (
            f"[text_to_speech] Áudio gerado:\n"
            f"  Arquivo: {dest.relative_to(self.workspace)}\n"
            f"  Tamanho: {size_kb} KB | Voice: {voice} | Model: {model}"
        )

    # --- kanban → tools/kanban.py | browser → tools/browser.py -----------------

