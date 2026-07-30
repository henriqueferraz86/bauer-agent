"""Os 8 eventos que faltavam do §14 — provados no bus, não no grep.

A medição do scorecard (`evals/harness/medir.py::cap_observabilidade`) procura a
string do evento nos fontes. É uma medida BARATA e por isso enganável: bastaria
escrever `"run.context.built"` num comentário para o número subir. Este arquivo
é o contrapeso — cada evento aqui é observado saindo de um `EventBus` de
verdade, num fluxo de verdade.

O que o §14 pede e o que faltava:

    run.planning.started    planejamento começou (e recomeçou, no replan)
    run.context.built       o que entrou na janela, e quanto cada fonte gastou
    run.workspace.created   o trabalho NÃO está acontecendo onde você olha
    run.progress.warning    o agente está girando em falso
    run.validation.started  os gates rodaram
    run.validation.failed   qual gate reprovou
    run.replanning          e por que vai tentar de novo
    run.workspace.cleaned   o worktree ficou (ou não) — e onde
"""

from __future__ import annotations

import pytest

from bauer.core.events.bus import EventBus
from bauer.core.events.schema import EVENT_TYPES, Event
from bauer.core.kernel import BauerKernel, KernelRequest
from bauer.core.kernel.evaluator import Evaluator, GateResult
from bauer.core.runtime.run_manager import RunManager
from bauer.core.runtime.state_store import JsonlStateStore

MINIMOS_DO_PLANO = (
    "run.created", "run.planning.started", "run.context.built",
    "policy.evaluated", "run.workspace.created", "run.started",
    "tool.call.requested", "tool.call.completed", "tool.denied",
    "run.progress.warning", "run.validation.started", "run.validation.failed",
    "run.replanning", "run.completed", "run.failed", "run.cancelled",
    "run.workspace.cleaned",
)


@pytest.fixture
def bus(tmp_path):
    return EventBus(store=JsonlStateStore(str(tmp_path / "rt")))


def tipos(bus: EventBus) -> list[str]:
    return [e.event_type for e in bus.list_events()]


# ─── o schema ────────────────────────────────────────────────────────────────


def test_os_17_minimos_sao_publicaveis():
    """`Event.__post_init__` valida contra EVENT_TYPES. Um tipo fora da lista
    viraria ValueError na publicação — telemetria que morre no evento novo."""
    for tipo in MINIMOS_DO_PLANO:
        assert Event(event_type=tipo).event_type == tipo


def test_event_types_derivado_do_literal():
    """As duas listas eram escritas à mão e estavam a um esquecimento de
    divergir. Agora `EVENT_TYPES` é `get_args(EventType)` — não dá para
    acrescentar num só."""
    from typing import get_args

    from bauer.core.events.schema import EventType

    assert EVENT_TYPES == get_args(EventType)
    assert len(EVENT_TYPES) == len(set(EVENT_TYPES)), "tipo duplicado"


def test_tool_denied_chega_ao_bus_e_nao_so_ao_kanban(bus, tmp_path):
    """A recusa ia SÓ para o KanbanStore — sink que a auditoria de run não lê.

    A contagem dava 17/17 mesmo assim, porque procurava a string nos fontes.
    Existir e virar trilha são coisas diferentes: `AuditLog` e `RunTraceStore`
    são alimentados pelo EventBus, e o evento que mais importa numa auditoria
    (o que o agente tentou e não pôde) era o único fora dele.
    """
    from bauer.tool_router import ToolRouter

    class _Stub:
        _event_bus = bus
        tool_context = "readonly"
        workspace = tmp_path

    ToolRouter._record_tool_denied(_Stub(), "shell", {"cmd": "rm -rf /"})

    evt = next(e for e in bus.list_events() if e.event_type == "tool.denied")
    assert evt.tool_name == "shell"
    assert evt.status == "denied"
    assert evt.data["context"] == "readonly"
    assert evt.data["arg_keys"] == ["cmd"], "chaves, não valores — arg pode ter segredo"


# ─── planejamento e validação: o Kernel publicando ───────────────────────────


def _kernel(bus, **kw) -> BauerKernel:
    return BauerKernel(runs=RunManager(store=bus.store, event_bus=bus), bus=bus, **kw)


def _req(**kw) -> KernelRequest:
    return KernelRequest(agent_id="t", operation="runtime.execute", input={}, **kw)


