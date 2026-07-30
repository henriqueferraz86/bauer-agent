"""Os 3 sinais de estagnação do §13 que não são detectáveis por chamada.

`tool_guardrails` cobre os outros seis, e cobre bem — mas todos olham UMA
chamada: mesma tool com mesmos args, mesmo erro, leitura idempotente. Estes três
têm outra escala: nenhuma chamada individual parece errada; o que está errado é
a sequência.

    alterações revertidas            escreve, desfaz, reescreve
    tokens crescendo sem progresso   a janela enche e o disco não muda
    plano sem mudança entre replans  o replan devolve a mesma saída

O contrapeso de cada teste é o "NÃO dispara": um detector heurístico que erra
contra trabalho legítimo é desligado na primeira semana, e aí não detecta nada.
"""

from __future__ import annotations

import pytest

from bauer.progress_signals import (
    CRESCIMENTO_MINIMO_TOKENS,
    RODADAS_PARADAS_PARA_AVISAR,
    SinaisDeProgresso,
)


class _Ctx:
    """ContextManager fake — só o que o detector lê."""

    def __init__(self) -> None:
        self.used_tokens = 0


def _sinais(estados: list[str], ctx=None, **kw) -> SinaisDeProgresso:
    """Detector com a trajetória do workspace injetada, sem tocar em git."""
    seq = iter(estados)
    return SinaisDeProgresso(".", ctx=ctx, estado_fn=lambda ws: next(seq, estados[-1]),
                             **kw)


# ─── alterações revertidas ───────────────────────────────────────────────────


def test_voltar_ao_estado_anterior_avisa():
    """A → B → A. Cada edição é legítima; só a trajetória mostra o círculo."""
    s = _sinais(["A", "B", "A"])
    assert s.rodada(1) == []
    assert s.rodada(2) == []

    avisos = s.rodada(3)

    assert [a.code for a in avisos] == ["reverted_changes"]
    assert avisos[0].data == {"rodada_original": 1, "rodada_atual": 3}


def test_progresso_linear_nunca_avisa():
    """A → B → C → D é exatamente o que se quer que o agente faça."""
    s = _sinais(["A", "B", "C", "D"])
    assert [s.rodada(n) for n in (1, 2, 3, 4)] == [[], [], [], []]


def test_avisa_uma_vez_so_por_estado():
    """Oscilar 20 vezes não pode gerar 20 avisos idênticos — o nudge repetido
    ocupa a janela e o sinal vira ruído."""
    s = _sinais(["A", "B", "A", "B", "A"])
    total = sum(len(s.rodada(n)) for n in range(1, 6))

    assert total == 2, "um aviso por estado revisitado, não por revisita"


def test_rodada_sem_mudanca_nao_e_reversao():
    """Estado igual ao da rodada anterior é o agente PENSANDO, não desfazendo."""
    s = _sinais(["A", "A", "A"])
    assert [a for n in (1, 2, 3) for a in s.rodada(n) if a.code == "reverted_changes"] == []


# ─── tokens crescendo sem progresso ──────────────────────────────────────────


def test_janela_crescendo_sobre_disco_parado_avisa():
    ctx = _Ctx()
    s = _sinais(["A"] * 6, ctx=ctx)
    s.rodada(1)
    ctx.used_tokens = CRESCIMENTO_MINIMO_TOKENS + 500

    avisos = [a for n in range(2, 6) for a in s.rodada(n)]

    assert [a.code for a in avisos] == ["token_growth_no_progress"]
    assert avisos[0].data["rodadas_paradas"] >= RODADAS_PARADAS_PARA_AVISAR


def test_pensar_algumas_rodadas_e_normal():
    """Duas rodadas de leitura antes de editar é como se trabalha. Avisar aí
    seria treinar o agente a editar antes de entender."""
    ctx = _Ctx()
    s = _sinais(["A", "A", "A"], ctx=ctx)
    s.rodada(1)
    ctx.used_tokens = 99_999

    assert [a for n in (2, 3) for a in s.rodada(n)] == []


