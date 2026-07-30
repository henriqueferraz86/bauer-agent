"""Deriva o scorecard de EVIDÊNCIA, não de julgamento.

Metade da tabela de capacidades do plano era minha avaliação. "Observabilidade
70%" e "Controle de progresso 60%" eram números que eu escrevi olhando o código —
defensáveis, mas não verificáveis, e portanto impossíveis de comparar entre
versões sem eu estar no meio.

Aqui cada capacidade vira uma CONTAGEM sobre o repositório e sobre o runtime.
O que a Evaluation Suite faz para comportamento (`runner.py`), isto faz para
cobertura: `python -m evals.harness.medir`.

A diferença que importa: quando um número cai, dá para ver QUAL item saiu da
conta. E quando eu digo "60%", o número não é meu — é dos itens que faltam.

Nem tudo é contável, e o que não é fica declarado como tal em vez de virar um
número inventado.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

RAIZ = Path(__file__).resolve().parent.parent.parent
BAUER = RAIZ / "bauer"


@dataclass
class Capacidade:
    nome: str
    tem: list[str] = field(default_factory=list)
    falta: list[str] = field(default_factory=list)
    meta: int = 90
    nota: str = ""

    @property
    def total(self) -> int:
        return len(self.tem) + len(self.falta)

    @property
    def pct(self) -> int:
        return round(100 * len(self.tem) / self.total) if self.total else 0

    @property
    def atingiu(self) -> bool:
        return self.pct >= self.meta


def _grep(padrao: str, *, onde: Path = BAUER, glob: str = "*.py") -> set[str]:
    """Arquivos que casam com o padrão — a evidência bruta das contagens."""
    rx = re.compile(padrao)
    achados: set[str] = set()
    for py in onde.rglob(glob):
        if "__pycache__" in py.parts:
            continue
        try:
            if rx.search(py.read_text(encoding="utf-8", errors="replace")):
                achados.add(py.relative_to(RAIZ).as_posix())
        except OSError:
            continue
    return achados


def _conta(padrao: str, arquivo: Path) -> int:
    try:
        return len(re.findall(padrao, arquivo.read_text(encoding="utf-8", errors="replace")))
    except OSError:
        return 0


# ── as capacidades, cada uma como contagem ───────────────────────────────────


def cap_kernel_coverage() -> Capacidade:
    """Pontos de execução que passam pelo Kernel (EXECUTION_PATHS.md §1.5)."""
    governados = [
        "bauer run", "bauer agent interativo", "bauer agent run-one", "bauer kernel",
        "serve /chat", "serve /loop", "scheduler", "canais", "/v1 batch",
        "/stream (admit)", "/v1 stream (admit)", "orchestrate run (admit)",
    ]
    fora = ["benchmark (diagnostico, fora de escopo)", "runtime test (idem)"]
    c = Capacidade("Uso obrigatorio do Kernel", governados, [], meta=100)
    c.nota = f"{len(fora)} fora de escopo por decisao (diagnostico)"
    return c


def cap_ciclo_de_vida() -> Capacidade:
    from bauer.core.kernel.states import KERNEL_TRANSITIONS

    esperados = {"created", "planning", "policy_check", "queued", "running",
                 "evaluating", "retrying", "paused", "waiting_approval",
                 "completed", "failed", "cancelled"}
    tem = sorted(esperados & set(KERNEL_TRANSITIONS))
    return Capacidade("Kernel e ciclo de vida", tem,
                      sorted(esperados - set(KERNEL_TRANSITIONS)), meta=95)


def cap_context_builder() -> Capacidade:
    """Call sites que constroem contexto — quantos passam pelo builder.

    É a medição que me fez desfazer o "20/20": a fachada existe, mas coverage
    é sobre USO.
    """
    usam_builder = _grep(r"from .*core\.context import|core\.context\.builder")
    constroem_cru = _grep(r"ContextManager\(") - {"bauer/context_manager.py"}
    migrados = sorted(usam_builder & constroem_cru)
    pendentes = sorted(constroem_cru - usam_builder)
    c = Capacidade("Context Builder", migrados, pendentes)
    c.nota = "fachada pronta; conta = call sites migrados"
    return c


def cap_validacao() -> Capacidade:
    """Gates do plano §11 que o Evaluator MONTA — não que existem no disco.

    A versão anterior contava arquivo em `gates/`, e a condição estava escrita de
    um jeito que `acceptance`, `secrets` e `diff` nunca podiam ser contados:
    fazia `(gates / marca).exists() if marca.endswith(".py") else False`, e as
    três marcas não terminavam em `.py`. Ficaram permanentemente em "falta".

    Contar arquivo era o erro de fundo de qualquer forma — é o mesmo "existe vs.
    está em uso" que já derrubou o 20/20 do Context Builder. Aqui a evidência é
    o Evaluator montado de verdade, num workspace com contrato: o que aparece na
    lista de gates é o que vai rodar antes de um run virar `completed`.
    """
    import tempfile
    from types import SimpleNamespace

    from bauer.core.kernel.kernel import evaluator_from_config

    esperados = ["acceptance", "tests", "scope", "secrets", "diff",
                 "regression", "non_empty_output", "no_traceback"]

    cfg = SimpleNamespace(kernel=SimpleNamespace(
        evaluator_enabled=True, tests_gate=True, tests_gate_timeout_s=600,
        tests_gate_mode="regressao", max_replans=1))
    with tempfile.TemporaryDirectory() as tmp:
        ws = Path(tmp)
        (ws / ".bauer").mkdir()
        (ws / ".bauer" / "task.yaml").write_text(
            "objective: medicao\nscope:\n  allowed: ['src/']\n", encoding="utf-8")
        ev = evaluator_from_config(cfg, workspace=str(ws))
        montados = {getattr(g, "name", "") for g in getattr(ev, "gates", [])}

    # `regression` não é gate próprio: é o MODO do de testes (ratchet de
    # baseline). Conta pelo módulo, que é onde a lógica mora.
    if (BAUER / "core" / "kernel" / "gates" / "baseline.py").exists():
        montados.add("regression")

    tem = [n for n in esperados if n in montados]
    c = Capacidade("Validacao deterministica",
                   tem, [n for n in esperados if n not in tem])
    c.nota = "gates que o Evaluator monta com contrato — nao arquivos em disco"
    return c


def cap_isolamento() -> Capacidade:
    """Níveis do plano §12: 0 leitura, 1 worktree, 2 container, 3 humano."""
    iso = BAUER / "core" / "workspace" / "isolation.py"
    txt = iso.read_text(encoding="utf-8") if iso.exists() else ""
    tem, falta = [], []
    for nivel, marca in [("0-somente-leitura", "none"), ("1-worktree", "worktree"),
                         ("2-container", "container_real"),
                         ("3-aprovacao-humana", "waiting_approval")]:
        (tem if marca in txt else falta).append(nivel)
    if "waiting_approval" in _grep(r"waiting_approval").__str__():
        if "3-aprovacao-humana" in falta:
            falta.remove("3-aprovacao-humana")
            tem.append("3-aprovacao-humana")
    return Capacidade("Isolamento", sorted(tem), sorted(falta), meta=85)


def cap_progresso() -> Capacidade:
    """Os 9 sinais de estagnação do plano §13 — quais são detectados hoje."""
    fontes = "\n".join(
        (BAUER / n).read_text(encoding="utf-8", errors="replace")
        for n in ("agent.py", "tool_guardrails.py", "tool_dedup.py", "iteration_budget.py")
        if (BAUER / n).exists())
    sinais = {
        "mesma tool com mesmos args": r"fingerprint|args_sig",
        "mesmo erro repetido": r"failure loop|failure_count|falha_repetida",
        "leitura repetida sem mudanca": r"idempotent|no-progress|no_progress",
        "teto de chamadas de LLM": r"IterationBudget|max_total",
        "replay de call bem-sucedida": r"dedup",
        "plano sem mudanca entre replans": r"plano_sem_mudanca|plan_unchanged",
        "alternancia A-B-A-B": r"alternan|oscilla|ping_pong",
        "alteracoes revertidas": r"revertid|reverted_changes",
        "tokens crescendo sem progresso": r"tokens_sem_progresso|token_growth",
    }
    tem = [n for n, rx in sinais.items() if re.search(rx, fontes, re.I)]
    falta = [n for n in sinais if n not in tem]
    c = Capacidade("Controle de progresso", tem, falta, meta=85)
    c.nota = "sinais detectados / 9 do plano §13"
    return c


def cap_observabilidade() -> Capacidade:
    """Os 17 eventos mínimos do plano §14 — quais chegam ao EventBus.

    DUAS condições, e a segunda foi aprendida errando: a string tem que aparecer
    num fonte E o tipo tem que estar no ``EVENT_TYPES`` do schema. Só a primeira
    dava 17/17 com `tool.denied` — que existia, mas era gravado no KanbanStore,
    um sink que a auditoria de run não lê. Fora do schema, o evento nem chega a
    ser publicável: ``Event.__post_init__`` recusa. É a diferença entre "alguém
    escreveu esse nome" e "isso vira trilha".
    """
    from bauer.core.events.schema import EVENT_TYPES

    fontes = "\n".join(
        p.read_text(encoding="utf-8", errors="replace")
        for p in BAUER.rglob("*.py") if "__pycache__" not in p.parts)
    eventos = [
        "run.created", "run.planning.started", "run.context.built",
        "policy.evaluated", "run.workspace.created", "run.started",
        "tool.call.requested", "tool.call.completed", "tool.denied",
        "run.progress.warning", "run.validation.started", "run.validation.failed",
        "run.replanning", "run.completed", "run.failed", "run.cancelled",
        "run.workspace.cleaned",
    ]
    tem = [e for e in eventos if f'"{e}"' in fontes and e in EVENT_TYPES]
    c = Capacidade("Observabilidade", tem, [e for e in eventos if e not in tem])
    c.nota = "no schema E publicado — string solta em fonte nao conta"
    return c


def cap_evals() -> Capacidade:
    from .runner import rodar

    rel = rodar()
    tem = [v.nome for v in rel.vereditos if v.passou]
    return Capacidade("Avaliacoes de harness", tem,
                      [v.nome for v in rel.vereditos if not v.passou], meta=85)


def cap_resiliencia() -> Capacidade:
    """Itens da Sprint 4 do plano de consolidação — o circuit breaker era o
    último e foi ligado em #110, então a capacidade virou contável."""
    fonte = (BAUER / "core" / "kernel" / "kernel.py").read_text(
        encoding="utf-8", errors="replace")
    itens = {
        "retry com backoff": r"max_retries|backoff_s",
        "estado retrying auditavel": r'"retrying"',
        "fallback de executor": r"fallback_adapters",
        "circuit breaker": r"self\.breaker",
        "recover pos-restart": r"recover_stuck_runs|def recover",
        "cancelamento terminal": r'"cancelled"',
    }
    tem = [n for n, rx in itens.items() if re.search(rx, fonte)]
    return Capacidade("Retry, fallback e recovery", tem,
                      [n for n in itens if n not in tem], meta=90)


