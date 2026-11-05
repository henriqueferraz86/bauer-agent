"""Per-family reviewed policies and factories (plano 057).

Each module registers its ``ToolkitPolicy`` entries, settings models and code
factories on import. Importing a family module must stay cheap: it may not
import the SDK toolkit or any optional connector package at module level.
"""

from __future__ import annotations

from . import github, mcp, research  # noqa: E402,F401  (sprint 51)
