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
import os
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path

from .shell_runner import ShellError
from .tool_policy import load_tool_policy
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


_BAUER_PYTHON_CACHE: dict[str, str] = {}  # workspace_str → python_path
_BAUER_CONFIG_CACHE: dict[str, str] = {}  # workspace_str → config_yaml_path


def _find_bauer_config(workspace: Path) -> str | None:
    """Acha o config.yaml subindo a arvore a partir do workspace.

    Como o run_command executa subprocessos com cwd=workspace (sandbox),
    `bauer X` chamadas dentro do sandbox precisam apontar para o config.yaml
    da raiz do projeto — senao falha com 'Arquivo de config nao encontrado'.

    Returns absolute path quando achar, None caso contrario.
    """
    key = str(workspace)
    if key in _BAUER_CONFIG_CACHE:
        cached = _BAUER_CONFIG_CACHE[key]
        return cached or None

    search = Path(workspace).resolve()
    for _ in range(5):  # sobe ate 5 niveis
        candidate = search / "config.yaml"
        if candidate.exists():
            result = str(candidate).replace("\\", "/")
            _BAUER_CONFIG_CACHE[key] = result
            return result
        parent = search.parent
        if parent == search:
            break
        search = parent

    _BAUER_CONFIG_CACHE[key] = ""
    return None


def _find_bauer_python(workspace: Path) -> str:
    """Encontra o interpretador Python correto para rodar `python -m bauer.cli`.

    Estratégia (em ordem de prioridade):
    1. Python atual (sys.executable) — se bauer for importável a partir dele.
    2. .venv do projeto — sobe a árvore do workspace procurando .venv/Scripts/python.
    3. `python` no PATH — fallback genérico.

    Resultado em cache por workspace para evitar subprocessos repetidos.
    """
    import sys
    import subprocess
    import shutil as _shutil

    key = str(workspace)
    if key in _BAUER_PYTHON_CACHE:
        return _BAUER_PYTHON_CACHE[key]

    def _can_import_bauer(python_path: str) -> bool:
        try:
            # nosec: args são hardcoded — nenhum input do usuário; shell=False (default)
            r = subprocess.run(
                [python_path, "-c", "import bauer.cli"],
                capture_output=True, timeout=5,
            )
            return r.returncode == 0
        except Exception:
            return False

    result: str | None = None

    # 1) Python do processo atual
    current = sys.executable
    if current and _can_import_bauer(current):
        result = current.replace("\\", "/")

    # 2) Venv do projeto — sobe a árvore a partir do workspace
    if result is None:
        search_root = workspace
        for _ in range(5):  # sobe até 5 níveis
            for venv_name in (".venv", "venv", ".env"):
                for python_rel in (
                    Path(venv_name) / "Scripts" / "python.exe",  # Windows
                    Path(venv_name) / "bin" / "python",           # Linux/Mac
                ):
                    candidate = search_root / python_rel
                    if candidate.exists() and _can_import_bauer(str(candidate)):
                        result = str(candidate).replace("\\", "/")
                        break
                if result:
                    break
            if result:
                break
            parent = search_root.parent
            if parent == search_root:
                break
            search_root = parent

    # 3) `python` no PATH
    if result is None:
        fallback = _shutil.which("python") or _shutil.which("python3") or "python"
        result = fallback.replace("\\", "/")

    _BAUER_PYTHON_CACHE[key] = result
    return result


class ToolError(Exception):
    """Erro de execução de tool com mensagem legível."""


class SandboxError(ToolError):
    """Tentativa de acesso fora do workspace."""


class DryRunResult:
    """Retornado quando dry_run=True: descreve o que teria acontecido sem executar."""
    def __init__(self, tool: str, summary: str):
        self.tool = tool
        self.summary = summary

    def __str__(self) -> str:
        return f"[dry_run] {self.tool}: {self.summary}"


# Níveis de permissão: do menos ao mais privilegiado
_PERMISSION_LEVELS = ("read", "write", "execute", "network", "system")
_RISK_LEVELS = ("low", "medium", "high", "critical")

# Padrões proibidos no código Python submetido a execute_code.
# Defesa em profundidade: mesmo dentro do subprocesso isolado,
# bloqueamos os vetores mais óbvios de destruição do sistema.
_CODE_DENYLIST: list[tuple] = [
    (re.compile(r"\bos\.system\s*\("),
     "os.system() — use a tool run_command ou shell_runner"),
    (re.compile(r"\bsubprocess\b.{0,120}shell\s*=\s*True", re.DOTALL),
     "subprocess com shell=True — use shell=False com lista de args"),
    (re.compile(r"\bshutil\.rmtree\s*\(\s*[\"'/]"),
     "shutil.rmtree em caminho absoluto/raiz"),
    (re.compile(r"\bos\.(remove|unlink)\s*\(\s*[\"'/]"),
     "os.remove/unlink em caminho absoluto"),
    (re.compile(r"\beval\s*\(\s*(?:open|input|__import__)"),
     "eval(open(...)) / eval(input(...)) — exec de código dinâmico"),
]

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
    "memory":         {"permission": "read",    "risk": "low",    "approval": False},
    "session_search": {"permission": "read",    "risk": "low",    "approval": False},
    "kanban_list":    {"permission": "read",    "risk": "low",    "approval": False},
    "kanban_show":    {"permission": "read",    "risk": "low",    "approval": False},
    "process":        {"permission": "read",    "risk": "low",    "approval": False},
    "code_symbols":   {"permission": "read",    "risk": "low",    "approval": False},
    "find_definition":{"permission": "read",    "risk": "low",    "approval": False},
    "get_imports":    {"permission": "read",    "risk": "low",    "approval": False},
    "find_usages":    {"permission": "read",    "risk": "low",    "approval": False},
    # G15: LSP tools
    "lsp_hover":      {"permission": "read",    "risk": "low",    "approval": False},
    "lsp_definitions":{"permission": "read",    "risk": "low",    "approval": False},
    "lsp_references": {"permission": "read",    "risk": "low",    "approval": False},
    "lsp_diagnostics":{"permission": "read",    "risk": "low",    "approval": False},
    # Escrita local — workspace-scoped
    "write_file":     {"permission": "write",   "risk": "medium", "approval": False},
    "append_file":    {"permission": "write",   "risk": "medium", "approval": False},
    "patch":          {"permission": "write",   "risk": "medium", "approval": False},
    "create_dir":     {"permission": "write",   "risk": "low",    "approval": False},
    "move_file":      {"permission": "write",   "risk": "medium", "approval": False},
    "delete_file":    {"permission": "write",   "risk": "high",   "approval": True},
    "skill_manage":   {"permission": "write",   "risk": "low",    "approval": False},
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


def _syntax_check(path, text: str) -> str | None:
    """Valida sintaxe de arquivos .py/.json/.yaml recém-escritos.

    Retorna descrição curta do erro ou None se OK/extensão não suportada.
    Feedback imediato pro modelo: sem isto, um write_file com syntax error
    só era descoberto tool calls depois, ao tentar executar/importar.
    """
    suffix = str(getattr(path, "suffix", "")).lower()
    try:
        if suffix == ".py":
            import ast as _ast
            _ast.parse(text)
        elif suffix == ".json":
            import json as _json
            _json.loads(text)
        elif suffix in (".yaml", ".yml"):
            import yaml as _yaml
            _yaml.safe_load(text)
        return None
    except SyntaxError as exc:
        return f"Python syntax error na linha {exc.lineno}: {exc.msg}"
    except Exception as exc:
        kind = suffix.lstrip(".").upper()
        return f"{kind} syntax error: {str(exc)[:200]}"


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

# Limite de chars no OUTPUT de read_file (após paginação + numeração de linha).
# Caracteres são proxy de tokens; ~100K chars ≈ 25-35K tokens.
_MAX_READ_BYTES = 100_000
# Ceiling absoluto de tamanho de arquivo que read_file abre (G17.1).
# Acima disso, mesmo leitura paginada é recusada → use search_text/grep.
_MAX_FILE_BYTES = 5_000_000
# Número de linhas lidas por padrão quando 'limit' não é informado (G17.1).
_DEFAULT_READ_LINES = 2000
# Limite de resultados de busca.
_MAX_SEARCH_RESULTS = 50


def _normalize_tool_context(value: str | None) -> str:
    raw = (value if value is not None else os.environ.get("BAUER_TOOL_CONTEXT", "supervisor"))
    context = _TOOL_CONTEXT_ALIASES.get(str(raw).strip().lower(), str(raw).strip().lower())
    return context if context in _TOOL_CONTEXTS else "supervisor"


