"""Wizard interativo para criação de specs (spec-driven development).

Fluxo manual : id → title → purpose → behavior → interface → ACs → linked files → salvar.
Fluxo auto   : título + descrição → LLM gera spec → preview YAML → confirmar/editar/cancelar.
"""

from __future__ import annotations

import json
import re

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm, Prompt
from rich.rule import Rule
from rich.syntax import Syntax

from .spec_manager import Spec, SpecManager

console = Console(highlight=False)

# ─── prompt para geração automática de spec ─────────────────────────────────

_AUTO_SPEC_PROMPT = """\
Você é um especialista em spec-driven development. Gere um spec técnico para a tarefa abaixo.

Título: {title}
Descrição: {description}

Retorne APENAS um JSON válido com este formato (sem texto adicional, sem markdown):
{{
  "purpose": "1-3 frases descrevendo o que faz e por que existe",
  "behavior": [
    "Regra de comportamento que a implementação DEVE respeitar",
    "Regra 2",
    "Regra 3"
  ],
  "acceptance_criteria": [
    "Given <contexto>, When <ação>, Then <resultado esperado>",
    "AC 2",
    "AC 3"
  ],
  "interface": {{
    "inputs": [
      {{"name": "param", "type": "str", "description": "descrição", "required": true}}
    ],
    "outputs": [
      {{"name": "resultado", "type": "str", "description": "descrição"}}
    ]
  }}
}}
"""


# ─── helpers ────────────────────────────────────────────────────────────────


def _header(title: str, subtitle: str = "") -> None:
    console.print()
    console.print(Rule(f"[bold cyan]{title}[/bold cyan]"))
    if subtitle:
        console.print(f"[dim]{subtitle}[/dim]")
    console.print()


def _ask(prompt: str, default: str = "") -> str:
    return Prompt.ask(f"[bold]{prompt}[/bold]", default=default).strip()


def _collect_list(prompt: str, hint: str = "") -> list[str]:
    """Coleta lista de strings — uma por linha, linha vazia termina."""
    if hint:
        console.print(f"[dim]{hint}[/dim]")
    console.print("[dim](Enter vazio para terminar)[/dim]")
    items: list[str] = []
    while True:
        line = Prompt.ask(f"  [bold]{prompt} {len(items)+1}[/bold]", default="").strip()
        if not line:
            break
        items.append(line)
    return items


# ─── Auto-geração via LLM ────────────────────────────────────────────────────


def _slugify(text: str) -> str:
    """Converte texto em slug válido para ID de spec."""
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug[:40] if slug else "meu-spec"


def _call_model_for_spec(title: str, description: str) -> dict | None:
    """Chama o modelo configurado para gerar campos do spec. Retorna dict ou None."""
    try:
        from .config_loader import load_config
        from .ollama_client import OllamaClient

        cfg = load_config()
        base_url = cfg.get("base_url", "http://localhost:11434")
        model = cfg.get("model", "phi4-mini")
        client = OllamaClient(base_url=base_url)

        prompt = _AUTO_SPEC_PROMPT.format(
            title=title,
            description=description.strip() or title,
        )
        messages = [
            {
                "role": "system",
                "content": "Você é um especialista em spec-driven development. "
                           "Responda APENAS com JSON válido, sem texto adicional.",
            },
            {"role": "user", "content": prompt},
        ]
        reply = "".join(client.chat_stream(model, messages))
    except Exception as exc:
        console.print(f"[red]Erro ao chamar modelo: {exc}[/red]")
        return None

    # Tenta fazer parse do JSON
    for attempt in (
        lambda t: json.loads(t.strip()),
        lambda t: json.loads(re.search(r"```(?:json)?\s*(\{.*?\})\s*```", t, re.DOTALL).group(1)),  # type: ignore[union-attr]
        lambda t: json.loads(re.search(r"\{[\s\S]*\}", t).group()),  # type: ignore[union-attr]
    ):
        try:
            return attempt(reply)
        except Exception:
            continue

    console.print("[red]Modelo não retornou JSON válido.[/red]")
    return None


