"""S12 nível 1 — o run autônomo trabalha num worktree, não no master.

A regra vem do CONTRATO, não é geral. O plano original dizia "nenhum `bauer run`
altera diretamente a branch principal" — isso quebraria o fluxo de trabalho deste
projeto, onde fix pequeno vai direto ao master sem branch nem PR. Quem decide é
`TaskContract.isolation`, e a omissão preserva o comportamento de hoje.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from bauer.core.task import TaskContract
from bauer.core.workspace.isolation import Isolamento, finalizar, preparar


def _repo(tmp_path: Path) -> Path:
    r = tmp_path / "proj"
    r.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=r, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=r, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=r, check=True)
    (r / "a.py").write_text("x = 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=r, check=True)
    subprocess.run(["git", "commit", "-qm", "base"], cwd=r, check=True)
    return r


def _contrato(**kw):
    return TaskContract.from_mapping({"objective": "x", **kw})


def _branch(repo: Path) -> str:
    return subprocess.run(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=repo,
                          capture_output=True, text=True).stdout.strip()


# ─── quem decide ─────────────────────────────────────────────────────────────


def test_sem_contrato_nao_isola(tmp_path):
    """Omissão preserva o comportamento de hoje — por decisão, não por acidente."""
    repo = _repo(tmp_path)
    iso = preparar(repo, None, run_id="r1")

    assert not iso.isolado
    assert iso.workspace == repo


def test_isolation_none_nao_isola(tmp_path):
    repo = _repo(tmp_path)
    assert not preparar(repo, _contrato(isolation="none"), run_id="r1").isolado


def test_isolation_worktree_isola(tmp_path):
    repo = _repo(tmp_path)
    iso = preparar(repo, _contrato(isolation="worktree"), run_id="r1")

    assert iso.isolado
    assert iso.workspace != repo
    assert iso.workspace.is_dir()
    assert _branch(repo) == "main", "o branch do repo original NÃO muda"
    assert _branch(iso.workspace).startswith("bauer/")


def test_trabalho_no_worktree_nao_toca_o_master(tmp_path):
    """O ponto todo do isolamento."""
    repo = _repo(tmp_path)
    iso = preparar(repo, _contrato(isolation="worktree"), run_id="r1")

    (iso.workspace / "a.py").write_text("x = 999\n", encoding="utf-8")

    assert (repo / "a.py").read_text(encoding="utf-8") == "x = 1\n"


# ─── degradação honesta ──────────────────────────────────────────────────────


def test_fora_de_repo_git_avisa_e_segue(tmp_path):
    """Não dá para isolar o que não é versionado — e recusar a rodar seria pior."""
    iso = preparar(tmp_path, _contrato(isolation="worktree"), run_id="r1")

    assert not iso.isolado
    assert iso.workspace == tmp_path
    assert "não é repo git" in iso.aviso


def test_container_degrada_para_worktree_com_aviso(tmp_path):
    """Ser explícito é melhor que fingir que containeriza (S12 nível 2 é P1)."""
    repo = _repo(tmp_path)
    iso = preparar(repo, _contrato(isolation="container"), run_id="r1")

    assert iso.isolado, "degrada para o que existe"
    assert "container ainda não implementado" in iso.aviso


def test_nivel_desconhecido_nao_isola_e_avisa(tmp_path):
    repo = _repo(tmp_path)
    iso = preparar(repo, _contrato(isolation="banana"), run_id="r1")

    assert not iso.isolado and "desconhecido" in iso.aviso


# ─── o contrato viaja para o worktree ────────────────────────────────────────


def test_contrato_e_copiado_para_o_worktree(tmp_path):
    """`.bauer/task.yaml` costuma ser NÃO versionado (é da tarefa, não do
    projeto), então não apareceria no worktree novo. Sem ele lá, os gates
    rodariam sem escopo — isolamento sem escopo é meia governança."""
    repo = _repo(tmp_path)
    (repo / ".bauer").mkdir()
    (repo / ".bauer" / "task.yaml").write_text(
        "isolation: worktree\nscope:\n  allowed:\n    - a.py\n", encoding="utf-8")

    iso = preparar(repo, TaskContract.descobrir(repo), run_id="r1")

    copiado = TaskContract.descobrir(iso.workspace)
    assert copiado is not None and copiado.scope.allowed == ["a.py"]


# ─── finalização ─────────────────────────────────────────────────────────────


def test_commita_o_trabalho_e_reporta_artefato(tmp_path):
    repo = _repo(tmp_path)
    iso = preparar(repo, _contrato(isolation="worktree"), run_id="r1")
    (iso.workspace / "novo.py").write_text("y = 2\n", encoding="utf-8")

    linha = finalizar(iso, objetivo="criar novo.py", sucesso=True)

    assert linha, "o artefato da tarefa é o diff, e ele precisa ser anunciado"
    log = subprocess.run(["git", "log", "--oneline", "-1", iso.worktree.branch],
                         cwd=repo, capture_output=True, text=True).stdout
    assert "criar novo.py" in log


def test_sem_mudanca_remove_o_worktree(tmp_path):
    """Worktree vazio só polui o repo."""
    repo = _repo(tmp_path)
    iso = preparar(repo, _contrato(isolation="worktree"), run_id="r1")
    caminho = iso.workspace

    assert finalizar(iso, objetivo="nada", sucesso=True) == ""
    assert not caminho.exists()


def test_falha_preserva_o_worktree(tmp_path):
    """O diff parcial é a evidência de onde o agente parou — jogá-lo fora custa
    mais que o disco que ocupa."""
    repo = _repo(tmp_path)
    iso = preparar(repo, _contrato(isolation="worktree"), run_id="r1")
    (iso.workspace / "meio.py").write_text("z = 3\n", encoding="utf-8")

    finalizar(iso, objetivo="parou no meio", sucesso=False)

    assert iso.workspace.exists()


def test_finalizar_sem_isolamento_e_noop(tmp_path):
    assert finalizar(Isolamento(workspace=tmp_path), objetivo="x", sucesso=True) == ""


# ─── nome do branch ──────────────────────────────────────────────────────────


@pytest.mark.parametrize("run_id", ["run-abc123", "run/com barra", "  "])
def test_run_id_sujo_nao_quebra_o_branch(tmp_path, run_id):
    repo = _repo(tmp_path)
    iso = preparar(repo, _contrato(isolation="worktree"), run_id=run_id)
    assert iso.isolado and " " not in iso.worktree.branch
