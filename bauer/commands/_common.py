"""Primitivas compartilhadas pelos módulos de comando da CLI.

Mora aqui (e não em cli.py) para evitar import circular: os módulos de comando
em bauer/commands/*.py importam destas primitivas, e cli.py também as importa.
Mantém um único `console` para formatação consistente em toda a CLI.
"""

from __future__ import annotations

from pathlib import Path

from rich.console import Console

# Console único da CLI — mesma configuração que o cli.py usava.
console = Console(highlight=False, legacy_windows=False)

# Diretórios-padrão relativos ao CWD, usados como default em vários comandos.
_PROJECT_WORKSPACE = Path("workspace")
_SPECS_DIR = Path("specs")
