# Plan 023: System prompt deixa de ensinar tool-call-como-JSON quando o modo é `native`

> **Executor instructions**: Follow this plan step by step. Run every
> verification command and confirm the expected result before moving to the
> next step. If anything in the "STOP conditions" section occurs, stop and
> report — do not improvise. When done, update the status row for this plan
> in `plans/README.md`.
>
> **Drift check (run first)**: `git diff --stat ced7dc2..HEAD -- bauer/agent.py bauer/commands/serve_cmd.py`
> If any in-scope file changed since this plan was written, compare the
> "Current state" excerpts against the live code before proceeding; on a
> mismatch, treat it as a STOP condition.

## Status

- **Priority**: P1
- **Effort**: M
- **Risk**: MED
- **Depends on**: none
- **Category**: bug
- **Planned at**: commit `ced7dc2`, 2026-07-18

## Why this matters

O `_build_system_prompt` instrui o modelo a responder tool calls **como JSON de texto** (`{"action": "NOME", "args": {...}}`). Esse é o protocolo do **tool bridge**. Mas o mesmo system prompt é usado quando o Bauer roda em modo **`native`** (function calling nativo do Ollama), onde as tools são passadas pela API e o modelo deve emitir `tool_calls` estruturados — não JSON no corpo do texto.

O resultado observado num deploy real: modelos fortes (qwen3-coder:30b) ignoram a instrução e usam nativo corretamente; modelos menores (qwen2.5:7b) **obedecem** a instrução e emitem o JSON como texto — que o caminho nativo do Ollama **não executa**. Sintoma: "0 tools", arquivos não criados, o modelo "descrevendo" a ação em vez de executá-la. Corrigir isso — não ensinar o formato JSON quando `native` — remove a maior fonte de inconsistência de tool calling com modelos locais.

## Current state

- `bauer/agent.py` — função `_build_system_prompt(router)` (linha 540) monta o system prompt. É **compartilhada** entre o CLI (`bauer agent`) e o serve (`bauer serve`).
- `bauer/commands/serve_cmd.py:93-94` — o serve chama:
  ```python
  from ..agent import _build_system_prompt
  system_prompt = _build_system_prompt(router)
  ```

Excerto do trecho problemático (`bauer/agent.py`, dentro do return de `_build_system_prompt`, ~linhas 587-595):

```python
        "# FERRAMENTAS DISPONIVEIS\n"
        f"Voce pode usar estas ferramentas: {tool_names}\n"
        f"{tools_section}\n\n"
        "# QUANDO USAR FERRAMENTA\n"
        "Use UMA ferramenta SOMENTE se a pergunta exigir ler/escrever arquivos ou listar diretorios.\n"
        "Nesse caso, responda SOMENTE com o JSON abaixo (sem texto antes ou depois):\n"
        '{"action": "NOME_DA_TOOL", "args": {"parametro": "valor"}}\n\n'
        "# QUANDO NAO USAR FERRAMENTA (maioria dos casos)\n"
        "Para saudacoes, perguntas, explicacoes, codigo, matematica, conversas — responda em TEXTO PURO.\n\n"
```

O `tool_mode` (`"native"` ou `"bridge"`) é resolvido em `bauer/preflight.py:226-234` e fica em `RuntimeState.tool_mode`. No serve, o estado vem de `_get_or_run_state(...)` (variável `state` em `serve_cmd.py`), acessível como `state["tool_mode"]` (dict) — **confirme o nome exato do campo lendo o retorno de `_get_or_run_state` e a dataclass `RuntimeState` em `bauer/runtime_state.py` antes de usar**.

Convenção do repo: funções privadas com prefixo `_`, sem type stubs externos; testes em `tests/` com pytest. Veja um teste de CLI existente como padrão: `tests/test_serve_service_restart.py` (usa `CliRunner`).

## Commands you will need

| Purpose   | Command                                            | Expected on success |
|-----------|----------------------------------------------------|---------------------|
| Sintaxe   | `python -c "import ast; ast.parse(open('bauer/agent.py',encoding='utf-8').read())"` | sem erro |
| Testes    | `python -m pytest tests/test_agent_system_prompt.py -q` | todos passam |
| Lint      | `python -m ruff check bauer/agent.py bauer/commands/serve_cmd.py` | `All checks passed!` |

(Rode com `PYTHONHASHSEED=0` se a suíte completa for executada — o repo tem 2 flakes conhecidos de ordem de hash.)

## Scope

**In scope**:
- `bauer/agent.py` — assinatura e corpo de `_build_system_prompt`
- `bauer/commands/serve_cmd.py` — o call site do serve
- `tests/test_agent_system_prompt.py` (criar)

**Out of scope** (NÃO tocar):
- A lógica de execução de tools (`run_one_turn`, tool_router) — este plano só muda o **texto** do system prompt.
- O call site do CLI interativo (`bauer agent`), a menos que ele também chame `_build_system_prompt` sem `tool_mode` — nesse caso, passe `tool_mode` lá também usando o mesmo padrão, mas não altere mais nada.

## Git workflow

- Branch: `advisor/023-system-prompt-mode-aware`
- Conventional commits (ex.: `fix(agent): system prompt não ensina JSON-tool em modo native`)
- Não faça push nem abra PR salvo instrução explícita.

## Steps

### Step 1: Adicionar parâmetro `tool_mode` a `_build_system_prompt`

Mude a assinatura de `def _build_system_prompt(router: ToolRouter) -> str:` para:

```python
def _build_system_prompt(router: ToolRouter, tool_mode: str = "bridge") -> str:
```

O default `"bridge"` **preserva o comportamento atual** para qualquer chamador que não passe o parâmetro.

