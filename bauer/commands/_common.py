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

#: Console de ERRO. O Rich decide o destino na CONSTRUÇÃO (`stderr=True`), não
#: por argumento de `print()` — quem aceita `err=True` por chamada é o
#: `typer.echo`/`click.echo`. Misturar as duas APIs custou caro: um
#: `console.print(..., err=True)` no tratador de erro do `bauer agent run-one`
#: levantava `TypeError` DENTRO do `except`, soterrando a exceção original em
#: "During handling of the above exception..." e impedindo o
#: `raise typer.Exit(code=1)` da linha seguinte — o código de saída deixava de
#: ser o combinado. Este objeto existe para não haver mais motivo de escrever
#: aquele argumento.
console_err = Console(highlight=False, legacy_windows=False, stderr=True)

# Diretórios-padrão relativos ao CWD, usados como default em vários comandos.
_PROJECT_WORKSPACE = Path("workspace")
_SPECS_DIR = Path("specs")

# Paths canônicos derivados de ~/.bauer/ — avaliados no import para uso como
# defaults de typer.Option (espelham o que o cli.py calculava).
_WORKSPACE_DIR = get_bauer_home() / "workspace"
_COMPANIES_DIR = get_bauer_home() / "workspace" / "companies"
_MEMORY_DIR = memory_dir()
_RUNTIME_STATE_DEFAULT = runtime_state_path()


def _loaded_config_path(config: Path) -> Path:
    """Caminho do config que SERÁ carregado — para exibir ao usuário.

    Espelha o fallback do `load_config` em vez de imprimir o path pedido.
    Sem isso, rodar de um diretório sem `config.yaml` anunciava
    `<cwd>/config.yaml` (inexistente) enquanto lia o de `$BAUER_HOME`.
    Degrada para o path pedido quando nenhum dos dois existe — quem reporta
    o erro de verdade é o `load_config`, não a linha de display.
    """
    from ..config_loader import ConfigError, resolve_config_path

    try:
        return resolve_config_path(config).resolve()
    except ConfigError:
        return config.resolve()


# Aliases curtos → nome de arquivo Markdown de memória.
_FILE_ALIASES = {
    "memory": "MEMORY.md",
    "decisions": "DECISIONS.md",
    "failures": "FAILED_ATTEMPTS.md",
    "experience": "MODEL_EXPERIENCE.md",
    "prefs": "USER_PREFERENCES.md",
    "lessons": "RUNTIME_LESSONS.md",
}
