# Plano 021: Criar `bauer run` para executar uma tarefa de ponta a ponta

> **Instruções ao executor**: siga este plano na ordem. Rode todas as
> verificações e confirme o resultado esperado antes de avançar. Se uma
> condição de STOP ocorrer, pare e reporte; não improvise. Ao concluir, mude a
> linha 021 em `plans/README.md` para `DONE`.
>
> **Drift check (rode primeiro)**:
> `git diff --stat ffd3a3d..HEAD -- bauer/cli.py bauer/commands/agent_cmd.py bauer/agent.py bauer/serve_loop.py bauer/server.py bauer/config_loader.py bauer/paths.py tests/test_agent_loop_cmd.py tests/test_serve_loop.py README.md`
> Se a arquitetura descrita abaixo não corresponder mais ao código vivo, pare.

## Status

- **Prioridade**: P1
- **Esforço**: L
- **Risco**: MED
- **Depende de**: nenhum; executar isoladamente do plano 013, pois ambos tocam `bauer/agent.py`
- **Categoria**: dx / tech-debt / docs
- **Planejado no commit**: `ffd3a3d`, 2026-07-12

## Por que isso importa

O fluxo principal — entrar numa pasta e pedir ao Bauer que conclua uma tarefa
sem intervenção — não possui comando direto. Hoje é preciso abrir `bauer agent`
e digitar `/loop`; paralelamente, `orchestrate`, `kernel`, `dispatch` e `daemon`
parecem alternativas, embora tenham contratos diferentes. A CLI expõe 76
comandos de primeiro nível e 280 comandos-folha visíveis.

Este plano cria o contrato simples abaixo e mantém `/loop` como equivalente
interativo compatível:

```powershell
cd C:\caminho\do\projeto
bauer run "implemente a funcionalidade, rode os testes e corrija até passar"
```

O comando deve usar o CWD como workspace, o config canônico do Bauer, o motor
autônomo compartilhado com a Web e limites claramente exibidos.

## Estado atual

- `bauer/cli.py:41-48,95-144` registra 76 entradas no app raiz, mas não possui
  `bauer run`.
- `bauer/commands/agent_cmd.py:85-137` detecta/adota o CWD somente no fluxo
  interativo. `agent()` (linhas 140+) também concentra a montagem de cliente,
  router, workspace, sessões, memória e fallbacks.
- `bauer/config_loader.py:932-950` procura primeiro `config.yaml` no CWD e só
  depois `~/.bauer/config.yaml`. Um projeto com seu próprio `config.yaml` pode
  ser interpretado como configuração do Bauer. `bauer/paths.py:32-35` já possui
  `config_path()`, o caminho canônico que o novo comando deve usar.
- `bauer/agent.py:_run_loop_mode()` implementa o `/loop` da CLI.
  `_resolve_loop_config()` chama `load_config()` sem receber o config escolhido
  por `agent`, podendo cair silenciosamente nos defaults.
- `bauer/serve_loop.py:88-147` já possui `run_loop_rounds()`, motor puro usado
  pelo `/loop` Web. `bauer/server.py:1729-1748` mantém outra resolução de
  limites. CLI e Web não devem conservar duas máquinas de estado equivalentes.
- `bauer/config_loader.py` define limites distintos:

```python
class ToolsSection(_StrictSection):
    max_tool_calls: int = Field(ge=1, default=500)   # sessão do ToolRouter
    max_tool_turns: int = Field(ge=1, default=150)   # um turno/rodada

class LoopSection(_StrictSection):
    max_minutes: int = Field(ge=1, default=30)
    max_tool_calls: int = Field(ge=1, default=120)   # execução autônoma
    max_cost_usd: float = Field(ge=0.0, default=2.0)
    approval_mode: Literal["threshold", "deny_all", "yolo"] = "threshold"
```

- `bauer/cost_meter.py:49-61` retorna custo zero sem usage ou após erro;
  `bauer/usage_pricing.py:80-99` usa preço genérico para modelo desconhecido.
  A interface deve dizer **custo estimado**, não teto de faturamento.
- Convenções: Typer, comandos em `bauer/commands/*_cmd.py`, textos de usuário
  em português, imports pesados lazy e testes CLI com `CliRunner` seguindo
  `tests/test_cli.py`.

## Comandos de verificação

| Objetivo | Comando | Esperado |
|---|---|---|
| Baseline | `uv run pytest tests/test_agent_loop_cmd.py tests/test_serve_loop.py -q` | todos passam |
| Novo comando | `uv run pytest tests/test_run_cmd.py -q` | todos passam |
| Regressão | `uv run pytest tests/test_agent_loop_cmd.py tests/test_serve_loop.py tests/test_autonomous_budget.py -q` | todos passam |
| Help | `uv run bauer run --help` | explica CWD, aprovação e custo estimado |
| Lint | `uv run ruff check bauer/ --select E9,F63,F7,F82` | exit 0 |
| Suíte | `uv run pytest -q` | todos passam |

