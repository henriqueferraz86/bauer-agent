# Plan 013: Extrair os handlers de slash-command do `agent.py` para `bauer/agent_slash_commands.py`

> **Executor instructions**: Follow this plan step by step. Run every
> verification command and confirm the expected result before moving on. If
> anything in "STOP conditions" occurs, stop and report — do not improvise.
> When done, update this plan's status row in `plans/README.md`.
>
> **Drift check (run first)**: `git diff --stat 2c9d86f..HEAD -- bauer/agent.py`
> If `bauer/agent.py` changed since this plan was written, compare the
> "Current state" excerpts and line numbers against the live code before
> proceeding; on any mismatch, treat it as a STOP condition (line numbers below
> WILL shift and must be re-derived).

## Status

- **Priority**: P3
- **Effort**: M
- **Risk**: MED
- **Depends on**: none (mas rode DEPOIS que a suíte de testes estiver verde —
  ela é a rede de segurança deste refactor)
- **Category**: tech-debt
- **Planned at**: commit `2c9d86f`, 2026-07-06

## Why this matters

`bauer/agent.py` tem ~4900 linhas — é um god object, ~10x a mediana do repo, e
toca todo o loop do agente. Isso torna cada mudança no loop arriscada e o
arquivo difícil de navegar. Uma auditoria anterior adiou o refactor até
existirem characterization tests; eles existem agora (`test_agent.py`,
`test_agent_extended.py`, `test_agent_loop_elite.py`, `test_agent_coverage.py`).

Este plano faz a fatia de MENOR risco e alto valor: extrair os 8 handlers de
slash-command (`_handle_*_cmd`, ~822 linhas contíguas) para um módulo próprio.
Esses handlers são funções-folha — processam comandos como `/kanban`, `/spec`,
`/memory` digitados na sessão, usam imports lazy internos e NÃO participam do
loop de tokens/tools do agente. Movê-los tira ~800 linhas do god object sem
tocar na lógica crítica. **NÃO** é o refactor completo do `agent.py` — é o
primeiro corte seguro; os demais (loop, recovery, native tools) ficam para
planos futuros.

## Current state

- `bauer/agent.py` — loop do agente + handlers de comando + system prompt.
- Os 8 handlers a extrair são **contíguos**, das linhas **2081 a 2903**
  (a próxima função, `_resolve_planning_checkpoint`, começa na 2904):

| Função | Linha | Assinatura |
|--------|-------|------------|
| `_handle_kanban_cmd` | 2081 | `(console, workspace="workspace")` |
| `_handle_spec_cmd` | 2161 | `(user_input, console, workspace="workspace")` |
| `_handle_agent_cmd` | 2236 | `(user_input, console)` |
| `_handle_task_cmd` | 2341 | `(user_input, console, workspace="workspace")` |
| `_handle_dispatch_cmd` | 2440 | `(user_input, console, workspace="workspace")` |
| `_handle_ops_cmd` | 2637 | `(user_input, console, workspace="workspace")` |
| `_handle_memory_cmd` | 2717 | `(user_input, console)` |
| `_handle_project_cmd` | 2805 | `(console, workspace="workspace")` |

- Exemplo confirmando que são funções-folha com import lazy
  (`_handle_memory_cmd`, linha 2717):

```python
# bauer/agent.py:2717,2732-2738
def _handle_memory_cmd(user_input: str, console) -> None:
    ...
    try:
        from .memory_manager import MemoryManager
    except ImportError:
        console.print("[red]MemoryManager nao disponivel.[/red]")
        return
    mm = MemoryManager()
    ...
```

- **Despacho no loop principal** (linhas ~4511–4657 de `agent.py`): o loop
  chama esses handlers. Este bloco de despacho **PERMANECE** em `agent.py` — só
  as DEFINIÇÕES das funções se movem. Exemplo:

```python
# bauer/agent.py:4637-4644
        if user_input.lower() in _KANBAN_CMDS:
            _handle_kanban_cmd(console, active_workspace)
            continue
        ...
        if user_input.lower().startswith("/task "):
            _handle_task_cmd(user_input, console, active_workspace)
            continue
```

- **Constantes de comando** (`_SPEC_CMDS`, `_KANBAN_CMDS`, `_DISPATCH_CMDS`,
  `_OPS_CMDS`, `_PROJECT_CMDS`) estão nas linhas 51–56 de `agent.py`. Elas são
  usadas pelo bloco de despacho (que fica) — mantenha-as em `agent.py`. Se
  algum handler as referenciar internamente, importe-as no novo módulo.

- **CRÍTICO — os testes importam os handlers direto de `bauer.agent`**:
  - `tests/test_agent.py`: `from bauer.agent import _handle_task_cmd`,
    `_handle_project_cmd`, `_handle_dispatch_cmd`
  - `tests/test_agent_coverage.py`: `from bauer.agent import ... _handle_spec_cmd ...`

  Portanto, após mover as funções, `agent.py` DEVE re-exportá-las
  (`from .agent_slash_commands import _handle_kanban_cmd, ...`) para que
  `from bauer.agent import _handle_*_cmd` continue funcionando. Se você não
  fizer isso, esses testes quebram no import.

