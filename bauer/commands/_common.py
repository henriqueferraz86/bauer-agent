"""Primitivas compartilhadas pelos módulos de comando da CLI.

Mora aqui (e não em cli.py) para evitar import circular: os módulos de comando
em bauer/commands/*.py importam destas primitivas, e cli.py também as importa.
Mantém um único `console` para formatação consistente em toda a CLI.
"""

from __future__ import annotations

from pathlib import Path

from rich.console import Console

from ..paths import get_bauer_home, memory_dir, runtime_state_path

# Console único da CLI — mesma configuração que o cli.py usava.
console = Console(highlight=False, legacy_windows=False)

# Diretórios-padrão relativos ao CWD, usados como default em vários comandos.
_PROJECT_WORKSPACE = Path("workspace")
_SPECS_DIR = Path("specs")

# Paths canônicos derivados de ~/.bauer/ — avaliados no import para uso como
# defaults de typer.Option (espelham o que o cli.py calculava).
_WORKSPACE_DIR = get_bauer_home() / "workspace"
_COMPANIES_DIR = get_bauer_home() / "workspace" / "companies"
_MEMORY_DIR = memory_dir()
_RUNTIME_STATE_DEFAULT = runtime_state_path()

# Aliases curtos → nome de arquivo Markdown de memória.
_FILE_ALIASES = {
    "memory": "MEMORY.md",
    "decisions": "DECISIONS.md",
    "failures": "FAILED_ATTEMPTS.md",
    "experience": "MODEL_EXPERIENCE.md",
    "prefs": "USER_PREFERENCES.md",
    "lessons": "RUNTIME_LESSONS.md",
}
