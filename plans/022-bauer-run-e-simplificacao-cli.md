# Plano 022: `bauer run` governado + simplificação da superfície de comandos

> **Relação com o 021**: este plano **substitui e corrige** o
> [021](021-bauer-run-autonomous-entrypoint.md). O 021 foi escrito no commit
> `ffd3a3d`, ANTES do Kernel 6c e do `/loop` web (`serve_loop.py`) entrarem no
> master (PRs #35, #36). Duas premissas do 021 ficaram desatualizadas e uma
> lacuna precisa ser preenchida — ver "Correções ao 021" abaixo. Marque a
> linha 021 do índice como `SUPERSEDED (022)`.

## Status

- **Prioridade**: P1
- **Esforço**: L (fatiado em A/B/C — cada fatia entrega valor sozinha)
- **Risco**: MED
- **Categoria**: dx / arquitetura
- **Planejado no commit**: `3c98726`, 2026-07-12

---

## Diagnóstico (análise ponta a ponta, com evidência do código vivo)

O prompt original tem **três** dores. Medi cada uma:

### Dor 1 — "a lista de comandos é grande e confusa"
Real e maior do que parece: **76 entradas de primeiro nível** (46 grupos +
~24 folhas diretas no root de `cli.py`) e **~252 comandos-folha** no total.
Pior: **seis verbos** parecem "rodar uma tarefa" sem contrato claro entre si —
`agent`, `orchestrate`, `kernel`, `dispatch`, `daemon`, `worker` — mais o
`/loop` que vive *dentro* de `bauer agent`. Não há porta de entrada óbvia.

### Dor 2 — "não sei qual comando roda uma tarefa de início ao fim, sem parar"
Real. Hoje o caminho é `bauer agent` (interativo) → digitar `/loop`, ou o botão
de modo autônomo na web. **Não existe `bauer run "tarefa"`.** E há um footgun de
config confirmado: `bauer agent --config` tem default `Path("config.yaml")`
(agent_cmd.py:143) e `load_config` procura o CWD **antes** de `~/.bauer`
(config_loader.py:936). Entrar numa pasta de projeto que tenha seu próprio
`config.yaml` (comum) faz o Bauer carregar o config ERRADO.

### Dor 3 — "a config do loop (max calls, custo máximo) me confunde"
Real e subestimada. Existem **três** limites da família `max_tool_calls`, com
significados diferentes, e nada explica a diferença:

| Campo | Default | Escopo real |
|---|---|---|
| `tools.max_tool_calls` | 500 | sessão INTEIRA do ToolRouter |
| `tools.max_tool_turns` | 150 | UM turno / rodada |
| `loop.max_tool_calls` | 120 | execução autônoma (/loop) |

Somam-se ainda `loop.max_minutes`, `loop.max_cost_usd`, o `bauer budget` /
`bauer autonomy` (BudgetManager: `daily_budget_usd`) e o gate de orçamento do
Kernel. **Cinco+ lugares** onde algo chamado "budget"/"max cost" mora. E o
"custo máximo" é uma **estimativa** (cost_meter retorna 0 sem usage; preço
genérico p/ modelo desconhecido em usage_pricing) — não um teto de faturamento,
mas nada diz isso.

---

## Correções ao plano 021 (por que este plano existe)

1. **[CRÍTICO] `bauer run` tem de passar pelo Kernel.** O 021 desenha `bauer
   run` como fachada fina sobre o motor de loop, mas foi escrito antes do 6c.
   Se `bauer run` reusar o `_run_loop_mode` da CLI, nasce um **quarto caminho
   de execução NÃO-governado** — exatamente a fragmentação que os Sprints 1–6c
   eliminaram. Evidência: `agent._run_loop_mode` tem um `while True` próprio e
   **não recebe/usa o `kernel`** (o param `kernel=` de `run_agent_session` só
   governa o turno manual do 6c-3, não o `/loop`). Já o `/loop` **web** passa
   por `kernel.admit()`. Ou seja, hoje **o mesmo `/loop` é governado na web e
   não-governado na CLI** — assimetria a corrigir.

2. **O motor unificado já existe — e não é o do 021.** `serve_loop.run_loop_
   rounds()` (criado no PR #36) já é o núcleo puro, governado, usado pela web,
   com evento `loop.round.completed` e admissão pelo Kernel. O alvo da
   unificação não é "criar" — é fazer a CLI (`_run_loop_mode`) e o novo `bauer
   run` **delegarem** a ele, aposentando o `while True` duplicado.

3. **A Dor 1 não pode ser 100% adiada.** O 021 empurra a curvatura da árvore de
   comandos para "próximo plano". Correto que `bauer run` é a maior alavanca
   isolada, mas ele resolve a Dor 2, não a Dor 1. Este plano inclui a Fatia B
   (curar o topo) como parte do escopo, porque é metade da queixa original.

**O que o 021 acerta e é preservado aqui**: identificar `bauer run` como a peça
faltante; o footgun do config; rotular custo como estimado; exit codes 0/2/130;
disciplina de escopo e STOP; não remover/renomear comandos.

---

## Fatia A — `bauer run` governado (resolve Dor 2)

Cria `bauer run "tarefa"`: síncrono, workspace = CWD, config canônico
(`paths.config_path()`, nunca o `config.yaml` do projeto), governado pelo
Kernel quando `kernel.enabled`, limites exibidos em PT antes e depois.

**Arquivos**: `bauer/commands/run_cmd.py` (criar), `bauer/cli.py` (registrar
perto de `agent`), `bauer/serve_loop.py` (helper de limites; ver Fatia C),
`bauer/commands/agent_cmd.py` (extrair bootstrap de sessão reusável),
`tests/test_run_cmd.py` (criar).

**Motor**: reusa `serve_loop.run_loop_rounds()` com uma `turn_fn` que roda
`run_one_turn_with_fallback` no workspace do CWD. Quando `kernel_enabled(cfg)`,
envolve com `kernel.admit()` (mesma governança da web): kill-switch, policy e
budget ANTES do 1º token; kill-switch/cancelamento checados entre rodadas.

**Contrato**:
```text
bauer run TASK
  --workspace PATH      default: CWD (recusa raiz/home/~/.bauer via is_sensitive_dir)
  --config PATH         default: paths.config_path() — IGNORA config.yaml do projeto
  --model / --models TEXT
  --max-minutes / --max-tool-calls / --max-cost FLOAT   (só APERTAM o teto do config)
  --approval threshold|deny_all|yolo
```
- TASK vazio = erro (nunca abre wizard/prompt/stdin).
- Banner antes: workspace, modelo/provider, approval, limites efetivos,
  "custo é ESTIMADO (depende de usage + tabela de preços)".
- Exit: concluído → 0; parada incompleta → 2; Ctrl+C → 130; sempre restaura o
  approval callback.

**Verificação**: `bauer run --help` explica CWD, aprovação e custo estimado;
`tests/test_run_cmd.py` cobre CWD→workspace, config canônico vence config do
projeto, overrides só apertam, exit 0/2/130, pasta sensível recusada antes de
criar cliente, kill-switch bloqueia a admissão, não lê stdin.

## Fatia B — simplificar a superfície (resolve Dor 1)

Sem remover nem renomear nada (compatibilidade total): reorganizar a
**apresentação** do `--help`.

1. **`bauer` sem args** deixa de listar 76 entradas cruas: mostra um painel
   curto com as ~8 portas principais — `run`, `agent`, `serve`, `chat`,
   `kanban`, `models`, `config`, `doctor` — e "use `bauer all` para a lista
   completa".
2. Agrupar o `--help` do root por seção (Typer `rich_help_panel`): "Começar
   aqui" (run/agent/serve), "Projeto" (kanban/project/spec/factory),
   "Modelos & custo" (models/budget/cost), "Avançado" (kernel/orchestrate/
   dispatch/daemon/worker/runtime…). Um comando não muda de lugar — só ganha
   um rótulo de painel.
3. Doc de "qual comando eu uso?" no README com uma árvore de decisão de 5
   linhas (tarefa pontual → `bauer run`; conversar → `bauer agent`; servir a
   UI → `bauer serve`).

**Verificação**: `bauer --help` mostra ≤ 8 na seção "Começar aqui"; todos os
comandos antigos continuam invocáveis (teste de descoberta em `test_cli.py`).

## Fatia C — desembaraçar os limites (resolve Dor 3)

1. **Um helper único de resolução** em `serve_loop.py`:
   `resolve_loop_limits(loop_section, overrides, *, clamp_to_config)` —
   `clamp_to_config=False` p/ CLI (flag substitui), `True` p/ HTTP/`bauer run`
   (só aperta). `server.py:_loop_limits` e o `bauer run` delegam a ele. Fim das
   duas resoluções paralelas.
2. **Banner + `bauer run`/`/loop` em PT claro**: "vou trabalhar até: 30 min OU
   120 chamadas de ferramenta OU ~US$2,00 de custo ESTIMADO — o que vier
   primeiro. Ctrl+C para."
3. **README com a tabela** dos 3 `max_tool_calls` + o que é `bauer budget`
   (teto de runtime/Kernel, ledger diário — outro escopo, não o do `bauer
   run`). Deixar explícito: tempo + nº de tools são os guardrails PRIMÁRIOS;
   custo é estimativa.

**Não** renomear as chaves de config (quebraria configs existentes) — a clareza
vem do helper único + banner + docs.

---

## Sequência e gates

Ordem: **A → C → B** (o `bauer run` primeiro porque é a maior alavanca e seu
banner já é o veículo da Fatia C; B por último por ser cosmético e sem risco).

Cada fatia fecha com: `pytest` das suítes tocadas verde + `ruff check
--select E9,F63,F7,F82` limpo + `bauer --help` e `bauer run --help` OK +
`git status` só com arquivos em escopo.

## Critérios de conclusão

- [ ] `bauer run TASK` roda de ponta a ponta do CWD, config canônico, **pelo
      Kernel** quando `kernel.enabled` (mesma governança da web).
- [ ] CLI `/loop`, web `/loop` e `bauer run` compartilham `run_loop_rounds()` —
      um único motor, sem `while True` duplicado em `_run_loop_mode`.
- [ ] Config do projeto (`./config.yaml`) nunca é carregado como config do Bauer.
- [ ] `bauer` sem args mostra ~8 portas; nenhum comando removido/renomeado.
- [ ] Um só helper resolve limites (CLI e HTTP); custo rotulado "estimado".
- [ ] Tools/custo contados uma vez só (dono único — herdar a checagem do #36).
- [ ] Exit codes 0/2/130 testados.

## Condições de STOP

- Reusar `run_loop_rounds()` exigir perder stop reasons ou a verificação de
  loop-skill da CLI → pare e reporte (pode exigir uma dataclass de resultado).
- Fazer `bauer run` passar pelo Kernel exigir mudar a API HTTP de `/loop` → pare.
- Extrair o bootstrap de `agent()` mudar company/memória/sessões/fallback → pare.
- Qualquer necessidade de subprocesso/stdin simulado para alimentar o `/loop`.
- Baseline já vermelha → reporte as falhas preexistentes, não as "conserte".

## Notas

- `bauer run` permanece fachada fina: rodadas em `serve_loop.py`, bootstrap em
  `agent_cmd.py`, apresentação em `run_cmd.py`, governança no Kernel.
- Próximo plano (fora deste): unificar `budget` / `cost` / daemon / Kernel num
  ledger só — é o que fecha de vez a Dor 3 na camada de dados.
