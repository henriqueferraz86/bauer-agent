"""S10 — TaskContract: o que a tarefa pode tocar e o que precisa provar.

Este contrato existe por causa de dois contornos que o S11 me obrigou a fazer:

- o gate de testes virou ratchet (reprova só falha nova) porque não sabia quais
  testes importavam para *aquela* tarefa;
- o gate de escopo foi **descartado** porque não havia com o que comparar os
  arquivos alterados.

O caso concreto: a tarefa mandava mexer só no `doc.py`, o agente obedeceu, e o
run falhou por um teste quebrado em outro módulo que a tarefa proibia tocar.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from bauer.core.kernel.gates.scope import ScopeGate, arquivos_alterados
from bauer.core.task import TaskContract


def _contrato(**scope):
    return TaskContract.from_mapping({"objective": "x", "scope": scope})


def _repo(tmp_path: Path) -> Path:
    r = tmp_path / "p"
    (r / "tests").mkdir(parents=True)
    subprocess.run(["git", "init", "-q"], cwd=r, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=r, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=r, check=True)
    (r / "doc.py").write_text("A = 1\n", encoding="utf-8")
    (r / "stats.py").write_text("B = 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=r, check=True)
    subprocess.run(["git", "commit", "-qm", "b"], cwd=r, check=True)
    return r


# ─── escopo ──────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("caminho,ok", [
    ("doc.py", True),
    ("tests/test_a.py", True),          # prefixo de diretório
    ("tests/sub/test_b.py", True),
    ("stats.py", False),                # fora do allowed
    ("migrations/001.sql", False),      # forbidden explícito
])
def test_perimetro(caminho, ok):
    c = _contrato(allowed=["doc.py", "tests/"], forbidden=["migrations/"])
    assert (c.scope.viola(caminho) is None) is ok


def test_forbidden_vence_allowed():
    """Proibição é mais forte que permissão — senão `allowed: ['.']` anularia
    todo forbidden e a seção viraria decoração."""
    c = _contrato(allowed=["."], forbidden=["segredos.env"])
    assert c.scope.viola("segredos.env") is not None
    assert c.scope.viola("qualquer.py") is None


def test_allowed_vazio_nao_bloqueia_tudo():
    """Contrato pela metade não pode paralisar o agente.

    `allowed` vazio = "sem restrição declarada", não "nada permitido". O sandbox
    do tool_router continua valendo de qualquer forma.
    """
    c = _contrato(forbidden=["migrations/"])
    assert c.scope.viola("qualquer/coisa.py") is None
    assert c.scope.viola("migrations/x.sql") is not None


def test_sem_escopo_declarado():
    assert TaskContract.from_mapping({"objective": "x"}).tem_escopo is False


# ─── carga ───────────────────────────────────────────────────────────────────


def test_descobre_no_workspace(tmp_path):
    (tmp_path / ".bauer").mkdir()
    (tmp_path / ".bauer" / "task.yaml").write_text(
        "objective: corrigir X\n"
        "scope:\n  allowed:\n    - doc.py\n"
        "validation:\n  timeout_seconds: 90\n  selection: full\n",
        encoding="utf-8")

    c = TaskContract.descobrir(tmp_path)

    assert c.objective == "corrigir X"
    assert c.scope.allowed == ["doc.py"]
    assert c.validation.timeout_seconds == 90
    assert c.validation.selection == "full"


def test_sem_arquivo_e_ausencia_nao_erro(tmp_path):
    assert TaskContract.descobrir(tmp_path) is None


def test_yaml_quebrado_e_ausencia_nao_erro(tmp_path):
    """YAML mal editado não pode paralisar toda execução."""
    (tmp_path / ".bauer").mkdir()
    (tmp_path / ".bauer" / "task.yaml").write_text("scope: [: {{", encoding="utf-8")

    assert TaskContract.descobrir(tmp_path) is None


def test_selection_invalida_cai_para_related():
    c = TaskContract.from_mapping({"validation": {"selection": "banana"}})
    assert c.validation.selection == "related"


# ─── ScopeGate ───────────────────────────────────────────────────────────────


def test_alteracao_fora_do_escopo_reprova(tmp_path):
    """O caso que motivou o S10, agora pego."""
    repo = _repo(tmp_path)
    (repo / "stats.py").write_text("B = 2\n", encoding="utf-8")

    r = ScopeGate(repo, _contrato(allowed=["doc.py"])).check(request=None, result={})

    assert not r.passed
    assert "stats.py" in r.reason and "fora do escopo" in r.reason


def test_alteracao_dentro_do_escopo_passa(tmp_path):
    repo = _repo(tmp_path)
    (repo / "doc.py").write_text("A = 2\n", encoding="utf-8")

    r = ScopeGate(repo, _contrato(allowed=["doc.py"])).check(request=None, result={})

    assert r.passed and "todos no escopo" in r.reason


def test_ruido_do_bauer_nao_conta_como_violacao(tmp_path):
    """`memory/runtime/` é do Kernel, não do agente — acusá-lo seria reprovar
    todo run pelo próprio rastro do Bauer."""
    repo = _repo(tmp_path)
    (repo / "memory" / "runtime").mkdir(parents=True)
    (repo / "memory" / "runtime" / "runs.jsonl").write_text("{}\n", encoding="utf-8")

    r = ScopeGate(repo, _contrato(allowed=["doc.py"])).check(request=None, result={})

    assert r.passed


def test_sem_contrato_nao_reprova(tmp_path):
    """Run sem contrato segue como hoje — o sandbox do tool_router continua lá."""
    repo = _repo(tmp_path)
    (repo / "stats.py").write_text("B = 2\n", encoding="utf-8")

    r = ScopeGate(repo, None).check(request=None, result={})

    assert r.passed and "sem escopo declarado" in r.reason


def test_fora_de_repo_git_nao_afirma_o_que_nao_sabe(tmp_path):
    r = ScopeGate(tmp_path, _contrato(allowed=["doc.py"])).check(request=None, result={})
    assert r.passed and "não é repo git" in r.reason


def test_renomeio_julga_o_destino(tmp_path):
    repo = _repo(tmp_path)
    subprocess.run(["git", "mv", "doc.py", "stats2.py"], cwd=repo, check=True)

    alterados = arquivos_alterados(repo)

    assert "stats2.py" in alterados, "o destino é o que passa a existir"


# ─── ligação com o Kernel ────────────────────────────────────────────────────


def test_scope_gate_entra_sozinho_quando_ha_contrato(tmp_path):
    """Quem escreveu o perímetro quer que ele valha — sem segunda flag."""
    from bauer.config_loader import BauerConfig, KernelSection, ModelSection
    from bauer.core.kernel.kernel import evaluator_from_config

    (tmp_path / ".bauer").mkdir()
    (tmp_path / ".bauer" / "task.yaml").write_text(
        "scope:\n  allowed:\n    - doc.py\n", encoding="utf-8")
    cfg = BauerConfig(model=ModelSection(provider="ollama", name="x"),
                      kernel=KernelSection(evaluator_enabled=True))

    nomes = [g.name for g in evaluator_from_config(cfg, workspace=str(tmp_path)).gates]

    assert "scope" in nomes


def test_sem_contrato_nao_monta_scope_gate(tmp_path):
    from bauer.config_loader import BauerConfig, KernelSection, ModelSection
    from bauer.core.kernel.kernel import evaluator_from_config

    cfg = BauerConfig(model=ModelSection(provider="ollama", name="x"),
                      kernel=KernelSection(evaluator_enabled=True))

    nomes = [g.name for g in evaluator_from_config(cfg, workspace=str(tmp_path)).gates]

    assert "scope" not in nomes


def test_contrato_manda_no_timeout_do_gate_de_testes(tmp_path):
    """O contrato conhece a tarefa; o config é só o default de quem não declarou."""
    from bauer.config_loader import BauerConfig, KernelSection, ModelSection
    from bauer.core.kernel.kernel import evaluator_from_config

    (tmp_path / ".bauer").mkdir()
    (tmp_path / ".bauer" / "task.yaml").write_text(
        "validation:\n  timeout_seconds: 45\n", encoding="utf-8")
    cfg = BauerConfig(model=ModelSection(provider="ollama", name="x"),
                      kernel=KernelSection(evaluator_enabled=True, tests_gate=True,
                                           tests_gate_timeout_s=600))

    ev = evaluator_from_config(cfg, workspace=str(tmp_path))

    assert next(g for g in ev.gates if g.name == "tests").timeout_s == 45


@pytest.mark.parametrize("padrao", [".", "./", "*", "**", "/"])
def test_padrao_de_projeto_inteiro(padrao):
    """`allowed: ['.']` é a forma natural de escrever "tudo".

    Sem tratar o caso, o normalizador comia o ponto e sobrava string vazia — o
    contrato mais PERMISSIVO possível virava o mais restritivo, inverso exato da
    intenção de quem escreveu.
    """
    c = _contrato(allowed=[padrao])
    assert c.scope.viola("qualquer/coisa.py") is None