## Escopo

**Em escopo — únicos arquivos permitidos:**

- `bauer/commands/run_cmd.py` (criar)
- `bauer/commands/agent_cmd.py`
- `bauer/agent.py`
- `bauer/serve_loop.py`
- `bauer/server.py`
- `bauer/cli.py`
- `bauer/config_loader.py` (somente tipos/helpers de limites)
- `bauer/paths.py` (somente resolução canônica)
- `tests/test_run_cmd.py` (criar)
- `tests/test_agent_loop_cmd.py`
- `tests/test_serve_loop.py`
- `tests/test_cli.py` (apenas descoberta/help)
- `README.md`
- `plans/README.md`

**Fora de escopo:**

- Reorganizar os outros comandos sob `bauer advanced`.
- Remover/renomear comandos existentes.
- Unificar o ledger de `bauer budget`, `cost`, daemon e Kernel.
- Alterar preços ou integrar billing real.
- Mudar a API HTTP de `/loop`.
- Implementar background; `bauer run` será síncrono.
- Usar subprocesso/stdin simulado para alimentar `/loop` no `bauer agent`.

## Git

- Branch: `advisor/021-bauer-run-autonomous-entrypoint`
- Commits sugeridos:
  - `refactor(loop): unifica motor de rodadas autonomas`
  - `feat(cli): adiciona entrada autonoma bauer run`
  - `docs(cli): documenta bauer run e budgets`
- Não fazer push/PR sem autorização.

## Etapas

### 1. Fixar a baseline de semântica

Garanta em `tests/test_serve_loop.py` casos para: duas rodadas somente-texto
concluem; uma rodada com tools limpa a confirmação; budget esgotado; parada
externa; erro de provider; teto duro. Preserve os equivalentes de
`tests/test_agent_loop_cmd.py`.

**Verifique**: `uv run pytest tests/test_agent_loop_cmd.py tests/test_serve_loop.py -q` → verde.

### 2. Unificar a resolução de limites

Em `bauer/serve_loop.py`, crie `ResolvedLoopLimits` imutável e
`resolve_loop_limits(loop_section, overrides, *, clamp_to_config)`:

- CLI: flag substitui config (`False`), compatível com `/loop` atual.
- HTTP: request só reduz o teto (`True`), compatível com `server.py`.
- Validar minutos/tools positivos, custo não negativo e approval mode.
- Mensagens devem indicar a flag inválida.

Faça `agent.py` receber a `LoopSection` já carregada; não use `load_config()`
implícito no motor. Faça `server.py:_loop_limits()` delegar ao mesmo helper e
manter seu JSON atual.

**Verifique**: testes CLI e Web provam precedência e clamp.

### 3. Usar uma única máquina de estado

Mantenha `serve_loop.run_loop_rounds()` como núcleo puro. Adapte
`agent.py:_run_loop_mode()` como adapter de console/telemetria/verificação,
injetando uma `turn_fn` baseada em `_run_tool_loop_body()`.

Preserve todos os stop reasons da CLI. Se a tupla atual for insuficiente, crie
uma dataclass pequena em `serve_loop.py`; esse módulo não pode importar
`agent.py`. Defina um único dono para contabilizar tools e custo — não conte
duas vezes — e cubra N tools → exatamente N no budget/resumo.

**Verifique**:
`uv run pytest tests/test_agent_loop_cmd.py tests/test_serve_loop.py tests/test_autonomous_budget.py -q` → verde.

### 4. Extrair a inicialização reutilizável

Em `agent_cmd.py`, extraia de `agent()` uma função interna que monte os
componentes da sessão sem abrir o prompt. `agent()` continua chamando-a e deve
manter seleção de modelo, company, dispatcher, sessões, memória, fallbacks,
router e safe mode. A função recebe config/models/workspace resolvidos; não
escolhe CWD/config internamente. Não duplique esse bootstrap em `run_cmd.py`.

**Verifique**: `uv run pytest tests/test_commands_agent.py tests/test_agent_loop_cmd.py -q` → verde; `bauer agent --help` compatível.

### 5. Implementar `bauer run`

Crie `bauer/commands/run_cmd.py`, registre-o perto de `agent` em `cli.py` e
implemente:

```text
bauer run TASK
  --workspace PATH          default: CWD
  --config PATH             default: ~/.bauer/config.yaml
  --models PATH
  --model TEXT
  --max-minutes INTEGER
  --max-tool-calls INTEGER
  --max-cost FLOAT          custo estimado USD
  --approval TEXT           threshold | deny_all | yolo
```