### Convenções do repo a seguir
- Português nos comentários. Imports lazy dentro de função para quebrar ciclo
  (padrão já usado nos handlers — preserve-os como estão).
- Novo módulo segue o estilo dos demais em `bauer/` (docstring de módulo no
  topo explicando o propósito; `from __future__ import annotations`).

## Commands you will need

| Purpose        | Command                                                        | Expected |
|----------------|----------------------------------------------------------------|----------|
| Suíte completa | `.venv/Scripts/python.exe -m pytest tests/ -q`                 | all pass |
| Testes agent   | `.venv/Scripts/python.exe -m pytest tests/ -k agent -q`        | all pass |
| Import agent   | `.venv/Scripts/python.exe -c "import bauer.agent"`             | exit 0   |
| Re-export OK   | `.venv/Scripts/python.exe -c "from bauer.agent import _handle_task_cmd, _handle_spec_cmd, _handle_project_cmd, _handle_dispatch_cmd, _handle_kanban_cmd, _handle_agent_cmd, _handle_ops_cmd, _handle_memory_cmd; print('ok')"` | imprime `ok` |
| Contagem linhas| `wc -l bauer/agent.py`                                          | reduzida ~800 |

## Scope

**In scope**:
- `bauer/agent.py` (remover as 8 defs; adicionar o import de re-export)
- `bauer/agent_slash_commands.py` (criar — recebe as 8 defs + helpers exclusivos)

**Out of scope** (NÃO tocar, mesmo parecendo relacionado):
- O loop do agente: `run_one_turn`, `_collect_response`, `_recover_empty_response`,
  `_collect_with_fallback`, `_run_native_tool_turn`, `_native_turn_interactive`,
  `run_one_turn_with_fallback`. Estes são o coração do loop e ficam para planos
  futuros.
- O bloco de DESPACHO no loop principal (linhas ~4511–4657) — ele permanece em
  `agent.py`; só as definições das funções migram.
- As constantes `_*_CMDS` (linhas 51–56) — permanecem em `agent.py`.
- Qualquer mudança de COMPORTAMENTO dos handlers — este é um move puro
  (cut/paste + import), zero mudança de lógica.
- `_resolve_planning_checkpoint`, `_seed_kanban_from_backlog`,
  `_maybe_planning_checkpoint` e outros helpers de App Factory (linha 2904+) —
  NÃO são handlers de slash-command; ficam.

## Git workflow

- Branch: `advisor/013-agent-extract-slash-commands`
- Commit style: conventional commits. Ex.:
  `refactor(agent): extrai handlers de slash-command para agent_slash_commands.py`
- NÃO faça push nem PR sem instrução.

## Steps

### Step 1: Mapear as dependências das 8 funções

Antes de mover, descubra o que cada handler referencia que é definido no
nível de módulo de `agent.py` (funções/constantes fora das próprias 8):

```
sed -n '2081,2903p' bauer/agent.py | grep -oE "_[a-zA-Z_]+" | sort -u
```

Para cada nome que apareça, verifique se é: (a) helper LOCAL definido dentro de
um handler (move junto, nada a fazer), (b) uma das 8 funções (referência
cruzada — resolvida quando todas se mudam juntas), (c) constante `_*_CMDS`
(importar no novo módulo), ou (d) OUTRO helper module-level de `agent.py`
usado só pelos handlers (mover junto) ou também pelo resto (importar).

Anote a lista de nomes classe (c)/(d). Se algum handler referenciar um helper
que TAMBÉM é usado pelo loop do agente (fora dos handlers), você vai IMPORTÁ-LO
no novo módulo (`from .agent import <nome>`) — mas cuidado com ciclo (ver
STOP conditions).

**Verify**: você tem uma lista escrita dos nomes (c)/(d). (Sem comando; é
trabalho de análise. Não pule.)

### Step 2: Criar `bauer/agent_slash_commands.py` e mover as 8 funções

1. Crie o arquivo com cabeçalho:

```python
"""Handlers dos slash-commands da sessão do agente (/kanban, /spec, /agent,
/task, /dispatch, /ops, /memory, /project).

Extraídos de agent.py (god object) — são funções-folha que processam comandos
digitados na sessão; NÃO participam do loop de tokens/tools. O bloco de
despacho que decide qual handler chamar permanece em agent.py.
"""

from __future__ import annotations

from typing import Any
```

2. **Recorte** (cut, não copy) as linhas 2081–2903 de `agent.py` e cole no novo
   módulo. Inclua os helpers locais que estejam DENTRO desse range.
3. Se o Step 1 identificou constantes `_*_CMDS` ou helpers module-level usados
   pelos handlers, adicione os imports necessários no topo do novo módulo
   (ex.: `from .agent import _SPEC_CMDS` — mas veja o aviso de ciclo abaixo).

