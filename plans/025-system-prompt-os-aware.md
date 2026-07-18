# Plan 025: System prompt descreve o SO real (não "Windows" cravado) em servidores Linux

> **Executor instructions**: Follow step by step; run every verification and
> confirm before proceeding. On any STOP condition, stop and report. Update the
> status row in `plans/README.md` when done.
>
> **Drift check (run first)**: `git diff --stat ced7dc2..HEAD -- bauer/agent.py`
> Mismatch vs "Current state" → STOP.

## Status

- **Priority**: P2
- **Effort**: S
- **Risk**: LOW
- **Depends on**: none (toca a mesma função do plano 023 — se ambos forem executados, faça 023 antes e re-aplique o excerto)
- **Category**: bug
- **Planned at**: commit `ced7dc2`, 2026-07-18

## Why this matters

O `_build_system_prompt` (`bauer/agent.py:577-586`) crava no prompt que o agente **roda em Windows** — com `shell=False`, exemplos de path `C:/...`, e proibição de `dir`. Mas o Bauer roda também em servidores **Linux** (deploy real: Ubuntu + Ollama). Nesses casos o modelo recebe instruções erradas sobre o ambiente: pode evitar comandos válidos do Unix ou se confundir com paths. O prompt deve descrever o SO **real** da máquina onde o serve está rodando.

## Current state

Excerto (`bauer/agent.py`, dentro de `_build_system_prompt`, ~577-586):

```python
        "# CONSTRAINTS DO AMBIENTE (LEIA — evita erros recorrentes)\n"
        "- Voce roda em **Windows** com Python no venv. Subprocess usa `shell=False`.\n"
        "- TODAS as tools de arquivo (read_file, write_file, list_dir, etc) trabalham\n"
        "  em paths RELATIVOS ao workspace. Nunca passe paths absolutos do tipo\n"
        "  `C:/...` ou `/Users/...`. Use `.`, `subdir/arquivo.py`, etc.\n"
        "- `..` e permitido se o path resolvido ficar dentro do workspace. `../fora` e BLOQUEADO.\n"
        "- Em run_command NAO use: `dir` (use tool list_dir), `cat`/`head`/`tail` (use read_file).\n"
        "- `pip install`, `npm install`, `git push`, `rm` precisam `confirm: true` no args.\n"
        "- Antes de `python script.py`, LEIA o script para descobrir se exige argumentos.\n"
```

O módulo pode detectar o SO com `platform.system()` (retorna `"Windows"`, `"Linux"`, `"Darwin"`). Confirme se `platform` já está importado em `bauer/agent.py`; se não, adicione `import platform` no topo (junto aos demais imports).

## Commands you will need

| Purpose | Command | Expected |
|---------|---------|----------|
| Sintaxe | `python -c "import ast; ast.parse(open('bauer/agent.py',encoding='utf-8').read())"` | sem erro |
| Testes  | `python -m pytest tests/test_agent_system_prompt.py -q` | passam |
| Lint    | `python -m ruff check bauer/agent.py` | `All checks passed!` |

## Scope

**In scope**: `bauer/agent.py` (a linha de constraints do ambiente), `tests/test_agent_system_prompt.py` (adicionar caso — mesmo arquivo do plano 023, ou criar se 023 não rodou).

**Out of scope**: qualquer mudança de comportamento das tools; só o **texto** do prompt muda.

## Git workflow

- Branch: `advisor/025-system-prompt-os-aware`
- Conventional commit (ex.: `fix(agent): system prompt reflete o SO real, não Windows fixo`)

## Steps

### Step 1: Detectar o SO e montar a linha de constraint apropriada

Antes do `return`, calcule:

```python
    import platform
    _os = platform.system()
    if _os == "Windows":
        _env_line = "- Voce roda em **Windows** com Python no venv. Subprocess usa `shell=False`.\n"
        _no_cmds = "- Em run_command NAO use: `dir` (use tool list_dir), `cat`/`head`/`tail` (use read_file).\n"
        _abs_example = "`C:/...` ou `/Users/...`"
    else:
        _env_line = f"- Voce roda em **{_os}** (Unix) com Python no venv. Subprocess usa `shell=False`.\n"
        _no_cmds = "- Em run_command prefira as tools: `ls` (use list_dir), `cat`/`head`/`tail` (use read_file).\n"
        _abs_example = "`/root/...` ou `/home/...`"
```

Substitua a linha fixa `"- Voce roda em **Windows**..."` por `_env_line`, a linha do `dir` por `_no_cmds`, e o exemplo de path absoluto por `{_abs_example}` na string correspondente. Mantenha o resto igual.

**Verify**: `python -c "import ast; ast.parse(open('bauer/agent.py',encoding='utf-8').read())"` → sem erro

### Step 2: Teste

Adicione a `tests/test_agent_system_prompt.py` (ou crie): um teste que faz monkeypatch de `platform.system` para `"Linux"` e verifica que o prompt **não** contém `"roda em **Windows**"` e **contém** `"Unix"`. Outro para `"Windows"` mantendo o texto Windows.

**Verify**: `python -m pytest tests/test_agent_system_prompt.py -q` → passam

## Done criteria

- [ ] `platform.system()` decide a linha de ambiente do prompt
- [ ] Com SO Linux, o prompt não diz "roda em **Windows**"
- [ ] `python -m pytest tests/test_agent_system_prompt.py -q` passa
- [ ] `python -m ruff check bauer/agent.py` → `All checks passed!`
- [ ] `git status` sem arquivos fora de escopo
- [ ] Status atualizado em `plans/README.md`

## STOP conditions

- O bloco de constraints não bater com o excerto (drift).
- `platform` não puder ser importado (improvável — stdlib).

## Maintenance notes

- Se surgirem constraints específicas de macOS (Darwin) vs Linux, estenda o `if/elif`.
- Revisor: confira que os exemplos de path e os comandos proibidos ficaram coerentes com cada SO.
