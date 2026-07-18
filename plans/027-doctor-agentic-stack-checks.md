# Plan 027: `bauer doctor` valida a stack agêntica (tools do factory expostas, tool mode efetivo, projeto governado)

> **Executor instructions**: Follow step by step; run every verification and
> confirm before proceeding. On any STOP condition, stop and report. Update the
> status row in `plans/README.md` when done.
>
> **Drift check (run first)**: `git diff --stat ced7dc2..HEAD -- bauer/cli.py`
> Mismatch vs "Current state" → STOP.

## Status

- **Priority**: P2
- **Effort**: S-M
- **Risk**: LOW
- **Depends on**: none (complementa 024 e 026, mas independe deles)
- **Category**: dx
- **Planned at**: commit `ced7dc2`, 2026-07-18

## Why this matters

Todos os problemas de tool calling e App Factory de um deploy real só foram descobertos cavando `GET /events`, `GET /status` e `ollama show --modelfile` na mão. O `bauer doctor` (já turbinado com GPU/config/tools-vs-contexto) é o lugar natural para tornar isso auto-diagnosticável. Este plano adiciona ao doctor três checagens da stack agêntica, cada uma com nota acionável: (1) as tools do App Factory estão realmente expostas ao modelo? (2) o tool mode efetivo é `native` ou `bridge`? (3) há projeto governado e o gate está coerente?

## Current state

O comando `doctor` está em `bauer/cli.py` (função `def doctor(...)`, ~linha 236). Ele já:
- monta uma tabela principal (`Status`, `Config`, `Ollama`, `Aceleração`, `Modelo`, `Contexto`, `Tool mode`, `RAM`, `Profile`);
- tem um painel "Web search";
- emite uma lista de "Notas" (`notes`), incluindo um aviso de tools-vs-contexto quando `tool_allowlist` está vazio e o contexto é pequeno.

O `report.state.tool_mode` já existe (de `preflight`). O tool_allowlist **efetivo** vem de `bauer/commands/_runtime.py::_effective_tool_allowlist(cfg)`. As tools do factory são `app_factory_init`/`app_factory_status`. O estado do factory vem de `bauer/app_factory.py` (`get_active_project`, `is_governed`, `current_gate`, `missing_planning_docs`).

Confirme o trecho exato das "Notas" lendo `bauer/cli.py` em torno de onde `notes` é montado (procure `notes = list(report.findings)`).

## Commands you will need

| Purpose | Command | Expected |
|---------|---------|----------|
| Sintaxe | `python -c "import ast; ast.parse(open('bauer/cli.py',encoding='utf-8').read())"` | sem erro |
| Doctor  | `python -m bauer.cli doctor --config <cfg> --models <models> --state-file <tmp>` | roda sem crash (mesmo Ollama offline) |
| Lint    | `python -m ruff check bauer/cli.py` | (erro pré-existente na ~linha 515 sobre `lstrip` é aceitável — não introduza novos) |

Para o doctor rodar isolado, use um config/models mínimos (veja como `tests`/a sessão anterior geraram: `model.provider: ollama`, um `models.yaml` com uma entrada válida).

## Scope

**In scope**: `bauer/cli.py` (função `doctor`), opcionalmente um helper em `bauer/app_factory.py` se precisar de um resumo pronto.

**Out of scope**: mudar a resolução de allowlist (`_effective_tool_allowlist`) ou o preflight — o doctor só **lê e reporta**.

## Git workflow

- Branch: `advisor/027-doctor-agentic-checks`
- Conventional commit (ex.: `feat(doctor): checagens da stack agêntica (factory, tool mode, gate)`)

## Steps

### Step 1: Nota — tools do App Factory expostas?

No bloco de `notes` do doctor, calcule o allowlist efetivo e verifique se as tools do factory sobreviveram:

```python
    from .commands._runtime import _effective_tool_allowlist
    _eff = _effective_tool_allowlist(cfg)  # None = todas as tools
    if _eff is not None and not ({"app_factory_init", "app_factory_status"} <= set(_eff)):
        notes.append(
            "App Factory: as tools app_factory_init/status NÃO estão expostas ao modelo "
            "(cortadas pelo tool_allowlist). Para conduzir Spec-Driven Development pelo chat, "
            "inclua-as em tools.tool_allowlist."
        )
```

**Verify**: `python -c "import ast; ast.parse(open('bauer/cli.py',encoding='utf-8').read())"` → sem erro

### Step 2: Nota — tool mode efetivo

Emita uma nota quando o tool mode for `bridge` (menos confiável para tarefas agênticas), reaproveitando `report.state.tool_mode`:

```python
    if getattr(report.state, "tool_mode", "") == "bridge":
        notes.append(
            "Tool mode = bridge (tool calls por prompt). Menos confiável em modelos locais "
            "para tarefas multi-passo. Prefira um modelo com supports_tools: true no models.yaml "
            "(ex.: qwen2.5:7b, qwen3-coder:30b) para tool calling nativo."
        )
```

**Verify**: idem sintaxe ok.

### Step 3: Painel/nota — projeto governado e gate

Adicione um pequeno bloco best-effort que reporta o projeto App Factory ativo e o gate. Precisa da `workspace` do doctor — se o `doctor` não tiver `workspace` no escopo, derive do config: `Path(cfg.agent.workspace)` (best-effort, não crash):

```python
    try:
        from . import app_factory as _af
        from pathlib import Path as _P
        _ws = _P(getattr(cfg.agent, "workspace", ".") or ".")
        _proj = _af.get_active_project(_ws)
        if _proj is not None and _af.is_governed(_proj):
            _g = _af.current_gate(_proj)
            _missing = _af.missing_planning_docs(_proj)
            _line = f"App Factory ativo: {_proj.name} | gate {_g.slug if _g else '?'}"
            if _missing:
                _line += f" | docs pendentes: {', '.join(_missing)}"
            notes.append(_line)
    except Exception:
        pass
```

**Verify**: `python -m bauer.cli doctor --config <cfg> --models <models> --state-file <tmp>` → roda sem crash e, num projeto não-governado, simplesmente não mostra a linha do factory.

### Step 4: Teste

Se houver teste de doctor, estenda-o; senão, um teste leve que chama a função `doctor` via `CliRunner` com um config/models mínimo e verifica `exit_code == 0` e que, com tool_allowlist cortando o factory, a nota aparece no output. Padrão: `tests/test_serve_service_restart.py` (uso de `CliRunner`).

**Verify**: `python -m pytest tests/test_doctor_agentic.py -q` → passa (crie o arquivo)

## Done criteria

- [ ] Doctor emite nota quando as tools do factory estão cortadas do allowlist efetivo
- [ ] Doctor emite nota quando `tool_mode == "bridge"`
- [ ] Doctor mostra projeto governado + gate quando existe (e nada quando não existe)
- [ ] `python -m bauer.cli doctor ...` roda sem crash com Ollama offline
- [ ] `python -m ruff check bauer/cli.py` não introduz novos erros (o de `lstrip` ~515 é pré-existente)
- [ ] `git status` sem arquivos fora de escopo
- [ ] Status atualizado em `plans/README.md`

## STOP conditions

- A estrutura de `notes`/`report.state` divergir do descrito (drift em `cli.py`).
- `cfg.agent.workspace` não existir (confirme o campo em `config_loader.py`).

## Maintenance notes

- Se o plano 026 (detecção via Ollama) landar, a nota de tool mode pode incorporar o resultado da detecção real.
- Revisor: as três checagens devem ser best-effort — o doctor nunca pode crashar por causa delas.
