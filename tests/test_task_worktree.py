"""Testes do task_worktree — worktree git por task + commit do diff."""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from bauer import task_worktree as wt


def _git(args, cwd):
    return subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True)


@pytest.fixture
def git_repo(tmp_path):
    """Cria um repo git temporário com um commit inicial."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(["init", "-b", "main"], repo)
    _git(["config", "user.email", "t@t.com"], repo)
    _git(["config", "user.name", "Test"], repo)
    (repo / "README.md").write_text("inicial\n", encoding="utf-8")
    _git(["add", "-A"], repo)
    _git(["commit", "-m", "init"], repo)
    return repo


# ── detecção de repo ─────────────────────────────────────────────────────────

def test_is_git_repo_true(git_repo):
    assert wt.is_git_repo(git_repo) is True


def test_is_git_repo_false(tmp_path):
    plain = tmp_path / "plain"
    plain.mkdir()
    assert wt.is_git_repo(plain) is False


def test_repo_root(git_repo):
    root = wt.repo_root(git_repo)
    assert root is not None
    assert root.resolve() == git_repo.resolve()


# ── no-op gracioso fora de git ───────────────────────────────────────────────

def test_create_worktree_noop_outside_git(tmp_path):
    plain = tmp_path / "plain"
    plain.mkdir()
    assert wt.create_worktree(plain, "042") is None


# ── ciclo completo: cria worktree, escreve, commita ──────────────────────────

def test_worktree_create_commit_cycle(git_repo):
    info = wt.create_worktree(git_repo, "042")
    assert info is not None
    assert info.branch == "bauer/task-042"
    assert info.path.exists()

    # Worker "produz artefato" dentro do worktree
    (info.path / "saida.txt").write_text("resultado real\n", encoding="utf-8")
    (info.path / "novo.py").write_text("print('x')\n", encoding="utf-8")

    commit = wt.commit_worktree(info, "task 042: entrega")
    assert commit.committed is True
    assert commit.branch == "bauer/task-042"
    assert commit.commit  # sha curto
    assert "saida.txt" in commit.changed_files
    assert "novo.py" in commit.changed_files

    # O branch existe no repo principal e tem o commit
    branches = _git(["branch", "--list", "bauer/task-042"], git_repo).stdout
    assert "bauer/task-042" in branches


def test_commit_worktree_no_changes(git_repo):
    info = wt.create_worktree(git_repo, "043")
    assert info is not None
    commit = wt.commit_worktree(info, "nada mudou")
    assert commit.committed is False
    assert commit.changed_files == []


def test_summarize_artifact_committed(git_repo):
    info = wt.create_worktree(git_repo, "044")
    (info.path / "a.txt").write_text("x", encoding="utf-8")
    commit = wt.commit_worktree(info, "msg")
    line = wt.summarize_artifact(commit)
    assert "bauer/task-044" in line
    assert "a.txt" in line


def test_summarize_artifact_no_diff(git_repo):
    info = wt.create_worktree(git_repo, "045")
    commit = wt.commit_worktree(info, "msg")
    assert "nenhuma mudança" in wt.summarize_artifact(commit)


def test_remove_worktree(git_repo):
    info = wt.create_worktree(git_repo, "046")
    assert info.path.exists()
    assert wt.remove_worktree(info) is True
    assert not info.path.exists()


def test_branch_name_sanitizes():
    assert wt._branch_name("a b/c").startswith("bauer/task-")
    assert " " not in wt._branch_name("x y")