def test_contexto_estavel_nao_avisa_por_muitas_rodadas():
    """Parado E sem gastar contexto não é o modo de falha caro que este sinal
    procura — pode ser espera legítima."""
    ctx = _Ctx()
    s = _sinais(["A"] * 8, ctx=ctx)

    assert [a for n in range(1, 9) for a in s.rodada(n)] == []


def test_mudar_o_disco_zera_a_contagem():
    ctx = _Ctx()
    s = _sinais(["A", "A", "A", "B", "B", "B"], ctx=ctx)
    s.rodada(1)
    ctx.used_tokens = 50_000
    s.rodada(2); s.rodada(3)          # noqa: E702 — parado
    s.rodada(4)                        # mexeu: recomeça a contagem

    assert [a for n in (5, 6) for a in s.rodada(n)] == []


def test_avisa_uma_vez_por_periodo_parado():
    ctx = _Ctx()
    s = _sinais(["A"] * 9, ctx=ctx)
    s.rodada(1)
    ctx.used_tokens = 50_000

    assert sum(len(s.rodada(n)) for n in range(2, 10)) == 1


# ─── fronteira e efeitos ─────────────────────────────────────────────────────


def test_fora_de_repo_git_o_detector_se_desliga():
    """Sem fonte de verdade não se adivinha — mesma regra dos gates."""
    s = SinaisDeProgresso(".", estado_fn=lambda ws: None)
    assert [s.rodada(n) for n in range(1, 6)] == [[], [], [], [], []]


def test_aviso_vira_evento_auditavel(tmp_path):
    from bauer.core.events.bus import EventBus
    from bauer.core.runtime.state_store import JsonlStateStore

    bus = EventBus(store=JsonlStateStore(str(tmp_path / "rt")))
    s = _sinais(["A", "B", "A"], bus=bus, run_id="run-3")
    for n in (1, 2, 3):
        s.rodada(n)

    evt = next(e for e in bus.list_events() if e.event_type == "run.progress.warning")
    assert evt.run_id == "run-3"
    assert evt.data["code"] == "reverted_changes"


def test_ctx_quebrado_nao_derruba_o_laco():
    class _Explode:
        @property
        def used_tokens(self):
            raise RuntimeError("contador quebrado")

    s = _sinais(["A"] * 6, ctx=_Explode())
    for n in range(1, 6):
        s.rodada(n)   # não pode levantar


