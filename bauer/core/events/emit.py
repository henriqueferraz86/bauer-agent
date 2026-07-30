"""Emissão tolerante de evento para quem NÃO é dono do bus.

O Kernel tem `self.bus` e um `_publish` próprio. Mas três dos eventos mínimos do
plano §14 acontecem fora dele — o worktree é criado antes do Kernel existir, o
contexto é montado pelo `ContextBuilder`, e o aviso de estagnação nasce no
guardrail, dentro do laço de turno. Esses módulos recebem o bus por parâmetro e
publicam por aqui.

Duas regras, as mesmas do `_publish` do Kernel:

1. **Sem bus, no-op.** O bus é opcional em todas as assinaturas. Um teste que
   constrói `ContextBuilder(applied_context=8000)` continua funcionando, e o
   caminho sem governança não passa a exigir infraestrutura.
2. **Telemetria nunca derruba o run.** Se a publicação falha, o trabalho segue.
   Perder um evento é ruim; perder a tarefa por causa dele é pior.
"""

from __future__ import annotations

from typing import Any


def emitir(bus: Any, event_type: str, **campos: Any) -> None:
    if bus is None:
        return
    try:
        bus.publish(event_type, **campos)
    except Exception as exc:  # noqa: BLE001 — ver regra 2 acima
        from ...logging_config import log_suppressed

        log_suppressed(f"events.emit.{event_type}", exc)
