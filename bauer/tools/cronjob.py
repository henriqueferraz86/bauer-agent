"""Cronjob tools: agenda e roda jobs recorrentes (.bauer_cron.json).

Mixin herdado por ToolRouter. _cronjob (action=run) dispara via
self._execute_code, resolvido por heranca de ExecToolsMixin.
"""

from __future__ import annotations

import json
from pathlib import Path

from .base import ToolError


class CronjobToolsMixin:
    """Agendamento simples de jobs recorrentes no workspace."""

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
                from ..shell_runner import _DENYLIST as _SR_DENYLIST
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
