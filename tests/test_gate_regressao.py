"""O gate reprova o que PIOROU, não o que já estava vermelho.

O problema apareceu no primeiro uso real do gate de testes: a tarefa mandava
mexer SÓ num arquivo, o agente obedeceu e fez o trabalho certo, e o run falhou
por um teste quebrado em outro módulo que a tarefa proibia tocar.

O gate valida o PROJETO; a tarefa tem ESCOPO. Sem TaskContract (S10) para
reconciliar os dois, um projeto com suíte já vermelha reprovaria todo run
autônomo — o que na prática obriga a desligar o gate, e gate desligado não
governa nada.

Mesma solução que este repositório já usa para `except:pass`: um ratchet.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from bauer.core.kernel.gates.baseline import (
    caminho_baseline, comparar, extrair_falhas, gravar_baseline, ler_baseline,
)
from bauer.core.kernel.gates.tests import TestsGate


class _Step:
    def __init__(self, name="test", cmd=("pytest",), ok=False, output="",
                 skipped=False, reason=""):
        self.name, self.cmd, self.ok = name, list(cmd), ok
        self.output, self.skipped, self.reason = output, skipped, reason


class _Res:
    def __init__(self, ok, stack="python", steps=(), summary=""):
        self.ok, self.stack, self.steps, self.summary = ok, stack, list(steps), summary


def _repo(tmp_path: Path) -> Path:
    r = tmp_path / "p"
    r.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=r, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=r, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=r, check=True)
    (r / "a.py").write_text("x = 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=r, check=True)
    subprocess.run(["git", "commit", "-qm", "b"], cwd=r, check=True)
    (r / "novo.py").write_text("y = 2\n", encoding="utf-8")   # deixa sujo
    return r


def _saida(*ids):
    return "\n".join(f"FAILED {i} - assert 1 == 2" for i in ids)


# ─── extração de IDs ─────────────────────────────────────────────────────────


def test_extrai_falhas_do_pytest():
    saida = (
        "F.F                                       [100%]\n"
        "FAILED tests/test_a.py::test_um - assert 6 == 3\n"
        "ERROR tests/test_b.py\n"
        "2 failed in 0.02s\n"
    )
    assert extrair_falhas(saida) == {"tests/test_a.py::test_um", "tests/test_b.py"}


def test_saida_sem_formato_conhecido_nao_inventa():
    assert extrair_falhas("npm ERR! test failed\n1 failing") == set()


# ─── comparação ──────────────────────────────────────────────────────────────


def test_primeira_execucao_estabelece_baseline_sem_reprovar():
    """Herdar projeto vermelho não pode ser culpa de quem chegou agora."""
    regrediu, motivo = comparar({"t::a", "t::b"}, 2, None)
    assert regrediu is False
    assert "baseline estabelecido" in motivo


def test_falha_nova_reprova():
    base = {"falhas": ["t::a"], "total": 1}
    regrediu, motivo = comparar({"t::a", "t::b"}, 2, base)
    assert regrediu is True
    assert "t::b" in motivo
    assert "t::a" not in motivo, "só a NOVA entra no motivo"


def test_falha_preexistente_nao_reprova():
    base = {"falhas": ["t::a", "t::b"], "total": 2}
    regrediu, motivo = comparar({"t::a", "t::b"}, 2, base)
    assert regrediu is False
    assert "pré-existente" in motivo and "continua vermelho" in motivo


def test_menos_falhas_nao_reprova():
    base = {"falhas": ["t::a", "t::b"], "total": 2}
    assert comparar({"t::a"}, 1, base)[0] is False


def test_sem_ids_compara_contagem():
    """Runner sem formato reconhecido: grosseiro, mas não deixa piora passar."""
    base = {"falhas": [], "total": 1}
    assert comparar(set(), 3, base)[0] is True
    assert comparar(set(), 1, base)[0] is False


# ─── o ratchet no gate ───────────────────────────────────────────────────────


def test_primeiro_run_vermelho_passa_e_grava_baseline(tmp_path):
    repo = _repo(tmp_path)
    res = _Res(False, "python", [_Step(output=_saida("tests/t.py::test_x"))])

    r = TestsGate(repo, verify_fn=lambda *a, **k: res).check(request=None, result={})

    assert r.passed, "projeto herdado vermelho não trava o primeiro run"
    assert ler_baseline(repo)["falhas"] == ["tests/t.py::test_x"]


def test_segundo_run_com_falha_nova_reprova(tmp_path):
    repo = _repo(tmp_path)
    gravar_baseline(repo, falhas={"tests/t.py::test_x"}, total=1)
    res = _Res(False, "python", [
        _Step(output=_saida("tests/t.py::test_x", "tests/t.py::test_y"))])

    r = TestsGate(repo, verify_fn=lambda *a, **k: res).check(request=None, result={})

    assert not r.passed
    assert "tests/t.py::test_y" in r.reason
    assert "que passavam agora falham" in r.reason


def test_suite_verde_aperta_o_baseline(tmp_path):
    """Dívida paga fica travada — quem consertou não quer o teste de volta."""
    repo = _repo(tmp_path)
    gravar_baseline(repo, falhas={"tests/t.py::test_x"}, total=1)

    r = TestsGate(repo, verify_fn=lambda *a, **k: _Res(True)).check(
        request=None, result={})

    assert r.passed
    assert ler_baseline(repo)["falhas"] == [], "verde reseta o baseline"


def test_baseline_encolhe_quando_falha_some(tmp_path):
    repo = _repo(tmp_path)
    gravar_baseline(repo, falhas={"t::a", "t::b"}, total=2)
    res = _Res(False, "python", [_Step(output=_saida("t::a"))])

    TestsGate(repo, verify_fn=lambda *a, **k: res).check(request=None, result={})

    assert ler_baseline(repo)["falhas"] == ["t::a"], "o ratchet aperta sozinho"


def test_modo_estrito_reprova_qualquer_falha(tmp_path):
    """Onde a suíte já é verde, qualquer falha deve derrubar — sem indulgência."""
    repo = _repo(tmp_path)
    res = _Res(False, "python", [_Step(output=_saida("t::a"))])

    r = TestsGate(repo, modo="estrito",
                  verify_fn=lambda *a, **k: res).check(request=None, result={})

    assert not r.passed
    assert not caminho_baseline(repo).exists(), "modo estrito não usa baseline"


def test_baseline_ilegivel_nao_derruba_o_gate(tmp_path):
    repo = _repo(tmp_path)
    p = caminho_baseline(repo)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("{lixo", encoding="utf-8")
    res = _Res(False, "python", [_Step(output=_saida("t::a"))])

    r = TestsGate(repo, verify_fn=lambda *a, **k: res).check(request=None, result={})

    assert r.passed, "baseline corrompido vira 'sem baseline', não erro"


def test_baseline_fica_no_ruido_ignorado_pelo_git(tmp_path):
    """O arquivo mora em memory/runtime/ — que o gate já ignora ao detectar
    mudança. Senão gravá-lo sujaria o repo e faria o gate disparar sozinho no
    run seguinte."""
    from bauer.core.kernel.gates.tests import _e_ruido

    repo = _repo(tmp_path)
    rel = caminho_baseline(repo).relative_to(repo).as_posix()
    assert _e_ruido(rel)


# ─── config ──────────────────────────────────────────────────────────────────


def test_modo_default_e_regressao(tmp_path):
    from bauer.config_loader import BauerConfig, KernelSection, ModelSection
    from bauer.core.kernel.kernel import evaluator_from_config

    cfg = BauerConfig(model=ModelSection(provider="ollama", name="x"),
                      kernel=KernelSection(evaluator_enabled=True, tests_gate=True))
    ev = evaluator_from_config(cfg, workspace=str(tmp_path))
    gate = next(g for g in ev.gates if g.name == "tests")

    assert gate.modo == "regressao", "o default tem de ser o modo usável"


def test_modo_estrito_pelo_config(tmp_path):
    from bauer.config_loader import BauerConfig, KernelSection, ModelSection
    from bauer.core.kernel.kernel import evaluator_from_config

    cfg = BauerConfig(model=ModelSection(provider="ollama", name="x"),
                      kernel=KernelSection(evaluator_enabled=True, tests_gate=True,
                                           tests_gate_mode="estrito"))
    ev = evaluator_from_config(cfg, workspace=str(tmp_path))

    assert next(g for g in ev.gates if g.name == "tests").modo == "estrito"
