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
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

_VALID_STATUSES = {"TODO", "IN_PROGRESS", "DONE", "BLOCKED"}
_HEADING_RE = re.compile(r"^## \[([A-Z_]+)\] (.+)$")
_ID_RE = re.compile(r"^id:\s*(\d+)\s*$")


@dataclass
class Task:
    id: str       # zero-padded, e.g. "001"
    status: str   # TODO | IN_PROGRESS | DONE | BLOCKED
    title: str
    description: str = ""
    spec_id: str = ""   # ID do spec vinculado (vazio = sem spec)


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
                "Status validos: TODO | IN_PROGRESS | DONE | BLOCKED\n\n---\n",
                encoding="utf-8",
            )
            created.append(self.tasks_file)

        return created

    # --- tarefas -------------------------------------------------------------

    def add_task(self, title: str, description: str = "", spec_id: str = "") -> Task:
        """Adiciona tarefa ao TASKS.md. ID é sequencial e imutável.

        Args:
            title: Título da tarefa.
            description: Descrição opcional.
            spec_id: ID do spec vinculado (vazio = sem spec). Quando definido,
                     escreve `spec: <id>` no bloco — o agent usa isso para
                     carregar o contrato automaticamente.
        """
        if not self.tasks_file.exists():
            self.init_project("Projeto")

        existing = self.list_tasks()
        task_id = str(len(existing) + 1).zfill(3)
        ts = _today()

        block = f"\n## [TODO] {title}\nid: {task_id}\ncriado: {ts}\n"
        if spec_id.strip():
            block += f"spec: {spec_id.strip()}\n"
        if description.strip():
            block += f"\n{description.strip()}\n"
        block += "\n---\n"

        with self.tasks_file.open("a", encoding="utf-8") as f:
            f.write(block)

        return Task(id=task_id, status="TODO", title=title, description=description, spec_id=spec_id)

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
                    ))
                current_status, current_title = hm.group(1), hm.group(2)
                current_id = None
                current_spec_id = None
                desc_lines = []
                continue

            if current_status is not None:
                id_m = _ID_RE.match(line)
                if id_m:
                    current_id = id_m.group(1).zfill(3)
                    continue
                # Parseia campo spec:
                if line.startswith("spec:"):
                    current_spec_id = line[5:].strip()
                    continue
                stripped = line.strip()
                if stripped and not stripped.startswith("criado:") and stripped != "---":
                    desc_lines.append(stripped)

        if current_id:
            tasks.append(Task(
                id=current_id,
                status=current_status,  # type: ignore[arg-type]
                title=current_title,    # type: ignore[arg-type]
                description=" ".join(desc_lines).strip(),
                spec_id=current_spec_id or "",
            ))

        return tasks

    def update_task_status(self, task_id: str, new_status: str) -> Task:
        """Atualiza o status de uma tarefa. ID e título são imutáveis."""
        task_id = str(task_id).zfill(3)
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

    def get_project_info(self) -> str:
        """Retorna o conteúdo do PROJECT.md."""
        if not self.project_file.exists():
            return "[PROJECT.md nao encontrado — rode: bauer project init]"
        return self.project_file.read_text(encoding="utf-8")


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")
