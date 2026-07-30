"""S11 — o run só conclui se os testes passarem.

O primeiro gate SUBSTANTIVO do harness: ele executa, não interpreta prosa. Toda
a custódia do S8 (11 caminhos) existe para isto — até aqui rodavam dois gates,
ambos farejando texto do output.

Três propriedades decidem se o gate é usável ou um estorvo, e cada uma tem teste
aqui: só dispara quando o run MUDOU arquivos; roda só o passo `test` (nada de
install/serve); e reprova quando não consegue afirmar que passou.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from bauer.core.kernel.gates.tests import TestsGate, houve_mudanca


class _Res:
    """VerifyResult falso — o gate não pode depender de npm/pytest reais."""

    def __init__(self, ok, stack="python", steps=(), summary=""):
        self.ok, self.stack, self.steps, self.summary = ok, stack, list(steps), summary


class _Step:
    def __init__(self, name, cmd, ok, output="", skipped=False, reason=""):
        self.name, self.cmd, self.ok = name, cmd, ok
        self.output, self.skipped, self.reason = output, skipped, reason


def _git(tmp_path: Path) -> Path:
    repo = tmp_path / "proj"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True)
    (repo / "a.py").write_text("x = 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "base"], cwd=repo, check=True)
    return repo


# ─── quando o gate dispara ───────────────────────────────────────────────────


def test_repo_limpo_nao_roda_testes(tmp_path):
    """Turno de conversa não pode disparar a suíte."""
    repo = _git(tmp_path)
    chamou = []
    g = TestsGate(repo, verify_fn=lambda *a, **k: chamou.append(1) or _Res(True))

    r = g.check(request=None, result={})

    assert r.passed and chamou == [], "sem mudança, o gate nem executa"
    assert "nenhuma mudança" in r.reason


def test_repo_sujo_roda_testes(tmp_path):
    repo = _git(tmp_path)
    (repo / "b.py").write_text("y = 2\n", encoding="utf-8")
    chamou = []
    g = TestsGate(repo, verify_fn=lambda *a, **k: chamou.append(1) or _Res(True))

    assert g.check(request=None, result={}).passed
    assert chamou == [1], "arquivo novo = mudança = testa"


def test_artefatos_do_proprio_bauer_nao_contam_como_mudanca(tmp_path):
    """O Kernel grava memory/runtime/*.jsonl DENTRO do workspace.

    Sem ignorar isso, `git status` fica sujo já na primeira execução e o gate
    dispararia em TODO run — inclusive turno de conversa, que é justamente o que
    a regra "só quando mudou arquivo" existe para evitar. Medido num projeto
    recém-criado: `?? memory/`.
    """
    repo = _git(tmp_path)
    (repo / "memory" / "runtime").mkdir(parents=True)
    (repo / "memory" / "runtime" / "runs.jsonl").write_text("{}\n", encoding="utf-8")
    (repo / "__pycache__").mkdir()
    (repo / "__pycache__" / "a.pyc").write_bytes(b"x")

    chamou = []
    g = TestsGate(repo, verify_fn=lambda *a, **k: chamou.append(1) or _Res(True))
    r = g.check(request=None, result={})

    assert r.passed and chamou == [], "só ruído do Bauer não é mudança de código"

    # e uma mudança DE VERDADE junto do ruído continua contando
    (repo / "b.py").write_text("y = 2\n", encoding="utf-8")
    assert houve_mudanca(repo, {}) is True


def test_plano_python_usa_python_m_pytest():
    """`pytest` puro NÃO põe o cwd em sys.path; `python -m pytest` põe.

    Num projeto de layout plano (módulo na raiz, teste importando ele) o
    primeiro morre com ImportError na coleta. Medido contra projeto real: o gate
    reprovava por erro de coleta, não pelo código — falso negativo que atingiria
    todo projeto Python de layout plano.
    """
    import tempfile
    from bauer.app_verify import plan_verification

    raiz = Path(tempfile.mkdtemp())
    (raiz / "tests").mkdir()
    (raiz / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")

    _stack, plano = plan_verification(raiz)
    cmd = next(c for n, c in plano if n == "test")

    assert cmd[1:3] == ["-m", "pytest"], "roda via -m, nao pelo executavel pytest"
    assert Path(cmd[0]).name.lower().startswith("python"), cmd[0]


def test_fora_de_repo_usa_tools_mutantes(tmp_path):
    """Sem git, a evidência vem das tools que o executor registrou."""
    (tmp_path / "x.py").write_text("z = 1\n", encoding="utf-8")

    assert houve_mudanca(tmp_path, {"tools_usadas": ["read_file", "grep"]}) is False
    assert houve_mudanca(tmp_path, {"tools_usadas": ["read_file", "patch"]}) is True
    assert houve_mudanca(tmp_path, {}) is False


def test_lista_de_mutantes_vem_do_tool_dedup(tmp_path):
    """Duas listas divergindo seria um bug silencioso — o gate deixaria passar
    mudança feita por uma tool que o dedup já sabe ser mutante."""
    from bauer.core.kernel.gates.tests import _tools_mutantes
    from bauer.tool_dedup import MUTATING_TOOLS

    assert _tools_mutantes() is MUTATING_TOOLS


# ─── o que o gate roda ───────────────────────────────────────────────────────


def test_so_o_passo_test_sem_install_nem_serve(tmp_path):
    """Gate não instala dependência nem sobe o app: seriam minutos e efeito
    colateral fora do escopo da tarefa."""
    repo = _git(tmp_path)
    (repo / "b.py").write_text("y = 2\n", encoding="utf-8")
    visto = {}

    def _fake(ws, **kw):
        visto.update(kw)
        return _Res(True)

    TestsGate(repo, timeout_s=42, verify_fn=_fake).check(request=None, result={})

    assert visto["only"] == ["test"]
    assert visto["install"] is False
    assert visto["timeout"] == 42, "o teto de tempo é do gate, não do app_verify"


# ─── veredito ────────────────────────────────────────────────────────────────


def test_teste_falhando_reprova_com_a_cauda_do_output(tmp_path):
    """O motivo vira replan_feedback — precisa ser acionável, não 'falhou'."""
    repo = _git(tmp_path)
    (repo / "b.py").write_text("y = 2\n", encoding="utf-8")
    res = _Res(False, "python", [
        _Step("test", ["python", "-m", "pytest"], ok=False,
              output="E   assert 1 == 2\nFAILED tests/test_x.py::test_soma"),
    ])

    r = TestsGate(repo, verify_fn=lambda *a, **k: res).check(request=None, result={})

    assert not r.passed
    assert "FAILED tests/test_x.py::test_soma" in r.reason
    assert "python -m pytest" in r.reason, "o comando que falhou entra no motivo"


def test_stack_nao_detectada_nao_reprova(tmp_path):
    """Projeto de documentação não tem suíte — reprovar puniria trabalho válido."""
    repo = _git(tmp_path)
    (repo / "README.md").write_text("doc\n", encoding="utf-8")

    r = TestsGate(repo, verify_fn=lambda *a, **k: _Res(False, "unknown")).check(
        request=None, result={})

    assert r.passed and "stack não detectada" in r.reason


def test_gate_quebrado_nao_reprova_o_run(tmp_path):
    """Infra do gate falhando é problema do gate — mesma regra do Evaluator."""
    repo = _git(tmp_path)
    (repo / "b.py").write_text("y = 2\n", encoding="utf-8")

    def _explode(*a, **k):
        raise OSError("disco cheio")

    r = TestsGate(repo, verify_fn=_explode).check(request=None, result={})

    assert r.passed and "ignorado" in r.reason


# ─── integração com o Evaluator e o config ───────────────────────────────────


def test_evaluator_monta_o_gate_pelo_config(tmp_path):
    from bauer.config_loader import BauerConfig, KernelSection, ModelSection
    from bauer.core.kernel.kernel import evaluator_from_config

    cfg = BauerConfig(
        model=ModelSection(provider="ollama", name="x"),
        kernel=KernelSection(enabled=True, evaluator_enabled=True,
                             tests_gate=True, tests_gate_timeout_s=120),
    )
    ev = evaluator_from_config(cfg, workspace=str(tmp_path))
    nomes = [g.name for g in ev.gates]

    assert "tests" in nomes
    gate = next(g for g in ev.gates if g.name == "tests")
    assert gate.timeout_s == 120


def test_gate_desligado_por_default(tmp_path):
    """Custa tempo real — entra por escolha, não por herança."""
    from bauer.config_loader import BauerConfig, KernelSection, ModelSection
    from bauer.core.kernel.kernel import evaluator_from_config

    cfg = BauerConfig(model=ModelSection(provider="ollama", name="x"),
                      kernel=KernelSection(evaluator_enabled=True))
    ev = evaluator_from_config(cfg, workspace=str(tmp_path))

    assert "tests" not in [g.name for g in ev.gates]


def test_sem_workspace_nao_monta_o_gate():
    """Sem projeto não há onde rodar — e o gate não pode chutar um caminho."""
    from bauer.config_loader import BauerConfig, KernelSection, ModelSection
    from bauer.core.kernel.kernel import evaluator_from_config

    cfg = BauerConfig(model=ModelSection(provider="ollama", name="x"),
                      kernel=KernelSection(evaluator_enabled=True, tests_gate=True))
    ev = evaluator_from_config(cfg)

    assert "tests" not in [g.name for g in ev.gates]


def test_reprovacao_vira_replan_feedback(tmp_path):
    """O ciclo inteiro: gate reprova -> Kernel replaneja -> executor recebe o
    motivo -> corrige. É o que troca 'o agente disse que terminou' por 'os
    testes passaram'."""
    from bauer.core.events.bus import EventBus
    from bauer.core.kernel import BauerKernel, KernelRequest
    from bauer.core.kernel.evaluator import Evaluator
    from bauer.core.runtime.run_manager import RunManager
    from bauer.core.runtime.state_store import JsonlStateStore

    repo = _git(tmp_path)
    (repo / "b.py").write_text("y = 2\n", encoding="utf-8")
    tentativas = []

    def _verify(*a, **k):
        # 1ª passada: teste falha; 2ª: passa (o agente "corrigiu")
        if len(tentativas) <= 1:
            return _Res(False, "python", [
                _Step("test", ["pytest"], ok=False, output="FAILED test_soma")])
        return _Res(True, "python")

    store = JsonlStateStore(str(tmp_path / "rt"))
    bus = EventBus(store=store)
    kernel = BauerKernel(
        runs=RunManager(store=store, event_bus=bus), bus=bus,
        evaluator=Evaluator([TestsGate(repo, verify_fn=_verify)], max_replans=1),
    )

    def _executor(payload):
        tentativas.append(payload.get("replan_feedback"))
        return {"output": "feito"}

    out = kernel.execute(KernelRequest(agent_id="t", operation="runtime.execute",
                                       input={}), executor=_executor)

    assert out.status == "completed"
    assert tentativas[0] is None
    assert "FAILED test_soma" in (tentativas[1] or ""), \
        "a segunda passada recebe a cauda do output de teste para corrigir"


# ─── app_verify.only ─────────────────────────────────────────────────────────


def test_only_filtra_o_plano(tmp_path):
    from bauer.app_verify import verify_project

    proj = tmp_path / "p"
    (proj / "tests").mkdir(parents=True)   # sem isto o plano python não tem passo de teste
    (proj / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
    (proj / "tests" / "test_x.py").write_text("def test_x(): pass\n", encoding="utf-8")
    rodados = []

    def _runner(cmd, cwd, timeout):
        rodados.append(cmd[0])
        return 0, "ok"

    verify_project(proj, runner=_runner, which=lambda e: "/usr/bin/" + e,
                   install=False, only=["test"])

    assert rodados, "o passo de teste tem de rodar"
    assert all(c != "pip" for c in rodados), "install NÃO roda com only=['test']"


def test_only_vazio_reporta_em_vez_de_passar_calado(tmp_path):
    from bauer.app_verify import verify_project

    proj = tmp_path / "p"
    proj.mkdir()
    (proj / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")

    res = verify_project(proj, runner=lambda *a: (0, ""), which=lambda e: e,
                         only=["passo-que-nao-existe"])

    assert not res.ok and "passo" in res.summary.lower()


# ─── escolha do interpretador (o falso negativo do pipx) ─────────────────────


def test_prefere_venv_do_projeto(tmp_path):
    from bauer.app_verify import _candidatos_python

    venv = tmp_path / ".venv" / ("Scripts" if __import__("os").name == "nt" else "bin")
    venv.mkdir(parents=True)
    exe = venv / ("python.exe" if __import__("os").name == "nt" else "python")
    exe.write_text("", encoding="utf-8")

    assert _candidatos_python(tmp_path)[0] == str(exe), \
        "o venv do projeto é a única fonte que garante as dependências dele"


def test_path_vem_antes_de_sys_executable(tmp_path):
    """Sob pipx, sys.executable é o venv ISOLADO do Bauer — nunca tem pytest.

    Medido num run real: `.../venvs/bauer-agent/bin/python -m pytest` →
    "No module named pytest". O gate reprovava o trabalho do agente por
    ambiente, não por código.
    """
    import sys as _sys
    from bauer.app_verify import _candidatos_python

    cands = _candidatos_python(tmp_path)
    if _sys.executable in cands and len(cands) > 1:
        assert cands.index(_sys.executable) == len(cands) - 1, \
            "sys.executable é o ÚLTIMO recurso"


def test_escolhe_quem_tem_o_modulo(tmp_path):
    from bauer import app_verify

    orig = app_verify._tem_modulo
    try:
        app_verify._tem_modulo = lambda exe, mod: "bom" in exe
        app_verify._candidatos_python = lambda root: ["/x/ruim", "/x/bom", "/x/outro"]
        assert app_verify.python_exe(tmp_path, precisa="pytest") == "/x/bom"
        # sem `precisa`, não paga o custo de sondar
        assert app_verify.python_exe(tmp_path) == "/x/ruim"
    finally:
        app_verify._tem_modulo = orig
        import importlib
        importlib.reload(app_verify)


def test_ninguem_tem_o_modulo_devolve_o_primeiro(tmp_path):
    """Falhar é ok — mas com erro acionável no motivo, não com crash."""
    from bauer import app_verify
    import importlib

    orig_t, orig_c = app_verify._tem_modulo, app_verify._candidatos_python
    try:
        app_verify._tem_modulo = lambda exe, mod: False
        app_verify._candidatos_python = lambda root: ["/x/a", "/x/b"]
        assert app_verify.python_exe(tmp_path, precisa="pytest") == "/x/a"
    finally:
        app_verify._tem_modulo, app_verify._candidatos_python = orig_t, orig_c
        importlib.reload(app_verify)
