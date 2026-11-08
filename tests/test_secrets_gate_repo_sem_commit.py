"""K07 (auditoria 2026-10-12): SecretsGate em repo sem primeiro commit e com falha.

Antes, ``git diff HEAD`` falhava num ``git init`` recém-criado (não há HEAD), o
gate lia isso como "não é repo git" e APROVAVA — sem nem olhar os arquivos não
versionados. Qualquer falha operacional do git tinha o mesmo destino. Agora:
não-repo passa, repo sem HEAD é comparado com a árvore vazia, e falha do
scanner reprova com o motivo explícito (o gate é crítico).
"""

from __future__ import annotations

import subprocess

import pytest

from bauer.core.kernel.evaluator import Evaluator
from bauer.core.kernel.gates import SecretsGate
from bauer.core.kernel.gates import secrets as secrets_mod

CHAVE = "sk-" + "a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6q7R8s9T0u1V2w3X4"


def _git(ws, *args):
    r = subprocess.run(["git", *args], cwd=ws, capture_output=True, text=True)
    if r.returncode != 0:
        pytest.skip(f"git indisponível: {r.stderr!r}")
    return r


@pytest.fixture
def repo_vazio(tmp_path):
    ws = tmp_path / "novo"
    ws.mkdir()
    _git(ws, "init", "-q")
    return ws


def _commit(ws):
    _git(ws, "add", "-A")
    _git(ws, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "i")


def test_segredo_untracked_em_repo_sem_commit_reprova(repo_vazio):
    (repo_vazio / "new.py").write_text(f'OPENAI_API_KEY="{CHAVE}"\n', encoding="utf-8")

    r = SecretsGate(repo_vazio).check(request=None, result={})

    assert not r.passed
    assert "segredo" in r.reason
    assert CHAVE not in r.reason


def test_segredo_staged_em_repo_sem_commit_reprova(repo_vazio):
    (repo_vazio / "new.py").write_text(f'API_KEY = "{CHAVE}"\n', encoding="utf-8")
    _git(repo_vazio, "add", "new.py")

    assert not SecretsGate(repo_vazio).check(request=None, result={}).passed


def test_segredo_staged_e_apagado_da_arvore_ainda_reprova_sem_commit(repo_vazio):
    """O index é o que o primeiro commit levaria, mesmo que a árvore de
    trabalho já não mostre mais a chave."""
    alvo = repo_vazio / "new.py"
    alvo.write_text(f'API_KEY = "{CHAVE}"\n', encoding="utf-8")
    _git(repo_vazio, "add", "new.py")
    alvo.write_text("API_KEY = None\n", encoding="utf-8")

    assert not SecretsGate(repo_vazio).check(request=None, result={}).passed


def test_segredo_staged_e_apagado_da_arvore_reprova_com_head(tmp_path):
    ws = tmp_path / "repo"
    ws.mkdir()
    (ws / "a.py").write_text("x = 1\n", encoding="utf-8")
    _git(ws, "init", "-q")
    _commit(ws)
    (ws / "a.py").write_text(f'API_KEY = "{CHAVE}"\n', encoding="utf-8")
    _git(ws, "add", "a.py")
    (ws / "a.py").write_text("x = 2\n", encoding="utf-8")

    assert not SecretsGate(ws).check(request=None, result={}).passed


def test_repo_sem_commit_sem_segredo_passa(repo_vazio):
    (repo_vazio / "a.py").write_text("x = 1\n", encoding="utf-8")
    _git(repo_vazio, "add", "a.py")
    (repo_vazio / "b.py").write_text("y = 2\n", encoding="utf-8")

    r = SecretsGate(repo_vazio).check(request=None, result={})

    assert r.passed and "nenhum segredo" in r.reason


def test_repo_sem_commit_e_sem_arquivos_passa(repo_vazio):
    r = SecretsGate(repo_vazio).check(request=None, result={})
    assert r.passed and r.reason == "nada adicionado"


def test_fora_de_repo_continua_passando_mesmo_sem_git(tmp_path, monkeypatch):
    """Sem ``.git`` não há diff — nem é preciso chamar o binário."""
    def explode(*a, **k):
        raise FileNotFoundError("git")

    monkeypatch.setattr(secrets_mod.subprocess, "run", explode)
    r = SecretsGate(tmp_path).check(request=None, result={})
    assert r.passed and "não é repo git" in r.reason


def test_git_ausente_num_repo_reprova_explicitamente(repo_vazio, monkeypatch):
    (repo_vazio / "new.py").write_text(f'K = "{CHAVE}"\n', encoding="utf-8")

    def explode(*a, **k):
        raise FileNotFoundError("git")

    monkeypatch.setattr(secrets_mod.subprocess, "run", explode)
    r = SecretsGate(repo_vazio).check(request=None, result={})

    assert not r.passed
    assert "não foi possível escanear" in r.reason
    assert "git não encontrado" in r.reason


def test_timeout_do_git_reprova_explicitamente(repo_vazio, monkeypatch):
    def lento(cmd, **k):
        raise subprocess.TimeoutExpired(cmd, k.get("timeout"))

    monkeypatch.setattr(secrets_mod.subprocess, "run", lento)
    r = SecretsGate(repo_vazio, timeout_s=1).check(request=None, result={})

    assert not r.passed and "excedeu" in r.reason


@pytest.mark.parametrize("subcomando", ["diff", "ls-files", "rev-parse"])
def test_erro_operacional_do_git_reprova(tmp_path, monkeypatch, subcomando):
    ws = tmp_path / "repo"
    ws.mkdir()
    (ws / "a.py").write_text("x = 1\n", encoding="utf-8")
    _git(ws, "init", "-q")
    _commit(ws)
    real = subprocess.run

    def falha_em(cmd, **k):
        if cmd[1] == subcomando:
            return subprocess.CompletedProcess(cmd, 128, "", "fatal: erro de teste")
        return real(cmd, **k)

    monkeypatch.setattr(secrets_mod.subprocess, "run", falha_em)
    r = SecretsGate(ws).check(request=None, result={})

    assert not r.passed
    assert "não foi possível escanear" in r.reason


def test_falha_do_scanner_bloqueia_a_conclusao_no_evaluator(repo_vazio, monkeypatch):
    """O verdict crítico chega ao Evaluator como reprovação, não como aviso."""
    (repo_vazio / "a.py").write_text("x = 1\n", encoding="utf-8")

    def scanner_quebrado(*a, **k):
        raise RuntimeError("scanner quebrou")

    import bauer.secrets_scanner as scanner
    monkeypatch.setattr(scanner, "scan", scanner_quebrado)

    verdict = Evaluator(gates=[SecretsGate(repo_vazio)]).evaluate(
        run_id="r", request=None, result={"output": "ok"})

    assert not verdict.passed
    assert "secrets" in verdict.reason
