"""Onboarding — primeira experiência de quem chega ao Bauer.

- welcome_screen(): tela inteligente mostrada em `bauer` (sem args) e `bauer start`.
  Detecta o estado (sem config / quase lá / pronto) e mostra o caminho certo.
- guide_tour(): tour curto explicando os modos principais.

Tudo defensivo: se algo falhar na detecção, cai num default amigável em vez
de quebrar — o objetivo é nunca deixar o iniciante perdido.
"""

from __future__ import annotations

import os
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.text import Text
from rich import box

# Paleta consistente com o logo/painel do agent.
_ACCENT = "#00d4aa"
_BLUE = "#3b82f6"
_PURPLE = "#7c3aed"
_DIM = "#6b7280"

# Providers que rodam sem API key (estado "pronto" imediato).
_NO_KEY_PROVIDERS = {"ollama", "opencode"}
# Provider → variável de ambiente da chave (para detectar "quase lá").
_KEY_ENV = {
    "groq": "GROQ_API_KEY",
    "openai-api": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "gemini": "GEMINI_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
    "mistral": "MISTRAL_API_KEY",
    "xai": "XAI_API_KEY",
    "together": "TOGETHER_API_KEY",
    "deepseek": "DEEPSEEK_API_KEY",
    "github": "GITHUB_TOKEN",
}