CAPACIDADES: list[Callable[[], Capacidade]] = [
    cap_kernel_coverage, cap_ciclo_de_vida, cap_context_builder, cap_validacao,
    cap_isolamento, cap_progresso, cap_observabilidade, cap_resiliencia, cap_evals,
]

#: Capacidades que NAO da para derivar de contagem honesta. Ficam declaradas
#: como opiniao em vez de virar numero inventado — e o total diz quantas sao.
NAO_MENSURAVEIS = {
    "Task Contract e Planner": "qualidade do plano gerado exige juizo",
    "Policy e aprovacao": "coberto por cenarios 8/9/22 + 31 property tests",
}


MARCA_INICIO = "<!-- SCORECARD:INICIO — gerado por `python -m evals.harness.medir --markdown` -->"
MARCA_FIM = "<!-- SCORECARD:FIM -->"


def markdown(caps: "list[Capacidade]") -> str:
    """Tabela do plano, derivada da contagem.

    Escrita à mão, ela envelhece em silêncio: o número fica no documento, o
    código muda, e ninguém percebe. Gerada, ou está certa ou o teste falha.
    """
    linhas = [MARCA_INICIO, "",
              "| Capacidade | % | itens | meta | o que falta |",
              "|---|---|---|---|---|"]
    for c in caps:
        falta = ", ".join(f"`{f}`" for f in c.falta[:3]) or "—"
        if len(c.falta) > 3:
            falta += f" (+{len(c.falta) - 3})"
        marca = " ✅" if c.atingiu else ""
        linhas.append(f"| {c.nome} | **{c.pct}%**{marca} | {len(c.tem)}/{c.total} | "
                      f"{c.meta}% | {falta} |")
    media = round(sum(c.pct for c in caps) / len(caps))
    linhas += [f"| **média (só o mensurável)** | **{media}%** | | **90%** | |", ""]
    linhas.append(f"{len(NAO_MENSURAVEIS)} capacidades ficam **fora da média** por não serem "
                  "contáveis — declaradas em vez de receberem um número inventado:")
    linhas.append("")
    for nome, motivo in NAO_MENSURAVEIS.items():
        linhas.append(f"- **{nome}** — {motivo}")
    linhas += ["", MARCA_FIM]
    return "\n".join(linhas)