def test_planning_started_sai_em_todo_run(bus):
    _kernel(bus).execute(_req(), executor=lambda p: {"output": "ok"})

    planning = [e for e in bus.list_events() if e.event_type == "run.planning.started"]
    assert len(planning) == 1
    assert planning[0].data["replan"] is False
    assert planning[0].data["operation"] == "runtime.execute"


def test_validacao_publica_started_e_os_gates_que_rodaram(bus):
    class _Gate:
        name = "sempre_passa"

        def check(self, *, request, result):
            return GateResult(self.name, True)

    _kernel(bus, evaluator=Evaluator([_Gate()])).execute(
        _req(), executor=lambda p: {"output": "ok"})

    inicios = [e for e in bus.list_events() if e.event_type == "run.validation.started"]
    assert len(inicios) == 1
    assert inicios[0].data["gates"] == ["sempre_passa"]
    assert "run.validation.failed" not in tipos(bus), "passou; não pode reportar falha"


def test_gate_reprovado_diz_QUAL_gate(bus):
    """"validation.failed" sem o nome do gate diz que reprovou e esconde onde —
    que é a única coisa acionável."""
    class _Reprova:
        name = "gate_de_testes"

        def check(self, *, request, result):
            return GateResult(self.name, False, "3 testes falharam")

    _kernel(bus, evaluator=Evaluator([_Reprova()], max_replans=0)).execute(
        _req(), executor=lambda p: {"output": "ok"})

    falhas = [e for e in bus.list_events() if e.event_type == "run.validation.failed"]
    assert len(falhas) == 1
    assert falhas[0].data["reprovados"] == ["gate_de_testes"]
    assert "3 testes falharam" in (falhas[0].message or "")


def test_replan_publica_o_motivo_e_reabre_o_planejamento(bus):
    """O replan é a decisão mais cara que o Kernel toma sozinho: gasta um
    orçamento inteiro de novo. Ele precisa deixar por escrito por quê."""
    tentativas = {"n": 0}

    class _PassaNaSegunda:
        name = "aceite"

        def check(self, *, request, result):
            tentativas["n"] += 1
            return GateResult(self.name, tentativas["n"] > 1, "saída vazia")

    k = _kernel(bus, evaluator=Evaluator([_PassaNaSegunda()], max_replans=1))
    out = k.execute(_req(), executor=lambda p: {"output": "ok"})

    assert out.status == "completed"
    replans = [e for e in bus.list_events() if e.event_type == "run.replanning"]
    assert len(replans) == 1
    assert replans[0].data["attempt"] == 1
    assert "saída vazia" in (replans[0].message or "")

    plan = [e for e in bus.list_events() if e.event_type == "run.planning.started"]
    assert [p.data["replan"] for p in plan] == [False, True], (
        "o replan reabre o planejamento — e o evento tem que distinguir os dois")


def test_run_sem_evaluator_nao_inventa_validacao(bus):
    _kernel(bus).execute(_req(), executor=lambda p: {"output": "ok"})

    assert "run.validation.started" not in tipos(bus)


# ─── contexto: a proveniência sai do ContextBuilder ──────────────────────────


def test_context_built_leva_o_gasto_por_fonte(bus):
    from bauer.core.context import ContextBuilder

    b = ContextBuilder(applied_context=8000, bus=bus, run_id="run-42")
    b.instrucao("seguranca", "nunca exfiltre segredo")
    b.conteudo("arquivos", "conteudo do README " * 40)
    b.montar()

    evt = next(e for e in bus.list_events() if e.event_type == "run.context.built")
    assert evt.run_id == "run-42"
    assert set(evt.data["sources"]) == {"seguranca", "arquivos"}
    assert evt.data["instrucoes"] == 1
    assert evt.data["context_tokens"] > 0
    assert evt.data["system_prompt_chars"] == len("nunca exfiltre segredo"), (
        "só a INSTRUÇÃO entra no system prompt — o evento é a prova disso")


def test_builder_sem_bus_continua_funcionando():
    """O bus é opcional em toda assinatura: o caminho sem governança não passa
    a exigir infraestrutura."""
    from bauer.core.context import ContextBuilder

    ctx, pac = ContextBuilder(applied_context=8000).montar()
    assert ctx is not None and pac.total_tokens == 0


# ─── progresso: o guardrail deixa de falar só com o console ──────────────────