def _read_env_file(env_path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    try:
        if env_path.exists():
            for line in env_path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, _, v = line.partition("=")
                out[k.strip()] = v.strip().strip('"').strip("'")
    except Exception:
        pass
    return out


def _key_present(env_var: str, config_path: Path) -> bool:
    if os.environ.get(env_var):
        return True
    return bool(_read_env_file(config_path.parent / ".env").get(env_var))


def detect_state(config_path: Path) -> dict:
    """Retorna {state, provider, model, hint}. state ∈ {fresh, almost, ready}."""
    if not config_path.exists():
        return {"state": "fresh", "provider": "", "model": "", "hint": ""}
    try:
        from .config_loader import load_config
        cfg = load_config(config_path)
        provider = cfg.model.provider
        model = cfg.model.name
    except Exception:
        # Config existe mas inválido — trata como quase lá (precisa ajustar).
        return {"state": "almost", "provider": "", "model": "",
                "hint": "config.yaml existe mas está inválido — rode `bauer init` de novo."}

    if provider in _NO_KEY_PROVIDERS or provider == "openai":
        return {"state": "ready", "provider": provider, "model": model, "hint": ""}
    env_var = _KEY_ENV.get(provider)
    if env_var and not _key_present(env_var, config_path):
        return {"state": "almost", "provider": provider, "model": model,
                "hint": f"falta a chave {env_var} — rode `bauer model` ou ponha no .env"}
    return {"state": "ready", "provider": provider, "model": model, "hint": ""}


def _logo(console: Console) -> None:
    """Imprime o logo BAUER em gradiente (reusa o ascii_intro)."""
    try:
        from .ascii_intro import _logo_rows, _SUBTITLE
        from rich.align import Align
        console.print()
        for row in _logo_rows("BAUER"):
            console.print(Align.center(row))
        console.print(Align.center(Text(_SUBTITLE, style=f"italic {_DIM}")))
        console.print()
    except Exception:
        console.print(Text("\n  BAUER — adaptive LLM runtime\n", style=f"bold {_ACCENT}"))


def _steps_panel(title: str, steps: list[tuple[str, str]], *, border: str) -> Panel:
    body = Text()
    for i, (cmd, desc) in enumerate(steps):
        if i:
            body.append("\n")
        body.append(f"  {i + 1}. ", style=_DIM)
        body.append(cmd, style=f"bold {_PURPLE}")
        body.append(f"   {desc}", style="white")
    return Panel(body, title=Text(title, style=f"bold {_ACCENT}"),
                 title_align="left", border_style=border, box=box.ROUNDED, padding=(1, 2))


def welcome_screen(console: Console | None = None, config_path: str | Path = "config.yaml") -> None:
    """Tela de boas-vindas inteligente — orienta conforme o estado atual."""
    con = console or Console()
    cfg_path = Path(config_path)
    info = detect_state(cfg_path)
    _logo(con)

    if info["state"] == "fresh":
        con.print(Text("  Bem-vindo! Vamos configurar em 1 passo.", style="bold white"))
        con.print(_steps_panel(
            "Comece aqui",
            [
                ("bauer init", "configurar provider e modelo (wizard)"),
                ("bauer doctor", "checar se o ambiente está ok"),
                ("bauer agent", "conversar com o agente (com tools)"),
            ],
            border=_ACCENT,
        ))
        con.print(
            f"  [dim]Sem cartão? Escolha[/dim] [bold {_ACCENT}]Groq[/bold {_ACCENT}] "
            f"[dim]ou[/dim] [bold {_ACCENT}]OpenCode[/bold {_ACCENT}] [dim]no init (grátis).[/dim]"
        )
    elif info["state"] == "almost":
        con.print(Text("  Quase lá — falta só a credencial.", style="bold yellow"))
        if info["hint"]:
            con.print(f"  [yellow]→[/yellow] {info['hint']}")
        con.print(_steps_panel(
            "Próximo passo",
            [
                ("bauer model", "escolher provider/modelo e colar a chave"),
                ("bauer doctor", "confirmar conectividade"),
                ("bauer agent", "começar a usar"),
            ],
            border="yellow",
        ))
    else:  # ready
        prov = info["provider"] or "?"
        model = info["model"] or "?"
        con.print(
            f"  [bold {_ACCENT}]⚡ Você está pronto![/bold {_ACCENT}]  "
            f"[dim]{model} ({prov})[/dim]"
        )
        con.print(_steps_panel(
            "O que fazer agora",
            [
                ("bauer agent", "conversar com o agente — tools + memória (uso diário)"),
                ("bauer model", "trocar de provider/modelo"),
                ("bauer guide", "tour rápido dos modos"),
            ],
            border=_ACCENT,
        ))

    con.print(
        f"  [dim]Todos os comandos:[/dim] [bold]bauer --help[/bold]   "
        f"[dim]·  Tour:[/dim] [bold]bauer guide[/bold]\n"
    )


# ─────────────────────────────────────────────────────────────────────────────
# Tour interativo
# ─────────────────────────────────────────────────────────────────────────────

_TOUR: list[tuple[str, str]] = [
    (
        "O que é o Bauer",
        "Um runtime de agente para LLMs locais e cloud. Roda com o que você tem\n"
        "(Ollama local, ou providers grátis como Groq/OpenCode) e se adapta.",
    ),
    (
        "1) Configurar — bauer init",
        "Wizard que escolhe provider + modelo e salva no config.yaml.\n"
        "Dica: Groq e OpenCode são grátis e não pedem cartão.",
    ),
    (
        "2) Conversar — bauer agent",
        "Modo principal: agente com ferramentas (ler/escrever arquivos, shell, web)\n"
        "e memória persistente. É o que você usa no dia a dia.\n"
        "  • bauer chat  = modo mínimo, só conversa (sem tools)",
    ),
    (
        "3) Trocar de modelo — bauer model / /model",
        "Troca provider/modelo a qualquer momento. Dentro do agent, digite /model.\n"
        "O menu mostra quais são GRÁTIS e quais são PAGOS.",
    ),
    (
        "4) Canais — bauer gateway",
        "Conecte o agente ao Telegram/Discord: `bauer gateway init` → `gateway start`.\n"
        "Você conversa com o Bauer pelo chat, com sessão por usuário.",
    ),
    (
        "5) Rodar como serviço",
        "Mantenha o gateway ativo mesmo depois de fechar o terminal:\n\n"
        "  • bauer gateway start -b          inicia em background (terminal livre)\n"
        "  • bauer gateway service install   registra como serviço do sistema\n"
        "    (systemd no Linux · Task Scheduler no Windows)\n"
        "  • bauer gateway service logs      acompanha os logs em tempo real\n\n"
        "Com o serviço instalado o gateway sobe automaticamente no boot.",
    ),
    (
        "6) Diagnóstico — bauer doctor / status",
        "`bauer doctor` checa provider, modelo, RAM e conectividade.\n"
        "`bauer status` mostra um dashboard do estado atual.",
    ),
    (
        "7) Referência — todos os comandos",
        "ESSENCIAIS\n"
        "  bauer start          tela de boas-vindas\n"
        "  bauer init           wizard de primeiro uso\n"
        "  bauer agent          agente interativo (uso diário)\n"
        "  bauer chat           conversa mínima (sem tools)\n"
        "  bauer model          trocar provider/modelo\n"
        "  bauer doctor         diagnóstico do ambiente\n"
        "  bauer status         dashboard de estado\n"
        "  bauer guide          este tour\n"
        "\n"
        "TASKS E PROJETOS\n"
        "  bauer task           criar/listar/gerenciar tasks\n"
        "  bauer project        visão do projeto (PROJECT.md)\n"
        "  bauer dispatch       processar tasks READY\n"
        "  bauer kanban         kanban ao vivo no browser\n"
        "  bauer boards         multi-board por projeto\n"
        "  bauer runtime        supervisor always-on\n"
        "  bauer daemon         pool de workers autônomos\n"
        "  bauer cron           automações agendadas\n"
        "  bauer ops            filas, lanes, claims\n"
        "\n"
        "ORQUESTRAÇÃO\n"
        "  bauer orchestrate    tasks complexas multi-passo\n"
        "  bauer spec           contratos de features (SDD)\n"
        "\n"
        "CANAIS\n"
        "  bauer gateway        Telegram + Discord + outbox\n"
        "  bauer telegram       bridge Telegram standalone\n"
        "  bauer discord        bridge Discord standalone\n"
        "\n"
        "SKILLS E PLUGINS\n"
        "  bauer skill-*        instalar/listar/remover skills\n"
        "  bauer skills-hub     catálogo de skills curadas\n"
        "  bauer plugin         plugins Bauer\n"
        "\n"
        "CONFIG E AUTH\n"
        "  bauer config         ler/escrever config.yaml\n"
        "  bauer models         models.yaml\n"
        "  bauer memory         memória Markdown\n"
        "  bauer auth           OAuth / API key\n"
        "  bauer learning       adaptive learning engine\n"
        "  bauer research       pesquisa e trajectories\n"
        "  bauer migrate        importar de Hermes/OpenClaw\n"
        "\n"
        "SERVIDOR\n"
        "  bauer serve          HTTP REST + SSE\n"
        "  bauer logs           logs em tempo real\n"
        "  bauer tools          Tool Bridge\n"
        "  bauer company        multi-empresa (namespaces isolados)\n"
        "\n"
        "Detalhes de cada sub-comando: bauer <grupo> --help",
    ),
]


def guide_tour(console: Console | None = None, *, interactive: bool = True) -> None:
    """Tour curto pelos modos do Bauer. Enter avança; q sai."""
    con = console or Console()
    _logo(con)
    total = len(_TOUR)
    for idx, (title, body) in enumerate(_TOUR, 1):
        con.print(Panel(
            Text(body, style="white"),
            title=Text(f"{idx}/{total}  {title}", style=f"bold {_ACCENT}"),
            title_align="left", border_style=_BLUE, box=box.ROUNDED, padding=(1, 2),
        ))
        if interactive and idx < total:
            try:
                ans = con.input("  [dim]Enter para continuar · q para sair[/dim] ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                con.print()
                return
            if ans in ("q", "quit", "sair"):
                return
    con.print(
        f"\n  [bold {_ACCENT}]Pronto![/bold {_ACCENT}] Comece com "
        f"[bold {_PURPLE}]bauer agent[/bold {_PURPLE}].\n"
    )
