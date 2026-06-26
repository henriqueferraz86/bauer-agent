"""Tools de kanban: criar/listar/comentar/completar/bloquear tasks e afins.

Mixin herdado por ToolRouter. Cluster coeso — inclui os helpers _load_kanban
e _workspace_task_to_kanban (usados so aqui). Acesso ao store via ..kanban_store.
"""

from __future__ import annotations

import os

from ..workspace_manager import WorkspaceError, WorkspaceManager
from .base import ToolError


class KanbanToolsMixin:
    """Ferramentas de gestao de tasks no kanban (SQLite/Markdown)."""

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
            from ..kanban_store import KanbanStore

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
            from ..kanban_store import KanbanStore

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
