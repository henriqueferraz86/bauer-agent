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

import json
import logging
import os
import re
from contextvars import ContextVar
from pathlib import Path

logger = logging.getLogger(__name__)

from .tool_policy import load_tool_policy
from .tools.agent_misc import MiscToolsMixin
from .tools.browser import BrowserToolsMixin
from .tools.channel import ChannelToolsMixin
from .tools.code_intel import CodeIntelToolsMixin
from .tools.cronjob import CronjobToolsMixin
from .tools.execution import ExecToolsMixin
from .tools.factory import FactoryToolsMixin
from .tools.fs import FsToolsMixin
from .tools.kanban import KanbanToolsMixin
from .tools.mcp import McpToolsMixin
from .tools.media import MediaToolsMixin
from .tools.media import _looks_multimodal  # noqa: F401 — re-export p/ testes
from .tools.memory import MemoryToolsMixin
from .tools.session import SessionToolsMixin
from .tools.skills import SkillsToolsMixin
from .tools.social import SocialToolsMixin
from .tools.utility import UtilityToolsMixin
from .tools.web import WebToolsMixin
from .unicode_utils import sanitize_surrogates as _sanitize_surrogates

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

# P4: exceções e tipos compartilhados moram em tools/base.py para evitar import
# circular com os mixins. Re-exportadas aqui — `from bauer.tool_router import
# ToolError, SandboxError, DryRunResult` continua funcionando.
from .tools.base import DryRunResult, SandboxError, ToolError  # noqa: E402

# ─── Runtime ids (session/run) por turno — ContextVar, não atributo de instância ──
#
# O bauer serve reusa a MESMA instância de ToolRouter entre requests (o router
# default, e — desde o router-por-projeto — cada router de projeto também é
# reusado por qualquer sessão que trabalhe naquele projeto). Antes, o serve
# fazia `router._runtime_session_id = sid` na instância compartilhada a cada
# request: com dois turnos concorrentes no MESMO router (duas abas, mesmo
# projeto), o segundo `=` sobrescrevia o id do primeiro NO MEIO da execução —
# uma corrida real (policy/approval/audit atribuiriam eventos ao run/sessão
# errada). Uma ContextVar isola por thread/task: o serve instala o par
# (session_id, run_id) no início de cada turno (dentro da MESMA thread que
# executa as tool calls) e restaura ao final — `self._runtime_session_id`/
# `_runtime_run_id` (properties abaixo) sempre leem o valor do turno atual.
_runtime_ids: "ContextVar[tuple[str | None, str | None] | None]" = ContextVar(
    "bauer_tool_router_runtime_ids", default=None
)


def set_runtime_ids(session_id: "str | None", run_id: "str | None"):
    """Instala (session_id, run_id) do turno atual nesta thread/task.

    Retorna um token para `reset_runtime_ids` — mesmo padrão do
    `delta_stream.set_sink`/`cost_meter.cost_sink` (também ContextVars
    instaladas por turno). Chamar SEMPRE na mesma thread que executa as tool
    calls do turno (contextvars não cruzam `threading.Thread` automaticamente
    — se o turno roda numa thread de fundo, instale lá dentro, não no
    request handler)."""
    return _runtime_ids.set((session_id, run_id))


def reset_runtime_ids(token) -> None:
    try:
        _runtime_ids.reset(token)
    except Exception:  # noqa: BLE001
        _runtime_ids.set(None)

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
    # process START executa comando arbitrário → gate via command-guards +
    # ShellRunner.validate (ver execute()). approval LLM fica False p/ não
    # disparar em list/poll/kill/log (inofensivos); o start já é barrado pelos
    # guards determinísticos, sem custo de chamada de rede por ação.
    "process":        {"permission": "execute", "risk": "high",   "approval": False},
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
    # Redes sociais (Postiz) — publicação é pública e praticamente irreversível
    "social_list_channels": {"permission": "network", "risk": "low",  "approval": False},
    "social_post":          {"permission": "network", "risk": "high", "approval": True},
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
    _MAX_SEARCH_RESULTS,
)

