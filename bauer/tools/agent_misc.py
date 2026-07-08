"""Tools agentic diversas: todo (lista da sessao), clarify (pergunta ao
usuario) e process (gerenciador de subprocessos start/poll/log/kill).

Mixin herdado por ToolRouter — primitivas que nao se encaixam numa categoria
maior, mas tampouco fazem parte do nucleo de dispatch.
"""

from __future__ import annotations

from .base import ToolError


class MiscToolsMixin:
    """todo, clarify e process — primitivas agentic avulsas."""

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
            import threading

            def _timeout_handler(signum, frame):
                raise TimeoutError

            # signal.SIGALRM só existe em Unix E só pode ser armado na MAIN
            # thread. O bridge executa tools num ThreadPoolExecutor (thread
            # worker) — armar o alarm ali levanta "signal only works in main
            # thread" no Linux (no Windows nem existe SIGALRM). Só usa o alarm
            # quando é seguro; senão input() sem timeout.
            _can_alarm = (
                hasattr(signal, "SIGALRM")
                and threading.current_thread() is threading.main_thread()
            )
            if _can_alarm:
                try:
                    signal.signal(signal.SIGALRM, _timeout_handler)
                    signal.alarm(300)  # timeout de 5 min p/ não travar indefinido
                    answer = input(prompt).strip()
                    signal.alarm(0)
                except (ValueError, AttributeError, OSError):
                    answer = input(prompt).strip()
            else:
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
            cmd_str = str(command)

            # SEGURANÇA: process start executa comando arbitrário (Popen com
            # shell=True) — SEM o gate abaixo era um bypass total do run_command
            # (allowlist/denylist/safe_mode/aprovação). Mesmas regras aqui:
            # 1. Encadeamento shell burlaria a allowlist do 1º token
            #    (`echo x && del ...`): operadores fora de aspas são bloqueados.
            import shlex as _shlex
            try:
                _lex = _shlex.shlex(cmd_str, posix=True, punctuation_chars=True)
                _lex.whitespace_split = True
                _ops = {t for t in _lex if t and set(t) <= set(";&|<>()")}
            except ValueError as exc:
                raise ToolError(f"process start: comando inválido — {exc}") from exc
            if _ops:
                raise ToolError(
                    "process start: encadeamento/redirecionamento shell "
                    f"({', '.join(sorted(_ops))}) não é permitido — inicie UM "
                    "processo por chamada, sem &&, ;, |, > etc."
                )
            # 2. Mesmo gate do run_command background (G17.3): valida allowlist/
            #    denylist/safe_mode via ShellRunner.validate(), sem executar.
            _runner = getattr(self, "_shell_runner", None)
            if _runner is None:
                raise ToolError(
                    "process start: shell desabilitado (router sem ShellRunner) — "
                    "habilite tools.shell_enabled no config.yaml."
                )
            try:
                _runner.validate(cmd_str, confirm=bool(args.get("confirm", False)))
            except Exception as exc:  # Blocked/SafeMode/ShellError
                raise ToolError(f"process start: {exc}") from exc

            label = str(args.get("label", cmd_str[:40]))
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
            # Buffers LIMITADOS (ring): um processo longo (servidor, watch)
            # produzindo logs indefinidamente encheria a memória da sessão.
            # deque com maxlen descarta as linhas mais antigas — só as últimas
            # ~2000 importam (log usa [-max_lines:]).
            from collections import deque as _deque
            stdout_buf: "_deque[str]" = _deque(maxlen=2000)
            stderr_buf: "_deque[str]" = _deque(maxlen=2000)

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
            max_lines = self._coerce_int(args.get("max_lines", 50), default=50, minimum=1)
            # deque não suporta slice — materializa só a cauda pedida.
            stdout_lines = list(info["stdout_buf"])[-max_lines:]
            stderr_lines = list(info["stderr_buf"])[-max_lines:]
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
