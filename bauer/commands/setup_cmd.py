"""`bauer setup` — wizard de primeiro uso.

Detecta o Ollama e os modelos locais, gera um config.yaml canônico com
defaults sensatos (api_key forte, host local, provider ollama) e orienta os
próximos passos. NÃO instala pacotes nem roda sudo — quando algo falta
(Ollama offline, nenhum modelo baixado), aponta o comando certo. Não-
destrutivo: confirma antes de sobrescrever um config existente.

A lógica pura (escolha de modelo, montagem do config) vive em funções
separadas e testáveis; o comando só orquestra e fala com o usuário.
"""

from __future__ import annotations

import secrets
from typing import Any, Dict, List, Optional

import typer

from ._common import console

# Modelo sugerido quando nenhum está instalado: 7B com suporte a tools, bom
# equilíbrio entre qualidade e velocidade em hardware modesto.
_SUGGESTED_MODEL = "qwen2.5:7b"


def pick_model(installed: List[str], preferred: Optional[str] = None) -> Optional[str]:
    """Escolhe o modelo ativo.

    Precedência: o preferido (se instalado) → o primeiro instalado que NÃO
    seja de embedding (esses não geram chat) → None se a lista está vazia.
    """
    if preferred and preferred in installed:
        return preferred
    for name in installed:
        low = name.lower()
        if "embed" in low or "bge" in low:  # modelos de embedding não conversam
            continue
        return name
    return installed[0] if installed else None


def render_config(
    model: str,
    api_key: str,
    *,
    provider: str = "ollama",
    ollama_host: str = "http://localhost:11434",
    serve_host: str = "127.0.0.1",
    serve_port: int = 8000,
) -> Dict[str, Any]:
    """Monta o dict de config canônico do Bauer para uso local.

    Só campos que existem no schema (BauerConfig é estrito). `auto_tool_allowlist`
    fica no default True — o runtime enxuga as tools sozinho em modelo local.
    """
    return {
        "model": {
            "provider": provider,
            "name": model,
            "requested_context": 8192,
        },
        "ollama": {
            "host": ollama_host,
        },
        "serve": {
            "host": serve_host,
            "port": serve_port,
            "api_key": api_key,
        },
        "tools": {
            "web_enabled": True,
            "shell_enabled": True,
        },
    }


def setup(
    force: bool = typer.Option(False, "--force", help="Sobrescreve config existente sem perguntar"),
    model: str = typer.Option("", "--model", help="Modelo a usar (padrão: detecta o instalado)"),
):
    """Configura o Bauer para uso local em poucos segundos.

    Detecta o Ollama, escolhe um modelo, gera o config.yaml canônico com uma
    api_key forte e mostra os próximos passos.
    """
    import yaml

    from ..paths import config_path as _config_path

    console.print("\n[bold]Bauer Setup[/bold] — configuração de primeiro uso\n")

    # 1. Ollama disponível? (não instala — orienta)
    from ..ollama_client import OllamaClient

    oc = OllamaClient()
    alive, reason = oc.is_alive()
    installed: List[str] = []
    if alive:
        console.print("  Ollama: [green]ativo[/green]")
        try:
            installed = oc.list_models()
        except Exception:
            installed = []
    else:
        console.print(f"  Ollama: [yellow]offline[/yellow] ({reason})")
        console.print("  [dim]Instale e inicie: curl -fsSL https://ollama.com/install.sh | sh[/dim]")

    # 2. modelo ativo
    chosen = pick_model(installed, preferred=model or None)
    if chosen is None:
        chosen = model or _SUGGESTED_MODEL
        console.print(f"  Nenhum modelo baixado — usando [green]{chosen}[/green] [dim](baixe com: ollama pull {chosen})[/dim]")
    else:
        suffix = "" if chosen in installed else "  [dim](ainda não baixado)[/dim]"
        console.print(f"  Modelo: [green]{chosen}[/green]{suffix}")

    # 3. grava config canônico (não-destrutivo)
    cfg_path = _config_path()
    if cfg_path.exists() and not force:
        if not typer.confirm(f"\n{cfg_path} já existe. Sobrescrever?", default=False):
            console.print("Cancelado — nada foi alterado.")
            raise typer.Exit(code=0)

    api_key = secrets.token_hex(32)
    data = render_config(chosen, api_key)
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    cfg_path.write_text(
        yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )

    # Cria a pasta workspace (get_bauer_home()/workspace) — a MESMA que serve e
    # agent usam por padrão (_WORKSPACE_DIR). Sem isso, ela só surgia no primeiro
    # uso, e quem procurava logo após instalar não a encontrava.
    from ..paths import get_bauer_home

    workspace = get_bauer_home() / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)

    console.print(f"\n[green]✓[/green] Config gravado em {cfg_path}")
    console.print(f"[green]✓[/green] Workspace pronto em {workspace}")
    console.print(
        f"  API key do serve: [dim]{api_key}[/dim]\n"
        "  [yellow]Guarde essa chave[/yellow] — ela protege o [bold]bauer serve[/bold] (header X-API-Key)."
    )
    console.print("\n[bold]Próximos passos:[/bold]")
    if not alive:
        console.print("  1. Suba o Ollama (veja acima) e baixe um modelo: [bold]ollama pull " + chosen + "[/bold]")
    elif chosen not in installed:
        console.print(f"  1. Baixe o modelo: [bold]ollama pull {chosen}[/bold]")
    console.print("  2. Cheque o ambiente: [bold]bauer doctor[/bold]")
    console.print("  3. Suba o servidor:  [bold]bauer serve[/bold]")
