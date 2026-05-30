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
    ):
        self.workspace = Path(workspace).resolve()
        self._llm_client = llm_client  # cliente LLM opcional (vision_analyze, delegate_task)
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
        return {"name": name, "description": info["description"], "args": info["args"]}

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

        result = self._tools[name]["fn"](args)

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

        lines = [f"exit: {result.returncode}"]
        if result.stdout.strip():
            lines.append("--- stdout ---")
            out = result.stdout
            if len(out) > 8000:
                out = out[:8000] + f"\n[... truncado — {len(result.stdout)} chars total]"
            lines.append(out.rstrip())
        if result.stderr.strip():
            lines.append("--- stderr ---")
            err = result.stderr
            if len(err) > 4000:
                err = err[:4000] + f"\n[... truncado]"
            lines.append(err.rstrip())
        if not result.stdout.strip() and not result.stderr.strip():
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
        python = _find_bauer_python(self.workspace)
        cmd = [python, "-m", "bauer.cli", "agent", "run-one", full_task]
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
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

        output = result.stdout.strip()
        if result.returncode != 0:
            err = result.stderr.strip()
            raise ToolError(
                f"delegate_task: sub-agente falhou (exit {result.returncode}).\n"
                f"Erro: {err[:500] if err else 'sem detalhes'}"
            )

        if not output:
            return "[sub-agente] Tarefa concluida sem output."

        if len(output) > 8000:
            output = output[:8000] + f"\n[... truncado — {len(result.stdout)} chars]"

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
