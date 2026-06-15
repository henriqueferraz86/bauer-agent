"""Worktree git por task — isola o trabalho do worker e captura o resultado
como diff commitável (artefato real, não prosa).

Quando o workspace do worker é um repositório git, o dispatcher cria um
`git worktree` dedicado num branch `bauer/task-<id>`, roda o worker lá, e ao
concluir commita as mudanças — o artefato da task passa a ser um diff
reviewable num branch isolado (sem contenção entre workers paralelos).

Quando o workspace NÃO é um repo git (caso comum do sandbox `workspace/`),
todas as funções degradam para no-op e o comportamento atual é preservado.

Espelha o conceito de "worktree workspace kind" do Hermes, adaptado ao Bauer.
Tudo via subprocess `git` — sem dependências novas.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class WorktreeInfo:
    """Worktree criado para uma task."""
    path: Path
    branch: str
    repo_root: Path


@dataclass
class CommitResult:
    """Resultado de commitar o trabalho do worker no worktree."""
    committed: bool
    branch: str = ""
    commit: str = ""
    changed_files: list[str] = field(default_factory=list)
    message: str = ""


def _git(args: list[str], cwd: str | Path, timeout: float = 30.0) -> subprocess.CompletedProcess:
    """Roda git silenciosamente; nunca levanta — retorna o CompletedProcess."""
    return subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )


def is_git_repo(path: str | Path) -> bool:
    """True se `path` está dentro de uma árvore de trabalho git."""
    p = Path(path)
    if not p.exists():
        return False
    try:
        r = _git(["rev-parse", "--is-inside-work-tree"], p)
        return r.returncode == 0 and r.stdout.strip() == "true"
    except Exception:
        return False


def repo_root(path: str | Path) -> Path | None:
    """Raiz do repositório git que contém `path`, ou None."""
    try:
        r = _git(["rev-parse", "--show-toplevel"], path)
        if r.returncode == 0 and r.stdout.strip():
            return Path(r.stdout.strip())
    except Exception:
        pass
    return None


def _branch_name(task_id: str) -> str:
    safe = "".join(c if (c.isalnum() or c in "-_") else "-" for c in str(task_id)).strip("-")
    return f"bauer/task-{safe or 'unknown'}"


def create_worktree(workspace: str | Path, task_id: str) -> WorktreeInfo | None:
    """Cria um git worktree dedicado para a task num branch isolado.

    Retorna None (no-op) se o workspace não for um repo git ou se o git falhar.
    O worktree fica em `<repo_root>/.bauer_worktrees/<task_id>`.
    """
    root = repo_root(workspace)
    if root is None:
        return None
    branch = _branch_name(task_id)
    wt_dir = root / ".bauer_worktrees" / str(task_id)
    try:
        wt_dir.parent.mkdir(parents=True, exist_ok=True)
        # Se o worktree já existe (retry da mesma task), reutiliza.
        if wt_dir.exists() and is_git_repo(wt_dir):
            return WorktreeInfo(path=wt_dir, branch=branch, repo_root=root)
        # -B reseta o branch para o HEAD atual mesmo se já existir.
        r = _git(["worktree", "add", "-B", branch, str(wt_dir)], root)
        if r.returncode != 0:
            return None
        return WorktreeInfo(path=wt_dir, branch=branch, repo_root=root)
    except Exception:
        return None


def commit_worktree(wt: WorktreeInfo, message: str) -> CommitResult:
    """Commita TODAS as mudanças do worktree. Sem mudanças → committed=False.

    Retorna os arquivos alterados para o handoff da task (artefato = diff).
    """
    try:
        _git(["add", "-A"], wt.path)
        # Há algo staged?
        diff = _git(["diff", "--cached", "--name-only"], wt.path)
        changed = [ln.strip() for ln in diff.stdout.splitlines() if ln.strip()]
        if not changed:
            return CommitResult(committed=False, branch=wt.branch, changed_files=[])
        r = _git(["commit", "-m", message], wt.path)
        if r.returncode != 0:
            return CommitResult(committed=False, branch=wt.branch, changed_files=changed,
                                message=r.stderr.strip()[:300])
        sha = _git(["rev-parse", "--short", "HEAD"], wt.path).stdout.strip()
        return CommitResult(
            committed=True,
            branch=wt.branch,
            commit=sha,
            changed_files=changed,
            message=message,
        )
    except Exception as exc:  # noqa: BLE001
        return CommitResult(committed=False, branch=wt.branch, message=str(exc)[:300])


def remove_worktree(wt: WorktreeInfo) -> bool:
    """Remove o worktree do disco (branch preservado). Silencioso."""
    try:
        r = _git(["worktree", "remove", "--force", str(wt.path)], wt.repo_root)
        return r.returncode == 0
    except Exception:
        return False


def summarize_artifact(commit: CommitResult) -> str:
    """Linha de handoff legível com o artefato (branch + arquivos)."""
    if not commit.committed:
        if commit.changed_files:
            return f"[worktree] {len(commit.changed_files)} arquivo(s) alterado(s), commit falhou: {commit.message}"
        return "[worktree] nenhuma mudança de arquivo (sem diff)."
    files = ", ".join(commit.changed_files[:8])
    extra = f" (+{len(commit.changed_files) - 8})" if len(commit.changed_files) > 8 else ""
    return (
        f"[worktree] branch `{commit.branch}` @ {commit.commit} — "
        f"{len(commit.changed_files)} arquivo(s): {files}{extra}"
    )