# G18.4: padrões de nomes de modelos multimodais conhecidos (capability check
# das tools de visão). Lista generosa; é só um HINT — configurar
# auxiliary.vision_model sempre bypassa a checagem.

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
    MediaToolsMixin,
    MemoryToolsMixin,
    MiscToolsMixin,
    SessionToolsMixin,
    SkillsToolsMixin,
    SocialToolsMixin,
    UtilityToolsMixin,
    WebToolsMixin,
):
    """Roteador central do Tool Bridge.

    Uso:
        router = ToolRouter(workspace=Path("workspace"))
        result = router.execute('{"action": "list_dir", "args": {"path": "."}}')

    P4: tools por categoria vivem em mixins (bauer/tools/*.py) herdados aqui.
    """

    # `_runtime_session_id`/`_runtime_run_id` são properties (não atributos
    # simples): leem primeiro a ContextVar `_runtime_ids` (instalada pelo serve
    # por turno via `set_runtime_ids`), caindo para o valor passado no
    # construtor quando nenhum turno instalou a ContextVar (CLI e outros
    # callers diretos, que não usam set_runtime_ids). Ver comentário da
    # ContextVar acima — evita a corrida de sobrescrever a instância
    # compartilhada entre turnos concorrentes.
    @property
    def _runtime_session_id(self) -> "str | None":
        ids = _runtime_ids.get()
        if ids is not None:
            return ids[0]
        return self.__dict__.get("_runtime_session_id_default")

    @_runtime_session_id.setter
    def _runtime_session_id(self, value: "str | None") -> None:
        self.__dict__["_runtime_session_id_default"] = value

    @property
    def _runtime_run_id(self) -> "str | None":
        ids = _runtime_ids.get()
        if ids is not None:
            return ids[1]
        return self.__dict__.get("_runtime_run_id_default")

    @_runtime_run_id.setter
    def _runtime_run_id(self, value: "str | None") -> None:
        self.__dict__["_runtime_run_id_default"] = value

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
        run_id: str = "",
        tool_context: str | None = None,
        tool_policy_path: str | Path | None = None,
        tool_allowlist: "list[str] | set[str] | None" = None,
        policy_enabled: bool = False,
        policy_rules_path: str | Path | None = None,
        policy_root: str | Path = "memory/runtime",
        postiz_api_key: str = "",
        postiz_api_url: str = "",
    ):
        self.workspace = Path(workspace).resolve()
        self._postiz_api_key = postiz_api_key
        self._postiz_api_url = postiz_api_url or "https://api.postiz.com"
        # Modo "toolset enxuto": quando não-vazio, SÓ estas tools são expostas
        # (schema OpenAI, system prompt, parsing do bridge) e executáveis. Encolhe
        # o prompt drasticamente — essencial para modelos locais (Ollama/CPU) onde
        # avaliar ~14k tokens das 79 tools leva ~100s. Vazio/None = todas as tools.
        self._tool_allowlist: set[str] = set(tool_allowlist or ())
        # Guardado como atributo: a tool `process` (start) valida o comando com
        # o MESMO gate do run_command (allowlist/denylist/safe_mode) via
        # shell_runner.validate() — sem runner, process start é negado.
        self._shell_runner = shell_runner
        self._llm_client = llm_client  # cliente LLM opcional (vision_analyze, delegate_task)
        self._model_name = model_name  # nome do modelo configurado (para delegate_task)
        # G18.4: cliente multimodal dedicado (auxiliary.vision_model). As tools de
        # visão usam ele se presente; senão caem no _llm_client principal.
        self._vision_client = vision_client
        self._dry_run = dry_run          # SAFETY-002: simula execução sem side effects
        # /loop autônomo: callback opcional (str,str)->"once"|"session"|"always"|"deny"
        # setado/resetado pelo handler do /loop; None preserva o comportamento
        # atual (yolo=False, sem auto-aprovação) em todos os outros fluxos.
        self._approval_callback = None
        self._max_tool_calls = max_tool_calls  # LIMITS-001: teto de chamadas por sessão
        self._max_retries = max_retries        # LIMITS-001: max tentativas por tool
        self._tool_call_count = 0              # contador redefenido por sessão
        self.tool_context = _normalize_tool_context(tool_context)
        self._tool_policy = load_tool_policy(self.workspace, explicit_path=tool_policy_path)
        self._policy_enabled = policy_enabled
        # Modo de enforcement quando policy_enabled: "enforce" (ask BLOQUEIA e
        # cria approval pendente — semântica original) ou "audit" (ask PASSA com
        # registro de auto-aprovação; deny continua bloqueando). O serve local
        # usa "audit" por padrão para ter trilha sem travar o shell do operador;
        # ver server.py. Default "enforce" preserva o comportamento de quem liga
        # policy_enabled diretamente.
        self._policy_mode = "enforce"
        self._policy_rules_path = policy_rules_path
        self._policy_root = Path(policy_root)
        self._runtime_session_id = session_id or None
        self._runtime_run_id = run_id or None
        self._event_bus = None
        try:
            from .core.events import EventBus
            self._event_bus = EventBus(root=Path(workspace).resolve().parent / "runtime")
        except Exception:
            self._event_bus = None
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
                    "offset": "int — linha inicial 1-indexed (default: 1)",
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
                "Delega uma subtarefa a um sub-agente e retorna o resultado. "
                "Se 'agent_name' for fornecido e o agente tiver URL configurada no "
                "registry, dispatcha via HTTP para aquela instância bauer serve remota. "
                "Sem agent_name, executa localmente no mesmo processo."
            ),
            "args": {
                "task": "str — descricao completa da tarefa a delegar (obrigatorio)",
                "context": "str — contexto adicional para o sub-agente (opcional)",
                "agent_name": "str — nome do agente no registry (opcional; sem este campo, executa local)",
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
                "Inicia a governanca da App Factory num projeto NOVO: cria <path>/docs/ "
                "com os esqueletos dos 7 docs de planejamento (SPEC, ARCHITECTURE, "
                "BACKLOG, TASKS, DECISIONS, PROJECT_CONTEXT, PROGRESS) + docs de "
                "entrega + README/.env.example/CI, e fixa <path>/ como projeto ativo. "
                "Depois disso a escrita de codigo fica BLOQUEADA ate os 7 docs "
                "estarem preenchidos. Cada ideia vive na SUA pasta: nunca reutilize "
                "a pasta de outro projeto — init em pasta ja governada por outra "
                "ideia (ou com projeto completo) e recusado."
            ),
            "args": {
                "idea": "str — descricao da ideia/aplicacao (obrigatorio)",
                "path": (
                    "str — pasta do NOVO projeto (OBRIGATORIO): nome do app em "
                    "kebab-case, ex.: idea 'BauerInvest' → path 'bauerinvest'. "
                    "Nunca '.' nem a raiz do workspace."
                ),
                "stack": "str — stack preferida, ex: FastAPI+React (opcional)",
                "overwrite": "bool — descartar esqueleto de OUTRA ideia na mesma pasta (opcional, default false; projeto completo nunca e sobrescrito)",
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
                "poll (verifica status), log (stdout/stderr), kill (encerra), write (stdin). "
                "start aplica as MESMAS regras do run_command (allowlist/denylist/"
                "safe_mode) e nao aceita encadeamento shell (&&, ;, |, >)."
            ),
            "args": {
                "action": "str — start | list | poll | log | kill | write (obrigatorio)",
                "command": "str — comando a executar (obrigatorio para start; 1 processo, sem && ; | >)",
                "confirm": "bool — bypass safe_mode para risco medio no start (default: false)",
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
                "Gera imagem a partir de prompt de texto. Providers: openai "
                "(DALL-E), xai (grok-imagine-image, retorna URL publica) e "
                "openrouter (gemini image, retorna base64/arquivo). Sem provider "
                "explicito, escolhe pela API key disponivel no ambiente."
            ),
            "args": {
                "prompt": "str — descricao detalhada da imagem (obrigatorio)",
                "provider": "str — openai | xai | openrouter (default: auto por env key)",
                "model": "str — ex: dall-e-3, grok-imagine-image, google/gemini-2.5-flash-image",
                "size": "str — 1024x1024 | 1792x1024 | 1024x1792 (so openai; default: 1024x1024)",
                "quality": "str — standard | hd (so dall-e-3; default: standard)",
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

        # ── Tools de redes sociais (Postiz) ─────────────────────────────────
        self._tools["social_list_channels"] = {
            "fn": self._social_list_channels,
            "description": (
                "Lista as contas/redes sociais conectadas na instancia Postiz "
                "(instagram, x, linkedin, tiktok, etc). Use antes de social_post "
                "para saber os IDs de canal disponiveis."
            ),
            "args": {},
        }
        self._tools["social_post"] = {
            "fn": self._social_post,
            "description": (
                "Publica ou agenda um post em uma ou mais redes sociais via Postiz. "
                "Acao PUBLICA e praticamente irreversivel — use social_list_channels "
                "primeiro para confirmar os IDs de canal certos. Para midia, prefira "
                "media_urls com uma URL PUBLICA (ex.: retorno de image_generate com "
                "provider=xai/openrouter) — media_paths reenvia pro storage do "
                "proprio Postiz, que em instancia self-hosted sem storage publico "
                "devolve URL localhost e plataformas como Instagram rejeitam."
            ),
            "args": {
                "content": "str — texto do post (obrigatorio)",
                "channels": "list[str] — IDs de integracao do Postiz (obrigatorio)",
                "media_urls": "list[str] — URLs publicas de midia ja hospedada (preferido)",
                "media_paths": "list[str] — arquivos locais (imagem/video); sobe pro storage do Postiz",
                "schedule_at": "str — data/hora ISO 8601 (opcional; default: agora)",
                "post_type": "str — 'schedule' ou 'draft' (default: 'schedule')",
                "settings": (
                    "dict — settings especificos da plataforma (opcional). "
                    "Instagram exige {'post_type': 'post'|'story'} — aplicado "
                    "automaticamente se omitido."
                ),
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
        # Toolset enxuto tem prioridade: fora do allowlist = indisponível em
        # qualquer contexto (não aparece no schema/prompt nem executa).
        if self._tool_allowlist and name not in self._tool_allowlist:
            return False
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
        except Exception as exc:
            logger.debug("runtime tool event publish failed for %s: %s", name, exc)

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
        action = {"action": tool_name, "args": tool_args}
        return self.execute(action)

    def _evaluate_runtime_policy(self, tool_name: str, args: dict) -> None:
        operation = _policy_operation_for_tool(tool_name)
        if operation is None:
            return
        approval_id = str(args.get("approval_id") or "")
        try:
            from .core.policy import ApprovalManager, PolicyEngine

            root = self._policy_root
            approvals = ApprovalManager(root=root, event_bus=self._event_bus)
            if approval_id and approvals.is_approved(approval_id, operation=operation, tool_name=tool_name):
                return
            engine = PolicyEngine(workspace=self.workspace, rules_path=self._policy_rules_path)
            decision = engine.evaluate(operation, args)
            if self._event_bus is not None:
                self._event_bus.publish(
                    "policy.evaluated",
                    run_id=self._runtime_run_id,
                    session_id=self._runtime_session_id,
                    tool_name=tool_name,
                    status=decision.action,
                    message=decision.reason,
                    data={
                        "operation": operation,
                        "risk_level": decision.risk_level,
                        "matched_rules": decision.matched_rules,
                    },
                )
            if decision.action == "deny":
                if self._event_bus is not None:
                    self._event_bus.publish(
                        "approval.denied",
                        run_id=self._runtime_run_id,
                        session_id=self._runtime_session_id,
                        tool_name=tool_name,
                        status="denied",
                        message=decision.reason,
                        data={"operation": operation, "risk_level": decision.risk_level},
                    )
                raise ToolError(f"policy denied: {decision.reason}")
            if decision.action == "ask":
                # Modo audit: registra a trilha e deixa passar (operador presente;
                # sem fluxo de aprovar-e-retomar no chat, bloquear todo shell
                # quebraria o uso normal). deny já foi tratado acima e bloqueia.
                if self._policy_mode == "audit":
                    if self._event_bus is not None:
                        self._event_bus.publish(
                            "approval.accepted",
                            run_id=self._runtime_run_id,
                            session_id=self._runtime_session_id,
                            tool_name=tool_name,
                            status="auto",
                            message=f"auto-approved (audit mode): {decision.reason}",
                            data={"operation": operation, "risk_level": decision.risk_level},
                        )
                    return
                record = approvals.request(
                    operation=operation,
                    tool_name=tool_name,
                    reason=decision.reason,
                    risk_level=decision.risk_level,
                    payload=args,
                    run_id=self._runtime_run_id,
                    session_id=self._runtime_session_id,
                )
                if self._runtime_run_id:
                    try:
                        from .core.runtime.run_manager import RunManager

                        RunManager(root=root, event_bus=self._event_bus).update_run(
                            self._runtime_run_id,
                            status="waiting_approval",
                        )
                    except Exception:
                        pass
                raise ToolError(
                    f"waiting_approval: approval_id={record.id} operation={operation} "
                    f"risk={decision.risk_level} reason={decision.reason}"
                )
        except ToolError:
            raise
        except Exception as exc:
            raise ToolError(f"policy evaluation failed: {exc}") from exc

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
                "Exemplo: {\"action\": \"list_dir\", \"args\": {\"path\": \".\"}}"
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

        if self._policy_enabled:
            self._evaluate_runtime_policy(name, args)

        # LIMITS-001: enforça max_tool_calls por sessão
        self._tool_call_count += 1
        if self._tool_call_count > self._max_tool_calls:
            raise ToolError(
                f"Limite de {self._max_tool_calls} chamadas de tool por sessão atingido. "
                "Use reset_call_count() para iniciar nova sessão ou aumente max_tool_calls."
            )

        # Wave 4.5: command guards — HARDLINE always blocked; DANGEROUS denied
        # when no interactive approver is available (non-interactive mode).
        # `process` incluso: start executa comando arbitrário (Popen) e passava
        # por fora de TODOS os guards do run_command.
        if _APPROVAL_AVAILABLE and name in {"run_command", "execute_code", "process"}:
            _cmd = str(args.get("command", args.get("code", "")) or "")
            if _cmd.strip():  # process list/poll/log/kill não têm comando
                _guard_dec = _check_command_guards(
                    _cmd, approval_callback=self._approval_callback, yolo=False
                )
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
        #
        # Só roda quando o router tem um llm_client (um "cérebro"). Um router
        # montado só para executar tools, sem LLM (fixtures de teste de fs/shell,
        # utilitários da CLI), não deve disparar uma chamada de aprovação — o
        # llm_evaluate_tool resolveria o approval_model do config.yaml do CWD e
        # faria uma chamada de rede não-determinística que pode NEGAR a tool
        # (retornando string em vez de levantar ToolError). Era a raiz de um
        # flake de CI onde delete_file/run_command "DID NOT RAISE ToolError"
        # dependendo do config.yaml ambiente e da resposta do modelo.
        _sec = _TOOL_SECURITY.get(name, {})
        if _sec.get("approval") and not self._dry_run and self._llm_client is not None:
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
        import time as _time
        _t0 = _time.monotonic()
        _audit_error: Exception | None = None
        self._publish_tool_event(
            "tool.call.requested",
            name,
            args,
            status="requested",
        )

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
            self._publish_tool_event(
                "tool.call.failed",
                name,
                args,
                status="failed",
                duration_ms=_duration_ms,
                error=str(_exc)[:300],
            )
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
        self._publish_tool_event(
            "tool.call.completed",
            name,
            args,
            status="completed",
            duration_ms=_duration_ms,
            result_preview=result[:200] if isinstance(result, str) else None,
        )
        try:
            from .plugin_hooks import hooks as _hooks
            _hooks.emit("post_tool_call", action=name, args=args, result=result, error=None)
        except Exception:
            pass  # hooks nunca bloqueiam execução

        return result
    def _publish_tool_event(
        self,
        event_type: str,
        tool_name: str,
        args: dict,
        *,
        status: str,
        duration_ms: float | None = None,
        error: str | None = None,
        result_preview: str | None = None,
    ) -> None:
        if self._event_bus is None:
            return
        data = {
            "args": args,
            "permission": _TOOL_SECURITY.get(tool_name, {}).get("permission"),
            "risk": _TOOL_SECURITY.get(tool_name, {}).get("risk"),
        }
        if duration_ms is not None:
            data["duration_ms"] = duration_ms
        if result_preview is not None:
            data["result_preview"] = result_preview
        try:
            self._event_bus.publish(
                event_type,  # type: ignore[arg-type]
                run_id=self._runtime_run_id,
                session_id=self._runtime_session_id,
                tool_name=tool_name,
                status=status,
                message=error,
                data=data,
            )
        except Exception:
            pass

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


def _policy_operation_for_tool(tool_name: str) -> str | None:
    mapping = {
        "run_command": "shell.execute",
        "execute_code": "shell.execute",
        "process": "shell.execute",
        "delete_file": "filesystem.delete",
        "write_file": "filesystem.write",
        "append_file": "filesystem.write",
        "patch": "filesystem.write",
        "move_file": "filesystem.write",
        "create_dir": "filesystem.write",
        "read_file": "filesystem.read",
        "list_dir": "filesystem.read",
        "glob_files": "filesystem.read",
        "regex_search": "filesystem.read",
        "search_text": "filesystem.read",
        "social_post": "social.publish",
        "channel_send": "social.publish",
        "send_message": "social.publish",
        "browser_click": "os.ui_control",
        "browser_type": "os.ui_control",
        "browser_press": "os.ui_control",
        "browser_cdp": "os.ui_control",
    }
    return mapping.get(tool_name)

    # P4: todas as tools por categoria foram extraídas para bauer/tools/*.py
    # (mixins herdados na declaração da classe). Esta classe contém agora só o
    # núcleo: __init__, dispatch (execute/_parse/execute_native_call), sandbox,
    # segurança/contexto e os schemas registrados em self._tools.