class ToolRouter:
    """Roteador central do Tool Bridge.

    Uso:
        router = ToolRouter(workspace=Path("workspace"))
        result = router.execute('{"action": "list_dir", "args": {"path": "."}}')
    """

    def __init__(
        self,
        workspace: str | Path = "workspace",
        shell_runner=None,
        web_enabled: bool = False,
        web_config=None,
        llm_client=None,
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

    def _get_browser_executor(self):
        """Executor de thread única e persistente para as browser tools (G18).

        Playwright sync é thread-affine: a página/contexto criados num
        browser_navigate só podem ser dirigidos pela MESMA thread. Como o
        execute() roda tools com timeout em threads descartáveis, cada call
        de browser caía numa thread diferente → 'cannot switch to a different
        thread'. Aqui mantemos um único worker (max_workers=1) vivo pela
        sessão inteira para que todas as browser tools rodem na mesma thread.
        """
        import concurrent.futures as _cf
        if self._browser_executor is None:
            self._browser_executor = _cf.ThreadPoolExecutor(
                max_workers=1, thread_name_prefix="bauer-browser"
            )
        return self._browser_executor

    def close_browser_executor(self) -> None:
        """Encerra a thread dedicada do browser (chamar no fim da sessão)."""
        if self._browser_executor is not None:
            self._browser_executor.shutdown(wait=False)
            self._browser_executor = None

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
                import warnings
                secrets_found = [m["name"] for m in scan_result.matches]
                warnings.warn(
                    f"[secrets_scanner] Segredos detectados no output de '{name}': "
                    f"{', '.join(set(secrets_found))}. Redagidos automaticamente.",
                    stacklevel=2,
                )
                result = scan_result.redacted_text
        except Exception:
            pass  # scanner nunca bloqueia execução

        # Escanear output de tools por binários/shellcode suspeitos
        try:
            from .binary_scanner import scan as _scan_binary
            _bin_result = _scan_binary(result)
            if _bin_result.is_suspicious:
                import warnings
                warnings.warn(
                    f"[binary_scanner] Conteúdo suspeito no output de '{name}': "
                    f"{_bin_result.summary()}",
                    stacklevel=2,
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

    # --- run_command (Fase 5) --------------------------------------------------

    def _make_run_command(self, shell_runner):
        def _run_command(args: dict) -> str:
            cmd = args.get("command")
            if not cmd:
                raise ToolError("run_command requer 'command'.")
            confirm = args.get("confirm", False)
            if not isinstance(confirm, bool):
                raise ToolError("run_command: 'confirm' deve ser true ou false.")
            background = args.get("background", False)
            if not isinstance(background, bool):
                raise ToolError("run_command: 'background' deve ser true ou false.")

            # Transparência: `bauer <sub>` ou `python -m bauer <sub>`
            #   → `<venv_python> -m bauer.cli <sub> --config <root>/config.yaml`
            # Resolve tres problemas:
            #   1. AppLocker bloqueando bauer.exe no venv
            #   2. `python` do sistema sem venv ativo → ModuleNotFoundError: typer
            #   3. cwd=workspace (sandbox) sem config.yaml → "Erro de config: arquivo nao encontrado"
            cmd_str = str(cmd).strip()
            _is_bauer_cmd = False
            _rest = ""
            if cmd_str == "bauer" or cmd_str.startswith("bauer "):
                _rest = cmd_str[len("bauer"):].strip()
                _is_bauer_cmd = True
            else:
                import re as _re_pre
                _py_bauer = _re_pre.match(
                    r"^(python3?|py)\s+-m\s+bauer(?:\.cli)?(.*)", cmd_str, _re_pre.IGNORECASE
                )
                if _py_bauer:
                    _rest = _py_bauer.group(2).strip()
                    _is_bauer_cmd = True

            if _is_bauer_cmd:
                python = _find_bauer_python(shell_runner.workspace)
                # Injeta --config <root>/config.yaml se nao explicitamente passado.
                # IMPORTANTE: typer nao tem --config global; cada subcomando declara
                # o seu. Por isso anexamos AO FINAL (depois de subcomandos), e so se
                # _rest tiver pelo menos um token (sem subcomando, --config eh inutil).
                cfg_path = _find_bauer_config(shell_runner.workspace)
                if cfg_path and _rest and "--config" not in _rest:
                    _rest = f"{_rest} --config \"{cfg_path}\""
                cmd_str = f'"{python}" -m bauer.cli {_rest}' if _rest else f'"{python}" -m bauer.cli'

            # `cd` é builtin do shell — não existe como processo externo.
            # Retorna orientação em vez de erro opaco da allowlist.
            import sys
            import re as _re
            _cd_match = _re.match(r"^cd\s+(.+)$", cmd_str.strip())
            if cmd_str.strip() == "cd" or _cd_match:
                target = _cd_match.group(1).strip() if _cd_match else "."
                return (
                    f"[run_command] 'cd' nao pode ser executado como subprocesso — "
                    f"e um builtin do shell sem efeito fora dele.\n"
                    f"Alternativas:\n"
                    f"  • Use 'list_dir' com path='{target}' para listar o diretorio\n"
                    f"  • Use 'read_file' com path='{target}/arquivo' para ler arquivos\n"
                    f"  • Passe o caminho completo nos proximos comandos: "
                    f"  run_command 'python {target}/script.py'"
                )

            # `which` nao existe no Windows — traduz automaticamente para `where`.
            if sys.platform == "win32":
                _which_match = _re.match(r"^which\s+(.+)$", cmd_str.strip())
                if _which_match:
                    cmd_str = f"where {_which_match.group(1)}"

                # `dir` no Windows e builtin do CMD — nao existe como executavel
                # com shell=False. Sugere usar tool list_dir (sem subprocess).
                if _re.match(r"^dir(\s|$)", cmd_str.strip()):
                    return (
                        "[run_command] 'dir' e builtin do CMD do Windows e nao funciona "
                        "como subprocesso com shell=False.\n"
                        "Use a tool 'list_dir' (path='.') em vez de run_command."
                    )

                # `cat`, `head`, `tail` no Windows podem nao estar disponiveis
                # (so existem com Git bash / WSL). Sugere usar tool read_file.
                _cat_head_tail = _re.match(r"^(cat|head|tail)\s+(.+)$", cmd_str.strip())
                if _cat_head_tail:
                    cmd_name = _cat_head_tail.group(1)
                    target = _cat_head_tail.group(2).split()[0]
                    return (
                        f"[run_command] '{cmd_name}' pode nao estar disponivel no Windows.\n"
                        f"Para ler arquivos, prefira a tool 'read_file' com path='{target}'."
                    )

            # ── Modo background (G17.3) ────────────────────────────────────
            # Lança destacado e registra no mesmo registry da tool 'process'
            # (start/poll/log/kill). Mantem o gate de seguranca via validate().
            if background:
                import subprocess as _sp
                import threading as _th
                try:
                    cmd_args = shell_runner.validate(cmd_str, confirm=confirm)
                except ShellError as exc:
                    raise ToolError(str(exc)) from exc
                try:
                    proc = _sp.Popen(
                        cmd_args,
                        cwd=str(shell_runner.workspace),
                        stdout=_sp.PIPE, stderr=_sp.PIPE, stdin=_sp.PIPE,
                        text=True, encoding="utf-8", errors="replace",
                        shell=False,
                    )
                except FileNotFoundError:
                    raise ToolError(f"Comando nao encontrado: '{cmd_args[0]}'.")
                except OSError as exc:
                    raise ToolError(f"Erro ao iniciar background: {exc}") from exc

                pid_str = str(proc.pid)
                stdout_buf: list[str] = []
                stderr_buf: list[str] = []

                def _reader(stream, buf):
                    try:
                        for line in stream:
                            buf.append(line)
                    except Exception:
                        pass

                _th.Thread(target=_reader, args=(proc.stdout, stdout_buf), daemon=True).start()
                _th.Thread(target=_reader, args=(proc.stderr, stderr_buf), daemon=True).start()
                self._processes[pid_str] = {
                    "proc": proc,
                    "label": cmd_str[:40],
                    "command": cmd_str,
                    "stdout_buf": stdout_buf,
                    "stderr_buf": stderr_buf,
                }
                return (
                    f"[run_command background] PID {pid_str}: {cmd_str}\n"
                    f"Acompanhe com process(action='poll'|'log', pid='{pid_str}'); "
                    f"encerre com process(action='kill', pid='{pid_str}')."
                )

            try:
                result = shell_runner.run(cmd_str, confirm=confirm)
            except ShellError as exc:
                raise ToolError(str(exc)) from exc

            lines = [f"$ {' '.join(result.command)}"]
            lines.append(f"exit: {result.returncode} ({result.elapsed_ms}ms)")
            if result.stdout:
                lines.append("--- stdout ---")
                lines.append(result.stdout.rstrip())
            if result.stderr:
                lines.append("--- stderr ---")
                lines.append(result.stderr.rstrip())
            if result.truncated:
                lines.append(f"[saida truncada — limite {shell_runner.max_output_bytes} bytes]")
            return "\n".join(lines)

        return _run_command

    # --- tools -----------------------------------------------------------------

    def _list_dir(self, args: dict) -> str:
        path = args.get("path", ".")
        p = self._sandbox(str(path))

        if not p.exists():
            raise ToolError(f"Nao encontrado: '{path}'")
        if not p.is_dir():
            raise ToolError(f"'{path}' nao e um diretorio — use read_file para arquivos.")

        entries = sorted(p.iterdir(), key=lambda e: (not e.is_dir(), e.name.lower()))
        if not entries:
            return f"{path}/ (vazio)"

        lines = [f"Conteudo de {path}/"]
        for e in entries:
            suffix = "/" if e.is_dir() else ""
            size = f"  ({e.stat().st_size} bytes)" if e.is_file() else ""
            lines.append(f"  {e.name}{suffix}{size}")
        return "\n".join(lines)

    @staticmethod
    def _coerce_int(value, default: int, minimum: int) -> int:
        """Coage value para int >= minimum; default em falha. (G17.1)"""
        try:
            n = int(value)
        except (TypeError, ValueError):
            return default
        return n if n >= minimum else default

    def _read_file(self, args: dict) -> str:
        """Le arquivo com paginacao (offset/limit) + numeracao de linha + dedup. (G17.1)

        Espelha o read_file do Hermes/Claude Code:
          - offset (1-indexed) e limit selecionam uma janela de linhas
          - cada linha sai prefixada com seu numero (facilita patch/edit)
          - ceiling de tamanho de arquivo + cap de chars no output
          - dedup anti-loop: re-leitura identica de arquivo inalterado e bloqueada
        """
        path = args.get("path")
        if not path:
            raise ToolError("read_file requer 'path'.")
        offset = self._coerce_int(args.get("offset", 1), default=1, minimum=1)
        limit = self._coerce_int(args.get("limit", _DEFAULT_READ_LINES),
                                 default=_DEFAULT_READ_LINES, minimum=1)

        p = self._sandbox(str(path))
        if not p.exists():
            raise ToolError(f"Arquivo nao encontrado: '{path}'")
        if p.is_dir():
            raise ToolError(f"'{path}' e um diretorio — use list_dir.")

        size = p.stat().st_size
        if size > _MAX_FILE_BYTES:
            raise ToolError(
                f"Arquivo muito grande: {size} bytes (limite: {_MAX_FILE_BYTES}).\n"
                f"Use search_text/regex_search para localizar trechos, "
                f"ou read_file com offset+limit menores."
            )

        # ── Dedup anti-loop (G17.1) ───────────────────────────────────────
        # Se o modelo re-le a MESMA janela de um arquivo inalterado, devolve
        # stub; apos 2 hits, bloqueia para nao queimar o budget de iteracoes.
        resolved = str(p)
        try:
            mtime = p.stat().st_mtime
        except OSError:
            mtime = 0.0
        key = (offset, limit)
        tracked = self._read_tracker.get(resolved)
        if tracked and tracked["key"] == key and tracked["mtime"] == mtime:
            hits = tracked["hits"] + 1
            tracked["hits"] = hits
            if hits >= 2:
                raise ToolError(
                    f"BLOQUEADO: read_file('{path}', offset={offset}, limit={limit}) "
                    f"foi chamado {hits + 1}x e o arquivo NAO mudou. "
                    "Use o conteudo que voce ja leu — pare de reler o mesmo trecho."
                )
            return (
                f"[read_file] '{path}' inalterado desde a ultima leitura "
                f"(offset={offset}, limit={limit}). Reaproveite o resultado anterior."
            )

        raw = p.read_bytes()
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            raise ToolError(f"'{path}' parece ser binario — read_file so aceita texto UTF-8.")

        lines = text.splitlines()
        total = len(lines)
        start = offset - 1
        if total and start >= total:
            raise ToolError(
                f"offset {offset} esta alem do fim do arquivo "
                f"('{path}' tem {total} linha(s))."
            )
        window = lines[start:start + limit]
        end = start + len(window)  # exclusivo, 0-indexed

        width = max(len(str(end)), 1)
        body = "\n".join(f"{start + i + 1:>{width}}\t{ln}" for i, ln in enumerate(window))

        if len(body) > _MAX_READ_BYTES:
            raise ToolError(
                f"Leitura produziu {len(body)} chars (limite: {_MAX_READ_BYTES}).\n"
                f"Reduza 'limit' (atual: {limit}) ou avance 'offset' para ler menos linhas."
            )

        # Registra dedup + read-before-write (G17.2)
        self._read_tracker[resolved] = {"key": key, "mtime": mtime, "hits": 0}
        self._read_paths.add(resolved)

        header = f"# {path} — linhas {start + 1}-{end} de {total}"
        footer = ""
        if end < total:
            footer = (
                f"\n[... +{total - end} linha(s). Continue com "
                f"read_file('{path}', offset={end + 1}).]"
            )
        if not window:
            return f"{header}\n(arquivo vazio)"
        return f"{header}\n{body}{footer}"

    # --- read-before-write tracking (G17.2) ------------------------------------

    def _require_prior_read(self, p: Path, path: str, op: str) -> None:
        """Exige que um arquivo existente tenha sido lido antes de ser editado.

        Evita edicao/sobrescrita cega — o modelo precisa ter visto o conteudo
        atual (via read_file) nesta sessao. Arquivos novos sao isentos.
        """
        if str(p) not in self._read_paths:
            raise ToolError(
                f"{op}: '{path}' existe mas nao foi lido nesta sessao.\n"
                f"Leia com read_file('{path}') antes de edita-lo — "
                "editar as cegas corrompe arquivos."
            )

    def _mark_written(self, p: Path) -> None:
        """Apos escrever, o arquivo conta como 'lido' para o gate de
        read-before-write (o modelo conhece o conteudo que acabou de gravar).

        Limpa qualquer estado de dedup do arquivo: o conteudo mudou, entao a
        proxima read_file deve retornar conteudo real (nao um stub 'inalterado').
        """
        resolved = str(p)
        self._read_paths.add(resolved)
        self._read_tracker.pop(resolved, None)

    def _write_file(self, args: dict) -> str:
        path = args.get("path")
        content = args.get("content")
        overwrite = args.get("overwrite", False)

        if not path:
            raise ToolError("write_file requer 'path'.")
        if content is None:
            raise ToolError("write_file requer 'content'.")
        if not isinstance(overwrite, bool):
            raise ToolError("write_file: 'overwrite' deve ser true ou false.")

        p = self._sandbox(str(path))

        if p.exists() and not overwrite:
            raise ToolError(
                f"'{path}' ja existe e overwrite=false.\n"
                f"Leia o arquivo com read_file antes de sobrescrever.\n"
                f"Para sobrescrever: adicione \"overwrite\": true nos args."
            )
        # G17.2: sobrescrever arquivo existente exige leitura previa.
        if p.exists() and overwrite:
            self._require_prior_read(p, str(path), "write_file (overwrite)")

        p.parent.mkdir(parents=True, exist_ok=True)
        text = str(content)
        p.write_text(text, encoding="utf-8")
        self._mark_written(p)
        result = f"Gravado: '{path}' ({len(text)} chars)"
        # Verificação pós-write: o modelo recebe o erro de sintaxe IMEDIATAMENTE
        # em vez de descobrir 3 tool calls depois ao tentar executar o arquivo.
        syntax_err = _syntax_check(p, text)
        if syntax_err:
            result += (
                f"\n[ATENÇÃO — erro de sintaxe detectado] {syntax_err}\n"
                "Corrija com a tool patch antes de usar o arquivo."
            )
        return result

    def _search_text(self, args: dict) -> str:
        path = args.get("path", ".")
        pattern = args.get("pattern")

        if not pattern:
            raise ToolError("search_text requer 'pattern'.")

        p = self._sandbox(str(path))
        if not p.exists():
            raise ToolError(f"Nao encontrado: '{path}'")

        files = [p] if p.is_file() else sorted(p.rglob("*"))
        results: list[str] = []

        for f in files:
            if not f.is_file():
                continue
            try:
                text = f.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for i, line in enumerate(text.splitlines(), 1):
                if pattern.lower() in line.lower():
                    try:
                        rel = f.relative_to(self.workspace)
                    except ValueError:
                        rel = f
                    results.append(f"{rel}:{i}: {line.strip()}")
                    if len(results) >= _MAX_SEARCH_RESULTS:
                        results.append(f"... (limite de {_MAX_SEARCH_RESULTS} resultados atingido)")
                        return "\n".join(results)

        if not results:
            return f"Nenhum resultado para '{pattern}' em '{path}'"
        return "\n".join(results)

    # --- web tools (web_enabled) — via WebDispatcher -------------------------

    def _web_search(self, args: dict) -> str:
        query = args.get("query")
        if not query:
            raise ToolError("web_search requer 'query'.")
        max_results = min(int(args.get("max_results", 5)), 10)

        from .web.dispatcher import WebError
        try:
            return self._web.search_as_text(query, max_results=max_results)
        except WebError as exc:
            raise ToolError(str(exc)) from exc
        except Exception as exc:
            raise ToolError(f"Erro na busca web: {exc}") from exc

    def _web_fetch(self, args: dict) -> str:
        url = args.get("url")
        if not url:
            raise ToolError("web_fetch requer 'url'.")

        # Wave 4.5: SSRF prevention
        if _URL_SAFETY_AVAILABLE:
            try:
                _is_safe_url(url)
            except UrlSafetyError as exc:
                raise ToolError(f"[BLOCKED] SSRF: {exc}") from exc

        max_chars = int(args.get("max_chars", self._web.max_chars))

        from .web.dispatcher import WebError
        try:
            return self._web.extract(url, max_chars=max_chars)
        except WebError as exc:
            raise ToolError(str(exc)) from exc
        except Exception as exc:
            raise ToolError(f"Erro ao buscar URL: {exc}") from exc

    # --- tools de arquivo avançadas -------------------------------------------

    def _create_dir(self, args: dict) -> str:
        path = args.get("path")
        if not path:
            raise ToolError("create_dir requer 'path'.")
        p = self._sandbox(str(path))
        p.mkdir(parents=True, exist_ok=True)
        return f"Diretorio criado: '{path}'"

    def _delete_file(self, args: dict) -> str:
        path = args.get("path")
        if not path:
            raise ToolError("delete_file requer 'path'.")
        confirm = args.get("confirm", False)
        if not isinstance(confirm, bool):
            raise ToolError("delete_file: 'confirm' deve ser true ou false.")
        if not confirm:
            raise ToolError(
                f"delete_file: operacao destrutiva — adicione \"confirm\": true para confirmar exclusao de '{path}'."
            )
        p = self._sandbox(str(path))
        if not p.exists():
            raise ToolError(f"Arquivo nao encontrado: '{path}'")
        if p.is_dir():
            raise ToolError(f"'{path}' e um diretorio. Use run_command com 'rm -rf' para remover diretorios.")
        p.unlink()
        return f"Arquivo removido: '{path}'"

    def _append_file(self, args: dict) -> str:
        path = args.get("path")
        content = args.get("content")
        if not path:
            raise ToolError("append_file requer 'path'.")
        if content is None:
            raise ToolError("append_file requer 'content'.")
        p = self._sandbox(str(path))
        p.parent.mkdir(parents=True, exist_ok=True)
        text = str(content)
        with p.open("a", encoding="utf-8") as f:
            f.write(text)
        return f"Acrescentado em '{path}': {len(text)} chars"

    def _move_file(self, args: dict) -> str:
        src = args.get("src")
        dst = args.get("dst")
        overwrite = args.get("overwrite", False)
        if not src:
            raise ToolError("move_file requer 'src'.")
        if not dst:
            raise ToolError("move_file requer 'dst'.")
        if not isinstance(overwrite, bool):
            raise ToolError("move_file: 'overwrite' deve ser true ou false.")
        p_src = self._sandbox(str(src))
        p_dst = self._sandbox(str(dst))
        if not p_src.exists():
            raise ToolError(f"Origem nao encontrada: '{src}'")
        if p_dst.exists() and not overwrite:
            raise ToolError(
                f"'{dst}' ja existe e overwrite=false. Adicione \"overwrite\": true para sobrescrever."
            )
        p_dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(p_src), str(p_dst))
        return f"Movido: '{src}' → '{dst}'"

    def _diff_files(self, args: dict) -> str:
        path_a = args.get("path_a")
        path_b = args.get("path_b")
        context_lines = int(args.get("context_lines", 3))
        if not path_a:
            raise ToolError("diff_files requer 'path_a'.")
        if not path_b:
            raise ToolError("diff_files requer 'path_b'.")
        pa = self._sandbox(str(path_a))
        pb = self._sandbox(str(path_b))
        if not pa.exists():
            raise ToolError(f"Arquivo nao encontrado: '{path_a}'")
        if not pb.exists():
            raise ToolError(f"Arquivo nao encontrado: '{path_b}'")
        lines_a = pa.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)
        lines_b = pb.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)
        diff = list(difflib.unified_diff(
            lines_a, lines_b,
            fromfile=str(path_a),
            tofile=str(path_b),
            n=context_lines,
        ))
        if not diff:
            return f"Arquivos identicos: '{path_a}' e '{path_b}'"
        result = "".join(diff)
        if len(result) > _MAX_READ_BYTES:
            result = result[:_MAX_READ_BYTES] + "\n[... diff truncado]"
        return result

    # --- tools de busca --------------------------------------------------------

    def _glob_files(self, args: dict) -> str:
        pattern = args.get("pattern")
        base = args.get("path", ".")
        if not pattern:
            raise ToolError("glob_files requer 'pattern'.")
        p = self._sandbox(str(base))
        if not p.exists():
            raise ToolError(f"Diretorio nao encontrado: '{base}'")
        matches = sorted(p.glob(pattern))
        if not matches:
            return f"Nenhum arquivo encontrado com o padrao '{pattern}' em '{base}'"
        lines = []
        for m in matches[:200]:
            try:
                rel = m.relative_to(self.workspace)
            except ValueError:
                rel = m
            suffix = "/" if m.is_dir() else f"  ({m.stat().st_size} bytes)"
            lines.append(f"  {rel}{suffix}")
        result = f"Encontrados {len(matches)} arquivo(s) — padrao '{pattern}':\n" + "\n".join(lines)
        if len(matches) > 200:
            result += f"\n... (mostrando 200 de {len(matches)})"
        return result

    def _regex_search(self, args: dict) -> str:
        pattern = args.get("pattern")
        base = args.get("path", ".")
        flags_str = str(args.get("flags", "")).lower()
        if not pattern:
            raise ToolError("regex_search requer 'pattern'.")
        re_flags = 0
        if "i" in flags_str:
            re_flags |= re.IGNORECASE
        if "m" in flags_str:
            re_flags |= re.MULTILINE
        if "s" in flags_str:
            re_flags |= re.DOTALL
        try:
            compiled = re.compile(pattern, re_flags)
        except re.error as exc:
            raise ToolError(f"Regex inválida: {exc}") from exc

        p = self._sandbox(str(base))
        if not p.exists():
            raise ToolError(f"Nao encontrado: '{base}'")
        files = [p] if p.is_file() else sorted(p.rglob("*"))
        results: list[str] = []

        for f in files:
            if not f.is_file():
                continue
            try:
                text = f.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for i, line in enumerate(text.splitlines(), 1):
                if compiled.search(line):
                    try:
                        rel = f.relative_to(self.workspace)
                    except ValueError:
                        rel = f
                    results.append(f"{rel}:{i}: {line.strip()}")
                    if len(results) >= _MAX_SEARCH_RESULTS:
                        results.append(f"... (limite de {_MAX_SEARCH_RESULTS} resultados atingido)")
                        return "\n".join(results)

        if not results:
            return f"Nenhum resultado para regex '{pattern}' em '{base}'"
        return "\n".join(results)

    # --- tools de utilidade ----------------------------------------------------

    def _calculate(self, args: dict) -> str:
        expression = args.get("expression")
        if not expression:
            raise ToolError("calculate requer 'expression'.")

        # Avaliação segura: converte para AST e avalia apenas nós permitidos
        _SAFE_FUNCS = {
            "abs": abs, "round": round, "min": min, "max": max,
            "sum": sum, "int": int, "float": float, "pow": pow,
        }
        try:
            import math
            _SAFE_FUNCS.update({
                "sqrt": math.sqrt, "log": math.log, "log2": math.log2,
                "log10": math.log10, "ceil": math.ceil, "floor": math.floor,
                "pi": math.pi, "e": math.e, "sin": math.sin, "cos": math.cos,
                "tan": math.tan,
            })
        except ImportError:
            pass

        class _SafeEval(ast.NodeVisitor):
            ALLOWED = (
                ast.Expression, ast.BinOp, ast.UnaryOp, ast.Num, ast.Constant,
                ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Mod, ast.Pow,
                ast.FloorDiv, ast.USub, ast.UAdd, ast.Call, ast.Name,
                ast.Load, ast.List, ast.Tuple,
            )
            def generic_visit(self, node):
                if not isinstance(node, self.ALLOWED):
                    raise ToolError(f"Operacao nao permitida no calculo: {type(node).__name__}")
                return super().generic_visit(node)

        expr = str(expression).strip()
        try:
            tree = ast.parse(expr, mode="eval")
            _SafeEval().visit(tree)
            result = eval(compile(tree, "<calc>", "eval"), {"__builtins__": {}}, _SAFE_FUNCS)  # noqa: S307
        except ToolError:
            raise
        except ZeroDivisionError:
            raise ToolError("Divisao por zero.")
        except Exception as exc:
            raise ToolError(f"Expressao invalida: {exc}") from exc

        return f"{expr} = {result}"

    def _datetime_now(self, args: dict) -> str:
        fmt = str(args.get("format", "iso")).lower()
        tz_arg = str(args.get("tz", "utc")).lower()

        if tz_arg == "utc":
            now = datetime.now(timezone.utc)
        else:
            now = datetime.now()

        if fmt == "iso":
            return now.isoformat(timespec="seconds")
        elif fmt == "br":
            return now.strftime("%d/%m/%Y %H:%M:%S")
        elif fmt == "us":
            return now.strftime("%Y-%m-%d %H:%M:%S")
        elif fmt == "timestamp":
            return str(int(now.timestamp()))
        else:
            return now.isoformat(timespec="seconds")

    def _json_query(self, args: dict) -> str:
        data_arg = args.get("data")
        query = args.get("query")
        if not data_arg:
            raise ToolError("json_query requer 'data'.")
        if not query:
            raise ToolError("json_query requer 'query'.")

        # Tenta como arquivo primeiro, depois como string JSON
        raw: Any = None
        try:
            p = self._sandbox(str(data_arg))
            if p.exists() and p.is_file():
                raw = json.loads(p.read_text(encoding="utf-8"))
        except (SandboxError, Exception):
            pass

        if raw is None:
            try:
                raw = json.loads(str(data_arg))
            except json.JSONDecodeError as exc:
                raise ToolError(f"JSON inválido: {exc}") from exc

        # Navega pelo path: ".key.sub[0].field"
        query = query.strip()
        if query.startswith("."):
            query = query[1:]

        current = raw
        # Tokeniza: split por '.' respeitando '[n]'
        tokens: list[str] = re.split(r"\.(?![^\[]*\])", query) if query else []
        for token in tokens:
            if not token:
                continue
            # Verifica acesso de lista: nome[0]
            m = re.match(r"^(.*?)\[(\d+)\]$", token)
            if m:
                key, idx = m.group(1), int(m.group(2))
                if key:
                    if not isinstance(current, dict):
                        raise ToolError(f"Esperava objeto JSON em '{key}', encontrou {type(current).__name__}")
                    if key not in current:
                        raise ToolError(f"Chave '{key}' nao encontrada")
                    current = current[key]
                if not isinstance(current, list):
                    raise ToolError(f"Esperava lista para indice [{idx}], encontrou {type(current).__name__}")
                if idx >= len(current):
                    raise ToolError(f"Indice [{idx}] fora do range (len={len(current)})")
                current = current[idx]
            else:
                if isinstance(current, dict):
                    if token not in current:
                        raise ToolError(f"Chave '{token}' nao encontrada. Chaves disponíveis: {list(current.keys())[:10]}")
                    current = current[token]
                else:
                    raise ToolError(f"Esperava objeto JSON para acessar '{token}', encontrou {type(current).__name__}")

        return json.dumps(current, ensure_ascii=False, indent=2) if isinstance(current, (dict, list)) else str(current)

    def _encode_decode(self, args: dict) -> str:
        inp = args.get("input")
        operation = str(args.get("operation", "")).lower().strip()
        if inp is None:
            raise ToolError("encode_decode requer 'input'.")
        if not operation:
            raise ToolError("encode_decode requer 'operation'.")

        import base64
        import urllib.parse

        text = str(inp)
        if operation == "base64_encode":
            return base64.b64encode(text.encode()).decode()
        elif operation == "base64_decode":
            try:
                return base64.b64decode(text.encode()).decode("utf-8", errors="replace")
            except Exception as exc:
                raise ToolError(f"base64_decode falhou: {exc}") from exc
        elif operation == "url_encode":
            return urllib.parse.quote(text, safe="")
        elif operation == "url_decode":
            return urllib.parse.unquote(text)
        elif operation == "hex_encode":
            return text.encode().hex()
        elif operation == "hex_decode":
            try:
                return bytes.fromhex(text).decode("utf-8", errors="replace")
            except Exception as exc:
                raise ToolError(f"hex_decode falhou: {exc}") from exc
        else:
            raise ToolError(
                f"Operacao '{operation}' nao reconhecida. "
                "Use: base64_encode, base64_decode, url_encode, url_decode, hex_encode, hex_decode."
            )

    # --- http_request (web_enabled) -------------------------------------------

    def _http_request(self, args: dict) -> str:
        url = args.get("url")
        method = str(args.get("method", "GET")).upper()
        headers = args.get("headers") or {}
        body = args.get("body")
        max_chars = int(args.get("max_chars", 5000))

        if not url:
            raise ToolError("http_request requer 'url'.")
        if not url.startswith(("http://", "https://")):
            raise ToolError("URL deve comecar com http:// ou https://")
        if method not in ("GET", "POST", "PUT", "PATCH", "DELETE", "HEAD"):
            raise ToolError(f"Metodo '{method}' nao suportado. Use: GET, POST, PUT, PATCH, DELETE.")

        # Wave 4.5: SSRF prevention (replaces manual blocklist)
        if _URL_SAFETY_AVAILABLE:
            try:
                _is_safe_url(url)
            except UrlSafetyError as exc:
                raise ToolError(f"[BLOCKED] SSRF: {exc}") from exc
        else:
            # Fallback minimal blocklist when url_safety module unavailable
            import ipaddress as _ipaddress
            import urllib.parse as _urlparse
            _parsed = _urlparse.urlparse(url)
            _hostname = _parsed.hostname or ""
            _BLOCKED = ("localhost", "127.", "0.0.0.0", "::1")
            if any(_hostname.startswith(b) or _hostname == b.rstrip(".") for b in _BLOCKED):
                raise ToolError(f"Acesso bloqueado a host interno: '{_hostname}'")
            try:
                _addr = _ipaddress.ip_address(_hostname)
                if _addr.is_private or _addr.is_loopback or _addr.is_link_local:
                    raise ToolError(f"Acesso bloqueado a endereco IP privado: '{_hostname}'")
            except ValueError:
                pass

        import httpx

        if not isinstance(headers, dict):
            raise ToolError("http_request: 'headers' deve ser um objeto JSON.")

        # Prepara body
        json_body = None
        content_body = None
        if body is not None:
            if isinstance(body, dict):
                json_body = body
            else:
                content_body = str(body).encode()

        try:
            resp = httpx.request(
                method,
                url,
                headers=headers,
                json=json_body,
                content=content_body,
                timeout=15.0,
                follow_redirects=True,
            )
        except httpx.TimeoutException:
            raise ToolError(f"Timeout ao acessar {url}")
        except Exception as exc:
            raise ToolError(f"Erro na requisicao: {exc}")

        # Monta resposta
        content_type = resp.headers.get("content-type", "")
        lines = [
            f"HTTP {resp.status_code} {resp.reason_phrase}",
            f"Content-Type: {content_type}",
            f"Content-Length: {resp.headers.get('content-length', 'n/a')}",
            "---",
        ]

        if "json" in content_type:
            try:
                body_text = json.dumps(resp.json(), ensure_ascii=False, indent=2)
            except Exception:
                body_text = resp.text
        elif "text" in content_type or "html" in content_type or "xml" in content_type:
            body_text = resp.text
        else:
            body_text = f"[Conteudo binario — content-type: {content_type}]"

        if len(body_text) > max_chars:
            body_text = body_text[:max_chars] + f"\n[... truncado, limite de {max_chars} chars]"

        lines.append(body_text)
        return "\n".join(lines)

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

    # --- memory ----------------------------------------------------------------

    _MEMORY_FILE = ".bauer_memory.json"
    _MAX_VALUE_LEN = 10_000  # chars por valor
    _MAX_KEYS = 500

    def _memory_path(self) -> Path:
        return self.workspace / self._MEMORY_FILE

    def _memory_load(self) -> dict:
        p = self._memory_path()
        if not p.exists():
            return {}
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def _memory_save(self, data: dict) -> None:
        self._memory_path().write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _memory(self, args: dict) -> str:
        """Key-value persistente em .bauer_memory.json dentro do workspace."""
        from datetime import datetime, timezone as _tz

        action = str(args.get("action", "")).lower()
        if not action:
            raise ToolError("memory requer 'action': set | get | list | delete.")

        if action == "set":
            key = args.get("key", "").strip()
            value = args.get("value")
            if not key:
                raise ToolError("memory set requer 'key'.")
            if value is None:
                raise ToolError("memory set requer 'value'.")
            value_str = str(value)
            if len(value_str) > self._MAX_VALUE_LEN:
                raise ToolError(
                    f"Valor muito grande ({len(value_str)} chars). "
                    f"Limite: {self._MAX_VALUE_LEN} chars."
                )
            data = self._memory_load()
            if len(data) >= self._MAX_KEYS and key not in data:
                raise ToolError(
                    f"Limite de {self._MAX_KEYS} chaves atingido. "
                    "Use memory delete para liberar espaco."
                )
            ts = datetime.now(_tz.utc).isoformat()
            data[key] = {"value": value_str, "updated_at": ts}
            self._memory_save(data)
            return f"Memory['{key}'] = {value_str[:80]}{'...' if len(value_str) > 80 else ''}"

        elif action == "get":
            key = args.get("key", "").strip()
            if not key:
                raise ToolError("memory get requer 'key'.")
            data = self._memory_load()
            if key not in data:
                return f"Chave '{key}' nao encontrada na memory."
            entry = data[key]
            val = entry["value"] if isinstance(entry, dict) else str(entry)
            ts = entry.get("updated_at", "") if isinstance(entry, dict) else ""
            return f"Memory['{key}'] = {val}\n(atualizado: {ts})"

        elif action == "list":
            data = self._memory_load()
            if not data:
                return "Memory vazia."
            lines = [f"Memory ({len(data)} chaves):"]
            for k, v in sorted(data.items()):
                val = v["value"] if isinstance(v, dict) else str(v)
                preview = val[:60].replace("\n", " ") + ("..." if len(val) > 60 else "")
                lines.append(f"  {k}: {preview}")
            return "\n".join(lines)

        elif action == "delete":
            key = args.get("key", "").strip()
            if not key:
                raise ToolError("memory delete requer 'key'.")
            data = self._memory_load()
            if key not in data:
                return f"Chave '{key}' nao encontrada — nada removido."
            del data[key]
            self._memory_save(data)
            return f"Memory['{key}'] removido."

        else:
            raise ToolError(f"Acao desconhecida: '{action}'. Use: set | get | list | delete.")

    # --- execute_code ----------------------------------------------------------

    def _execute_code(self, args: dict) -> str:
        """Executa código Python em subprocesso isolado.

        Boas práticas:
        - Subprocesso separado — não tem acesso ao estado interno do Bauer
        - Timeout configurável (padrão 30s, máx 120s)
        - Arquivo temporário limpo após execução
        - Captura stdout + stderr + exit code
        """
        import subprocess
        import sys
        import tempfile

        code = args.get("code")
        if not code:
            raise ToolError("execute_code requer 'code'.")

        # Scan de conteúdo — bloqueia padrões destrutivos mesmo no subprocesso
        for pattern, label in _CODE_DENYLIST:
            if pattern.search(code):
                raise ToolError(
                    f"execute_code: código bloqueado — contém '{label}'. "
                    "Remova o padrão perigoso ou use a tool apropriada (run_command, delete_file, etc.)."
                )

        timeout = int(args.get("timeout", 30))
        timeout = max(1, min(timeout, 120))

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".py", delete=False, encoding="utf-8"
        ) as f:
            f.write(code)
            tmp_path = f.name

        try:
            result = subprocess.run(
                [sys.executable, tmp_path],
                capture_output=True,
                text=True,
                encoding="utf-8",   # evita UnicodeDecodeError (cp1252) no Windows
                errors="replace",
                timeout=timeout,
                cwd=str(self.workspace),
            )
        except subprocess.TimeoutExpired:
            raise ToolError(f"Timeout: codigo excedeu {timeout}s de execucao.")
        except Exception as exc:
            raise ToolError(f"Erro ao executar codigo: {exc}")
        finally:
            try:
                Path(tmp_path).unlink(missing_ok=True)
            except Exception:
                pass

        stdout = result.stdout or ""
        stderr = result.stderr or ""
        lines = [f"exit: {result.returncode}"]
        if stdout.strip():
            lines.append("--- stdout ---")
            out = stdout
            if len(out) > 8000:
                out = out[:8000] + f"\n[... truncado — {len(stdout)} chars total]"
            lines.append(out.rstrip())
        if stderr.strip():
            lines.append("--- stderr ---")
            err = stderr
            if len(err) > 4000:
                err = err[:4000] + f"\n[... truncado]"
            lines.append(err.rstrip())
        if not stdout.strip() and not stderr.strip():
            lines.append("(sem output)")

        return "\n".join(lines)

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

    # --- delegate_task ---------------------------------------------------------

    def _delegate_task(self, args: dict) -> str:
        """Delega subtarefa a sub-agente via subprocess bauer CLI.

        Boas práticas:
        - Sub-processo isolado: não compartilha memória, cliente ou contexto
        - Timeout configurável para evitar travamento
        - Contexto opcional passa como instrução inicial ao agente
        - Reutiliza a configuração do Bauer via `bauer agent run-one`
        """
        import subprocess
        import sys

        task = args.get("task", "").strip()
        if not task:
            raise ToolError("delegate_task requer 'task'.")

        context = args.get("context", "").strip()
        timeout = int(args.get("timeout", 120))
        timeout = max(10, min(timeout, 600))

        full_task = f"{context}\n\n{task}".strip() if context else task
        # Sanitização: remove null bytes e limita tamanho para evitar overflow de args
        full_task = full_task.replace("\x00", "").strip()
        if len(full_task) > 4096:
            full_task = full_task[:4096]

        # Tenta usar o cliente LLM diretamente se disponível (mais eficiente)
        if self._llm_client is not None:
            try:
                from .agent import run_one_turn
                messages = [{"role": "user", "content": full_task}]
                response = run_one_turn(self._llm_client, messages, tools=None)
                return f"[sub-agente]\n{response}"
            except Exception as exc:
                # Fallback para subprocess
                pass

        # Fallback: subprocess com bauer CLI
        # nosec: shell=False (default); full_task é passado como elemento de lista,
        # não como string de shell — sem risco de injeção.
        python = _find_bauer_python(self.workspace)
        cmd = [python, "-m", "bauer.cli", "agent", "run-one", full_task]
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                encoding="utf-8",   # evita UnicodeDecodeError em cp1252 no Windows
                errors="replace",   # substitui bytes inválidos por '?' em vez de explodir
                timeout=timeout,
                cwd=str(self.workspace),
            )
        except subprocess.TimeoutExpired:
            raise ToolError(
                f"delegate_task: sub-agente excedeu timeout de {timeout}s.\n"
                "Aumente 'timeout' ou quebre a tarefa em partes menores."
            )
        except FileNotFoundError:
            raise ToolError(
                "delegate_task: bauer CLI nao encontrado. "
                "Certifique-se de que o Bauer esta instalado no ambiente."
            )
        except Exception as exc:
            raise ToolError(f"delegate_task: erro ao chamar sub-agente: {exc}")

        output = (result.stdout or "").strip()
        if result.returncode != 0:
            err = (result.stderr or "").strip()
            raise ToolError(
                f"delegate_task: sub-agente falhou (exit {result.returncode}).\n"
                f"Erro: {err[:500] if err else 'sem detalhes'}"
            )

        if not output:
            return "[sub-agente] Tarefa concluida sem output."

        if len(output) > 8000:
            output = output[:8000] + f"\n[... truncado — {len(result.stdout or '')} chars]"

        return f"[sub-agente]\n{output}"

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

        # Usa llm_client se disponível
        if self._llm_client is not None:
            try:
                from .agent import run_one_turn
                response = run_one_turn(self._llm_client, [message], tools=None)
                return response
            except Exception as exc:
                raise ToolError(f"vision_analyze: erro ao chamar modelo: {exc}")

        raise ToolError(
            "vision_analyze requer um cliente LLM configurado com suporte a visao.\n"
            "Providers suportados: OpenAI (gpt-4o), Anthropic (claude-3-*), "
            "Google (gemini-*), OpenRouter.\n"
            "Configure no config.yaml e reinicie o agente."
        )

    # --- channel_send / channel_list — Bauer Gateway ----------------------------

    def _channel_send(self, args: dict) -> str:
        """Envia mensagem a um canal do gateway via outbox durável.

        A mensagem NÃO é entregue inline — entra no GatewayOutbox (SQLite)
        e o `bauer gateway start` (pump) entrega com retry. Isso torna o
        envio auditável e resiliente a quedas de rede no meio do turno.
        """
        channel_name = str(args.get("channel", "")).strip()
        text = str(args.get("text", "")).strip()
        if not channel_name:
            raise ToolError("channel_send requer 'channel'. Use channel_list para ver os nomes.")
        if not text:
            raise ToolError("channel_send requer 'text'.")

        from .gateway_channels import GatewayChannelRegistry
        from .gateway_outbox import GatewayOutbox

        registry = GatewayChannelRegistry(self.workspace)
        entry = registry.get(channel_name)
        if entry is None:
            known = ", ".join(c.name for c in registry.list_channels()) or "(nenhum)"
            raise ToolError(
                f"Canal '{channel_name}' não existe. Canais configurados: {known}. "
                "Registre com: bauer gateway-channel-add <nome> <plataforma> <target>"
            )
        if not entry.enabled:
            raise ToolError(f"Canal '{channel_name}' está desabilitado.")

        outbox = GatewayOutbox(self.workspace)
        message = outbox.enqueue(
            channel=entry.platform,
            target=entry.target,
            payload={"text": text, "source": "channel_send"},
            metadata=dict(entry.metadata),
        )
        return (
            f"Mensagem enfileirada para '{channel_name}' ({entry.platform}). "
            f"id={message.message_id} — entrega via `bauer gateway start`."
        )

    def _channel_list(self, args: dict) -> str:
        """Lista canais de notificação registrados no gateway."""
        from .gateway_channels import GatewayChannelRegistry

        registry = GatewayChannelRegistry(self.workspace)
        channels = registry.list_channels(include_disabled=True)
        if not channels:
            return (
                "Nenhum canal configurado. Registre com: "
                "bauer gateway-channel-add <nome> <plataforma> <target>"
            )
        lines = ["Canais do Bauer Gateway:"]
        for c in channels:
            state = "on" if c.enabled else "off"
            lines.append(f"- {c.name} [{c.platform}] → {c.target} ({state})")
        return "\n".join(lines)

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

    # --- G7: Code Intelligence Light ------------------------------------------

    def _code_symbols(self, args: dict) -> str:
        file_rel = str(args.get("file", "")).strip()
        if not file_rel:
            raise ToolError("code_symbols requer 'file'.")
        from .code_intelligence import get_python_symbols
        path = self._sandbox(file_rel)
        if not path.exists():
            raise ToolError(f"Arquivo nao encontrado: '{file_rel}'")
        result = get_python_symbols(str(path))
        if "error" in result:
            return f"[Erro de parse] {result['error']}"
        lines: list[str] = []
        for fn in result.get("functions", []):
            async_prefix = "async " if fn.get("is_async") else ""
            args_str = ", ".join(fn.get("args", []))
            lines.append(f"  func  L{fn['line']:4d}  {async_prefix}{fn['name']}({args_str})")
        for cls in result.get("classes", []):
            bases = ", ".join(cls.get("bases", []))
            base_str = f"({bases})" if bases else ""
            lines.append(f"  class L{cls['line']:4d}  {cls['name']}{base_str}")
        for var in result.get("variables", []):
            lines.append(f"  var   L{var['line']:4d}  {var['name']}")
        if not lines:
            return f"Nenhum simbolo encontrado em '{file_rel}'"
        return f"[Simbolos de {file_rel}]\n" + "\n".join(lines)

    def _find_definition(self, args: dict) -> str:
        symbol = str(args.get("symbol", "")).strip()
        workspace = str(args.get("workspace", ".")).strip() or "."
        if not symbol:
            raise ToolError("find_definition requer 'symbol'.")
        from .code_intelligence import find_symbol_definitions
        root = self._sandbox(workspace)
        results = find_symbol_definitions(symbol, str(root))
        if not results:
            return f"Definicao de '{symbol}' nao encontrada em '{workspace}'"
        lines = [f"  {r['type']:8s} L{r['line']:4d}  {r['file']}\n            {r['signature']}"
                 for r in results]
        return f"[Definicoes de '{symbol}']\n" + "\n".join(lines)

    def _get_imports(self, args: dict) -> str:
        file_rel = str(args.get("file", "")).strip()
        if not file_rel:
            raise ToolError("get_imports requer 'file'.")
        from .code_intelligence import get_imports
        path = self._sandbox(file_rel)
        if not path.exists():
            raise ToolError(f"Arquivo nao encontrado: '{file_rel}'")
        imports = get_imports(str(path))
        if not imports:
            return f"Nenhum import encontrado em '{file_rel}'"
        return f"[Imports de {file_rel}]\n" + "\n".join(f"  {imp}" for imp in imports)

    def _find_usages(self, args: dict) -> str:
        symbol = str(args.get("symbol", "")).strip()
        workspace = str(args.get("workspace", ".")).strip() or "."
        file_pattern = str(args.get("file_pattern", "*.py")).strip() or "*.py"
        if not symbol:
            raise ToolError("find_usages requer 'symbol'.")
        from .code_intelligence import get_call_sites
        root = self._sandbox(workspace)
        results = get_call_sites(symbol, str(root), file_pattern=file_pattern)
        if not results:
            return f"Nenhum uso de '{symbol}' encontrado em '{workspace}'"
        lines = [f"  {r['file']}:{r['line']}: {r['context']}" for r in results]
        return f"[Usos de '{symbol}']\n" + "\n".join(lines)

    # --- G15: LSP Tools --------------------------------------------------------

    def _lsp_call(self, method: str, file_rel: str, line: int, char: int) -> dict | list | None:
        """Helper: run an LSP async call synchronously using asyncio."""
        import asyncio
        from pathlib import Path
        from .lsp.servers import server_for_file
        from .lsp.manager import get_or_start

        file_abs = self._sandbox(file_rel)
        server_cfg = server_for_file(str(file_abs))
        if server_cfg is None:
            return None

        workspace = str(self.workspace)
        file_uri = file_abs.as_uri()

        async def _run():
            mgr = await get_or_start(server_cfg, workspace)
            if mgr is None:
                return None
            client = mgr.client()
            if method == "hover":
                return await client.hover(file_uri, line, char)
            if method == "definitions":
                return await client.definitions(file_uri, line, char)
            if method == "references":
                return await client.references(file_uri, line, char)
            if method == "diagnostics":
                return await client.diagnostics(file_uri)
            return None

        try:
            loop = asyncio.get_event_loop()
            if loop.is_closed():
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
            return loop.run_until_complete(_run())
        except RuntimeError:
            # Already in async context — can't run_until_complete
            return None
        except Exception:
            return None

    def _lsp_hover(self, args: dict) -> str:
        file_rel = str(args.get("file", "")).strip()
        line = int(args.get("line", 0))
        char = int(args.get("character", 0))
        if not file_rel:
            raise ToolError("lsp_hover requer 'file'.")
        result = self._lsp_call("hover", file_rel, line, char)
        if result is None:
            server_hint = "pyright" if file_rel.endswith(".py") else "typescript-language-server"
            return json.dumps({"error": "LSP server not running", "hint": f"pip/npm install {server_hint}"})
        return json.dumps(result, indent=2, ensure_ascii=False)

    def _lsp_definitions(self, args: dict) -> str:
        file_rel = str(args.get("file", "")).strip()
        line = int(args.get("line", 0))
        char = int(args.get("character", 0))
        if not file_rel:
            raise ToolError("lsp_definitions requer 'file'.")
        result = self._lsp_call("definitions", file_rel, line, char)
        if result is None:
            return json.dumps({"error": "LSP server not running"})
        return json.dumps(result, indent=2, ensure_ascii=False)

    def _lsp_references(self, args: dict) -> str:
        file_rel = str(args.get("file", "")).strip()
        line = int(args.get("line", 0))
        char = int(args.get("character", 0))
        if not file_rel:
            raise ToolError("lsp_references requer 'file'.")
        result = self._lsp_call("references", file_rel, line, char)
        if result is None:
            return json.dumps({"error": "LSP server not running"})
        return json.dumps(result, indent=2, ensure_ascii=False)

    def _lsp_diagnostics(self, args: dict) -> str:
        file_rel = str(args.get("file", "")).strip()
        if not file_rel:
            raise ToolError("lsp_diagnostics requer 'file'.")
        result = self._lsp_call("diagnostics", file_rel, 0, 0)
        if result is None:
            return json.dumps({"error": "LSP server not running"})
        return json.dumps(result, indent=2, ensure_ascii=False)

    # --- mcp_call --------------------------------------------------------------

    def _mcp_call(self, args: dict) -> str:
        """Chama tool em servidor MCP via stdio (JSON-RPC 2.0 puro — sem pacote 'mcp').

        Usa McpClient nativo do Bauer. Não requer pip install mcp.

        Configuração em config.yaml:
            mcp:
              servers:
                meu_servidor:
                  command: ["python", "-m", "meu_mcp_server"]
                  timeout: 30

        Ou via variável de ambiente:
            MCP_SERVER_MEU_SERVIDOR="python -m meu_mcp_server"
        """
        server_name = args.get("server", "").strip()
        tool_name = args.get("tool", "").strip()
        arguments = args.get("arguments", {})

        if not server_name:
            raise ToolError("mcp_call requer 'server'.")
        if not tool_name:
            raise ToolError("mcp_call requer 'tool'.")
        if not isinstance(arguments, dict):
            try:
                arguments = json.loads(str(arguments))
            except Exception:
                raise ToolError("mcp_call: 'arguments' deve ser um objeto JSON.")

        # Resolve configuração do servidor
        if "_get_mcp_server_cmd" in self.__dict__:
            import asyncio
            server_cmd = self._get_mcp_server_cmd(server_name)
            legacy_call = self._mcp_call_legacy_async(server_cmd, tool_name, arguments)
            try:
                return asyncio.run(legacy_call)
            finally:
                legacy_call.close()

        server_cmd, server_env, server_timeout = self._resolve_mcp_server(server_name)

        from .mcp_client import McpClient, McpServerConfig, McpError, McpToolError, McpTimeoutError
        cfg = McpServerConfig(
            name=server_name,
            command=server_cmd,
            env=server_env,
            timeout=server_timeout,
        )
        try:
            with McpClient(cfg) as client:
                return client.call_tool(tool_name, arguments)
        except McpToolError as exc:
            raise ToolError(str(exc)) from exc
        except McpTimeoutError as exc:
            raise ToolError(str(exc)) from exc
        except McpError as exc:
            raise ToolError(
                f"mcp_call: erro de conexao com '{server_name}': {exc}"
            ) from exc
        except Exception as exc:
            raise ToolError(
                f"mcp_call: erro inesperado chamando '{tool_name}' em '{server_name}': {exc}"
            ) from exc

    def _get_mcp_server_cmd(self, server_name: str) -> list[str]:
        """Compatibilidade com a API MCP anterior que retornava apenas o comando."""
        server_cmd, _, _ = self._resolve_mcp_server(server_name)
        return server_cmd

    async def _mcp_call_legacy_async(
        self,
        server_cmd: list[str],
        tool_name: str,
        arguments: dict,
    ) -> str:
        """Ponte para testes/extensoes que ainda sobrescrevem o cliente MCP legado."""
        raise ToolError(
            "mcp_call legado nao esta disponivel; use a configuracao MCP nativa do Bauer."
        )

    def _resolve_mcp_server(
        self, server_name: str
    ) -> tuple[list[str], dict[str, str], float]:
        """Resolve comando, env e timeout de um servidor MCP.

        Ordem de busca:
        1. Variável de ambiente: MCP_SERVER_<NAME>="python -m meu_servidor"
        2. config.yaml → mcp.servers.<name>
        3. Atributo legado self._mcp_config (compat)

        Returns:
            (command, env, timeout)
        """
        import os

        env_key = f"MCP_SERVER_{server_name.upper().replace('-', '_')}"
        env_val = os.environ.get(env_key, "")
        if env_val:
            return env_val.split(), {}, 30.0

        # Tenta McpSection do config_loader (injetado via self._mcp_config)
        mcp_config = getattr(self, "_mcp_config", None)
        if mcp_config is not None:
            servers = getattr(mcp_config, "servers", None) or {}
            if server_name in servers:
                srv = servers[server_name]
                if hasattr(srv, "command"):
                    # McpServerEntry (Pydantic)
                    cmd = srv.command if isinstance(srv.command, list) else srv.command.split()
                    env = dict(getattr(srv, "env", {}) or {})
                    timeout = float(getattr(srv, "timeout", 30))
                    return cmd, env, timeout
                elif isinstance(srv, dict) and "command" in srv:
                    cmd = srv["command"]
                    if isinstance(cmd, str):
                        cmd = cmd.split()
                    env = dict(srv.get("env", {}) or {})
                    timeout = float(srv.get("timeout", 30))
                    return cmd, env, timeout

        raise ToolError(
            f"Servidor MCP '{server_name}' nao configurado.\n"
            "Configure via:\n"
            f"  1. Variavel de ambiente: {env_key}=python -m meu_servidor\n"
            "  2. config.yaml:\n"
            "       mcp:\n"
            "         servers:\n"
            f"           {server_name}:\n"
            "             command: [\"python\", \"-m\", \"meu_servidor\"]\n"
            "             timeout: 30"
        )

    # ==========================================================================
    # CRONJOB
    # ==========================================================================

    _CRONJOB_FILE = ".bauer_cronjobs.json"

    def _cronjob_path(self) -> Path:
        return self.workspace / self._CRONJOB_FILE

    def _cronjob_load(self) -> dict:
        p = self._cronjob_path()
        if not p.exists():
            return {}
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def _cronjob_save(self, data: dict) -> None:
        self._cronjob_path().write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _parse_schedule(self, schedule: str) -> dict:
        """Parseia schedule string para dict normalizado.

        Formatos suportados:
          every 30m / every 2h / every 1d
          daily 09:00
          cron: */5 * * * *
        """
        s = schedule.strip().lower()

        if s.startswith("every "):
            rest = s[6:].strip()
            unit_map = {"m": "minutes", "h": "hours", "d": "days",
                        "min": "minutes", "hour": "hours", "day": "days",
                        "mins": "minutes", "hours": "hours", "days": "days"}
            for suffix, unit in sorted(unit_map.items(), key=lambda x: -len(x[0])):
                if rest.endswith(suffix):
                    try:
                        n = int(rest[: -len(suffix)].strip())
                        return {"type": "interval", "unit": unit, "value": n}
                    except ValueError:
                        pass
            raise ToolError(
                f"Schedule '{schedule}' invalido. Exemplos: 'every 30m', 'every 2h', 'every 1d'."
            )

        if s.startswith("daily "):
            time_str = schedule.strip()[6:].strip()
            try:
                h, m_str = time_str.split(":")
                return {"type": "daily", "hour": int(h), "minute": int(m_str)}
            except Exception:
                raise ToolError(
                    f"Schedule '{schedule}' invalido. Formato: 'daily HH:MM' (ex: 'daily 09:00')."
                )

        if s.startswith("cron:") or s.startswith("cron "):
            expr = schedule.strip()[5:].strip()
            parts = expr.split()
            if len(parts) != 5:
                raise ToolError(
                    f"Expressao cron invalida: '{expr}'. Formato: '*/5 * * * *' (5 campos)."
                )
            return {"type": "cron", "expression": expr}

        raise ToolError(
            f"Schedule '{schedule}' nao reconhecido.\n"
            "Formatos suportados:\n"
            "  every 30m | every 2h | every 1d\n"
            "  daily 09:00\n"
            "  cron: */5 * * * *"
        )

    def _cronjob_next_run(self, schedule: dict) -> str:
        """Calcula próxima execução como string legível."""
        from datetime import datetime, timezone as _tz, timedelta
        now = datetime.now(_tz.utc)

        if schedule["type"] == "interval":
            unit = schedule["unit"]
            val = schedule["value"]
            delta = timedelta(**{unit: val})
            nxt = now + delta
            return nxt.isoformat()

        if schedule["type"] == "daily":
            nxt = now.replace(
                hour=schedule["hour"], minute=schedule["minute"],
                second=0, microsecond=0,
            )
            if nxt <= now:
                nxt += timedelta(days=1)
            return nxt.isoformat()

        return "cron — calculado em runtime"

    def _cronjob(self, args: dict) -> str:
        """Gerencia tarefas agendadas persistentes."""
        from datetime import datetime, timezone as _tz

        action = str(args.get("action", "")).lower().strip()
        if not action:
            raise ToolError("cronjob requer 'action': create | list | delete | run | pause | resume.")

        jobs = self._cronjob_load()

        # ── create ──────────────────────────────────────────────────────────
        if action == "create":
            name = str(args.get("name", "")).strip()
            command = str(args.get("command", "")).strip()
            schedule_str = str(args.get("schedule", "")).strip()
            mode = str(args.get("mode", "python")).lower().strip()

            if not name:
                raise ToolError("cronjob create requer 'name'.")
            if not command:
                raise ToolError("cronjob create requer 'command'.")
            if not schedule_str:
                raise ToolError("cronjob create requer 'schedule'.")
            if mode not in ("python", "shell"):
                raise ToolError("cronjob: 'mode' deve ser 'python' ou 'shell'.")
            if name in jobs:
                raise ToolError(
                    f"Job '{name}' ja existe. Use delete primeiro ou escolha outro nome."
                )

            schedule = self._parse_schedule(schedule_str)
            next_run = self._cronjob_next_run(schedule)
            now = datetime.now(_tz.utc).isoformat()

            jobs[name] = {
                "command": command,
                "mode": mode,
                "schedule": schedule,
                "schedule_str": schedule_str,
                "status": "active",
                "created_at": now,
                "last_run": None,
                "last_result": None,
                "next_run": next_run,
                "run_count": 0,
            }
            self._cronjob_save(jobs)
            return (
                f"Job '{name}' criado.\n"
                f"  Modo:      {mode}\n"
                f"  Schedule:  {schedule_str}\n"
                f"  Prox. run: {next_run}\n"
                f"  Comando:   {command[:80]}{'...' if len(command) > 80 else ''}"
            )

        # ── list ────────────────────────────────────────────────────────────
        elif action == "list":
            if not jobs:
                return "Nenhum cronjob configurado."
            lines = [f"Cronjobs ({len(jobs)}):"]
            for jname, jdata in sorted(jobs.items()):
                status_icon = "▶" if jdata["status"] == "active" else "⏸"
                last = jdata.get("last_run") or "nunca"
                lines.append(
                    f"  {status_icon} {jname} [{jdata['mode']}] "
                    f"— {jdata['schedule_str']} "
                    f"| runs: {jdata['run_count']} | ultimo: {last[:19] if last != 'nunca' else 'nunca'}"
                )
            return "\n".join(lines)

        # ── delete ──────────────────────────────────────────────────────────
        elif action == "delete":
            name = str(args.get("name", "")).strip()
            if not name:
                raise ToolError("cronjob delete requer 'name'.")
            if name not in jobs:
                raise ToolError(f"Job '{name}' nao encontrado.")
            del jobs[name]
            self._cronjob_save(jobs)
            return f"Job '{name}' removido."

        # ── run ─────────────────────────────────────────────────────────────
        elif action == "run":
            name = str(args.get("name", "")).strip()
            if not name:
                raise ToolError("cronjob run requer 'name'.")
            if name not in jobs:
                raise ToolError(f"Job '{name}' nao encontrado.")

            job = jobs[name]
            now = datetime.now(_tz.utc).isoformat()

            if job["mode"] == "python":
                result = self._execute_code({"code": job["command"], "timeout": 60})
            else:
                # shell mode — aplica denylist antes de executar
                import subprocess
                import shlex as _shlex
                from .shell_runner import _DENYLIST as _SR_DENYLIST
                cmd_str = job["command"]
                for pattern in _SR_DENYLIST:
                    if pattern.search(cmd_str):
                        raise ToolError(
                            f"cronjob run: comando bloqueado por denylist — padrão '{pattern.pattern}'. "
                            f"Edite o job para remover o comando perigoso."
                        )
                try:
                    proc = subprocess.run(
                        _shlex.split(cmd_str),
                        capture_output=True, text=True,
                        encoding="utf-8", errors="replace",
                        timeout=60, cwd=str(self.workspace),
                    )
                    _out = proc.stdout or ""
                    _err = proc.stderr or ""
                    result = f"exit: {proc.returncode}\n"
                    if _out.strip():
                        result += f"--- stdout ---\n{_out.strip()}"
                    if _err.strip():
                        result += f"\n--- stderr ---\n{_err.strip()}"
                except subprocess.TimeoutExpired:
                    result = "Timeout: comando excedeu 60s."
                except Exception as exc:
                    result = f"Erro: {exc}"

            jobs[name]["last_run"] = now
            jobs[name]["last_result"] = result[:500]
            jobs[name]["run_count"] = jobs[name].get("run_count", 0) + 1
            jobs[name]["next_run"] = self._cronjob_next_run(job["schedule"])
            self._cronjob_save(jobs)
            return f"[{name}] Executado em {now[:19]}Z\n{result}"

        # ── pause / resume ──────────────────────────────────────────────────
        elif action in ("pause", "resume"):
            name = str(args.get("name", "")).strip()
            if not name:
                raise ToolError(f"cronjob {action} requer 'name'.")
            if name not in jobs:
                raise ToolError(f"Job '{name}' nao encontrado.")
            new_status = "paused" if action == "pause" else "active"
            jobs[name]["status"] = new_status
            self._cronjob_save(jobs)
            icon = "⏸" if new_status == "paused" else "▶"
            return f"{icon} Job '{name}' {new_status}."

        else:
            raise ToolError(
                f"Acao '{action}' desconhecida. Use: create | list | delete | run | pause | resume."
            )

    # ==========================================================================
    # SESSION_SEARCH
    # ==========================================================================

    def _session_search(self, args: dict) -> str:
        """Busca full-text/regex em memória persistente e logs de sessão.

        Fontes:
          memory   — .bauer_memory.json (chaves + valores)
          sessions — arquivos .jsonl / .json de sessão no workspace
          all      — ambas (padrão)
        """
        import re as _re

        action = str(args.get("action", "search")).lower().strip()
        source = str(args.get("source", "all")).lower().strip()

        if action == "recent":
            n = int(args.get("n", 10))
            return self._session_search_recent(n, source)

        if action != "search":
            raise ToolError("session_search: action deve ser 'search' ou 'recent'.")

        query = str(args.get("query", "")).strip()
        if not query:
            raise ToolError("session_search search requer 'query'.")

        results: list[str] = []

        # ── Busca em memory ───────────────────────────────────────────────
        if source in ("memory", "all"):
            mem = self._memory_load()
            try:
                pattern = _re.compile(query, _re.IGNORECASE)
            except _re.error:
                pattern = _re.compile(_re.escape(query), _re.IGNORECASE)

            mem_hits = []
            for key, entry in mem.items():
                val = entry["value"] if isinstance(entry, dict) else str(entry)
                ts = entry.get("updated_at", "") if isinstance(entry, dict) else ""
                if pattern.search(key) or pattern.search(val):
                    preview = val[:120].replace("\n", " ")
                    mem_hits.append(f"  [memory] {key}: {preview} ({ts[:10]})")

            if mem_hits:
                results.append(f"Memory ({len(mem_hits)} resultado(s)):")
                results.extend(mem_hits)

        # ── Busca em logs de sessão ───────────────────────────────────────
        if source in ("sessions", "all"):
            session_hits = self._search_session_files(query)
            if session_hits:
                results.append(f"\nSessoes ({len(session_hits)} resultado(s)):")
                results.extend(session_hits)

        if not results:
            return f"Nenhum resultado para '{query}' em '{source}'."

        header = f"session_search '{query}' em [{source}]:\n"
        return header + "\n".join(results)

    def _session_search_recent(self, n: int, source: str) -> str:
        """Retorna as N entradas mais recentes da memória/sessões."""
        results: list[str] = []
        n = max(1, min(n, 100))

        if source in ("memory", "all"):
            mem = self._memory_load()
            sorted_entries = sorted(
                mem.items(),
                key=lambda x: x[1].get("updated_at", "") if isinstance(x[1], dict) else "",
                reverse=True,
            )[:n]
            if sorted_entries:
                results.append(f"Memory (mais recentes {len(sorted_entries)}):")
                for key, entry in sorted_entries:
                    val = entry["value"] if isinstance(entry, dict) else str(entry)
                    ts = entry.get("updated_at", "")[:10] if isinstance(entry, dict) else ""
                    results.append(f"  [{ts}] {key}: {val[:80].replace(chr(10), ' ')}")

        return "\n".join(results) if results else "Nenhuma entrada recente encontrada."

    def _search_session_files(self, query: str, top_k: int = 20) -> list[str]:
        """Busca em sessões salvas — usa FTS5 (SqliteSessionStore) ou fallback JSONL.

        Tenta SqliteSessionStore primeiro (FTS5 semântico).
        Se o banco não existir, cai para busca linear em .jsonl.
        """
        # ── Caminho 1: SqliteSessionStore (FTS5) ──────────────────────────
        sessions_db_candidates = [
            self.workspace.parent / "memory" / "sessions" / "sessions.db",
            self.workspace / "memory" / "sessions" / "sessions.db",
        ]
        for db_path in sessions_db_candidates:
            if db_path.exists():
                try:
                    from .sqlite_session_store import SqliteSessionStore as _SqliteStore
                    store = _SqliteStore(db_path.parent)
                    results = store.search_sessions(query, top_k=top_k)
                    if results:
                        return [
                            f"  [session:{r['session_id']}] [{r['role']}] {r['snippet']}"
                            for r in results
                        ]
                    return []  # banco existe mas sem resultados
                except Exception:
                    pass  # fallback para JSONL

        # ── Caminho 2: fallback linear em .jsonl ──────────────────────────
        import re as _re
        hits: list[str] = []
        try:
            pattern = _re.compile(query, _re.IGNORECASE)
        except _re.error:
            pattern = _re.compile(_re.escape(query), _re.IGNORECASE)

        search_dirs = [self.workspace, self.workspace.parent]
        for d in search_dirs:
            if not d.exists():
                continue
            for ext in ("*.jsonl", "*.json"):
                for fpath in list(d.glob(ext))[:20]:
                    if fpath.name.startswith(".bauer_"):
                        continue
                    try:
                        text = fpath.read_text(encoding="utf-8", errors="ignore")
                        for i, line in enumerate(text.splitlines()):
                            if pattern.search(line):
                                preview = line[:100].strip()
                                hits.append(f"  [{fpath.name}:{i+1}] {preview}")
                                if len(hits) >= top_k:
                                    return hits
                    except Exception:
                        continue
        return hits

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
                from .agent import run_one_turn
                response = run_one_turn(self._llm_client, messages, tools=None)
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
                from .agent import run_one_turn
                synthesis = run_one_turn(
                    self._llm_client,
                    [{"role": "user", "content": synthesis_prompt}],
                    tools=None,
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
        if self._llm_client is None:
            raise ToolError(
                "video_analyze requer llm_client configurado com suporte a visao.\n"
                "Providers suportados: Gemini (gemini-*), OpenAI (gpt-4o)."
            )

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
            from .agent import run_one_turn
            result = run_one_turn(self._llm_client, [message], tools=None)
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
                resp = run_one_turn(self._llm_client, [msg], tools=None)
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
                from .agent import run_one_turn
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
                synthesis = run_one_turn(self._llm_client, [synth_msg], tools=None)
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
                from .agent import run_one_turn
                msg = {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": f"Frame {idx}/{total} do GIF. {query}"},
                        {"type": "image_url", "image_url": {"url": data_url}},
                    ],
                }
                resp = run_one_turn(self._llm_client, [msg], tools=None)
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

    def _load_skills(self) -> dict:
        p = self.workspace / self._SKILLS_FILE
        if p.exists():
            try:
                return json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                return {}
        return {}

    def _save_skills(self, skills: dict) -> None:
        p = self.workspace / self._SKILLS_FILE
        p.write_text(json.dumps(skills, ensure_ascii=False, indent=2), encoding="utf-8")

    def _skill_manage(self, args: dict) -> str:
        action = str(args.get("action", "")).strip().lower()
        name = str(args.get("name", "")).strip()
        if not action:
            raise ToolError("skill_manage: 'action' é obrigatório (create|update|delete).")
        if not name:
            raise ToolError("skill_manage: 'name' é obrigatório.")

        skills = self._load_skills()

        if action == "delete":
            if name not in skills:
                raise ToolError(f"skill_manage: skill '{name}' não encontrada.")
            del skills[name]
            self._save_skills(skills)
            return f"[skill_manage] Skill '{name}' removida."

        if action in ("create", "update"):
            description = str(args.get("description", "")).strip()
            content = str(args.get("content", "")).strip()
            if not description:
                raise ToolError("skill_manage: 'description' é obrigatório para create/update.")
            if not content:
                raise ToolError("skill_manage: 'content' é obrigatório para create/update.")
            if action == "create" and name in skills:
                raise ToolError(
                    f"skill_manage: skill '{name}' já existe. Use action='update' para editar."
                )
            tags = args.get("tags", [])
            if not isinstance(tags, list):
                tags = [str(tags)]
            import time as _time
            now = _time.time()
            existing = skills.get(name, {})
            skills[name] = {
                "name": name,
                "description": description,
                "content": content,
                "tags": tags,
                "created_at": existing.get("created_at", now),
                "updated_at": now,
            }
            self._save_skills(skills)
            verb = "criada" if action == "create" else "atualizada"
            return f"[skill_manage] Skill '{name}' {verb}. Tags: {tags or '—'}."

        raise ToolError(f"skill_manage: action '{action}' inválida. Use create|update|delete.")

    def _skill_view(self, args: dict) -> str:
        name = str(args.get("name", "")).strip()
        if not name:
            raise ToolError("skill_view: 'name' é obrigatório.")
        skills = self._load_skills()
        if name not in skills:
            available = ", ".join(sorted(skills.keys())) or "(nenhuma)"
            raise ToolError(f"skill_view: skill '{name}' não encontrada. Disponíveis: {available}")
        s = skills[name]
        import time as _time
        lines = [
            f"[skill] {s['name']}",
            f"Descrição: {s['description']}",
            f"Tags: {', '.join(s.get('tags', [])) or '—'}",
            f"Criada: {_time.strftime('%Y-%m-%d %H:%M', _time.localtime(s.get('created_at', 0)))}",
            f"Atualizada: {_time.strftime('%Y-%m-%d %H:%M', _time.localtime(s.get('updated_at', 0)))}",
            "",
            "─── Conteúdo ───",
            s["content"],
        ]
        return "\n".join(lines)

    def _skills_list(self, args: dict) -> str:
        skills = self._load_skills()
        if not skills:
            return "[skills_list] Nenhuma skill registrada."
        filt = str(args.get("filter", "")).strip().lower()
        results = []
        for s in skills.values():
            if filt:
                tag_match = any(filt in t.lower() for t in s.get("tags", []))
                name_match = filt in s["name"].lower()
                desc_match = filt in s.get("description", "").lower()
                if not (tag_match or name_match or desc_match):
                    continue
            results.append(s)
        if not results:
            return f"[skills_list] Nenhuma skill encontrada para filtro '{filt}'."
        lines = [f"[skills_list] {len(results)} skill(s):"]
        for s in sorted(results, key=lambda x: x["name"]):
            tags = ", ".join(s.get("tags", [])) or "—"
            lines.append(f"  • {s['name']} [{tags}] — {s.get('description', '')[:80]}")
        return "\n".join(lines)

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

    # =========================================================================
    # Kanban board
    # =========================================================================

    def _load_kanban(self) -> dict:
        wm = WorkspaceManager(self.workspace)
        tasks = wm.list_tasks()
        next_id = 1
        numeric_ids = [int(t.id) for t in tasks if t.id.isdigit()]
        if numeric_ids:
            next_id = max(numeric_ids) + 1

        board_tasks: dict[str, dict] = {}
        for task in tasks:
            public_id = self._kanban_public_id(task.id)
            board_tasks[public_id] = self._workspace_task_to_kanban(task, tasks)
        return {"tasks": board_tasks, "next_id": next_id}

    def _save_kanban(self, board: dict) -> None:
        raise ToolError("kanban: TASKS.md e a fonte unica; use as tools kanban_* para alterar tarefas.")

    def _kanban_public_id(self, task_id: str) -> str:
        raw = str(task_id).strip()
        if raw.upper().startswith("T"):
            raw = raw[1:]
        if raw.isdigit():
            return f"T{int(raw):04d}"
        return raw

    def _kanban_workspace_id(self, task_id: str) -> str:
        raw = str(task_id).strip()
        if raw.upper().startswith("T") and raw[1:].isdigit():
            raw = raw[1:]
        if raw.isdigit():
            return str(int(raw)).zfill(3)
        return raw.zfill(3)

    def _kanban_workspace_status(self, status: str) -> str:
        status_key = str(status).strip().lower()
        if status_key.upper() in self._WORKSPACE_TO_KANBAN_STATUS:
            return status_key.upper()
        if status_key not in self._KANBAN_TO_WORKSPACE_STATUS:
            raise ToolError("kanban: status deve ser todo | ready | in_progress | blocked | failed | done.")
        return self._KANBAN_TO_WORKSPACE_STATUS[status_key]

    def _kanban_status(self, workspace_status: str) -> str:
        return self._WORKSPACE_TO_KANBAN_STATUS.get(workspace_status.upper(), workspace_status.lower())

    def _workspace_task_to_kanban(self, task, all_tasks: list | None = None) -> dict:  # type: ignore[no-untyped-def]
        all_tasks = all_tasks or []
        public_id = self._kanban_public_id(task.id)
        children = [
            self._kanban_public_id(child.id)
            for child in all_tasks
            if child.parent_id and child.parent_id == task.id
        ]
        parent_id = self._kanban_public_id(task.parent_id) if task.parent_id else ""
        return {
            "id": public_id,
            "workspace_id": task.id,
            "title": task.title,
            "description": task.description,
            "status": self._kanban_status(task.status),
            "priority": task.priority or "medium",
            "assignee": task.assignee,
            "parent_id": parent_id,
            "children": children,
            "comments": list(task.comments),
            "created_at": task.created_at,
            "updated_at": task.created_at,
        }

    def _kanban_get_task(self, task_id: str) -> dict:
        workspace_id = self._kanban_workspace_id(task_id)
        wm = WorkspaceManager(self.workspace)
        try:
            task = wm.get_task(workspace_id)
        except WorkspaceError as exc:
            raise ToolError(f"kanban: tarefa '{task_id}' não encontrada.") from exc
        return self._workspace_task_to_kanban(task, wm.list_tasks())

    def _kanban_enforce_worker_scope(self, task_id: str, action: str) -> dict:
        workspace_id = self._kanban_workspace_id(task_id)
        pinned_raw = os.environ.get("BAUER_KANBAN_TASK", "").strip()
        run_id = os.environ.get("BAUER_KANBAN_RUN_ID", "").strip()
        claim_id = os.environ.get("BAUER_KANBAN_CLAIM_ID", "").strip()
        if not pinned_raw:
            return {"worker": False, "task_id": workspace_id, "run_id": run_id, "claim_id": claim_id}

        pinned_id = self._kanban_workspace_id(pinned_raw)
        if workspace_id != pinned_id:
            self._kanban_record_protocol_violation(
                pinned_id,
                action,
                f"Worker pinned to {self._kanban_public_id(pinned_id)} tried {action} on {self._kanban_public_id(workspace_id)}.",
                run_id=run_id,
            )
            raise ToolError(
                "kanban: worker protocol violation - esta sessao so pode alterar "
                f"{self._kanban_public_id(pinned_id)}."
            )

        wm = WorkspaceManager(self.workspace)
        try:
            task = wm.get_task(workspace_id)
        except WorkspaceError as exc:
            raise ToolError(f"kanban: tarefa '{task_id}' nao encontrada.") from exc

        task_claim = task.metadata.get("claim_id", "")
        if claim_id and task_claim and task_claim != claim_id:
            self._kanban_record_protocol_violation(
                workspace_id,
                action,
                "Worker claim_id does not match task claim_id.",
                run_id=run_id or task.metadata.get("run_id", ""),
            )
            raise ToolError("kanban: worker protocol violation - claim_id nao confere.")

        return {
            "worker": True,
            "task_id": workspace_id,
            "run_id": run_id or task.metadata.get("run_id", ""),
            "claim_id": claim_id,
        }

    def _kanban_record_protocol_violation(self, task_id: str, action: str, message: str, *, run_id: str = "") -> None:
        try:
            from .kanban_store import KanbanStore

            store = KanbanStore(self.workspace)
            store.append_event(
                task_id,
                "worker.protocol_violation",
                actor="worker",
                run_id=run_id,
                message=message,
                metadata={"action": action},
            )
            if run_id:
                store.update_run(run_id, error=message, metadata={"protocol_violation": action})
        except Exception:
            return

    def _kanban_record_worker_event(
        self,
        task_id: str,
        ctx: dict,
        event_type: str,
        message: str,
        *,
        run_status: str | None = None,
        summary: str | None = None,
        error: str | None = None,
        metadata: dict | None = None,
    ) -> None:
        try:
            from .kanban_store import KanbanStore

            store = KanbanStore(self.workspace)
            run_id = str(ctx.get("run_id", ""))
            actor = "worker" if ctx.get("worker") else "tool"
            if run_id and run_status:
                store.update_run(
                    run_id,
                    status=run_status,
                    summary=summary,
                    error=error,
                    metadata=metadata or {},
                )
            store.append_event(
                task_id,
                event_type,
                actor=actor,
                run_id=run_id,
                message=message,
                metadata=metadata or {},
            )
        except Exception:
            return

    def _kanban_clear_claim_metadata(self, workspace_id: str):
        wm = WorkspaceManager(self.workspace)
        return wm.update_task_metadata(
            workspace_id,
            metadata={
                "claim_id": None,
                "claim_expires": None,
                "claimed_by": None,
                "worker_pid": None,
                "heartbeat_at": None,
            },
        )

    def _kanban_create(self, args: dict) -> str:
        title = str(args.get("title", "")).strip()
        if not title:
            raise ToolError("kanban_create: 'title' é obrigatório.")
        priority = str(args.get("priority", "medium")).lower()
        valid_priorities = ("low", "medium", "high", "critical")
        if priority not in valid_priorities:
            raise ToolError(f"kanban_create: priority deve ser {valid_priorities}.")

        wm = WorkspaceManager(self.workspace)
        parent_id = str(args.get("parent_id", "")).strip()
        parent_workspace_id = self._kanban_workspace_id(parent_id) if parent_id else ""
        status_arg = str(args.get("status", "todo")).strip().lower()
        workspace_status = self._kanban_workspace_status(status_arg)
        metadata = {"dispatch": "true"} if workspace_status == "READY" else None
        try:
            task = wm.add_task(
                title,
                description=str(args.get("description", "")),
                status=workspace_status,
                priority=priority,
                assignee=str(args.get("assignee", "")),
                parent_id=parent_workspace_id,
                metadata=metadata,
            )
        except WorkspaceError as exc:
            raise ToolError(f"kanban_create: {exc}") from exc

        task_id = self._kanban_public_id(task.id)
        return f"[kanban] Tarefa criada: {task_id} — '{title}' [{priority}]"

    def _kanban_list(self, args: dict) -> str:
        board = self._load_kanban()
        tasks = list(board["tasks"].values())
        status_filter = str(args.get("status", "all")).lower()
        assignee_filter = str(args.get("assignee", "")).strip().lower()
        priority_filter = str(args.get("priority", "")).strip().lower()

        if status_filter != "all":
            tasks = [t for t in tasks if t["status"] == status_filter]
        if assignee_filter:
            tasks = [t for t in tasks if assignee_filter in t.get("assignee", "").lower()]
        if priority_filter:
            tasks = [t for t in tasks if t["priority"] == priority_filter]

        if not tasks:
            return "[kanban] Nenhuma tarefa encontrada com esses filtros."

        tasks.sort(key=lambda t: (self._KANBAN_PRIORITY_ORDER.get(t["priority"], 9), t["id"]))

        _status_icons = {"todo": "⬜", "in_progress": "🔵", "blocked": "🔴", "done": "✅"}
        lines = [f"[kanban] {len(tasks)} tarefa(s):"]
        for t in tasks:
            icon = _status_icons.get(t["status"], "•")
            assignee = f" @{t['assignee']}" if t.get("assignee") else ""
            lines.append(
                f"  {icon} {t['id']} [{t['priority']}]{assignee} — {t['title']}"
            )
        return "\n".join(lines)

    # Legacy JSON-board implementation kept only as historical fallback code.
    # The registered kanban_* methods below are the TASKS.md-backed source of truth.
    def _legacy_kanban_show(self, args: dict) -> str:
        task_id = str(args.get("task_id", "")).strip()
        if not task_id:
            raise ToolError("kanban_show: 'task_id' é obrigatório.")
        board = self._load_kanban()
        if task_id not in board["tasks"]:
            raise ToolError(f"kanban_show: tarefa '{task_id}' não encontrada.")
        t = board["tasks"][task_id]
        import time as _time
        lines = [
            f"[kanban] {t['id']} — {t['title']}",
            f"  Status: {t['status']} | Prioridade: {t['priority']}",
            f"  Assignee: {t.get('assignee') or '—'}",
            f"  Pai: {t.get('parent_id') or '—'} | Filhos: {', '.join(t.get('children', [])) or '—'}",
            f"  Criado: {_time.strftime('%Y-%m-%d %H:%M', _time.localtime(t['created_at']))}",
            f"  Atualizado: {_time.strftime('%Y-%m-%d %H:%M', _time.localtime(t['updated_at']))}",
        ]
        if t.get("description"):
            lines += ["", "  Descrição:", f"    {t['description']}"]
        if t.get("comments"):
            lines.append("")
            lines.append("  Comentários:")
            for c in t["comments"]:
                ts = _time.strftime("%H:%M", _time.localtime(c.get("at", 0)))
                lines.append(f"    [{ts}] {c.get('author','?')}: {c['text']}")
        return "\n".join(lines)

    def _legacy_kanban_update_status(self, task_id: str, new_status: str, note: str = "") -> dict:
        board = self._load_kanban()
        if task_id not in board["tasks"]:
            raise ToolError(f"kanban: tarefa '{task_id}' não encontrada.")
        import time as _time
        t = board["tasks"][task_id]
        t["status"] = new_status
        t["updated_at"] = _time.time()
        if note:
            t["comments"].append({"author": "system", "text": note, "at": _time.time()})
        self._save_kanban(board)
        return t

    def _legacy_kanban_complete(self, args: dict) -> str:
        task_id = str(args.get("task_id", "")).strip()
        if not task_id:
            raise ToolError("kanban_complete: 'task_id' é obrigatório.")
        result = str(args.get("result", ""))
        t = self._kanban_update_status(task_id, "done", f"Concluído: {result}" if result else "")
        return f"[kanban] {task_id} '{t['title']}' marcado como done."

    def _legacy_kanban_block(self, args: dict) -> str:
        task_id = str(args.get("task_id", "")).strip()
        reason = str(args.get("reason", "")).strip()
        if not task_id:
            raise ToolError("kanban_block: 'task_id' é obrigatório.")
        if not reason:
            raise ToolError("kanban_block: 'reason' é obrigatório.")
        t = self._kanban_update_status(task_id, "blocked", f"Bloqueado: {reason}")
        return f"[kanban] {task_id} '{t['title']}' bloqueado — {reason}"

    def _legacy_kanban_unblock(self, args: dict) -> str:
        task_id = str(args.get("task_id", "")).strip()
        if not task_id:
            raise ToolError("kanban_unblock: 'task_id' é obrigatório.")
        note = str(args.get("note", "Bloqueio removido."))
        t = self._kanban_update_status(task_id, "todo", note)
        return f"[kanban] {task_id} '{t['title']}' desbloqueado."

    def _legacy_kanban_heartbeat(self, args: dict) -> str:
        task_id = str(args.get("task_id", "")).strip()
        progress = str(args.get("progress", "")).strip()
        if not task_id:
            raise ToolError("kanban_heartbeat: 'task_id' é obrigatório.")
        if not progress:
            raise ToolError("kanban_heartbeat: 'progress' é obrigatório.")
        board = self._load_kanban()
        if task_id not in board["tasks"]:
            raise ToolError(f"kanban_heartbeat: tarefa '{task_id}' não encontrada.")
        import time as _time
        t = board["tasks"][task_id]
        t["status"] = "in_progress"
        t["updated_at"] = _time.time()
        t["comments"].append({"author": "heartbeat", "text": progress, "at": _time.time()})
        self._save_kanban(board)
        return f"[kanban] ❤️ {task_id} — {progress}"

    def _legacy_kanban_comment(self, args: dict) -> str:
        task_id = str(args.get("task_id", "")).strip()
        comment = str(args.get("comment", "")).strip()
        if not task_id:
            raise ToolError("kanban_comment: 'task_id' é obrigatório.")
        if not comment:
            raise ToolError("kanban_comment: 'comment' é obrigatório.")
        author = str(args.get("author", "agent"))
        board = self._load_kanban()
        if task_id not in board["tasks"]:
            raise ToolError(f"kanban_comment: tarefa '{task_id}' não encontrada.")
        import time as _time
        board["tasks"][task_id]["comments"].append({
            "author": author, "text": comment, "at": _time.time()
        })
        board["tasks"][task_id]["updated_at"] = _time.time()
        self._save_kanban(board)
        return f"[kanban] Comentário adicionado em {task_id}."

    def _legacy_kanban_link(self, args: dict) -> str:
        parent_id = str(args.get("parent_id", "")).strip()
        child_id = str(args.get("child_id", "")).strip()
        if not parent_id or not child_id:
            raise ToolError("kanban_link: 'parent_id' e 'child_id' são obrigatórios.")
        if parent_id == child_id:
            raise ToolError("kanban_link: parent_id e child_id não podem ser iguais.")
        board = self._load_kanban()
        for tid in (parent_id, child_id):
            if tid not in board["tasks"]:
                raise ToolError(f"kanban_link: tarefa '{tid}' não encontrada.")
        import time as _time
        parent = board["tasks"][parent_id]
        child = board["tasks"][child_id]
        if child_id not in parent["children"]:
            parent["children"].append(child_id)
        child["parent_id"] = parent_id
        child["updated_at"] = _time.time()
        self._save_kanban(board)
        return f"[kanban] {child_id} vinculado como filho de {parent_id}."

    # --- TASKS.md-backed Kanban mutations ------------------------------------

    def _kanban_show(self, args: dict) -> str:
        task_id = str(args.get("task_id", "")).strip()
        if not task_id:
            raise ToolError("kanban_show: 'task_id' e obrigatorio.")
        t = self._kanban_get_task(task_id)
        lines = [
            f"[kanban] {t['id']} - {t['title']}",
            f"  Status: {t['status']} | Prioridade: {t['priority']}",
            f"  Assignee: {t.get('assignee') or '-'}",
            f"  Pai: {t.get('parent_id') or '-'} | Filhos: {', '.join(t.get('children', [])) or '-'}",
            f"  Criado: {t.get('created_at') or '-'}",
        ]
        if t.get("description"):
            lines += ["", "  Descricao:", f"    {t['description']}"]
        if t.get("comments"):
            lines.append("")
            lines.append("  Comentarios:")
            for c in t["comments"]:
                stamp = str(c.get("at", ""))[-14:-9] if c.get("at") else "--:--"
                lines.append(f"    [{stamp}] {c.get('author','?')}: {c['text']}")
        return "\n".join(lines)

    def _kanban_update_status(self, task_id: str, new_status: str, note: str = "") -> dict:
        workspace_id = self._kanban_workspace_id(task_id)
        workspace_status = self._kanban_workspace_status(new_status)
        wm = WorkspaceManager(self.workspace)
        try:
            task = wm.update_task_status(workspace_id, workspace_status)
            if note:
                task = wm.add_task_comment(workspace_id, note, author="system")
        except WorkspaceError as exc:
            raise ToolError(f"kanban: {exc}") from exc
        return self._workspace_task_to_kanban(task, wm.list_tasks())

    def _kanban_complete(self, args: dict) -> str:
        task_id = str(args.get("task_id", "")).strip()
        if not task_id:
            raise ToolError("kanban_complete: 'task_id' e obrigatorio.")
        ctx = self._kanban_enforce_worker_scope(task_id, "kanban_complete")
        result = str(args.get("result", ""))
        t = self._kanban_update_status(task_id, "done", f"Concluido: {result}" if result else "")
        task = self._kanban_clear_claim_metadata(self._kanban_workspace_id(task_id))
        t = self._workspace_task_to_kanban(task, WorkspaceManager(self.workspace).list_tasks())
        self._kanban_record_worker_event(
            self._kanban_workspace_id(task_id),
            ctx,
            "worker.completed_by_tool",
            result or "Task completed via kanban_complete.",
            run_status="succeeded",
            summary=result,
            metadata={"tool": "kanban_complete"},
        )
        return f"[kanban] {t['id']} '{t['title']}' marcado como done."

    def _kanban_block(self, args: dict) -> str:
        task_id = str(args.get("task_id", "")).strip()
        reason = str(args.get("reason", "")).strip()
        if not task_id:
            raise ToolError("kanban_block: 'task_id' e obrigatorio.")
        if not reason:
            raise ToolError("kanban_block: 'reason' e obrigatorio.")
        ctx = self._kanban_enforce_worker_scope(task_id, "kanban_block")
        t = self._kanban_update_status(task_id, "blocked", f"Bloqueado: {reason}")
        task = self._kanban_clear_claim_metadata(self._kanban_workspace_id(task_id))
        t = self._workspace_task_to_kanban(task, WorkspaceManager(self.workspace).list_tasks())
        self._kanban_record_worker_event(
            self._kanban_workspace_id(task_id),
            ctx,
            "worker.blocked_by_tool",
            reason,
            run_status="blocked",
            error=reason,
            metadata={"tool": "kanban_block"},
        )
        return f"[kanban] {t['id']} '{t['title']}' bloqueado - {reason}"

    def _kanban_unblock(self, args: dict) -> str:
        task_id = str(args.get("task_id", "")).strip()
        if not task_id:
            raise ToolError("kanban_unblock: 'task_id' e obrigatorio.")
        note = str(args.get("note", "Bloqueio removido."))
        t = self._kanban_update_status(task_id, "todo", note)
        return f"[kanban] {t['id']} '{t['title']}' desbloqueado."

    def _kanban_heartbeat(self, args: dict) -> str:
        task_id = str(args.get("task_id", "")).strip()
        progress = str(args.get("progress", "")).strip()
        if not task_id:
            raise ToolError("kanban_heartbeat: 'task_id' e obrigatorio.")
        if not progress:
            raise ToolError("kanban_heartbeat: 'progress' e obrigatorio.")
        ctx = self._kanban_enforce_worker_scope(task_id, "kanban_heartbeat")
        t = self._kanban_update_status(task_id, "in_progress", progress)
        self._kanban_record_worker_event(
            self._kanban_workspace_id(task_id),
            ctx,
            "worker.heartbeat",
            progress,
            run_status="running",
            metadata={"tool": "kanban_heartbeat", "progress": progress},
        )
        return f"[kanban] heartbeat {t['id']} - {progress}"

    def _kanban_comment(self, args: dict) -> str:
        task_id = str(args.get("task_id", "")).strip()
        comment = str(args.get("comment", "")).strip()
        if not task_id:
            raise ToolError("kanban_comment: 'task_id' e obrigatorio.")
        if not comment:
            raise ToolError("kanban_comment: 'comment' e obrigatorio.")
        ctx = self._kanban_enforce_worker_scope(task_id, "kanban_comment")
        author = str(args.get("author", "agent"))
        wm = WorkspaceManager(self.workspace)
        workspace_id = self._kanban_workspace_id(task_id)
        try:
            task = wm.add_task_comment(workspace_id, comment, author=author)
        except WorkspaceError as exc:
            raise ToolError(f"kanban_comment: {exc}") from exc
        self._kanban_record_worker_event(
            workspace_id,
            ctx,
            "worker.commented",
            comment,
            run_status="running" if ctx.get("run_id") else None,
            metadata={"tool": "kanban_comment", "author": author},
        )
        return f"[kanban] Comentario adicionado em {self._kanban_public_id(task.id)}."

    def _kanban_link(self, args: dict) -> str:
        parent_id = str(args.get("parent_id", "")).strip()
        child_id = str(args.get("child_id", "")).strip()
        if not parent_id or not child_id:
            raise ToolError("kanban_link: 'parent_id' e 'child_id' sao obrigatorios.")
        if self._kanban_workspace_id(parent_id) == self._kanban_workspace_id(child_id):
            raise ToolError("kanban_link: parent_id e child_id nao podem ser iguais.")

        wm = WorkspaceManager(self.workspace)
        parent_workspace_id = self._kanban_workspace_id(parent_id)
        child_workspace_id = self._kanban_workspace_id(child_id)
        try:
            wm.get_task(parent_workspace_id)
            child = wm.update_task_metadata(child_workspace_id, parent_id=parent_workspace_id)
        except WorkspaceError as exc:
            raise ToolError(f"kanban_link: {exc}") from exc
        return (
            f"[kanban] {self._kanban_public_id(child.id)} vinculado como filho de "
            f"{self._kanban_public_id(parent_workspace_id)}."
        )

    # =========================================================================
    # Browser automation (Playwright)
    # =========================================================================

    _BROWSER_CONSOLE_MSGS: list = []  # captura msgs de console entre calls

    def _ensure_browser(self) -> object:
        """Garante que o browser Playwright está iniciado. Retorna a Page ativa."""
        if self._browser_page is not None:
            return self._browser_page
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            raise ToolError(
                "browser_*: requer Playwright — execute: pip install playwright && playwright install chromium"
            )
        try:
            self._browser_pw = sync_playwright().__enter__()
            self._browser_ctx = self._browser_pw.chromium.launch(headless=True)
            self._browser_page = self._browser_ctx.new_page()
            # Captura mensagens de console
            self._BROWSER_CONSOLE_MSGS = []
            self._browser_page.on(
                "console",
                lambda msg: self._BROWSER_CONSOLE_MSGS.append(
                    f"[{msg.type}] {msg.text}"
                ),
            )
        except Exception as exc:
            raise ToolError(f"browser: falha ao iniciar Playwright — {exc}") from exc
        return self._browser_page

    def _browser_navigate(self, args: dict) -> str:
        url = str(args.get("url", "")).strip()
        if not url.startswith(("http://", "https://")):
            raise ToolError("browser_navigate: 'url' deve começar com http:// ou https://")
        wait_until = str(args.get("wait_until", "load"))
        valid_waits = ("load", "domcontentloaded", "networkidle")
        if wait_until not in valid_waits:
            wait_until = "load"
        page = self._ensure_browser()
        try:
            response = page.goto(url, wait_until=wait_until, timeout=30_000)
            status = response.status if response else "?"
            return f"[browser] Navegou para {url} — status HTTP {status} | título: {page.title()}"
        except Exception as exc:
            raise ToolError(f"browser_navigate: {exc}") from exc

    def _browser_snapshot(self, args: dict) -> str:
        page = self._ensure_browser()
        include_hidden = bool(args.get("include_hidden", False))
        try:
            # Retorna texto acessível via innerText em estrutura simplificada
            script = """
            () => {
                function walk(el, depth) {
                    let tag = el.tagName ? el.tagName.toLowerCase() : '';
                    let role = el.getAttribute ? (el.getAttribute('role') || '') : '';
                    let label = (el.getAttribute ? el.getAttribute('aria-label') : '') || el.innerText || el.textContent || '';
                    label = (label || '').replace(/\\s+/g, ' ').trim().slice(0, 120);
                    let hidden = el.hidden || (el.style && el.style.display === 'none') || (el.getAttribute && el.getAttribute('aria-hidden') === 'true');
                    if (hidden && !arguments[1]) return '';
                    let indent = '  '.repeat(depth);
                    let info = indent + (tag || '?');
                    if (role) info += `[role=${role}]`;
                    if (label) info += ` "${label}"`;
                    let children = Array.from(el.children || []).map(c => walk(c, depth+1)).filter(Boolean).join('\\n');
                    return children ? info + '\\n' + children : info;
                }
                return walk(document.body, 0);
            }
            """
            snapshot = page.evaluate(script)
            url = page.url
            title = page.title()
            return f"[browser_snapshot] {title} | {url}\n\n{snapshot[:8000]}"
        except Exception as exc:
            raise ToolError(f"browser_snapshot: {exc}") from exc

    def _browser_click(self, args: dict) -> str:
        selector = str(args.get("selector", "")).strip()
        if not selector:
            raise ToolError("browser_click: 'selector' é obrigatório.")
        by = str(args.get("by", "css")).lower()
        page = self._ensure_browser()
        try:
            if by == "text":
                page.get_by_text(selector).first.click(timeout=10_000)
            elif by == "role":
                page.get_by_role(selector).first.click(timeout=10_000)
            elif by == "xpath":
                page.locator(f"xpath={selector}").first.click(timeout=10_000)
            else:
                page.locator(selector).first.click(timeout=10_000)
            return f"[browser_click] Clicou em '{selector}' (by={by})"
        except Exception as exc:
            raise ToolError(f"browser_click: {exc}") from exc

    def _browser_type(self, args: dict) -> str:
        selector = str(args.get("selector", "")).strip()
        text = str(args.get("text", ""))
        if not selector:
            raise ToolError("browser_type: 'selector' é obrigatório.")
        clear_first = bool(args.get("clear_first", True))
        page = self._ensure_browser()
        try:
            loc = page.locator(selector).first
            if clear_first:
                loc.fill(text, timeout=10_000)
            else:
                loc.type(text, timeout=10_000)
            return f"[browser_type] Digitou {len(text)} chars em '{selector}'"
        except Exception as exc:
            raise ToolError(f"browser_type: {exc}") from exc

    def _browser_scroll(self, args: dict) -> str:
        direction = str(args.get("direction", "down")).lower()
        amount = int(args.get("amount", 500))
        selector = args.get("selector")
        page = self._ensure_browser()
        try:
            if direction == "top":
                page.evaluate("window.scrollTo(0, 0)")
            elif direction == "bottom":
                page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            elif direction == "up":
                page.evaluate(f"window.scrollBy(0, -{amount})")
            else:
                page.evaluate(f"window.scrollBy(0, {amount})")
            return f"[browser_scroll] Rolou {direction} ({amount}px)"
        except Exception as exc:
            raise ToolError(f"browser_scroll: {exc}") from exc

    def _browser_back(self, args: dict) -> str:
        page = self._ensure_browser()
        try:
            page.go_back(timeout=10_000)
            return f"[browser_back] Voltou para: {page.url}"
        except Exception as exc:
            raise ToolError(f"browser_back: {exc}") from exc

    def _browser_press(self, args: dict) -> str:
        key = str(args.get("key", "")).strip()
        if not key:
            raise ToolError("browser_press: 'key' é obrigatório (ex: Enter, Tab, Control+A).")
        selector = args.get("selector")
        page = self._ensure_browser()
        try:
            if selector:
                page.locator(str(selector)).first.press(key, timeout=10_000)
            else:
                page.keyboard.press(key)
            return f"[browser_press] Pressionou '{key}'"
        except Exception as exc:
            raise ToolError(f"browser_press: {exc}") from exc

    def _browser_console(self, args: dict) -> str:
        self._ensure_browser()
        max_lines = int(args.get("max_lines", 50))
        msgs = self._BROWSER_CONSOLE_MSGS[-max_lines:]
        if not msgs:
            return "[browser_console] Sem mensagens de console."
        return "[browser_console]\n" + "\n".join(msgs)

    def _browser_get_images(self, args: dict) -> str:
        include_data = bool(args.get("include_data_urls", False))
        page = self._ensure_browser()
        try:
            images = page.evaluate("""
            () => Array.from(document.images).map(img => ({
                src: img.src, alt: img.alt, width: img.naturalWidth, height: img.naturalHeight
            }))
            """)
            if not include_data:
                images = [i for i in images if not i["src"].startswith("data:")]
            if not images:
                return "[browser_get_images] Nenhuma imagem encontrada."
            lines = [f"[browser_get_images] {len(images)} imagem(ns):"]
            for img in images[:50]:
                lines.append(
                    f"  {img['width']}x{img['height']} | alt='{img['alt'][:60]}' | {img['src'][:120]}"
                )
            return "\n".join(lines)
        except Exception as exc:
            raise ToolError(f"browser_get_images: {exc}") from exc

    def _browser_vision(self, args: dict) -> str:
        query = str(args.get("query", "")).strip()
        if not query:
            raise ToolError("browser_vision: 'query' é obrigatório.")
        if self._llm_client is None:
            raise ToolError("browser_vision: llm_client não configurado.")
        page = self._ensure_browser()
        try:
            screenshot_bytes = page.screenshot(full_page=False)
        except Exception as exc:
            raise ToolError(f"browser_vision: falha ao capturar screenshot — {exc}") from exc

        import base64
        b64 = base64.b64encode(screenshot_bytes).decode()
        data_url = f"data:image/png;base64,{b64}"
        try:
            from .agent import run_one_turn
            msg = {
                "role": "user",
                "content": [
                    {"type": "text", "text": f"[screenshot do browser — {page.url}] {query}"},
                    {"type": "image_url", "image_url": {"url": data_url}},
                ],
            }
            return str(run_one_turn(self._llm_client, [msg], tools=None))
        except Exception as exc:
            raise ToolError(f"browser_vision: falha na análise — {exc}") from exc

    def _browser_dialog(self, args: dict) -> str:
        action = str(args.get("action", "accept")).lower()
        prompt_text = str(args.get("prompt_text", ""))
        page = self._ensure_browser()

        dialog_handled = {"done": False, "msg": ""}

        def _handle(dialog):
            if action == "dismiss":
                dialog.dismiss()
                dialog_handled["msg"] = f"[browser_dialog] Descartou diálogo '{dialog.type}': {dialog.message[:80]}"
            else:
                dialog.accept(prompt_text or "")
                dialog_handled["msg"] = f"[browser_dialog] Aceitou diálogo '{dialog.type}': {dialog.message[:80]}"
            dialog_handled["done"] = True

        page.once("dialog", _handle)
        # Aguarda até 5s por um diálogo
        try:
            page.wait_for_timeout(5_000)
        except Exception:
            pass
        if dialog_handled["done"]:
            return dialog_handled["msg"]
        page.remove_listener("dialog", _handle)
        return "[browser_dialog] Nenhum diálogo detectado em 5s."

    def _browser_cdp(self, args: dict) -> str:
        method = str(args.get("method", "")).strip()
        if not method:
            raise ToolError("browser_cdp: 'method' é obrigatório (ex: Page.captureScreenshot).")
        params = args.get("params", {})
        if not isinstance(params, dict):
            params = {}
        page = self._ensure_browser()
        try:
            client = page.context.new_cdp_session(page)
            result = client.send(method, params)
            client.detach()
            result_str = json.dumps(result, ensure_ascii=False)[:2000]
            return f"[browser_cdp] {method} → {result_str}"
        except Exception as exc:
            raise ToolError(f"browser_cdp: {exc}") from exc