Regras:

- TASK vazio é erro; nunca abre wizard.
- Workspace default é `Path.cwd().resolve()`.
- Recusar raiz, home e `~/.bauer` com `projects_registry.is_sensitive_dir()`.
- Config default é `paths.config_path()`, mesmo que o projeto tenha config próprio.
- Carregar config uma vez e propagá-lo até os limites.
- Mostrar workspace, modelo/provider, approval e limites efetivos antes de rodar.
- Execução síncrona; conclusão → 0; parada incompleta → 2; Ctrl+C → 130.
- Restaurar approval callback em qualquer saída.
- Não chamar `bauer agent` por subprocesso.

**Verifique**: `uv run bauer run --help` documenta o contrato e usa "pasta atual" e "custo estimado".

### 6. Cobrir o comando end-to-end

Crie `tests/test_run_cmd.py` com `CliRunner`, mocks e `tmp_path`. Cubra:

1. `run` aparece no help raiz.
2. CWD vira workspace.
3. `BAUER_HOME/config.yaml` vence um `config.yaml` de aplicação no projeto.
4. Overrides explícitos vencem defaults.
5. Sem flags usa `loop:` do config.
6. Banner diz custo estimado.
7. Exit codes 0, 2 e 130.
8. Approval modes chegam ao engine.
9. Pasta sensível é recusada antes de criar cliente.
10. Não abre prompt nem lê stdin.

Nenhum teste chama rede, browser, daemon ou serviço real.

**Verifique**: `uv run pytest tests/test_run_cmd.py -q` → verde.

### 7. Documentar a entrada principal e os escopos

No README, coloque `bauer run` nos primeiros passos e mantenha `/loop` como
equivalente interativo. Adicione tabela distinguindo:

- `tools.max_tool_calls`: sessão do ToolRouter;
- `tools.max_tool_turns`: um turno;
- `loop.max_tool_calls`: execução autônoma;
- `bauer budget`: runtime/Kernel, fora do `bauer run` deste plano.

Explique que custo depende de usage e tabela de preços; tempo + tools são os
guardrails primários.

**Verifique**:
`rg -n "bauer run|custo estimado|tools.max_tool_turns|loop.max_tool_calls" README.md` → encontra todos.

### 8. Gates finais

```powershell
uv run ruff check bauer/ --select E9,F63,F7,F82
uv run pytest tests/test_run_cmd.py tests/test_agent_loop_cmd.py tests/test_serve_loop.py tests/test_autonomous_budget.py -q
uv run pytest -q
uv run bauer --help
uv run bauer run --help
uv run bauer agent --help
git status --short
```

Todos devem sair 0. O status deve listar somente arquivos em escopo.

## Critérios de conclusão

- [ ] `bauer run TASK` usa CWD e config canônico por padrão.
- [ ] Config de aplicação no projeto não é carregado pelo novo comando.
- [ ] `/loop` continua compatível.
- [ ] CLI e Web usam `run_loop_rounds()` como máquina única.
- [ ] Tools/custo não são contabilizados duas vezes.
- [ ] Limites aparecem antes e depois; custo é rotulado estimado.
- [ ] Exit codes 0/2/130 estão testados.
- [ ] Nenhum comando foi removido/renomeado.
- [ ] Testes focados, suíte completa e lint passam.
- [ ] Somente arquivos em escopo foram alterados.
- [ ] Linha 021 do índice atualizada.

## Condições de STOP

Pare se:

- O drift check revelar outro `bauer run` ou motor comum concorrente.
- O plano 013 estiver em andamento ou outro executor editar `agent.py`.
- Reusar `run_loop_rounds()` exigir perder stop reasons ou loop-skill verification.
- Não for possível definir um único dono da contagem sem mudar a API HTTP.
- Extrair o bootstrap mudar company, memória, sessões ou fallback interativo.
- A solução depender de subprocesso/stdin simulado.
- For necessário tocar fora do escopo.
- Um gate falhar duas vezes após correção razoável.
- A baseline já estiver vermelha; reporte falhas preexistentes.

## Notas de manutenção

- `bauer run` deve permanecer fachada fina: rodadas em `serve_loop.py`, bootstrap
  compartilhado em `agent_cmd.py`, apresentação em `run_cmd.py`.
- Revisar especialmente dupla contagem, restauração de callbacks e seleção de config.
- Próximo plano recomendado: reduzir o help raiz para cerca de oito entradas e
  mover operações para `bauer advanced`, preservando aliases.
- Unificar `budget`, cost tracker, daemon e Kernel é outro plano; não ampliar este.