**Verify**: `.venv/Scripts/python.exe -c "import bauer.agent_slash_commands"` →
exit 0 (pode falhar se houver ciclo — nesse caso vá para a STOP condition de
ciclo).

### Step 3: Re-exportar de `agent.py`

No lugar de onde as funções estavam (ou junto aos outros imports de `agent.py`),
adicione:

```python
from .agent_slash_commands import (
    _handle_agent_cmd,
    _handle_dispatch_cmd,
    _handle_kanban_cmd,
    _handle_memory_cmd,
    _handle_ops_cmd,
    _handle_project_cmd,
    _handle_spec_cmd,
    _handle_task_cmd,
)
```

Isso preserva `from bauer.agent import _handle_*_cmd` (os testes dependem
disso) e mantém o bloco de despacho no loop funcionando sem mudanças.

**Verify**: o comando "Re-export OK" da tabela imprime `ok`.

### Step 4: Rodar a suíte completa

**Verify**: `.venv/Scripts/python.exe -m pytest tests/ -q` → all pass (mesmo
conjunto de testes que passava antes; nenhum novo, nenhum quebrado).

## Test plan

Este é um refactor puro (move sem mudar comportamento) — a rede de segurança
são os testes EXISTENTES, que devem continuar 100% verdes:
- `tests/test_agent.py`, `tests/test_agent_coverage.py` importam os handlers
  direto — provam que o re-export funciona.
- `tests/test_agent_extended.py`, `tests/test_agent_loop_elite.py` cobrem o loop
  — provam que o despacho e o loop seguem intactos.
- NÃO é necessário escrever testes novos. Se quiser, adicione um teste trivial
  em `tests/test_agent_slash_commands.py` que faça
  `from bauer.agent_slash_commands import _handle_memory_cmd` e verifique que é
  callable — opcional.
- Verificação final: `.venv/Scripts/python.exe -m pytest tests/ -q` → todos os
  testes que passavam antes continuam passando.

## Done criteria

TODAS devem valer:

- [ ] `bauer/agent_slash_commands.py` existe e contém as 8 funções `_handle_*_cmd`
- [ ] `grep -c "^def _handle_.*_cmd" bauer/agent.py` retorna `0` (defs saíram de agent.py)
- [ ] Comando "Re-export OK" imprime `ok` (`from bauer.agent import _handle_*_cmd` funciona)
- [ ] `.venv/Scripts/python.exe -m pytest tests/ -q` → todos passam (paridade com antes)
- [ ] `wc -l bauer/agent.py` mostra redução de ~800 linhas vs. o original (~4900 → ~4100)
- [ ] Nenhum comportamento alterado (é move puro): nenhum teste novo é necessário
      para passar; nenhum teste existente foi editado para acomodar mudança de
      lógica (só imports, se preciso)
- [ ] Apenas `bauer/agent.py` e `bauer/agent_slash_commands.py` modificados/criados (`git status`)
- [ ] Status atualizado em `plans/README.md`

## STOP conditions

Pare e reporte (não improvise) se:

- O drift check acusar mudança em `agent.py` — os números de linha 2081–2903
  e 4511–4657 vão ter mudado; re-derive-os antes de qualquer corte, e se a
  estrutura divergir do descrito, reporte.
- **Ciclo de import**: importar um helper de `agent.py` no novo módulo criar um
  ciclo (`ImportError` / `partially initialized module`). Nesse caso, NÃO force
  — reporte. A solução (mover o helper compartilhado para um terceiro módulo de
  utils) é uma decisão de escopo maior que o operador deve aprovar.
- Um handler acessar estado do LOOP do agente (variáveis locais de
  `run_agent_session`, não passadas por parâmetro) — isso significaria que ele
  não é uma função-folha e a extração é mais arriscada; reporte.
- A suíte de testes falhar após o move e a causa NÃO for um import trivial a
  corrigir (falha de lógica indica que algo além de mover aconteceu) — reverta
  e reporte.
- `grep -c "^def _handle_.*_cmd" bauer/agent.py` não zerar (alguma def não foi
  removida) ou algum teste que importa os handlers falhar no import.

## Maintenance notes

- Este é o PRIMEIRO corte do god object `agent.py`. Próximos candidatos
  (planos futuros, não incluídos): (1) extrair o system-prompt building
  (`_build_system_prompt`, `_specialists_section`); (2) extrair o pipeline de
  loop/recovery (`_collect_response`, `_recover_empty_response`,
  `_collect_with_fallback`); (3) App Factory checkpoint helpers. Cada um é seu
  próprio plano com sua própria rede de teste.
- O reviewer deve conferir que é um MOVE PURO: `git diff` do conteúdo das
  funções movidas deve ser essencialmente "linhas removidas de agent.py,
  idênticas adicionadas em agent_slash_commands.py" — nenhuma edição de lógica
  no meio.
- Se o "Mapa do código" do `CLAUDE.md` (plano 012) já existir, atualize-o para
  mencionar o novo módulo.
