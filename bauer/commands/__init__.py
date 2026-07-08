"""Subpacote de comandos da CLI do Bauer.

P4 Parte 1 (modularização): os grupos Typer do cli.py são extraídos para cá,
um arquivo por área (`<grupo>_cmd.py`). Cada módulo define seu próprio
`typer.Typer()` e seus comandos; cli.py importa o grupo e o registra com
`app.add_typer(...)`. Primitivas compartilhadas (console, helpers de config)
moram em `_common.py` para evitar import circular com cli.py.
"""

from __future__ import annotations
