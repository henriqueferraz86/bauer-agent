"""Tools de execucao: run_command, execute_code, delegate_task.

Mixin herdado por ToolRouter. Inclui o cluster de helpers que resolvem o
interpretador Python e o config.yaml para rodar bauer dentro do sandbox
(usados so por estas tools) e a denylist de codigo perigoso.
"""

from __future__ import annotations

import re
from pathlib import Path

from ..shell_runner import ShellError
from .base import ToolError


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


_BAUER_PYTHON_CACHE: dict[str, str] = {}  # workspace_str -> python_path
_BAUER_CONFIG_CACHE: dict[str, str] = {}  # workspace_str -> config_yaml_path


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


class ExecToolsMixin:
    """run_command (shell), execute_code (sandbox Python) e delegate_task (sub-agente)."""

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

        timeout = self._coerce_int(args.get("timeout", 30), default=30, minimum=1)
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

    def _resolve_delegate_agent(self, agent_name: str, task: str):
        """Resolve o AgentDef a usar em `delegate_task`.

        Com `agent_name` explícito: busca exata no registry (None se não achar
        — nome errado não deve cair silenciosamente num especialista errado).
        Sem `agent_name`: tenta `auto_select(task)` (keyword/Jaccard match) —
        fallback determinístico para quando o modelo esquece de escolher um
        especialista mesmo com a lista disponível no system prompt.

        Retorna (AgentDef | None, matched_by_name: bool, agents_file: str) —
        o caminho do registry é devolvido (não guardado em self) porque
        múltiplas `delegate_task` podem rodar em paralelo na mesma thread pool
        (ver `_exec_action` em agent.py); estado de instância aqui correria
        risco de uma chamada ler o path resolvido por outra.

        Best-effort: registry ausente/corrompido nunca bloqueia a delegação,
        só degrada para None.
        """
        import os as _os
        from pathlib import Path as _Path

        # Prioridade: _bauer_home explícito (setado pelo caller/teste) > env var
        # BAUER_AGENTS_FILE (isolamento hermético — ver tests/conftest.py,
        # mesmo padrão de BAUER_CONFIG/BAUER_HOME) > CWD-relative (produção).
        _home = getattr(self, "_bauer_home", None)
        _env_override = _os.environ.get("BAUER_AGENTS_FILE")
        if _home:
            _agents_file = str(_home / "agents.yaml")
        elif _env_override:
            _agents_file = _env_override
        else:
            _agents_file = str(_Path("agents.yaml").resolve())
        try:
            from ..agent_registry import AgentRegistry

            _reg = AgentRegistry(_agents_file)
            if agent_name:
                return _reg.get(agent_name), True, _agents_file
            return _reg.auto_select(task), False, _agents_file
        except Exception:
            return None, False, _agents_file

    def _delegate_task(self, args: dict) -> str:
        """Delega subtarefa a sub-agente local ou remoto.

        Resolução do agente (nesta ordem):
          1. `agent_name` explícito → busca exata no registry.
          2. Sem `agent_name` → `AgentRegistry.auto_select(task)` escolhe o
             especialista com melhor overlap de palavras (best-effort).
        Com agente resolvido: `url` no registry → dispatch remoto (HTTP);
        sem `url` → especialização LOCAL — o `system` prompt do agente (e
        `model`, se definido) são aplicados na chamada ao LLM. Sem nenhum
        agente resolvido, cai no comportamento genérico (LLM direto sem
        especialização, ou subprocess).
        """
        import subprocess
        import sys

        task = args.get("task", "").strip()
        if not task:
            raise ToolError("delegate_task requer 'task'.")

        context = args.get("context", "").strip()
        agent_name = str(args.get("agent_name", "") or "").strip()
        timeout = self._coerce_int(args.get("timeout", 120), default=120, minimum=1)
        timeout = max(10, min(timeout, 600))

        full_task = f"{context}\n\n{task}".strip() if context else task
        # Sanitização: remove null bytes e limita tamanho para evitar overflow de args
        full_task = full_task.replace("\x00", "").strip()
        if len(full_task) > 4096:
            full_task = full_task[:4096]

        _ag, _matched_by_name, _agents_file = self._resolve_delegate_agent(agent_name, full_task)
        _resolved_name = agent_name if (_ag and _matched_by_name) else (_ag.name if _ag else "")

        # ── Dispatch remoto via agent registry ────────────────────────────────
        if _ag and _ag.url:
            try:
                import httpx as _httpx

                endpoint = _ag.url.rstrip("/") + "/chat"
                _headers: dict[str, str] = {}
                if _ag.api_key:
                    _headers["X-API-Key"] = _ag.api_key
                try:
                    resp = _httpx.post(
                        endpoint,
                        json={"message": full_task},
                        headers=_headers,
                        timeout=_httpx.Timeout(connect=10.0, read=float(timeout),
                                               write=10.0, pool=5.0),
                    )
                    resp.raise_for_status()
                    return f"[agente remoto: {_resolved_name}]\n{resp.json().get('response', '')}"
                except _httpx.TimeoutException:
                    raise ToolError(
                        f"delegate_task: timeout ({timeout}s) aguardando {_resolved_name} "
                        f"em {endpoint}."
                    )
                except _httpx.HTTPStatusError as exc:
                    raise ToolError(
                        f"delegate_task: agente remoto {_resolved_name} retornou "
                        f"HTTP {exc.response.status_code}."
                    ) from exc
                except _httpx.ConnectError:
                    raise ToolError(
                        f"delegate_task: não foi possível conectar a {_resolved_name} "
                        f"em {endpoint}. Verifique se o bauer serve está rodando."
                    )
            except ToolError:
                raise
            except Exception:
                pass  # falha inesperada no dispatch remoto — continua com delegate local

        # Especialista LOCAL (agent resolvido, sem url): aplica o system prompt
        # dele — sem isto, "especialista" não fazia diferença nenhuma na
        # resposta, era só um rótulo.
        if self._llm_client is not None:
            try:
                _model = (
                    (_ag.model if _ag and _ag.model else "")
                    or self._model_name
                    or getattr(self._llm_client, "default_model", "")
                    or ""
                )
                messages = []
                if _ag and _ag.system:
                    messages.append({"role": "system", "content": _ag.system})
                messages.append({"role": "user", "content": full_task})
                chunks = list(self._llm_client.chat_stream(_model, messages))
                _label = f"especialista '{_resolved_name}'" if _ag else "sub-agente"
                return f"[{_label}]\n{''.join(chunks)}"
            except Exception:
                pass  # fallback para subprocess

        # Fallback: subprocess com bauer CLI
        # nosec: shell=False (default); full_task é passado como elemento de lista,
        # não como string de shell — sem risco de injeção.
        python = _find_bauer_python(self.workspace)
        cmd = [python, "-m", "bauer.cli", "agent", "run-one", full_task]
        if _resolved_name:
            cmd += ["--agent", _resolved_name, "--agents", _agents_file]
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