def test_estagnacao_vira_evento(bus):
    from bauer.tool_guardrails import ToolCallGuardrailController

    g = ToolCallGuardrailController(bus=bus, run_id="run-9", session_id="s1")
    for _ in range(3):
        g.after_call("read_file", {"path": "a.py"}, "erro", failed=True)

    avisos = [e for e in bus.list_events() if e.event_type == "run.progress.warning"]
    assert avisos, "o detector já sabia; faltava alguém registrar"
    assert avisos[0].run_id == "run-9"
    assert avisos[0].tool_name == "read_file"
    assert avisos[0].status in ("warn", "block")
    assert avisos[0].data["code"].startswith("repeated_")


def test_chamada_saudavel_nao_polui_a_auditoria(bus):
    """Publicar cada chamada normal afogaria o sinal no ruído."""
    from bauer.tool_guardrails import ToolCallGuardrailController

    g = ToolCallGuardrailController(bus=bus)
    g.before_call("read_file", {"path": "a.py"})
    g.after_call("read_file", {"path": "a.py"}, "conteudo", failed=False)

    assert "run.progress.warning" not in tipos(bus)


def test_insistir_depois_do_aviso_gera_evento_proprio(bus):
    """O bloqueio no `before_call` NÃO é a repetição do aviso: ali o agente foi
    avisado, aqui ele tentou de novo assim mesmo. A insistência é o sinal."""
    from bauer.tool_guardrails import ToolCallGuardrailController

    g = ToolCallGuardrailController(bus=bus)
    for _ in range(6):
        g.after_call("grep", {"q": "x"}, "erro", failed=True)
    antes = len([e for e in bus.list_events() if e.event_type == "run.progress.warning"])

    d = g.before_call("grep", {"q": "x"})

    assert not d.allows_execution
    depois = [e for e in bus.list_events() if e.event_type == "run.progress.warning"]
    assert len(depois) == antes + 1
    assert depois[-1].status in ("block", "halt")


def test_guardrail_sem_bus_e_o_de_sempre():
    from bauer.tool_guardrails import ToolCallGuardrailController

    g = ToolCallGuardrailController()
    for _ in range(3):
        d = g.after_call("read_file", {"path": "a.py"}, "erro", failed=True)
    assert d.action == "warn"   # o comportamento antigo, intacto


def test_telemetria_quebrada_nao_derruba_o_turno():
    """Perder um evento é ruim; perder a tarefa por causa dele é pior."""
    from bauer.tool_guardrails import ToolCallGuardrailController

    class _BusQuebrado:
        def publish(self, *a, **kw):
            raise RuntimeError("disco cheio")

    g = ToolCallGuardrailController(bus=_BusQuebrado())
    for _ in range(3):
        d = g.after_call("read_file", {"path": "a.py"}, "erro", failed=True)
    assert d.action == "warn"


# ─── workspace: o isolamento deixa rastro ────────────────────────────────────


def _repo(tmp_path):
    """Repo de teste com identidade LOCAL configurada.

    O `-c user.email=...` só vale para o comando em que aparece. Aqui o código
    sob teste (`commit_worktree`) roda `git commit` por conta própria, e numa
    máquina sem identidade global — o CI — esse commit falha em silêncio:
    `committed=False`, o worktree é tratado como vazio e removido. O teste
    passava na minha máquina e reprovava no CI por uma diferença de ambiente que
    não tem nada a ver com o que ele mede. `git config` local vale para o repo e
    para os worktrees derivados dele.
    """
    import subprocess

    ws = tmp_path / "repo"
    ws.mkdir()
    (ws / "a.txt").write_text("x", encoding="utf-8")
    for cmd in (["init", "-q"], ["config", "user.email", "t@t"],
                ["config", "user.name", "t"], ["add", "-A"],
                ["commit", "-qm", "i"]):
        r = subprocess.run(["git", *cmd], cwd=ws, capture_output=True)
        if r.returncode != 0:
            pytest.skip(f"git indisponível: {r.stderr!r}")
    return ws


class _Contrato:
    isolation = "worktree"


