"""Benchmark do agent — 10 tasks fixas e verificáveis, score histórico.

Fase 3.5: a métrica que importa não é "quantos testes passam" e sim "o agent
completa tarefas reais de ponta a ponta?". Este módulo roda um conjunto FIXO
de tarefas (mesmas tasks, sempre) contra o modelo/provider configurado e
grava o score em ~/.bauer/benchmarks/ — a série histórica mostra se mudanças
de código/modelo melhoraram ou regrediram o agent.

Uso::

    bauer benchmark run            # roda as 10 tasks com o modelo do config
    bauer benchmark history        # série histórica de scores

Agendamento semanal: CronTrigger no daemon ou agendador do SO chamando
`bauer benchmark run`.

Design: cada task tem um `check(response, workspace) -> bool` determinístico.
Sem julgamento por LLM — o resultado é binário e reproduzível.
"""

from __future__ import annotations

import json
import re
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

BENCH_DIR = Path.home() / ".bauer" / "benchmarks"


@dataclass
class BenchTask:
    id: str
    prompt: str
    check: Callable[[str, Path], bool]
    setup: Callable[[Path], None] | None = None
    description: str = ""


@dataclass
class TaskResult:
    task_id: str
    passed: bool
    duration_s: float
    tool_calls: int
    error: str = ""


@dataclass
class BenchmarkReport:
    model: str
    provider: str
    results: list[TaskResult] = field(default_factory=list)
    started_at: str = ""

    @property
    def score(self) -> float:
        if not self.results:
            return 0.0
        return sum(1 for r in self.results if r.passed) / len(self.results)

    @property
    def passed(self) -> int:
        return sum(1 for r in self.results if r.passed)

    def to_dict(self) -> dict:
        return {
            "model": self.model,
            "provider": self.provider,
            "started_at": self.started_at,
            "score": round(self.score, 3),
            "passed": self.passed,
            "total": len(self.results),
            "results": [
                {
                    "task": r.task_id,
                    "passed": r.passed,
                    "duration_s": round(r.duration_s, 2),
                    "tool_calls": r.tool_calls,
                    "error": r.error,
                }
                for r in self.results
            ],
        }


# ─── As 10 tasks fixas ─────────────────────────────────────────────────────────
# NUNCA mude o id ou a semântica de uma task existente — isso quebraria a
# comparabilidade da série histórica. Adicione tasks novas com ids novos.


def _setup_read(ws: Path) -> None:
    (ws / "dados.txt").write_text("codigo secreto: ZEBRA-7741", encoding="utf-8")


def _setup_sum(ws: Path) -> None:
    (ws / "a.txt").write_text("15", encoding="utf-8")
    (ws / "b.txt").write_text("27", encoding="utf-8")


def _setup_patch(ws: Path) -> None:
    (ws / "config.txt").write_text("modo: foo\nnivel: alto\n", encoding="utf-8")


def _setup_count(ws: Path) -> None:
    for name in ("um.py", "dois.py", "tres.py"):
        (ws / name).write_text("# vazio\n", encoding="utf-8")
    (ws / "leia.md").write_text("não é py\n", encoding="utf-8")


