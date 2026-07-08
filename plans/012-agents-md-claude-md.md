# Plan 012: Escrever `AGENTS.md` + `CLAUDE.md` úteis para execução por agentes

> **Executor instructions**: Follow this plan step by step. Run every
> verification command and confirm the expected result before moving on. If
> anything in "STOP conditions" occurs, stop and report. When done, update this
> plan's status row in `plans/README.md`.
>
> **Drift check (run first)**: `git diff --stat 2c9d86f..HEAD -- AGENTS.md CLAUDE.md README.md pyproject.toml`
> If any changed, re-read them before proceeding.

## Status

- **Priority**: P2
- **Effort**: S
- **Risk**: LOW
- **Depends on**: none
- **Category**: dx
- **Planned at**: commit `2c9d86f`, 2026-07-06

## Why this matters

Este repositório é rotineiramente editado por agentes de IA (o próprio Bauer,
Claude Code, etc.), e os planos em `plans/` são feitos para execução por
modelos com zero contexto. Hoje o `AGENTS.md` tem UMA linha
(`## Imported Claude Cowork project instructions`) e não há `CLAUDE.md`. Sem um
guia raiz, cada agente redescobre do zero: qual comando roda os testes, qual é
o interpretador do venv, as convenções (português nos comentários, mixins de
tools, config Pydantic estrita), e o que NÃO fazer (pip.exe direto quebra por
WDAC, shlex POSIX come backslash no Windows). Um `AGENTS.md`/`CLAUDE.md` curto e
correto é alta alavancagem: encurta toda tarefa futura de agente e reduz erro.

## Current state

- `AGENTS.md` (raiz) — 1 linha, sem conteúdo útil:
  ```
  ## Imported Claude Cowork project instructions
  ```
- Não existe `CLAUDE.md` na raiz.
- Fatos verificados na recon (use-os como fonte de verdade para o conteúdo):
  - **Runtime**: Python ≥ 3.11. Projeto = "Runtime adaptativo para LLMs locais
    e cloud" (`pyproject.toml`, `version = 0.2.0`, MIT).
  - **Testes**: `pytest tests/ -q` (config em `pyproject.toml`:
    `[tool.pytest.ini_options] testpaths=["tests"]`, `asyncio_mode="auto"`).
    Localmente, com WDAC, use o interpretador do venv:
    `.venv/Scripts/python.exe -m pytest` (Windows) — NÃO chame `pytest.exe`
    nem `pip.exe` direto (WDAC bloqueia binários standalone; use
    `python -m pytest` / `python -m pip`).
  - **Lint**: `ruff check bauer/ --select E9,F63,F7,F82` é o gate bloqueante no
    CI; `E,F,W` (ignorando E501,W291,W293,E302,E303) é informativo.
  - **Estrutura**: ~159 módulos em `bauer/`. God objects notáveis: `agent.py`
    (~4900 linhas, loop do agente), `cli.py` (~2300, comandos Typer, extraídos
    para `bauer/commands/`), `tool_router.py` (~1850, ToolRouter + 19 mixins em
    `bauer/tools/`).
  - **Convenções**: comentários/docstrings em **português**; config via seções
    Pydantic v2 `_StrictSection` (extra proibido) em `bauer/config_loader.py`;
    tools são mixins compostos em `ToolRouter`; segredos env-first
    (`.env` > config.yaml); `httpx` com `verify=shared_ssl_context()`
    (`bauer/http_shared.py`) por perf de SSL no Windows.
  - **Workflow git**: fixes pequenos vão direto no `master` (sem PR); features
    novas em branch + PR. Conventional commits. Nunca commitar/pushar sem o
    usuário pedir.
  - **Planos**: `plans/` contém planos de implementação self-contained gerados
    pelo skill `/improve`; executores leem o plano e atualizam o status em
    `plans/README.md`.
- O `README.md` tem a documentação completa do produto (modos de uso, serve,
  gateway, providers, tools) — o `CLAUDE.md` deve APONTAR para ele, não duplicá-lo.

### Convenções do repo a seguir
- Português. Markdown enxuto. Sem inventar comandos — use só os verificados
  acima e no `README.md`/`pyproject.toml`.

## Commands you will need

| Purpose      | Command                                                   | Expected |
|--------------|-----------------------------------------------------------|----------|
| Testes rodam | `.venv/Scripts/python.exe -m pytest tests/ -q -k config`  | passa (sanidade) |
| Ver README   | `grep -n "^## " README.md`                                | lista seções |

## Scope

**In scope**:
- `AGENTS.md` (substituir o stub)
- `CLAUDE.md` (criar)

**Out of scope** (NÃO tocar):
- `README.md` — não altere; apenas referencie.
- Qualquer código em `bauer/`.
- `.claude/` ou configs de ferramentas.

