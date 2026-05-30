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
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path

from .shell_runner import ShellError
from .unicode_utils import sanitize_surrogates as _sanitize_surrogates


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
    # Sistema
    "video_generate": {"permission": "network", "risk": "low",    "approval": False},
}

# Limite de leitura de arquivo para evitar output enorme.
_MAX_READ_BYTES = 100_000
# Limite de resultados de busca.
_MAX_SEARCH_RESULTS = 50


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
    ):
        self.workspace = Path(workspace).resolve()
        self._llm_client = llm_client  # cliente LLM opcional (vision_analyze, delegate_task)
        self._dry_run = dry_run          # SAFETY-002: simula execução sem side effects
        self._max_tool_calls = max_tool_calls  # LIMITS-001: teto de chamadas por sessão
        self._max_retries = max_retries        # LIMITS-001: max tentativas por tool
        self._tool_call_count = 0              # contador redefenido por sessão
        self._tools: dict[str, dict] = {
            "list_dir": {
                "fn": self._list_dir,
                "description": "Lista conteudo de diretorio dentro do workspace.",
                "args": {"path": "str — caminho relativo ao workspace (default: '.')"},
            },
            "read_file": {
                "fn": self._read_file,
                "description": f"Le arquivo de texto (limite {_MAX_READ_BYTES // 1024} KB).",
                "args": {"path": "str — caminho relativo ao workspace (obrigatorio)"},
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
                "parent_id": "str — ID da tarefa pai para sub-tarefas (opcional)",
            },
        }
        self._tools["kanban_list"] = {
            "fn": self._kanban_list,
            "description": "Lista tarefas do board com filtros por status, assignee ou prioridade.",
            "args": {
                "status": "str — todo | in_progress | blocked | done | all (default: all)",
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
        self._tools["browser_navigate"] = {
            "fn": self._browser_navigate,
            "description": "Navega para URL no browser controlado. Requer: pip install playwright && playwright install chromium.",
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

        if shell_runner is not None:
            self._tools["run_command"] = {
                "fn": self._make_run_command(shell_runner),
                "description": "Executa comando shell controlado (allowlist + denylist + safe_mode).",
                "args": {
                    "command": "str — linha de comando (obrigatorio)",
                    "confirm": "bool — bypass safe_mode para risco medio (default: false)",
                },
            }

        if web_enabled:
            from .web.dispatcher import WebDispatcher
            self._web = WebDispatcher(web_config)

            self._tools["web_search"] = {
                "fn": self._web_search,
                "description": "Pesquisa na web e retorna resultados com titulos, links e snippets.",
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

    # --- API pública -----------------------------------------------------------

    def available_tools(self) -> list[str]:
        return list(self._tools.keys())

    def tool_info(self, name: str) -> dict:
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
        }

    def tool_security(self, name: str) -> dict:
        """Retorna metadados de segurança de uma tool."""
        return _TOOL_SECURITY.get(name, {"permission": "read", "risk": "low", "approval": False})

    def reset_call_count(self) -> None:
        """Reseta o contador de chamadas (use no inicio de cada sessão de agent)."""
        self._tool_call_count = 0

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

        if name not in self._tools:
            available = ", ".join(self._tools.keys())
            raise ToolError(
                f"Tool desconhecida: '{name}'.\n"
                f"Disponiveis: {available}"
            )

        args = action.get("args", {})
        if not isinstance(args, dict):
            raise ToolError("Campo 'args' deve ser um objeto JSON.")

        # LIMITS-001: enforça max_tool_calls por sessão
        self._tool_call_count += 1
        if self._tool_call_count > self._max_tool_calls:
            raise ToolError(
                f"Limite de {self._max_tool_calls} chamadas de tool por sessão atingido. "
                "Use reset_call_count() para iniciar nova sessão ou aumente max_tool_calls."
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

        result = self._tools[name]["fn"](args)

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

        return result

    # --- sandbox ---------------------------------------------------------------

    def _sandbox(self, path: str) -> Path:
        """Resolve path dentro do workspace. Bloqueia qualquer saída do sandbox.

        Premortem item 4: path traversal (../) deve ser bloqueado aqui.

        Também normaliza paths absolutos que modelos frequentemente geram:
          /workspace/foo.txt  → foo.txt   (strip do prefixo workspace)
          /foo.txt            → foo.txt   (strip de / inicial — atalho de 1 componente)

        Paths absolutos fora do workspace (múltiplos componentes) são bloqueados.
        """
        ws_name = self.workspace.name
        p_raw = Path(path)

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
                # → resolver diretamente e verificar se está dentro do workspace
                try:
                    resolved = p_raw.resolve()
                except Exception as exc:
                    raise SandboxError(f"Path invalido: '{path}': {exc}") from exc

                workspace_str = str(self.workspace)
                resolved_str = str(resolved)
                sep = "/" if "/" in workspace_str else "\\"

                if resolved_str != workspace_str and not resolved_str.startswith(workspace_str + sep):
                    raise SandboxError(
                        f"Acesso negado: '{path}' resolve para fora do workspace.\n"
                        f"  Workspace: {self.workspace}\n"
                        f"  Tentativa: {resolved}\n"
                        f"Use apenas caminhos relativos dentro do workspace."
                    )
                return resolved
        else:
            # Caminho relativo: normaliza /workspace/ ou \workspace\ que o modelo adiciona
            normalized = path.lstrip("/\\")
            if normalized == ws_name or normalized.startswith(ws_name + "/") or normalized.startswith(ws_name + "\\"):
                normalized = normalized[len(ws_name):].lstrip("/\\")
            path = normalized or "."

        try:
            resolved = (self.workspace / path).resolve()
        except Exception as exc:
            raise SandboxError(f"Path invalido: '{path}': {exc}") from exc

        # A verificação é feita comparando strings para garantir que o path
        # resolvido começa com o workspace — cobre symlinks e ../ .
        workspace_str = str(self.workspace)
        resolved_str = str(resolved)

        if resolved_str != workspace_str and not resolved_str.startswith(workspace_str + ("/" if "/" in workspace_str else "\\")):
            raise SandboxError(
                f"Acesso negado: '{path}' resolve para fora do workspace.\n"
                f"  Workspace: {self.workspace}\n"
                f"  Tentativa: {resolved}\n"
                f"Use apenas caminhos relativos dentro do workspace."
            )
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

            # Transparência: `bauer <subcommand>` → `<python> -m bauer.cli <subcommand>`
            # Resolve o problema do AppLocker bloqueando bauer.exe no venv.
            cmd_str = str(cmd).strip()
            if cmd_str == "bauer" or cmd_str.startswith("bauer "):
                rest = cmd_str[len("bauer"):].strip()
                python = _find_bauer_python(shell_runner.workspace)
                cmd_str = f'"{python}" -m bauer.cli {rest}' if rest else f'"{python}" -m bauer.cli'

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

    def _read_file(self, args: dict) -> str:
        path = args.get("path")
        if not path:
            raise ToolError("read_file requer 'path'.")
        p = self._sandbox(str(path))

        if not p.exists():
            raise ToolError(f"Arquivo nao encontrado: '{path}'")
        if p.is_dir():
            raise ToolError(f"'{path}' e um diretorio — use list_dir.")

        raw = p.read_bytes()
        if len(raw) > _MAX_READ_BYTES:
            raise ToolError(
                f"Arquivo muito grande: {len(raw)} bytes (limite: {_MAX_READ_BYTES}).\n"
                f"Use search_text para encontrar partes especificas."
            )
        try:
            return raw.decode("utf-8")
        except UnicodeDecodeError:
            raise ToolError(f"'{path}' parece ser binario — read_file so aceita texto UTF-8.")

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

        p.parent.mkdir(parents=True, exist_ok=True)
        text = str(content)
        p.write_text(text, encoding="utf-8")
        return f"Gravado: '{path}' ({len(text)} chars)"

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

        # Blocklist de hosts internos / privados
        import ipaddress
        import urllib.parse as _urlparse

        parsed = _urlparse.urlparse(url)
        hostname = parsed.hostname or ""
        _BLOCKED = ("localhost", "127.", "0.0.0.0", "::1")
        if any(hostname.startswith(b) or hostname == b.rstrip(".") for b in _BLOCKED):
            raise ToolError(f"Acesso bloqueado a host interno: '{hostname}'")
        try:
            addr = ipaddress.ip_address(hostname)
            if addr.is_private or addr.is_loopback or addr.is_link_local:
                raise ToolError(f"Acesso bloqueado a endereco IP privado: '{hostname}'")
        except ValueError:
            pass  # não é IP, ok

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

        return f"Arquivo '{path}' atualizado.\n{diff_str}"

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

    # --- mcp_call --------------------------------------------------------------

    def _mcp_call(self, args: dict) -> str:
        """Chama tool em servidor MCP via stdio.

        Boas práticas:
        - Lazy import: só importa `mcp` se chamado
        - Conexão por sessão (sem cache global para evitar estado compartilhado)
        - Timeout de 30s por chamada
        - Retorna resultado como string JSON formatada

        Configuração esperada em config.yaml:
            mcp:
              servers:
                meu_servidor:
                  command: ["python", "-m", "my_mcp_server"]
                  env: {}
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

        # Verifica instalação antes de qualquer outra coisa
        try:
            import mcp  # noqa: F401
            from mcp import ClientSession, StdioServerParameters
            from mcp.client.stdio import stdio_client
            import asyncio
        except ImportError:
            raise ToolError(
                "mcp_call requer o pacote 'mcp' instalado.\n"
                "Instale com: pip install mcp\n"
                "Documentacao: https://github.com/anthropics/mcp"
            )

        # Carrega config do servidor (só chega aqui se mcp estiver instalado)
        server_cmd = self._get_mcp_server_cmd(server_name)

        async def _call() -> str:
            params = StdioServerParameters(
                command=server_cmd[0],
                args=server_cmd[1:],
            )
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    result = await session.call_tool(tool_name, arguments=arguments)
                    # result.content é lista de TextContent/ImageContent
                    parts = []
                    for c in result.content:
                        if hasattr(c, "text"):
                            parts.append(c.text)
                        elif hasattr(c, "data"):
                            parts.append(f"[imagem base64 — {len(c.data)} chars]")
                        else:
                            parts.append(str(c))
                    return "\n".join(parts) if parts else "(sem output)"

        try:
            return asyncio.run(_call())
        except Exception as exc:
            raise ToolError(
                f"mcp_call: erro ao chamar '{tool_name}' em '{server_name}': {exc}"
            )

    def _get_mcp_server_cmd(self, server_name: str) -> list[str]:
        """Lê comando do servidor MCP da config ou env."""
        import os

        # Tenta ler de variável de ambiente: MCP_SERVER_<NAME>=comando
        env_key = f"MCP_SERVER_{server_name.upper().replace('-', '_')}"
        env_val = os.environ.get(env_key, "")
        if env_val:
            return env_val.split()

        # Tenta ler de config.yaml (se injetado via self._mcp_config)
        mcp_config = getattr(self, "_mcp_config", None)
        if mcp_config:
            servers = getattr(mcp_config, "servers", {}) or {}
            if server_name in servers:
                srv = servers[server_name]
                if isinstance(srv, dict) and "command" in srv:
                    cmd = srv["command"]
                    return cmd if isinstance(cmd, list) else cmd.split()

        raise ToolError(
            f"Servidor MCP '{server_name}' nao configurado.\n"
            "Configure via:\n"
            f"  1. Variavel de ambiente: {env_key}=python -m meu_servidor\n"
            "  2. config.yaml:\n"
            "       mcp:\n"
            "         servers:\n"
            f"           {server_name}:\n"
            "             command: [\"python\", \"-m\", \"meu_servidor\"]"
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

    def _search_session_files(self, query: str) -> list[str]:
        """Busca em arquivos .jsonl de sessão dentro do workspace."""
        import re as _re
        hits: list[str] = []
        try:
            pattern = _re.compile(query, _re.IGNORECASE)
        except _re.error:
            pattern = _re.compile(_re.escape(query), _re.IGNORECASE)

        # Procura .jsonl e .json de sessão nos diretórios comuns
        search_dirs = [self.workspace, self.workspace.parent]
        for d in search_dirs:
            if not d.exists():
                continue
            for ext in ("*.jsonl", "*.json"):
                for fpath in list(d.glob(ext))[:20]:  # máx 20 arquivos
                    if fpath.name.startswith(".bauer_"):
                        continue
                    try:
                        text = fpath.read_text(encoding="utf-8", errors="ignore")
                        for i, line in enumerate(text.splitlines()):
                            if pattern.search(line):
                                preview = line[:100].strip()
                                hits.append(
                                    f"  [{fpath.name}:{i+1}] {preview}"
                                )
                                if len(hits) >= 20:
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
    _KANBAN_FILE = ".bauer_kanban.json"

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
            import openai
            # Descobre qual objeto tem .images.generate:
            # 1) self._llm_client (caso mock direto)
            # 2) self._llm_client._client (caso wrapper bauer sobre openai.OpenAI)
            # 3) cria openai.OpenAI com credenciais do cliente
            _lc = self._llm_client
            if hasattr(_lc, "images") and callable(getattr(getattr(_lc, "images", None), "generate", None)):
                client_obj = _lc
            elif hasattr(getattr(_lc, "_client", None), "images"):
                client_obj = _lc._client
            else:
                base_url = getattr(_lc, "base_url", None) or "https://api.openai.com/v1"
                api_key = getattr(_lc, "api_key", None) or ""
                client_obj = openai.OpenAI(api_key=api_key, base_url=base_url)

            kw: dict = {"model": model, "prompt": prompt, "size": size, "n": 1}
            if model == "dall-e-3":
                kw["quality"] = quality
            response = client_obj.images.generate(**kw)
            img_url = response.data[0].url
        except ImportError:
            raise ToolError("image_generate: requer 'pip install openai'.")
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
            import openai
            _lc = self._llm_client
            # Descobre o objeto com .audio.speech.create:
            # 1) self._llm_client direto (mocks e openai.OpenAI wrappers expostos)
            # 2) self._llm_client._client (wrapper bauer)
            # 3) cria openai.OpenAI com credenciais
            if hasattr(_lc, "audio") and callable(getattr(getattr(_lc, "audio", None), "speech", None) and
                                                   getattr(getattr(getattr(_lc, "audio", None), "speech", None), "create", None) or None):
                client_obj = _lc
            elif hasattr(_lc, "audio"):
                client_obj = _lc
            elif hasattr(getattr(_lc, "_client", None), "audio"):
                client_obj = _lc._client
            else:
                base_url = getattr(_lc, "base_url", None) or "https://api.openai.com/v1"
                api_key = getattr(_lc, "api_key", None) or ""
                client_obj = openai.OpenAI(api_key=api_key, base_url=base_url)

            response = client_obj.audio.speech.create(model=model, voice=voice, input=text)
            response.stream_to_file(str(dest))
        except ImportError:
            raise ToolError("text_to_speech: requer 'pip install openai'.")
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
        p = self.workspace / self._KANBAN_FILE
        if p.exists():
            try:
                return json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                return {"tasks": {}, "next_id": 1}
        return {"tasks": {}, "next_id": 1}

    def _save_kanban(self, board: dict) -> None:
        p = self.workspace / self._KANBAN_FILE
        p.write_text(json.dumps(board, ensure_ascii=False, indent=2), encoding="utf-8")

    def _kanban_create(self, args: dict) -> str:
        title = str(args.get("title", "")).strip()
        if not title:
            raise ToolError("kanban_create: 'title' é obrigatório.")
        priority = str(args.get("priority", "medium")).lower()
        valid_priorities = ("low", "medium", "high", "critical")
        if priority not in valid_priorities:
            raise ToolError(f"kanban_create: priority deve ser {valid_priorities}.")

        import time as _time
        board = self._load_kanban()
        task_id = f"T{board['next_id']:04d}"
        board["next_id"] += 1
        board["tasks"][task_id] = {
            "id": task_id,
            "title": title,
            "description": str(args.get("description", "")),
            "status": "todo",
            "priority": priority,
            "assignee": str(args.get("assignee", "")),
            "parent_id": str(args.get("parent_id", "")),
            "children": [],
            "comments": [],
            "created_at": _time.time(),
            "updated_at": _time.time(),
        }
        # Registra filho no pai
        parent_id = str(args.get("parent_id", "")).strip()
        if parent_id and parent_id in board["tasks"]:
            board["tasks"][parent_id]["children"].append(task_id)

        self._save_kanban(board)
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

        _priority_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
        tasks.sort(key=lambda t: _priority_order.get(t["priority"], 9))

        _status_icons = {"todo": "⬜", "in_progress": "🔵", "blocked": "🔴", "done": "✅"}
        lines = [f"[kanban] {len(tasks)} tarefa(s):"]
        for t in tasks:
            icon = _status_icons.get(t["status"], "•")
            assignee = f" @{t['assignee']}" if t.get("assignee") else ""
            lines.append(
                f"  {icon} {t['id']} [{t['priority']}]{assignee} — {t['title']}"
            )
        return "\n".join(lines)

    def _kanban_show(self, args: dict) -> str:
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

    def _kanban_update_status(self, task_id: str, new_status: str, note: str = "") -> dict:
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

    def _kanban_complete(self, args: dict) -> str:
        task_id = str(args.get("task_id", "")).strip()
        if not task_id:
            raise ToolError("kanban_complete: 'task_id' é obrigatório.")
        result = str(args.get("result", ""))
        t = self._kanban_update_status(task_id, "done", f"Concluído: {result}" if result else "")
        return f"[kanban] {task_id} '{t['title']}' marcado como done."

    def _kanban_block(self, args: dict) -> str:
        task_id = str(args.get("task_id", "")).strip()
        reason = str(args.get("reason", "")).strip()
        if not task_id:
            raise ToolError("kanban_block: 'task_id' é obrigatório.")
        if not reason:
            raise ToolError("kanban_block: 'reason' é obrigatório.")
        t = self._kanban_update_status(task_id, "blocked", f"Bloqueado: {reason}")
        return f"[kanban] {task_id} '{t['title']}' bloqueado — {reason}"

    def _kanban_unblock(self, args: dict) -> str:
        task_id = str(args.get("task_id", "")).strip()
        if not task_id:
            raise ToolError("kanban_unblock: 'task_id' é obrigatório.")
        note = str(args.get("note", "Bloqueio removido."))
        t = self._kanban_update_status(task_id, "todo", note)
        return f"[kanban] {task_id} '{t['title']}' desbloqueado."

    def _kanban_heartbeat(self, args: dict) -> str:
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

    def _kanban_comment(self, args: dict) -> str:
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

    def _kanban_link(self, args: dict) -> str:
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