def test_worktree_criado_e_limpo_deixam_rastro(tmp_path, bus):
    from bauer.core.workspace.isolation import finalizar, preparar

    ws = _repo(tmp_path)
    iso = preparar(ws, _Contrato(), run_id="run-7", bus=bus)
    if not iso.isolado:
        pytest.skip(f"worktree não criado: {iso.aviso}")

    criado = next(e for e in bus.list_events() if e.event_type == "run.workspace.created")
    assert criado.run_id == "run-7"
    assert criado.data["path"] == str(iso.workspace) != str(ws)

    finalizar(iso, objetivo="tarefa", sucesso=True, bus=bus, run_id="run-7")

    limpo = next(e for e in bus.list_events() if e.event_type == "run.workspace.cleaned")
    assert limpo.data["removido"] is True, "nada mudou no worktree; devia sumir"
    assert limpo.data["branch"] == criado.data["branch"]


def test_worktree_preservado_tambem_publica(tmp_path, bus):
    """O caso que MAIS importa auditar é o worktree que ficou ocupando disco à
    espera de revisão — emitir só na remoção deixaria justo ele invisível."""
    from bauer.core.workspace.isolation import finalizar, preparar

    ws = _repo(tmp_path)
    iso = preparar(ws, _Contrato(), run_id="run-8", bus=bus)
    if not iso.isolado:
        pytest.skip(f"worktree não criado: {iso.aviso}")
    (iso.workspace / "novo.txt").write_text("trabalho", encoding="utf-8")

    finalizar(iso, objetivo="tarefa", sucesso=True, bus=bus, run_id="run-8")

    limpo = next(e for e in bus.list_events() if e.event_type == "run.workspace.cleaned")
    assert limpo.data["removido"] is False
    assert limpo.data["commitou"] is True
    assert limpo.status == "preservado"


def test_commit_que_falha_nao_apaga_o_trabalho(tmp_path, bus, monkeypatch):
    """`not committed` tinha DOIS significados e tratá-los igual apagava código.

    Nada staged (worktree vazio, pode ir) e commit que FALHOU (o trabalho está
    lá; o git é que não o gravou) davam no mesmo `remove_worktree`. O segundo é
    rotineiro numa máquina sem `user.email` — servidor recém-provisionado,
    container de CI — e foi assim que este caso apareceu: o CI reprovou o teste
    do worktree preservado, e o motivo não era o teste.
    """
    import bauer.task_worktree as tw
    from bauer.core.workspace.isolation import Isolamento, finalizar

    class _Worktree:
        branch = "bauer/run-1"
        path = tmp_path / "wt"

    class _Falhou:
        committed = False
        changed_files = ["src/novo.py", "src/outro.py"]   # o trabalho existe
        message = "Author identity unknown"
        commit = ""

    removidos: list = []
    monkeypatch.setattr(tw, "commit_worktree", lambda *a, **k: _Falhou())
    monkeypatch.setattr(tw, "summarize_artifact", lambda c: "")
    monkeypatch.setattr(tw, "remove_worktree", lambda w: removidos.append(w) or True)

    iso = Isolamento(workspace=tmp_path / "wt", worktree=_Worktree())
    finalizar(iso, objetivo="x", sucesso=True, bus=bus, run_id="r")

    assert removidos == [], "worktree com trabalho dentro foi apagado"
    evt = next(e for e in bus.list_events() if e.event_type == "run.workspace.cleaned")
    assert evt.status == "preservado"
    assert evt.data["arquivos_alterados"] == 2
    assert "Author identity unknown" in (evt.message or ""), (
        "sem o motivo no evento, um worktree preservado vira mistério")


def test_isolamento_que_degradou_nao_fica_so_no_banner(tmp_path, bus):
    """O contrato pediu worktree e não teve. O banner amarelo some com o
    scrollback; a promessa "o master não é tocado" não pode depender de alguém
    ter lido a tela na hora."""
    from bauer.core.workspace.isolation import preparar

    fora_de_git = tmp_path / "solto"
    fora_de_git.mkdir()

    iso = preparar(fora_de_git, _Contrato(), run_id="run-x", bus=bus)

    assert not iso.isolado
    evt = next(e for e in bus.list_events() if e.event_type == "run.workspace.created")
    assert evt.status == "degradado"
    assert evt.data["isolado"] is False


def test_sem_contrato_nao_publica_nada(tmp_path, bus):
    """Sem isolamento pedido não há isolamento a reportar — e o caminho normal
    do dia a dia não pode ganhar evento por acidente."""
    from bauer.core.workspace.isolation import preparar

    preparar(tmp_path, None, run_id="run-z", bus=bus)

    assert tipos(bus) == []