def wizard_auto_spec(
    title: str,
    description: str,
    manager: SpecManager,
) -> Spec | None:
    """Gera um spec automaticamente via LLM e apresenta para confirmação.

    Fluxo:
      1. Chama o modelo com título + descrição da task
      2. Faz parse do JSON retornado
      3. Mostra preview YAML
      4. Usuário escolhe: salvar | editar (abre wizard manual) | cancelar
    """
    console.print()
    console.print("[bold cyan]Gerando spec automaticamente...[/bold cyan]")

    # Gera ID a partir do título
    spec_id = _slugify(title)
    if not Spec.valid_id(spec_id):
        spec_id = f"spec-{spec_id[:35]}"

    # Resolve conflito de ID
    if manager.get(spec_id):
        suffix = 2
        base = spec_id[:35]
        while manager.get(f"{base}-{suffix}"):
            suffix += 1
        spec_id = f"{base}-{suffix}"

    # Chama modelo
    with console.status("[dim]Consultando modelo...[/dim]"):
        data = _call_model_for_spec(title, description)

    if not data:
        console.print("[yellow]Abrindo wizard manual como alternativa.[/yellow]")
        return wizard_create_spec(manager)

    # Monta Spec com os dados gerados
    spec = Spec(
        id=spec_id,
        title=title,
        purpose=data.get("purpose", ""),
        behavior=data.get("behavior", []),
        interface=data.get("interface", {}),
        acceptance_criteria=data.get("acceptance_criteria", []),
        linked_files=[],
        status="draft",
    )

    # Preview YAML
    import yaml
    preview_yaml = yaml.dump(
        spec.to_dict(),
        allow_unicode=True,
        sort_keys=False,
        default_flow_style=False,
        width=80,
    )
    console.print()
    console.print("[bold]Spec gerado:[/bold]")
    console.print(Syntax(preview_yaml, "yaml", theme="monokai"))

    # Confirmação
    console.print()
    action = Prompt.ask(
        "[bold]O que fazer?[/bold]  [dim]salvar / editar (wizard manual) / cancelar[/dim]",
        choices=["salvar", "editar", "cancelar"],
        default="salvar",
    )

    if action == "cancelar":
        console.print("[dim]Spec não criado.[/dim]")
        return None

    if action == "editar":
        console.print("[dim]Abrindo wizard manual — você pode ajustar todos os campos.[/dim]")
        return wizard_create_spec(manager)

    # Salva
    path = manager.save(spec)
    console.print(
        Panel(
            f"[green]✓[/green] Spec [cyan]{spec_id}[/cyan] salvo em [dim]{path}[/dim]\n"
            f"[dim]Status: draft — altere com: bauer spec status {spec_id} approved[/dim]",
            title="[bold green]Spec criado[/bold green]",
            border_style="green",
        )
    )
    return spec


# ─── Wizard principal ────────────────────────────────────────────────────────