def test_estado_hasheia_o_patch_e_nao_a_lista_de_arquivos(tmp_path):
    """Com a lista de arquivos, mudar `a.py` de duas formas diferentes daria o
    mesmo estado (` M a.py` nas duas) e apareceria como reversão. É a diferença
    entre um sinal útil e um gerador de falso positivo."""
    import subprocess

    from bauer.progress_signals import _estado_do_workspace

    ws = tmp_path / "repo"
    ws.mkdir()
    (ws / "a.py").write_text("x = 1\n", encoding="utf-8")
    for cmd in (["init", "-q"], ["add", "-A"],
                ["-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "i"]):
        if subprocess.run(["git", *cmd], cwd=ws, capture_output=True).returncode:
            pytest.skip("git indisponível")

    (ws / "a.py").write_text("x = 2\n", encoding="utf-8")
    primeiro = _estado_do_workspace(ws)
    (ws / "a.py").write_text("x = 3\n", encoding="utf-8")
    segundo = _estado_do_workspace(ws)

    assert primeiro != segundo
    (ws / "a.py").write_text("x = 2\n", encoding="utf-8")
    assert _estado_do_workspace(ws) == primeiro, "voltar ao mesmo conteúdo é o mesmo estado"


def test_ruido_do_bauer_nao_conta_como_mudanca(tmp_path):
    """O Kernel grava `memory/runtime/*.jsonl` no workspace. Se isso mudasse o
    estado a cada rodada, o detector nunca veria "parado" nem "revertido"."""
    import subprocess

    from bauer.progress_signals import _estado_do_workspace

    ws = tmp_path / "repo"
    ws.mkdir()
    (ws / "a.py").write_text("x = 1\n", encoding="utf-8")
    for cmd in (["init", "-q"], ["add", "-A"],
                ["-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "i"]):
        if subprocess.run(["git", *cmd], cwd=ws, capture_output=True).returncode:
            pytest.skip("git indisponível")

    antes = _estado_do_workspace(ws)
    (ws / "memory").mkdir()
    (ws / "memory" / "runtime.jsonl").write_text('{"a":1}\n', encoding="utf-8")

    assert _estado_do_workspace(ws) == antes


# ─── plano sem mudança entre replans (no Kernel) ─────────────────────────────


def _kernel(tmp_path, evaluator):
    from bauer.core.events.bus import EventBus
    from bauer.core.kernel import BauerKernel
    from bauer.core.runtime.run_manager import RunManager
    from bauer.core.runtime.state_store import JsonlStateStore

    store = JsonlStateStore(str(tmp_path / "rt"))
    bus = EventBus(store=store)
    k = BauerKernel(runs=RunManager(store=store, event_bus=bus), bus=bus,
                    evaluator=evaluator)
    return k, bus


def _req():
    from bauer.core.kernel import KernelRequest

    return KernelRequest(agent_id="t", operation="runtime.execute", input={})


class _SempreReprova:
    name = "aceite"

    def check(self, *, request, result):
        from bauer.core.kernel.evaluator import GateResult

        return GateResult(self.name, False, "não atende")


def test_replan_que_devolve_a_mesma_saida_para(tmp_path):
    """O executor recebeu o feedback do gate e devolveu byte a byte a mesma
    coisa. Insistir gasta outro orçamento inteiro para chegar ao mesmo lugar."""
    from bauer.core.kernel.evaluator import Evaluator

    chamadas = {"n": 0}

    def _executor(payload):
        chamadas["n"] += 1
        return {"output": "sempre igual"}

    k, bus = _kernel(tmp_path, Evaluator([_SempreReprova()], max_replans=5))
    out = k.execute(_req(), executor=_executor)

    assert out.status == "failed"
    assert chamadas["n"] == 2, "parou no 1º replan estéril, não gastou os 5"
    assert "não foi incorporado" in (out.error or "")
    avisos = [e for e in bus.list_events()
              if e.event_type == "run.progress.warning"]
    assert [a.data["code"] for a in avisos] == ["plano_sem_mudanca"]


def test_replan_que_muda_algo_continua_tentando(tmp_path):
    """O sinal não pode cortar o replan que está de fato corrigindo o rumo —
    seria trocar um desperdício por uma tarefa abandonada no meio."""
    from bauer.core.kernel.evaluator import Evaluator, GateResult

    n = {"i": 0}

    class _PassaNaTerceira:
        name = "aceite"

        def check(self, *, request, result):
            return GateResult(self.name, n["i"] >= 3, "ainda não")

    def _executor(payload):
        n["i"] += 1
        return {"output": f"tentativa {n['i']}"}   # muda a cada passada

    k, _ = _kernel(tmp_path, Evaluator([_PassaNaTerceira()], max_replans=5))
    out = k.execute(_req(), executor=_executor)

    assert out.status == "completed"
    assert n["i"] == 3


def test_custo_e_duracao_nao_entram_na_assinatura():
    """Variam entre execuções idênticas: incluí-los faria toda passada parecer
    diferente, que é o oposto do que o sinal precisa detectar."""
    from bauer.core.kernel.kernel import _assinatura

    a = {"output": "x", "tool_calls_count": 2, "cost_estimate": 0.001, "duration": 1.2}
    b = {"output": "x", "tool_calls_count": 2, "cost_estimate": 0.009, "duration": 8.7}

    assert _assinatura(a) == _assinatura(b)
    assert _assinatura(a) != _assinatura({**a, "tool_calls_count": 3})