TASKS: list[BenchTask] = [
    BenchTask(
        id="echo",
        description="Seguir instrução literal",
        prompt="Responda exatamente com a palavra: BAUER_OK",
        check=lambda resp, ws: "BAUER_OK" in resp,
    ),
    BenchTask(
        id="calc",
        description="Aritmética via tool ou direta",
        prompt="Quanto é 17 multiplicado por 23? Responda apenas o número.",
        check=lambda resp, ws: "391" in resp,
    ),
    BenchTask(
        id="write",
        description="Criar arquivo com conteúdo exato",
        prompt="Crie um arquivo chamado nota.txt contendo exatamente o texto: ola mundo",
        check=lambda resp, ws: (ws / "nota.txt").exists()
        and "ola mundo" in (ws / "nota.txt").read_text(encoding="utf-8").lower(),
    ),
    BenchTask(
        id="read",
        description="Ler arquivo e extrair informação",
        prompt="Leia o arquivo dados.txt e me diga qual é o código secreto.",
        setup=_setup_read,
        check=lambda resp, ws: "ZEBRA-7741" in resp,
    ),
    BenchTask(
        id="multi_step",
        description="Ler 2 arquivos e somar",
        prompt="Leia os números nos arquivos a.txt e b.txt e responda a soma deles.",
        setup=_setup_sum,
        check=lambda resp, ws: "42" in resp,
    ),
    BenchTask(
        id="json_extract",
        description="Extrair campo de JSON",
        prompt='Do JSON {"usuario": {"nome": "carlota", "id": 9}} extraia o valor do campo nome e responda só ele.',
        check=lambda resp, ws: "carlota" in resp.lower(),
    ),
    BenchTask(
        id="count_files",
        description="Listar e contar",
        prompt="Quantos arquivos .py existem no workspace? Responda só o número.",
        setup=_setup_count,
        check=lambda resp, ws: re.search(r"\b3\b", resp) is not None,
    ),
    BenchTask(
        id="patch",
        description="Edição cirúrgica de arquivo",
        prompt="No arquivo config.txt, troque 'foo' por 'bar' (mantenha o resto igual).",
        setup=_setup_patch,
        check=lambda resp, ws: (ws / "config.txt").exists()
        and "modo: bar" in (ws / "config.txt").read_text(encoding="utf-8")
        and "nivel: alto" in (ws / "config.txt").read_text(encoding="utf-8"),
    ),
    BenchTask(
        id="graceful_missing",
        description="Falha graciosa em arquivo inexistente",
        prompt="Leia o arquivo inexistente_xyz.dat e me diga o conteúdo.",
        check=lambda resp, ws: bool(resp.strip())
        and any(t in resp.lower() for t in ("não encontrado", "nao encontrado", "não existe", "nao existe", "not found")),
    ),
    BenchTask(
        id="format_follow",
        description="Seguir formato de saída",
        prompt="Responda com exatamente estas três palavras em maiúsculas separadas por espaço: VERMELHO AZUL VERDE",
        check=lambda resp, ws: all(c in resp.upper() for c in ("VERMELHO", "AZUL", "VERDE")),
    ),
]


# ─── Runner ────────────────────────────────────────────────────────────────────


def run_benchmark(client, model: str, provider: str = "?", tasks: list[BenchTask] | None = None) -> BenchmarkReport:
    """Roda as tasks contra o cliente/modelo dados. Cada task em workspace limpo."""
    from .context_manager import ContextManager
    from .tool_router import ToolRouter

    report = BenchmarkReport(
        model=model,
        provider=provider,
        started_at=time.strftime("%Y-%m-%dT%H:%M:%S"),
    )

    for task in (tasks or TASKS):
        ws = Path(tempfile.mkdtemp(prefix=f"bauer_bench_{task.id}_"))
        if task.setup:
            task.setup(ws)
        ctx = ContextManager(applied_context=32768, provider=provider)
        ctx.add_user(task.prompt)
        router = ToolRouter(workspace=ws)

        start = time.monotonic()
        try:
            from .agent import run_one_turn
            response, tool_log = run_one_turn(ctx, router, client, model)
            passed = bool(task.check(response or "", ws))
            report.results.append(TaskResult(
                task_id=task.id,
                passed=passed,
                duration_s=time.monotonic() - start,
                tool_calls=len(tool_log),
            ))
        except Exception as exc:  # noqa: BLE001 — benchmark nunca aborta a série
            report.results.append(TaskResult(
                task_id=task.id,
                passed=False,
                duration_s=time.monotonic() - start,
                tool_calls=0,
                error=f"{type(exc).__name__}: {exc}"[:200],
            ))

    return report


def save_report(report: BenchmarkReport, bench_dir: Path | None = None) -> Path:
    """Grava o report em ~/.bauer/benchmarks/<timestamp>.json."""
    base = bench_dir or BENCH_DIR
    base.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    path = base / f"{stamp}.json"
    path.write_text(
        json.dumps(report.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return path


def load_history(bench_dir: Path | None = None) -> list[dict]:
    """Série histórica de reports (mais recente primeiro)."""
    base = bench_dir or BENCH_DIR
    if not base.exists():
        return []
    out: list[dict] = []
    for f in sorted(base.glob("*.json"), reverse=True):
        try:
            out.append(json.loads(f.read_text(encoding="utf-8")))
        except Exception:
            continue
    return out
