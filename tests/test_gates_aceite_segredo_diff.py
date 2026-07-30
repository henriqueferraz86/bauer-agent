"""Os três gates que faltavam do §11: aceite, segredo no diff e "mudou algo?".

Cada um ataca um falso-sucesso que NENHUM dos outros pega:

- **diff**: "pronto, implementei" com o repo intacto. O de testes não roda (não
  houve mudança), o de escopo passa (não violou nada), e os dois gates de texto
  olham a resposta — que é justamente a parte que mentiu.
- **secrets**: o run funciona, os testes passam, e a chave da API foi para o
  código. É o único gate cujo falso-negativo não tem desfazer.
- **acceptance**: a suíte do projeto continua verde e a coisa PEDIDA não
  funciona. `tests` responde "nada quebrou"; só o contrato sabe o que era para
  funcionar.

O que cada teste protege é tanto o "reprova quando deve" quanto o "NÃO reprova
quando não deve" — um gate que dispara no turno de conversa é pior que gate
nenhum, porque será desligado na primeira semana.
"""

from __future__ import annotations

import subprocess

import pytest

from bauer.core.kernel.gates import AcceptanceGate, DiffGate, SecretsGate
from bauer.core.task import TaskContract


def _repo(tmp_path):
    ws = tmp_path / "repo"
    ws.mkdir()
    (ws / "a.py").write_text("x = 1\n", encoding="utf-8")
    for cmd in (["init", "-q"], ["add", "-A"],
                ["-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "i"]):
        r = subprocess.run(["git", *cmd], cwd=ws, capture_output=True)
        if r.returncode != 0:
            pytest.skip(f"git indisponível: {r.stderr!r}")
    return ws


def _contrato(**kw) -> TaskContract:
    return TaskContract.from_mapping({"objective": "fazer", **kw})


# ═══ DiffGate ═════════════════════════════════════════════════════════════════


def test_diff_reprova_tarefa_que_nao_mudou_nada(tmp_path):
    ws = _repo(tmp_path)

    r = DiffGate(ws, _contrato()).check(request=None, result={})

    assert not r.passed
    assert "NENHUM arquivo" in r.reason


def test_diff_passa_quando_o_workspace_mudou(tmp_path):
    ws = _repo(tmp_path)
    (ws / "novo.py").write_text("y = 2\n", encoding="utf-8")

    assert DiffGate(ws, _contrato()).check(request=None, result={}).passed


def test_diff_nao_dispara_em_turno_de_conversa(tmp_path):
    """Sem contrato não há tarefa de código. Reprovar aqui transformaria o Bauer
    em algo que só sabe editar arquivos — pergunta, revisão e explicação são
    trabalho legítimo que não altera nada."""
    ws = _repo(tmp_path)

    r = DiffGate(ws, None).check(request=None, result={})

    assert r.passed and "sem contrato" in r.reason


def test_diff_ignora_o_ruido_do_proprio_bauer(tmp_path):
    """O Kernel grava `memory/runtime/*.jsonl` dentro do workspace. Se isso
    contasse como mudança, o gate passaria em TODO run e não valeria nada."""
    ws = _repo(tmp_path)
    (ws / "memory").mkdir()
    (ws / "memory" / "runtime.jsonl").write_text("{}\n", encoding="utf-8")

    assert not DiffGate(ws, _contrato()).check(request=None, result={}).passed


# ═══ SecretsGate ══════════════════════════════════════════════════════════════


CHAVE = "sk-" + "a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6q7R8s9T0u1V2w3X4"


def test_segredo_adicionado_reprova(tmp_path):
    ws = _repo(tmp_path)
    (ws / "a.py").write_text(f'API_KEY = "{CHAVE}"\n', encoding="utf-8")

    r = SecretsGate(ws).check(request=None, result={})

    assert not r.passed
    assert "segredo" in r.reason.lower()


def test_o_motivo_nao_vaza_o_segredo(tmp_path):
    """Reprovar por vazamento e vazar no motivo seria o pior desfecho possível —
    o motivo vira `replan_feedback`, entra no contexto do modelo e vai para o
    log de auditoria."""
    ws = _repo(tmp_path)
    (ws / "a.py").write_text(f'API_KEY = "{CHAVE}"\n', encoding="utf-8")

    r = SecretsGate(ws).check(request=None, result={})

    assert CHAVE not in r.reason
    assert CHAVE[:8] in r.reason, "sem NENHUMA pista o agente não acha a linha"


def test_arquivo_novo_nao_versionado_tambem_e_escaneado(tmp_path):
    """`git diff HEAD` não vê arquivo untracked — e é exatamente onde um `.env`
    recém-criado apareceria. Deixá-lo de fora abriria o buraco no caso mais
    provável."""
    ws = _repo(tmp_path)
    (ws / ".env.local").write_text(f"OPENAI_API_KEY={CHAVE}\n", encoding="utf-8")

    assert not SecretsGate(ws).check(request=None, result={}).passed


def test_remover_segredo_antigo_nao_reprova(tmp_path):
    """Só linhas ADICIONADAS contam. Reprovar quem apagou a credencial puniria a
    correção e ensinaria o agente a não mexer em arquivo com segredo."""
    ws = tmp_path / "repo"
    ws.mkdir()
    (ws / "a.py").write_text(f'API_KEY = "{CHAVE}"\n', encoding="utf-8")
    for cmd in (["init", "-q"], ["add", "-A"],
                ["-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "i"]):
        r = subprocess.run(["git", *cmd], cwd=ws, capture_output=True)
        if r.returncode != 0:
            pytest.skip("git indisponível")
    (ws / "a.py").write_text('API_KEY = os.environ["OPENAI_API_KEY"]\n', encoding="utf-8")

    assert SecretsGate(ws).check(request=None, result={}).passed


def test_segredo_preexistente_intocado_nao_trava_o_projeto(tmp_path):
    """Dívida herdada não pode travar todo run, para sempre, por algo que o
    agente não criou — mesma regra do ratchet do gate de testes."""
    ws = tmp_path / "repo"
    ws.mkdir()
    (ws / "velho.py").write_text(f'K = "{CHAVE}"\n', encoding="utf-8")
    for cmd in (["init", "-q"], ["add", "-A"],
                ["-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "i"]):
        r = subprocess.run(["git", *cmd], cwd=ws, capture_output=True)
        if r.returncode != 0:
            pytest.skip("git indisponível")
    (ws / "outro.py").write_text("z = 3\n", encoding="utf-8")

    assert SecretsGate(ws).check(request=None, result={}).passed


def test_fora_de_repo_git_nao_afirma_nada(tmp_path):
    r = SecretsGate(tmp_path).check(request=None, result={})
    assert r.passed and "não é repo git" in r.reason


# ═══ AcceptanceGate ═══════════════════════════════════════════════════════════


def test_comando_de_aceite_que_falha_reprova(tmp_path):
    chamadas = []

    def _fake(cmd, ws, timeout):
        chamadas.append(cmd)
        return (1, "AssertionError: esperava 200, veio 500", 0.1)

    c = _contrato(validation={"commands": ["pytest tests/test_api.py"]})
    r = AcceptanceGate(c, tmp_path, run_fn=_fake).check(request=None, result={})

    assert not r.passed
    assert "veio 500" in r.reason
    assert chamadas == ["pytest tests/test_api.py"]


def test_para_no_primeiro_que_falha(tmp_path):
    chamadas = []

    def _fake(cmd, ws, timeout):
        chamadas.append(cmd)
        return (1, "erro", 0.1)

    c = _contrato(validation={"commands": ["a", "b", "c"]})
    AcceptanceGate(c, tmp_path, run_fn=_fake).check(request=None, result={})

    assert chamadas == ["a"], "rodar o resto depois da 1ª falha é tempo jogado fora"


def test_todos_passando_aprova(tmp_path):
    c = _contrato(validation={"commands": ["a", "b"]})
    r = AcceptanceGate(c, tmp_path,
                       run_fn=lambda *a: (0, "ok", 0.1)).check(request=None, result={})

    assert r.passed and "2 comando(s)" in r.reason


def test_orcamento_e_do_CONJUNTO_nao_de_cada_comando(tmp_path):
    """Cinco comandos com teto de 600s cada seriam 50 minutos de laço autônomo
    preso num gate."""
    def _lento(cmd, ws, timeout):
        return (0, "", 7.0)

    c = _contrato(validation={"commands": ["a", "b", "c"], "timeout_seconds": 10})
    r = AcceptanceGate(c, tmp_path, timeout_s=10, run_fn=_lento).check(
        request=None, result={})

    assert not r.passed
    assert "orçamento de 10s esgotado" in r.reason


def test_criterio_em_prosa_nao_e_julgado_mas_e_declarado(tmp_path):
    """"a API deve responder em menos de 200ms" é critério para humano ou juiz
    LLM. Este gate é o determinístico — e declarar a fronteira vale mais que
    fingir que a cobriu."""
    c = _contrato(acceptance_criteria=["a API responde em menos de 200ms",
                                       "o endpoint aceita paginação"])
    r = AcceptanceGate(c, tmp_path).check(request=None, result={})

    assert r.passed
    assert "2 critério(s)" in r.reason and "prosa" in r.reason


def test_prosa_junto_com_comando_aparece_no_motivo(tmp_path):
    c = _contrato(acceptance_criteria=["deve ficar bonito"],
                  validation={"commands": ["a"]})
    r = AcceptanceGate(c, tmp_path,
                       run_fn=lambda *a: (0, "", 0.1)).check(request=None, result={})

    assert r.passed and "1 critério(s) em prosa" in r.reason


def test_sem_contrato_nao_ha_o_que_provar(tmp_path):
    assert AcceptanceGate(None, tmp_path).check(request=None, result={}).passed


def test_gate_quebrado_nao_reprova_o_run(tmp_path):
    """Mesma regra do Evaluator: gate com defeito é problema do gate, não do
    resultado. Reprovar por infra faria o harness derrubar trabalho bom."""
    def _explode(*a):
        raise OSError("shell indisponível")

    c = _contrato(validation={"commands": ["a"]})
    r = AcceptanceGate(c, tmp_path, run_fn=_explode).check(request=None, result={})

    assert r.passed and "ignorado" in r.reason


def test_contrato_e_snapshot_e_nao_relido_do_disco(tmp_path):
    """O ataque: o agente edita `.bauer/task.yaml` e escreve o próprio critério
    de aprovação. Pior, `.bauer/` está na lista de ruído do TestsGate — a edição
    não dispararia nem a suíte. O gate usa o contrato lido ANTES do run."""
    (tmp_path / ".bauer").mkdir()
    alvo = tmp_path / ".bauer" / "task.yaml"
    alvo.write_text("objective: x\nvalidation:\n  commands: ['pytest']\n",
                    encoding="utf-8")
    snapshot = TaskContract.descobrir(tmp_path)

    gate = AcceptanceGate(snapshot, tmp_path, run_fn=lambda *a: (1, "falhou", 0.1))
    # o "agente" reescreve o contrato para se auto-aprovar
    alvo.write_text("objective: x\nvalidation:\n  commands: []\n", encoding="utf-8")

    assert not gate.check(request=None, result={}).passed, (
        "reescrever o contrato durante o run não pode virar aprovação")


# ═══ composição: os gates entram no Evaluator ═════════════════════════════════


def test_contrato_liga_aceite_e_diff_sem_flag_extra(tmp_path):
    """Escrever um contrato de tarefa JÁ é o opt-in — pedir uma segunda flag
    faria o perímetro declarado não valer por esquecimento."""
    from types import SimpleNamespace

    from bauer.core.kernel.kernel import evaluator_from_config

    (tmp_path / ".bauer").mkdir()
    (tmp_path / ".bauer" / "task.yaml").write_text(
        "objective: x\nscope:\n  allowed: ['src/']\n", encoding="utf-8")

    ev = evaluator_from_config(
        SimpleNamespace(kernel=SimpleNamespace(evaluator_enabled=True,
                                               tests_gate=False, max_replans=1)),
        workspace=str(tmp_path))

    assert {"acceptance", "diff", "scope", "secrets"} <= {g.name for g in ev.gates}


def test_secrets_vale_mesmo_sem_contrato(tmp_path):
    """Segredo no diff não depende de a tarefa ter contrato — e é o único gate
    cujo custo de falso-negativo (credencial commitada) não tem desfazer."""
    from types import SimpleNamespace

    from bauer.core.kernel.kernel import evaluator_from_config

    ev = evaluator_from_config(
        SimpleNamespace(kernel=SimpleNamespace(evaluator_enabled=True,
                                               tests_gate=False, max_replans=1)),
        workspace=str(tmp_path))
    nomes = {g.name for g in ev.gates}

    assert "secrets" in nomes
    assert {"acceptance", "diff"}.isdisjoint(nomes), "sem contrato, não entram"
