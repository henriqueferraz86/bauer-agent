"""Roda o conjunto de tarefas em bauer_native e Agno, mede custo/latência/tokens.

Ver o docstring do pacote (``evals/compare_executors/__init__.py``) para o que
isto mede e o que não mede. Este módulo nunca é importado pela suíte
hermética — só por quem roda ``python -m evals.compare_executors`` de
propósito, com um provider real configurado.
"""

from __future__ import annotations

import json
import tempfile
import time
import traceback
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .tasks import TASKS, Task

from bauer.commands._runtime import _build_client, _build_router


@dataclass
class ExecutionResult:
    executor: str
    task_name: str
    output: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_usd: float = 0.0
    latency_s: float = 0.0
    error: str = ""


@dataclass
class ComparisonRow:
    task_name: str
    bauer_native: ExecutionResult
    agno: ExecutionResult
    #: preenchido manualmente por um humano após ler as duas saídas:
    #: "equivalente" | "bauer_native_melhor" | "agno_melhor" | "" (pendente)
    quality_verdict: str = ""


@dataclass
class Report:
    rows: list[ComparisonRow] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "rows": [
                {
                    "task_name": row.task_name,
                    "bauer_native": asdict(row.bauer_native),
                    "agno": asdict(row.agno),
                    "quality_verdict": row.quality_verdict,
                }
                for row in self.rows
            ],
        }


class _CostRecorder:
    """Mesmo contrato de sink que bauer/server.py::_TurnCostRecorder — soma o custo
    de todas as chamadas LLM de uma execução (pode ser mais de uma com retry)."""

    def __init__(self) -> None:
        self.total_usd = 0.0
        self.prompt_tokens = 0
        self.completion_tokens = 0

    def __call__(self, provider: str, model: str, usage: dict, cost_usd: float) -> None:
        self.total_usd += float(cost_usd or 0.0)
        if isinstance(usage, dict):
            self.prompt_tokens += int(usage.get("prompt_tokens") or 0)
            self.completion_tokens += int(usage.get("completion_tokens") or 0)


def _run_bauer_native(task: Task, cfg: Any, workspace: Path) -> ExecutionResult:
    from bauer.core.context.builder import ContextBuilder
    from bauer.cost_meter import cost_sink

    result = ExecutionResult(executor="bauer_native", task_name=task.name)
    recorder = _CostRecorder()
    token = cost_sink.set(recorder)
    try:
        client = _build_client(cfg)
        router = _build_router(cfg, workspace, llm_client=client)
        ctx, _ = (
            ContextBuilder(applied_context=8192, provider=cfg.model.provider)
            .instrucao("seguranca", "Você é o Bauer, um assistente de IA útil e direto.")
            .montar()
        )
        ctx.add_user(task.prompt)

        from bauer.agent import run_one_turn_with_fallback

        start = time.monotonic()
        answer, _tool_log = run_one_turn_with_fallback(ctx, router, client, cfg.model.name)
        result.latency_s = time.monotonic() - start
        result.output = answer
    except Exception as exc:  # noqa: BLE001 — a execução que falhou também é dado
        result.error = f"{type(exc).__name__}: {exc}\n{traceback.format_exc(limit=4)}"
    finally:
        cost_sink.reset(token)
    result.cost_usd = recorder.total_usd
    result.prompt_tokens = recorder.prompt_tokens
    result.completion_tokens = recorder.completion_tokens
    return result


def _run_agno(task: Task, cfg: Any, workspace: Path) -> ExecutionResult:
    from bauer.core.runtime.adapters.agno_adapter import AgnoRuntimeAdapter
    from bauer.cost_meter import cost_sink

    result = ExecutionResult(executor="agno", task_name=task.name)
    recorder = _CostRecorder()
    token = cost_sink.set(recorder)
    try:
        adapter = AgnoRuntimeAdapter(
            config=cfg,
            adapter_config={
                "workspace": str(workspace),
                "tool_context": "chat",
                "db_file": str(workspace / ".agno_compare_executors.db"),
            },
        )
        start = time.monotonic()
        run_result = adapter.run_agent({
            "task": task.prompt,
            "session_id": f"compare-executors-{task.name}",
            "user_id": "compare-executors",
            "agent_spec": {"provider": cfg.model.provider, "model": cfg.model.name},
        })
        result.latency_s = time.monotonic() - start
        if run_result.get("status") != "completed":
            result.error = str(run_result.get("error") or f"status={run_result.get('status')}")
        result.output = str(run_result.get("output") or "")
    except Exception as exc:  # noqa: BLE001
        result.error = f"{type(exc).__name__}: {exc}\n{traceback.format_exc(limit=4)}"
    finally:
        cost_sink.reset(token)
    result.cost_usd = recorder.total_usd
    result.prompt_tokens = recorder.prompt_tokens
    result.completion_tokens = recorder.completion_tokens
    return result


def run_comparison(cfg: Any, *, tasks: tuple[Task, ...] = TASKS) -> Report:
    """Roda cada tarefa nos dois executores, workspace isolado por tarefa."""
    report = Report()
    for task in tasks:
        workspace = Path(tempfile.mkdtemp(prefix=f"bauer-compare-{task.name}-"))
        if task.setup is not None:
            task.setup(workspace)
        native = _run_bauer_native(task, cfg, workspace)
        agno = _run_agno(task, cfg, workspace)
        report.rows.append(ComparisonRow(task_name=task.name, bauer_native=native, agno=agno))
    return report


def formatar(report: Report) -> str:
    """Inclui o texto de cada resposta — sem isto a revisão manual pareada
    exigiria abrir o JSON à parte; o objetivo é decidir lendo só este texto."""
    linhas = ["Comparação bauer_native vs Agno — métricas duras (qualidade: revisão manual)", ""]
    for row in report.rows:
        linhas.append(f"## {row.task_name}")
        for res in (row.bauer_native, row.agno):
            status = "ERRO" if res.error else "ok"
            linhas.append(
                f"  {res.executor:12} [{status}]  "
                f"custo=${res.cost_usd:.5f}  tokens={res.prompt_tokens}+{res.completion_tokens}  "
                f"latencia={res.latency_s:.2f}s"
            )
            if res.error:
                linhas.append(f"               erro: {res.error.splitlines()[0][:100]}")
            elif res.output:
                for linha in res.output.rstrip().splitlines() or [""]:
                    linhas.append(f"               | {linha}")
        linhas.append(f"  veredito de qualidade: {row.quality_verdict or '(pendente — revisão manual)'}")
        linhas.append("")
    return "\n".join(linhas)


def main(argv: "list[str] | None" = None) -> int:
    import argparse

    from bauer.config_loader import load_config

    ap = argparse.ArgumentParser(prog="python -m evals.compare_executors")
    ap.add_argument("--config", required=True, help="Caminho do config.yaml com provider real configurado.")
    ap.add_argument("--json", dest="json_out", default="", help="Grava o relatório completo como JSON.")
    args = ap.parse_args(argv)

    cfg = load_config(args.config)
    report = run_comparison(cfg)
    print(formatar(report))
    if args.json_out:
        Path(args.json_out).write_text(
            json.dumps(report.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8"
        )
    return 0 if all(not row.bauer_native.error and not row.agno.error for row in report.rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())