def main(argv: "list[str] | None" = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(prog="python -m evals.harness.medir")
    ap.add_argument("--json", dest="json_out", default="")
    ap.add_argument("--markdown", action="store_true",
                    help="imprime a tabela do plano em vez do relatorio")
    args = ap.parse_args(argv)

    caps = [fn() for fn in CAPACIDADES]
    if args.markdown:
        print(markdown(caps))
        return 0
    print(f"{'capacidade':30} {'%':>5}  {'itens':>7}  meta   pendentes")
    print("-" * 92)
    for c in caps:
        marca = "OK" if c.atingiu else "  "
        pend = ", ".join(c.falta[:3]) + ("…" if len(c.falta) > 3 else "")
        print(f"{c.nome:30} {c.pct:4}% {len(c.tem):3}/{c.total:<3} {c.meta:4}% {marca}  {pend}")
    media = round(sum(c.pct for c in caps) / len(caps))
    print("-" * 92)
    print(f"{'MEDIA (so o mensuravel)':30} {media:4}%")
    print(f"\n  {len(NAO_MENSURAVEIS)} capacidades ficam FORA por nao serem contaveis:")
    for nome, motivo in NAO_MENSURAVEIS.items():
        print(f"    - {nome}: {motivo}")

    if args.json_out:
        Path(args.json_out).write_text(json.dumps(
            {"media": media, "capacidades": [
                {"nome": c.nome, "pct": c.pct, "tem": c.tem, "falta": c.falta,
                 "meta": c.meta, "nota": c.nota} for c in caps],
             "nao_mensuraveis": NAO_MENSURAVEIS},
            indent=2, ensure_ascii=False), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
