"""Camada de UX do Bauer (Fase 12).

  tool_phase(name) → ToolPhase(label, icon): traduz o nome cru de uma tool no
  passo humano correspondente ("run_command" → "Executando comando"), para a
  narração de progresso do chat/CLI ("silêncio é o inimigo da UX").
"""

from __future__ import annotations

from .progress import ToolPhase, tool_phase

__all__ = ["ToolPhase", "tool_phase"]
