"""Workspace Manager do Bauer Agent (Fase 6).

Gerencia arquivos de projeto dentro do workspace:
  PROJECT.md — descrição e contexto do projeto
  TASKS.md   — lista de tarefas com status auditável

Regras:
  - Nenhuma tarefa é deletada (apenas muda status)
  - IDs são sequenciais e imutáveis após criação
  - Todo status change fica registrado no arquivo (sem logs separados)
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

_VALID_STATUSES = {"TODO", "READY", "IN_PROGRESS", "DONE", "BLOCKED", "FAILED"}
_HEADING_RE = re.compile(r"^## \[([A-Z_]+)\] (.+)$")
_ID_RE = re.compile(r"^id:\s*(\d+)\s*$")
_META_KEYS = {
    "priority",
    "assignee",
    "parent",
    "dispatch",
    "claim_id",
    "claim_expires",
    "claimed_by",
    "run_id",
    "worker_pid",
    "heartbeat_at",
    "attempts",
    "max_retries",
    "max_runtime_seconds",
    "last_error",
    "log",
}


@dataclass
class Task:
    id: str       # zero-padded, e.g. "001"
    status: str   # TODO | READY | IN_PROGRESS | DONE | BLOCKED | FAILED
    title: str
    description: str = ""
    spec_id: str = ""   # ID do spec vinculado (vazio = sem spec)
    priority: str = "medium"
    assignee: str = ""
    parent_id: str = ""
    created_at: str = ""
    comments: list[dict[str, str]] = field(default_factory=list)
    metadata: dict[str, str] = field(default_factory=dict)


class WorkspaceError(Exception):
    """Erro do workspace manager."""


class WorkspaceManager:
    """Gerencia PROJECT.md e TASKS.md dentro do workspace.

    Usage:
        wm = WorkspaceManager(workspace=Path("workspace"))
        wm.init_project("MeuApp", "Descrição do projeto")
        task = wm.add_task("Implementar login")
        wm.update_task_status("001", "IN_PROGRESS")
    """

    def __init__(self, workspace: str | Path = "workspace"):
        self.workspace = Path(workspace).resolve()
        self.tasks_file = self.workspace / "TASKS.md"
        self.project_file = self.workspace / "PROJECT.md"

    # --- inicialização -------------------------------------------------------

    def init_project(self, name: str, description: str = "") -> list[Path]:
        """Cria workspace/ e arquivos de projeto. Nunca sobrescreve existentes."""
        self.workspace.mkdir(parents=True, exist_ok=True)
        created: list[Path] = []

        if not self.project_file.exists():
            ts = _today()
            self.project_file.write_text(
                f"# Projeto: {name}\n\n"
                f"criado: {ts}\n\n"
                f"## Descricao\n\n{description.strip() or 'Sem descricao.'}\n\n---\n",
                encoding="utf-8",
            )
            created.append(self.project_file)

        if not self.tasks_file.exists():
            self.tasks_file.write_text(
                "# TASKS.md — Tarefas do projeto\n\n"
                "Status validos: TODO | READY | IN_PROGRESS | DONE | BLOCKED | FAILED\n\n---\n",
                encoding="utf-8",
            )
            created.append(self.tasks_file)

        return created

    # --- tarefas -------------------------------------------------------------

    def add_task(
        self,
        title: str,
        description: str = "",
        spec_id: str = "",
        status: str = "TODO",
        priority: str = "medium",
        assignee: str = "",
        parent_id: str = "",
        metadata: dict[str, str | int | None] | None = None,
    ) -> Task:
        """Adiciona tarefa ao TASKS.md. ID é sequencial e imutável.

        Args:
            title: Título da tarefa.
            description: Descrição opcional.
            spec_id: ID do spec vinculado (vazio = sem spec). Quando definido,
                     escreve `spec: <id>` no bloco — o agent usa isso para
                     carregar o contrato automaticamente.
        """
        status = status.upper().strip()
        if status not in _VALID_STATUSES:
            raise WorkspaceError(
                f"Status invalido: '{status}'. "
                f"Validos: {', '.join(sorted(_VALID_STATUSES))}"
            )

        if not self.tasks_file.exists():
            self.init_project("Projeto")

        existing = self.list_tasks()
        task_id = str(len(existing) + 1).zfill(3)
        ts = _today()

        priority = (priority or "medium").strip().lower()
        assignee = assignee.strip()
        parent_id = _normalize_task_id(parent_id) if parent_id.strip() else ""

        block = f"\n## [{status}] {title}\nid: {task_id}\ncriado: {ts}\n"
        if priority:
            block += f"priority: {priority}\n"
        if assignee:
            block += f"assignee: {assignee}\n"
        if parent_id:
            block += f"parent: {parent_id}\n"
        if spec_id.strip():
            block += f"spec: {spec_id.strip()}\n"
        extra_metadata: dict[str, str] = {}
        for key, value in (metadata or {}).items():
            meta_key = str(key).strip().lower()
            if meta_key in {"priority", "assignee", "parent", "spec", "id", "criado"}:
                continue
            if meta_key not in _META_KEYS or value is None:
                continue
            meta_value = _single_line(str(value))
            if meta_value:
                extra_metadata[meta_key] = meta_value
                block += f"{meta_key}: {meta_value}\n"
        if description.strip():
            block += f"\n{description.strip()}\n"
        block += "\n---\n"

        with self.tasks_file.open("a", encoding="utf-8") as f:
            f.write(block)

        return Task(
            id=task_id,
            status=status,
            title=title,
            description=description,
            spec_id=spec_id,
            priority=priority,
            assignee=assignee,
            parent_id=parent_id,
            created_at=ts,
            metadata=extra_metadata,
        )

    def list_tasks(self) -> list[Task]:
        """Lê e retorna todas as tarefas do TASKS.md."""
        if not self.tasks_file.exists():
            return []

        text = self.tasks_file.read_text(encoding="utf-8")
        tasks: list[Task] = []
        current_status: str | None = None
        current_title: str | None = None
        current_id: str | None = None
        desc_lines: list[str] = []

        current_spec_id: str | None = None
        current_priority: str = "medium"
        current_assignee: str = ""
        current_parent_id: str = ""
        current_created_at: str = ""
        current_comments: list[dict[str, str]] = []
        current_metadata: dict[str, str] = {}

        for line in text.splitlines():
            hm = _HEADING_RE.match(line)
            if hm:
                # Salva tarefa anterior se completa
                if current_id:
                    tasks.append(Task(
                        id=current_id,
                        status=current_status,  # type: ignore[arg-type]
                        title=current_title,    # type: ignore[arg-type]
                        description=" ".join(desc_lines).strip(),
                        spec_id=current_spec_id or "",
                        priority=current_priority or "medium",
                        assignee=current_assignee,
                        parent_id=current_parent_id,
                        created_at=current_created_at,
                        comments=list(current_comments),
                        metadata=dict(current_metadata),
                    ))
                current_status, current_title = hm.group(1), hm.group(2)
                current_id = None
                current_spec_id = None
                current_priority = "medium"
                current_assignee = ""
                current_parent_id = ""
                current_created_at = ""
                current_comments = []
                current_metadata = {}
                desc_lines = []
                continue

            if current_status is not None:
                id_m = _ID_RE.match(line)
                if id_m:
                    current_id = id_m.group(1).zfill(3)
                    continue
                if line.startswith("criado:"):
                    current_created_at = line[7:].strip()
                    continue
                # Parseia campo spec:
                if line.startswith("spec:"):
                    current_spec_id = line[5:].strip()
                    continue
                key, sep, value = line.partition(":")
                if sep and key.strip().lower() in _META_KEYS:
                    meta_key = key.strip().lower()
                    meta_value = value.strip()
                    current_metadata[meta_key] = meta_value
                    if meta_key == "priority":
                        current_priority = meta_value or "medium"
                    elif meta_key == "assignee":
                        current_assignee = meta_value
                    elif meta_key == "parent":
                        current_parent_id = _normalize_task_id(meta_value) if meta_value else ""
                    continue
                if line.startswith("comment:"):
                    current_comments.append(_parse_comment(line[8:].strip()))
                    continue
                stripped = line.strip()
                if stripped and stripped != "---":
                    desc_lines.append(stripped)

        if current_id:
            tasks.append(Task(
                id=current_id,
                status=current_status,  # type: ignore[arg-type]
                title=current_title,    # type: ignore[arg-type]
                description=" ".join(desc_lines).strip(),
                spec_id=current_spec_id or "",
                priority=current_priority or "medium",
                assignee=current_assignee,
                parent_id=current_parent_id,
                created_at=current_created_at,
                comments=list(current_comments),
                metadata=dict(current_metadata),
            ))

        return tasks

    def update_task_status(self, task_id: str, new_status: str) -> Task:
        """Atualiza o status de uma tarefa. ID e título são imutáveis."""
        task_id = _normalize_task_id(task_id)
        new_status = new_status.upper().strip()
        if new_status not in _VALID_STATUSES:
            raise WorkspaceError(
                f"Status invalido: '{new_status}'. "
                f"Validos: {', '.join(sorted(_VALID_STATUSES))}"
            )
        if not self.tasks_file.exists():
            raise WorkspaceError("TASKS.md nao encontrado. Rode: bauer project init")

        text = self.tasks_file.read_text(encoding="utf-8")

        # Encontra heading seguido imediatamente pelo id: TASK_ID
        pattern = re.compile(
            r"(## \[[A-Z_]+\] [^\n]+\n)(id:\s*" + re.escape(task_id) + r"\b)",
            re.MULTILINE,
        )
        m = pattern.search(text)
        if not m:
            raise WorkspaceError(f"Tarefa '{task_id}' nao encontrada em TASKS.md.")

        old_heading = m.group(1)
        new_heading = re.sub(r"\[[A-Z_]+\]", f"[{new_status}]", old_heading)
        new_text = text[: m.start(1)] + new_heading + text[m.start(1) + len(old_heading) :]
        self.tasks_file.write_text(new_text, encoding="utf-8")

        for t in self.list_tasks():
            if t.id == task_id:
                return t
        raise WorkspaceError(f"Erro interno: tarefa '{task_id}' nao encontrada apos update.")

    def update_task_metadata(
        self,
        task_id: str,
        *,
        priority: str | None = None,
        assignee: str | None = None,
        parent_id: str | None = None,
        metadata: dict[str, str | int | None] | None = None,
    ) -> Task:
        """Atualiza metadados opcionais de uma tarefa no TASKS.md."""
        updates: dict[str, str] = {}
        if priority is not None:
            updates["priority"] = priority.strip().lower()
        if assignee is not None:
            updates["assignee"] = assignee.strip()
        if parent_id is not None:
            updates["parent"] = _normalize_task_id(parent_id) if parent_id.strip() else ""
        for key, value in (metadata or {}).items():
            meta_key = str(key).strip().lower()
            if meta_key not in _META_KEYS:
                continue
            updates[meta_key] = "" if value is None else _single_line(str(value))
        if not updates:
            return self.get_task(task_id)

        def _apply(block: str) -> str:
            return _upsert_metadata(block, updates)

        return self._modify_task_block(task_id, _apply)

    def add_task_comment(self, task_id: str, text: str, author: str = "agent") -> Task:
        """Adiciona um comentario auditavel dentro do bloco da tarefa."""
        comment = _single_line(text)
        if not comment:
            raise WorkspaceError("Comentario vazio.")
        author = _single_line(author or "agent")
        line = f"comment: {_now_iso()} | {author} | {comment}"

        def _apply(block: str) -> str:
            lines = block.splitlines()
            insert_at = len(lines)
            for idx in range(len(lines) - 1, -1, -1):
                if lines[idx].strip() == "---":
                    insert_at = idx
                    break
            lines.insert(insert_at, line)
            return "\n".join(lines) + ("\n" if block.endswith("\n") else "")

        return self._modify_task_block(task_id, _apply)

    def get_task(self, task_id: str) -> Task:
        """Retorna uma tarefa por ID, aceitando '001' ou alias 'T0001'."""
        task_id = _normalize_task_id(task_id)
        for task in self.list_tasks():
            if task.id == task_id:
                return task
        raise WorkspaceError(f"Tarefa '{task_id}' nao encontrada em TASKS.md.")

    def get_project_info(self) -> str:
        """Retorna o conteúdo do PROJECT.md."""
        if not self.project_file.exists():
            return "[PROJECT.md nao encontrado — rode: bauer project init]"
        return self.project_file.read_text(encoding="utf-8")


    def _modify_task_block(self, task_id: str, apply) -> Task:  # type: ignore[no-untyped-def]
        task_id = _normalize_task_id(task_id)
        if not self.tasks_file.exists():
            raise WorkspaceError("TASKS.md nao encontrado. Rode: bauer project init")

        text = self.tasks_file.read_text(encoding="utf-8")
        start, end = _find_task_block_span(text, task_id)
        new_block = apply(text[start:end])
        self.tasks_file.write_text(text[:start] + new_block + text[end:], encoding="utf-8")
        return self.get_task(task_id)


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _normalize_task_id(task_id: str) -> str:
    raw = str(task_id).strip()
    if raw.upper().startswith("T") and raw[1:].isdigit():
        raw = raw[1:]
    if raw.isdigit():
        return str(int(raw)).zfill(3)
    return raw.zfill(3)


def _single_line(value: str) -> str:
    return re.sub(r"\s+", " ", str(value)).strip()


def _parse_comment(value: str) -> dict[str, str]:
    parts = [p.strip() for p in value.split("|", 2)]
    if len(parts) == 3:
        return {"at": parts[0], "author": parts[1], "text": parts[2]}
    return {"at": "", "author": "", "text": value.strip()}


def _find_task_block_span(text: str, task_id: str) -> tuple[int, int]:
    headings = list(re.finditer(r"^## \[[A-Z_]+\] .+$", text, re.MULTILINE))
    for idx, heading in enumerate(headings):
        start = heading.start()
        end = headings[idx + 1].start() if idx + 1 < len(headings) else len(text)
        block = text[start:end]
        if re.search(r"^id:\s*" + re.escape(task_id) + r"\s*$", block, re.MULTILINE):
            return start, end
    raise WorkspaceError(f"Tarefa '{task_id}' nao encontrada em TASKS.md.")


def _upsert_metadata(block: str, updates: dict[str, str]) -> str:
    lines = block.splitlines()
    for field_name, value in updates.items():
        prefix = f"{field_name}:"
        found_idx: int | None = None
        for idx, line in enumerate(lines):
            if line.lower().startswith(prefix):
                found_idx = idx
                break

        if found_idx is not None:
            if value:
                lines[found_idx] = f"{field_name}: {value}"
            else:
                del lines[found_idx]
            continue

        if not value:
            continue

        insert_at = 1
        for idx, line in enumerate(lines):
            lower = line.lower()
            if (
                lower.startswith("id:")
                or lower.startswith("criado:")
                or lower.startswith("spec:")
                or any(lower.startswith(f"{meta_key}:") for meta_key in _META_KEYS)
            ):
                insert_at = idx + 1
        lines.insert(insert_at, f"{field_name}: {value}")

    return "\n".join(lines) + ("\n" if block.endswith("\n") else "")