## Git workflow

- Branch: `advisor/012-agents-md-claude-md`
- Commit style: conventional commits. Ex.:
  `docs(agents): AGENTS.md + CLAUDE.md com comandos, convenções e armadilhas`
- NÃO faça push nem PR sem instrução.

## Steps

### Step 1: Escrever o `CLAUDE.md` (guia canônico)

Crie `CLAUDE.md` na raiz com estas seções (conteúdo vindo de "Current state" —
não invente fatos novos):

1. **Visão de uma linha** — o que é o Bauer (runtime adaptativo para LLMs
   locais/cloud) e link para `README.md` para detalhes de produto.
2. **Como rodar/testar/lint** — os comandos exatos (venv `python -m pytest`,
   ruff gate). Inclua o aviso WDAC (não usar `pip.exe`/`pytest.exe` direto;
   usar `python -m ...`).
3. **Mapa do código** — `bauer/agent.py` (loop), `bauer/cli.py` +
   `bauer/commands/` (comandos Typer), `bauer/tool_router.py` + `bauer/tools/`
   (mixins de tools), `bauer/config_loader.py` (config Pydantic estrita),
   `bauer/server.py` (API HTTP), `bauer/*_bridge.py` + `bauer/gateway_runtime.py`
   (canais de chat). Uma linha por item.
4. **Convenções** — português nos comentários; Pydantic `_StrictSection`;
   secrets env-first; `httpx` com `shared_ssl_context()`; tools = mixins.
5. **Armadilhas conhecidas** — WDAC bloqueia binários; nunca `shlex` POSIX em
   input no Windows (come backslash); nunca commitar/pushar sem pedido.
6. **Planos** — `plans/` tem planos self-contained; executores atualizam
   `plans/README.md`.

Mantenha curto (uma tela ou duas). O objetivo é orientar, não duplicar o README.

**Verify**: `test -f CLAUDE.md && grep -c "pytest" CLAUDE.md` → arquivo existe e
menciona pytest ao menos 1x.

### Step 2: Escrever o `AGENTS.md`

Substitua o stub de `AGENTS.md`. Duas opções válidas — escolha a que o operador
não vetou:
- **(a)** `AGENTS.md` com o mesmo conteúdo canônico (alguns agentes leem
  `AGENTS.md`, outros `CLAUDE.md`); OU
- **(b)** `AGENTS.md` curto que aponta: "Veja `CLAUDE.md` para o guia completo"
  + os 3-4 itens mais críticos (comando de teste, aviso WDAC, convenção de
  commit).

Prefira **(b)** para evitar duplicação divergente (um arquivo canônico
`CLAUDE.md`, `AGENTS.md` como ponteiro).

**Verify**: `test -f AGENTS.md && ! grep -q "Imported Claude Cowork" AGENTS.md`
→ o stub foi substituído.

## Test plan

Sem testes de código (é docs). Verificação de sanidade:
- `grep -n "python -m pytest" CLAUDE.md` retorna ≥1 (o comando de teste está lá).
- `grep -n "WDAC\|pip.exe\|python -m pip" CLAUDE.md` retorna ≥1 (a armadilha
  está documentada).
- Rode `.venv/Scripts/python.exe -m pytest tests/ -q -k config` só para
  confirmar que o comando documentado de fato funciona no ambiente.

## Done criteria

TODAS devem valer:

- [ ] `CLAUDE.md` existe e tem as 6 seções do Step 1
- [ ] `AGENTS.md` não contém mais "Imported Claude Cowork project instructions"
- [ ] `grep -n "python -m pytest" CLAUDE.md` retorna ≥1
- [ ] `grep -n "português\|Pydantic\|mixin" CLAUDE.md` retorna ≥1 (convenções)
- [ ] Nenhum arquivo fora do in-scope modificado (`git status`)
- [ ] Status atualizado em `plans/README.md`

## STOP conditions

Pare e reporte se:

- Já existir um `CLAUDE.md` com conteúdo substancial (o stub pode ter sido
  preenchido desde o planejamento) — não sobrescreva; reporte.
- Algum comando de "Current state" não funcionar no ambiente (ex.: o caminho do
  venv for diferente) — reporte o comando correto em vez de documentar um que
  não roda.

## Maintenance notes

- Quando comandos de build/test/lint mudarem, atualize `CLAUDE.md` — é o
  contrato que os agentes leem. Documentação de comando errada é pior que
  ausente.
- Se o `agent.py` for refatorado (plano 013), atualize o "Mapa do código".
- O reviewer deve conferir que nenhum comando documentado é inventado — todos
  precisam existir no `pyproject.toml`/`README.md`/CI.
