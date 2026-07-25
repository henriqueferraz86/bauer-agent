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


# Teto para o contexto inferido automaticamente em modelo LOCAL.
#
# Modelos modernos anunciam janelas nativas enormes (131072 é comum), mas cada
# 1k de contexto custa RAM/VRAM de KV cache — gravar 131072 no config de quem
# roda um 27b na própria máquina trocaria "contexto pequeno demais" por "não
# sobe". 32768 é o mesmo teto que os modelos curados do `models.yaml` já usam.
_TETO_CONTEXTO_LOCAL = 32768

# Piso histórico. Continua sendo o fallback quando não dá para descobrir nada
# sobre o modelo (Ollama offline, modelo fora do registry e sem /api/show).
_CONTEXTO_FALLBACK = 8192


def contexto_inicial_para(model: str, *, registry=None, client=None) -> int:
    """Contexto a gravar no config para *model*, derivado do MODELO.

    Antes isto era `8192` cravado, independente do que estivesse instalado —
    e o `requested_context` é TETO na regra do preflight
    (`min(requested, modelfile, env, ram_seguro)`). Resultado prático: quem
    rodava um 27b/30b com janela de 32k ficava preso em 8192 para sempre, sem
    nenhum aviso, e ainda perdia tools pelo auto-allowlist de contexto pequeno.

    Ordem de preferência:
      1. `models.yaml` — curado, e o `max_context_safe` de lá já pondera RAM;
      2. `/api/show` do Ollama — `context_length` nativo, cobre modelo que não
         está no registry (a maioria das instalações tem algum);
      3. 8192 — o comportamento antigo, só quando nada é descobrível.
    """
    if registry is not None:
        try:
            info = registry.get(model)
            if info is not None and info.max_context_safe:
                return min(int(info.max_context_safe), _TETO_CONTEXTO_LOCAL)
        except Exception as exc:  # noqa: BLE001 — setup nunca falha por causa disto
            from ..logging_config import log_suppressed
            log_suppressed("setup.contexto.registry", exc)

    if client is not None:
        try:
            from ..model_registry import auto_detect_from_ollama

            info = auto_detect_from_ollama(client, model)
            if info is not None and info.max_context_safe:
                return min(int(info.max_context_safe), _TETO_CONTEXTO_LOCAL)
        except Exception as exc:  # noqa: BLE001
            from ..logging_config import log_suppressed
            log_suppressed("setup.contexto.autodetect", exc)

    return _CONTEXTO_FALLBACK


def render_config(
    model: str,
    api_key: str,
    *,
    provider: str = "ollama",
    ollama_host: str = "http://localhost:11434",
    serve_host: str = "127.0.0.1",
    serve_port: int = 8000,
    registry=None,
    client=None,
) -> Dict[str, Any]:
    """Monta o dict de config canônico do Bauer para uso local.

    Só campos que existem no schema (BauerConfig é estrito). `auto_tool_allowlist`
    fica no default True — o runtime enxuga as tools sozinho em modelo local.
    """
    return {
        "model": {
            "provider": provider,
            "name": model,
            "requested_context": contexto_inicial_para(
                model, registry=registry, client=client
            ),
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

    # Contexto derivado do modelo escolhido, não mais 8192 cravado. O registry
    # é a fonte preferida (curado + ciente de RAM); o client cobre modelo que
    # não está nele, lendo o context_length nativo via /api/show.
    _reg = None
    try:
        from ..model_registry import load_registry
        _reg = load_registry()
    except Exception as exc:  # noqa: BLE001 — sem registry, cai no auto-detect/fallback
        from ..logging_config import log_suppressed
        log_suppressed("setup.load_registry", exc)

    data = render_config(chosen, api_key, registry=_reg, client=oc)
    _ctx_escolhido = data["model"]["requested_context"]
    if _ctx_escolhido > _CONTEXTO_FALLBACK:
        console.print(
            f"  Contexto: [green]{_ctx_escolhido:,}[/green] tokens "
            f"[dim](da janela do modelo; antes era {_CONTEXTO_FALLBACK} fixo)[/dim]"
        )
    else:
        console.print(f"  Contexto: [green]{_ctx_escolhido:,}[/green] tokens")
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