**Verify**: `python -c "import ast; ast.parse(open('bauer/agent.py',encoding='utf-8').read())"` → sem erro

### Step 2: Tornar a seção "# QUANDO USAR FERRAMENTA" condicional ao modo

Substitua o bloco fixo (excerto em "Current state") por uma variável construída antes do `return`, algo como:

```python
    if tool_mode == "native":
        tool_instructions = (
            "# FERRAMENTAS DISPONIVEIS\n"
            f"Voce tem estas ferramentas via function calling: {tool_names}\n"
            f"{tools_section}\n\n"
            "# COMO USAR FERRAMENTAS\n"
            "Quando a tarefa exigir ler/escrever arquivos, rodar comandos ou buscar na web,\n"
            "CHAME a ferramenta apropriada (function calling nativo). NAO escreva o comando\n"
            "em texto nem em JSON no corpo da resposta — invoque a tool de verdade.\n"
            "Para conversas, explicacoes e codigo em bloco, responda em TEXTO PURO.\n\n"
        )
    else:
        tool_instructions = (
            "# FERRAMENTAS DISPONIVEIS\n"
            f"Voce pode usar estas ferramentas: {tool_names}\n"
            f"{tools_section}\n\n"
            "# QUANDO USAR FERRAMENTA\n"
            "Use UMA ferramenta SOMENTE se a pergunta exigir ler/escrever arquivos ou listar diretorios.\n"
            "Nesse caso, responda SOMENTE com o JSON abaixo (sem texto antes ou depois):\n"
            '{"action": "NOME_DA_TOOL", "args": {"parametro": "valor"}}\n\n'
            "# QUANDO NAO USAR FERRAMENTA (maioria dos casos)\n"
            "Para saudacoes, perguntas, explicacoes, codigo, matematica, conversas — responda em TEXTO PURO.\n\n"
        )
```

E no `return`, troque as linhas fixas da seção de ferramentas por `f"{tool_instructions}"`. Mantenha todo o resto do prompt igual (as seções de AUTONOMIA, CONSTRAINTS, SHELL, etc.).

**Verify**: `python -c "import ast; ast.parse(open('bauer/agent.py',encoding='utf-8').read())"` → sem erro

### Step 3: Passar o `tool_mode` resolvido no call site do serve

Em `bauer/commands/serve_cmd.py`, onde está `system_prompt = _build_system_prompt(router)`, passe o modo vindo do estado. Primeiro **confirme o campo** lendo `bauer/runtime_state.py` (procure `tool_mode`). Depois:

```python
    _tmode = state.get("tool_mode", "bridge") if isinstance(state, dict) else getattr(state, "tool_mode", "bridge")
    system_prompt = _build_system_prompt(router, tool_mode=_tmode)
```

Se o CLI (`bauer agent`) também chamar `_build_system_prompt`, aplique o mesmo padrão lá (busque com `grep -rn "_build_system_prompt(" bauer/`).

**Verify**: `grep -rn "_build_system_prompt(" bauer/` → todo call site que tem `tool_mode` disponível o passa; nenhum quebra (o default cobre os demais)

### Step 4: Escrever testes

Crie `tests/test_agent_system_prompt.py` cobrindo:
- `native` NÃO contém a string `'{"action"'` nem `"responda SOMENTE com o JSON"`.
- `bridge` (default) CONTÉM `'{"action"'` (comportamento preservado).
- Ambos contêm os nomes das tools (`tool_names`).

Use um `ToolRouter` mínimo ou um stub com `available_tools()` e `tool_info()`. Veja `tests/test_desktop_api.py` para padrões de stub simples.

**Verify**: `python -m pytest tests/test_agent_system_prompt.py -q` → todos passam

## Test plan

- Novos testes em `tests/test_agent_system_prompt.py`:
  - `test_native_omits_json_tool_format` — modo native não ensina JSON.
  - `test_bridge_keeps_json_tool_format` — modo bridge (default) mantém.
  - `test_both_list_tool_names` — ambos listam as tools.
- Padrão estrutural: `tests/test_serve_service_restart.py` (imports, estilo).
- Verificação: `python -m pytest tests/test_agent_system_prompt.py -q` → 3 novos testes passam.

## Done criteria

- [ ] `python -c "import ast; ast.parse(open('bauer/agent.py',encoding='utf-8').read())"` sem erro
- [ ] `python -m pytest tests/test_agent_system_prompt.py -q` → 3 testes passam
- [ ] `python -m ruff check bauer/agent.py bauer/commands/serve_cmd.py` → `All checks passed!`
- [ ] `grep -n "responda SOMENTE com o JSON" bauer/agent.py` aparece **apenas** dentro do ramo `else`/bridge
- [ ] Nenhum arquivo fora do escopo modificado (`git status`)
- [ ] Linha de status atualizada em `plans/README.md`

## STOP conditions

Pare e reporte se:
- O campo `tool_mode` não existir em `RuntimeState`/`state` (o excerto de "Current state" assume que existe) — reporte como o estado é estruturado antes de adivinhar.
- O `_build_system_prompt` já tiver um parâmetro `tool_mode` (código já drifou) — compare com o excerto.
- Uma verificação falhar duas vezes após tentativa razoável de correção.

## Maintenance notes

- Se um terceiro modo de tools for adicionado (ex.: um formato específico do qwen3-coder via PARSER), estenda o `if/elif` em vez de duplicar o prompt.
- Revisor deve conferir: o prompt bridge ficou **byte-idêntico** ao original (nenhuma regressão pra quem usa cloud/bridge).
- Follow-up deixado de fora: medir empiricamente se o modo native se beneficia de instruções ainda mais enxutas — fora de escopo aqui.