def wizard_create_spec(manager: SpecManager) -> Spec | None:
    """Entrevista passo-a-passo para criar um spec. Retorna Spec ou None."""

    _header(
        "Novo Spec",
        "Spec-Driven Development — defina o CONTRATO antes do codigo.",
    )

    # ── 1. ID ─────────────────────────────────────────────────────────
    console.print("[bold]1/7 — ID do spec[/bold] [dim](slug: letras, números, hífens)[/dim]")
    console.print("[dim]Exemplos: orchestrator-dag, agent-registry, spec-manager[/dim]")
    while True:
        spec_id = _ask("ID", default="meu-feature")
        if not Spec.valid_id(spec_id):
            console.print("[red]ID inválido.[/red] Use letras minúsculas, números e hífens (2-51 chars).")
            continue
        if manager.get(spec_id):
            overwrite = Confirm.ask(
                f"[yellow]Spec '{spec_id}' já existe. Sobrescrever?[/yellow]", default=False
            )
            if not overwrite:
                continue
        break
    console.print()

    # ── 2. Título ──────────────────────────────────────────────────────
    console.print("[bold]2/7 — Título curto[/bold]")
    title = _ask("Título", default=spec_id.replace("-", " ").title())
    console.print()

    # ── 3. Purpose ────────────────────────────────────────────────────
    console.print("[bold]3/7 — Purpose[/bold] [dim](o que faz e por que existe — 1-3 frases)[/dim]")
    purpose = _ask("Purpose")
    console.print()

    # ── 4. Behavior ───────────────────────────────────────────────────
    console.print("[bold]4/7 — Behavior[/bold] [dim](regras que a implementação DEVE respeitar)[/dim]")
    behavior = _collect_list(
        "Regra",
        "Ex: 'O contexto nunca excede o limite configurado.'"
        " | 'Erros incluem causa, valor esperado e ação sugerida.'",
    )
    console.print()

    # ── 5. Interface ─────────────────────────────────────────────────
    console.print("[bold]5/7 — Interface[/bold] [dim](inputs e outputs principais)[/dim]")
    interface: dict = {}

    add_inputs = Confirm.ask("Definir inputs?", default=True)
    if add_inputs:
        inputs: list[dict] = []
        console.print("[dim]Para cada input: nome, tipo e descrição (linha vazia para parar)[/dim]")
        while True:
            name = _ask("  Input nome", default="").strip()
            if not name:
                break
            itype = _ask("  Input tipo", default="str")
            idesc = _ask("  Input descrição", default="")
            req = Confirm.ask("  Obrigatório?", default=True)
            inputs.append({"name": name, "type": itype, "description": idesc, "required": req})
        if inputs:
            interface["inputs"] = inputs

    add_outputs = Confirm.ask("Definir outputs?", default=True)
    if add_outputs:
        outputs: list[dict] = []
        console.print("[dim]Para cada output: nome, tipo e descrição (linha vazia para parar)[/dim]")
        while True:
            name = _ask("  Output nome", default="").strip()
            if not name:
                break
            otype = _ask("  Output tipo", default="str")
            odesc = _ask("  Output descrição", default="")
            outputs.append({"name": name, "type": otype, "description": odesc})
        if outputs:
            interface["outputs"] = outputs
    console.print()

    # ── 6. Acceptance Criteria ─────────────────────────────────────────
    console.print("[bold]6/7 — Acceptance Criteria[/bold] [dim](verificáveis — formato Given/When/Then ou livre)[/dim]")
    acs = _collect_list(
        "AC",
        "Ex: 'Given uma tarefa com 3 passos, when executa, then salva progresso após cada onda.'",
    )
    console.print()

    # ── 7. Linked files ───────────────────────────────────────────────
    console.print("[bold]7/7 — Arquivos vinculados[/bold] [dim](opcional — impl + testes)[/dim]")
    linked = _collect_list("Arquivo", "Ex: bauer/orchestrator.py | tests/test_orchestrator.py")
    console.print()

    # ── Status inicial ────────────────────────────────────────────────
    from rich.table import Table
    status_table = Table(show_lines=False, box=None)
    status_table.add_column("#", style="dim", width=3)
    status_table.add_column("status", style="cyan")
    status_table.add_column("significado")
    for i, (s, d) in enumerate([
        ("draft",       "rascunho — ainda sendo definido"),
        ("review",      "em revisão — aguardando aprovação"),
        ("approved",    "aprovado — pronto para implementar"),
        ("implemented", "implementado — código entregue"),
    ], 1):
        status_table.add_row(str(i), s, d)
    console.print(status_table)
    status_raw = Prompt.ask("[bold]Status inicial[/bold]", default="1").strip()
    status_map = {"1": "draft", "2": "review", "3": "approved", "4": "implemented"}
    status = status_map.get(status_raw, status_raw if status_raw in status_map.values() else "draft")
    console.print()

    # ── Preview YAML ──────────────────────────────────────────────────
    spec = Spec(
        id=spec_id,
        title=title,
        purpose=purpose,
        behavior=behavior,
        interface=interface,
        acceptance_criteria=acs,
        linked_files=linked,
        status=status,
    )
    import yaml
    preview_yaml = yaml.dump(spec.to_dict(), allow_unicode=True, sort_keys=False, default_flow_style=False, width=80)
    console.print(Syntax(preview_yaml, "yaml", theme="monokai"))

    if not Confirm.ask("[bold]Salvar este spec?[/bold]", default=True):
        console.print("[dim]Cancelado.[/dim]")
        return None

    path = manager.save(spec)

    # ── Oferecer criação de task vinculada (SDD: spec → task) ─────────
    task_created = False
    console.print()
    if Confirm.ask("Criar uma tarefa em TASKS.md vinculada a este spec?", default=True):
        try:
            from .workspace_manager import WorkspaceManager
            _wm = WorkspaceManager()
            _task_title = f"Implementar: {title}"
            _task_desc = (
                f"Spec: {spec_id}\n"
                f"Purpose: {purpose.split(chr(10))[0]}\n"
                f"ACs: {len(acs)} critério(s) definido(s)"
            )
            _task = _wm.add_task(_task_title, _task_desc, spec_id=spec_id)
            console.print(
                f"[green]✓[/green] Tarefa [cyan]{_task.id}[/cyan] criada em TASKS.md "
                f"[dim](spec: {spec_id})[/dim]"
            )
            task_created = True
        except Exception as exc:
            console.print(f"[dim]Task não criada (workspace não inicializado): {exc}[/dim]")
            console.print("[dim]Para criar manualmente: [bold]bauer task add[/bold][/dim]")

    console.print(Panel(
        f"[green]✓[/green] Spec [cyan]{spec_id}[/cyan] salvo em [dim]{path}[/dim]\n"
        + ("[green]✓[/green] Tarefa vinculada criada em TASKS.md\n" if task_created else "")
        + f"\n[dim]Fluxo SDD:[/dim]\n"
        f"  1. Escreva testes alinhados aos ACs acima\n"
        f"  2. Implemente até os testes passarem\n"
        f"  3. [bold]bauer spec status {spec_id} implemented[/bold]\n"
        f"  4. O agente lerá este spec como contrato do projeto",
        title="[bold green]Spec criado[/bold green]",
        border_style="green",
    ))
    return spec
