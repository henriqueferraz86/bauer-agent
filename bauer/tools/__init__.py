"""Subpacote de tools do Bauer Agent.

P4 (modularização): as ferramentas do ToolRouter são organizadas aqui por
categoria, cada uma como um *mixin* que `ToolRouter` herda. Isso mantém os
métodos como `self._nome` (resolvidos por herança) sem mudar o dispatch nem
os imports públicos — `from bauer.tool_router import ToolRouter` continua
funcionando.

Módulos:
  base    — exceções e tipos compartilhados (ToolError, SandboxError, DryRunResult)
  utility — calculate, datetime_now, json_query, encode_decode
"""

from __future__ import annotations
