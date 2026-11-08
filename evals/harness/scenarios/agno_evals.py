"""Cenários do plano 057 / sprint 56: evals do Agno convertidos em casos do harness.

O ``ReliabilityEval`` do Agno roda sobre a trilha de eventos do Bauer (sem db,
sem telemetria, sem modelo). Os cenários medem o que interessa ao harness:
uso correto de tools (incluindo detectar tool inesperada ou que falhou) e que
um eval aprovado nunca substitui o veredito do Kernel.
"""

from __future__ import annotations

from ..runner import cenario
from ._base import GatePlantado, kernel_temp, pedido


def _agno_disponivel() -> bool:
    from importlib.util import find_spec

    return find_spec("agno") is not None


def _trilha(nomes_ok: list[str], nomes_erro: list[str] = ()):  # type: ignore[assignment]
    from bauer.core.events.schema import Event
    from bauer.core.runtime.agno_evals import trace_from_events

    eventos = [Event(event_type="tool.call.completed", tool_name=n, run_id="r") for n in nomes_ok]
    eventos += [Event(event_type="tool.call.failed", tool_name=n, run_id="r") for n in nomes_erro]
    return trace_from_events(eventos, run_id="r")


@cenario("56.1 eval Agno confirma uso das tools esperadas na trilha Bauer")
def eval_uso_de_tools():
    if not _agno_disponivel():
        return True, "agno ausente: cenário não aplicável"
    from bauer.core.runtime.agno_evals import evaluate_tool_use

    v = evaluate_tool_use(_trilha(["bauer_github__get_issue", "bauer_sql__run_query"]),
                          expected=["bauer_github__get_issue", "bauer_sql__run_query"])
    return v.passed, f"passed={v.passed_calls}"


@cenario("56.2 eval Agno reprova tool inesperada ou que falhou", falso_sucesso=True)
def eval_detecta_uso_errado():
    if not _agno_disponivel():
        return True, "agno ausente: cenário não aplicável"
    from bauer.core.runtime.agno_evals import evaluate_tool_use

    inesperada = evaluate_tool_use(_trilha(["bauer_github__get_issue", "run_command"]),
                                   expected=["bauer_github__get_issue"])
    falhou = evaluate_tool_use(_trilha([], ["bauer_sql__run_query"]), expected=["bauer_sql__run_query"])
    ok = (not inesperada.passed and "run_command" in inesperada.failed_calls
          and not falhou.passed and any(m.startswith("bauer_sql__run_query") for m in falhou.missing_calls))
    return ok, f"inesperada={inesperada.failed_calls} faltando={falhou.missing_calls}"


@cenario("56.3 eval Agno aprovado não substitui gate do Kernel", critico=True, falso_sucesso=True)
def eval_nao_substitui_gate():
    if not _agno_disponivel():
        return True, "agno ausente: cenário não aplicável"
    from bauer.core.runtime.agno_evals import evaluate_tool_use

    v = evaluate_tool_use(_trilha(["read_file"]), expected=["read_file"])
    k, _ = kernel_temp(gates=[GatePlantado("teste", False, "testes vermelhos")])
    r = k.execute(pedido(), executor=lambda p: {"output": "pronto", "agno_eval_passed": v.passed})
    return v.passed and r.status != "completed", f"eval={v.passed} run={r.status}"
