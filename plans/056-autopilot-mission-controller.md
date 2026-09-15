# Plano 056: Integrar um autopilot persistente ao runtime always-on

> **Instruções ao executor**: siga este plano na ordem. O objetivo é entregar
> um MVP governado de operação contínua: o Bauer permanece ativo, retoma seu
> estado após reinício, escolhe o próximo objetivo permitido, materializa
> tarefas no Kanban, deixa o dispatcher executá-las, valida o resultado e
> pausa/escalona quando não puder continuar. Não transforme o MVP em um agente
> irrestrito que inventa escopo ou altera o próprio contrato de segurança.
> Rode cada verificação indicada antes de avançar. Atualize a linha deste plano
> em `plans/README.md` ao concluir, salvo se outro agente estiver mantendo o
> índice.

> **Drift check (primeiro comando)**:
> `git diff --stat 8335cb1..HEAD -- bauer/autonomous_planner.py bauer/goal_tracker.py bauer/task_dispatcher.py bauer/supervisor.py bauer/commands/runtime_cmd.py bauer/config_loader.py tests/`
> Se os trechos descritos em “Estado atual” não corresponderem ao código vivo,
> pare e reporte o drift; não adapte o desenho por suposição.

## Status

- **Prioridade**: P1
- **Esforço**: L (várias fatias PR-sized; estimativa grosseira)
- **Risco**: HIGH — amplia execução não supervisionada, embora preserve Kernel,
  budgets, worktrees e kill-switch
- **Depende de**: plano 055 concluído; plano 014 é contexto histórico, não uma
  dependência de arquivo, porque o artefato de arquitetura citado nele não está
  presente neste checkout
- **Categoria**: direção / arquitetura / confiabilidade
- **Planejado em**: commit `8335cb1`, 2026-09-15

## Por que isto importa

O Bauer já possui um supervisor always-on, dispatcher durável, scheduler,
Kanban, planner de objetivos, Kernel, budgets e kill-switch. Porém, o
`runtime start` supervisiona apenas serviços de infraestrutura; ele não possui
um controlador que escolha o próximo objetivo e conecte `GoalTracker`/
`AutonomousPlanner` ao Kanban. O usuário precisa continuar semeando tarefas ou
disparando `bauer run`/`/loop`.

Este plano fecha o circuito com uma missão declarada e uma fila de objetivos
persistente. O resultado será “abrir e deixar trabalhando” dentro de um escopo
conhecido, com retomada, validação, pausa segura e relatório. A geração de
novas metas pelo próprio modelo será uma extensão posterior e permanecerá
desligada por padrão no MVP.

## Estado atual

- `bauer/commands/runtime_cmd.py:417-490` expõe `bauer runtime start` e cria
  um `RuntimeSupervisor` com os serviços `dispatcher`, `cron`, `outbox` e
  `kanban`; não há serviço `autopilot` nessa lista.
- `bauer/supervisor.py:150-238` constrói os `ServiceSpec`; `:250-314`
  inicia, verifica e reinicia processos filhos; `:315-378` publica status,
  parada e inicialização em background.
- `bauer/task_dispatcher.py:331-363` faz o watchdog; `:365-455` escolhe e
  reivindica tasks `READY`; `:599-684` executa o worker em subprocesso; o
  comando padrão é `bauer orchestrate run`, com worktree por task quando
  aplicável (`:644-684`). Esse é o executor canônico a preservar.
- `bauer/autonomous_planner.py:245-261` oferece um planner async com
  `decompose_fn`, `execute_fn`, retry, timeout e eventos; `:261-372` atualiza
  um tracker durante a execução. O módulo é testado, mas não é importado pelo
  `RuntimeSupervisor` nem pelo dispatcher como controlador de ciclo.
- `bauer/goal_tracker.py:148-184` persiste objetivos em SQLite e `:281-291`
  lista objetivos `pending`/`running` por prioridade. Ainda não existe uma
  operação atômica de claim com lease/heartbeat para um controlador persistente.
- `bauer/core/kernel/entry.py:54-132` é a entrada governada para execução;
  novos caminhos devem usar `run_governed()`/`continue_governed()` e declarar
  `autonomous=True`. Não criar um caminho paralelo que fecha runs diretamente.
- `bauer/config_loader.py:49-60` torna as seções estritas; `:637-707`
  define Kernel; `:783-800` define os limites do `/loop`; não há uma seção
  `autopilot`.
- `bauer/serve_loop.py:42-109` e `:175-255` tratam limites e conclusão do
  loop de uma única missão, mas não fazem polling permanente nem retomada após
  reinício.
- O teste estrutural de referência do supervisor é
  `tests/test_runtime_supervisor.py`; o teste de persistência/planner é
  `tests/test_autonomous_planner.py`. Os testes devem continuar herméticos,
  usando `tmp_path`/`monkeypatch` e sem provider real.

### Decisão de arquitetura do MVP

O autopilot será um **produtor/reconciliador de objetivos e tasks**, não um
segundo executor de LLM. O caminho será:

```text
missão declarada / objetivos persistidos
  -> AutopilotController seleciona um objetivo
  -> planner decompõe em passos governados
  -> passos viram tasks READY idempotentes no Kanban
  -> dispatcher existente executa cada task via `bauer orchestrate run`
  -> controller reconcilia DONE/FAILED/BLOCKED
  -> Kernel/evaluator valida o objetivo
  -> DONE, replanejamento limitado ou escalonamento humano
```

Não chamar `AutonomousPlanner.execute_goal()` com um `execute_fn` que contorne
o dispatcher/Kernel. Se for necessário alterar o planner, introduzir uma
adaptação explícita que materialize passos no Kanban e aguarde seus resultados.

## Comandos de verificação

| Objetivo | Comando | Resultado esperado |
|---|---|---|
| Ambiente | `uv sync --frozen --extra dev` | saída 0, usando o ambiente do lock |
| Testes focados | `uv run pytest tests/test_autopilot.py tests/test_goal_tracker.py tests/test_runtime_supervisor.py tests/test_autonomous_planner.py -q --tb=short` | todos passam |
| Suíte | `uv run pytest tests/ -q --tb=short` | todos passam |
| Lint bloqueante | `uv run ruff check bauer/ --select E9,F63,F7,F82` | saída 0 |
| Lint amplo | `uv run ruff check bauer/ --select E,F,W --ignore E501,W291,W293,E302,E303` | não piora o baseline |
| Tipos | `uv run mypy bauer/` | saída 0 sem ampliar overrides |
| Lock | `uv lock --check` | saída 0 |

## Escopo

**Em escopo**

- `bauer/autopilot.py` — controlador, estados, ciclo de polling e reconciliação.
- `bauer/goal_tracker.py` — claim/lease/heartbeat/recovery atômicos e
  idempotência de materialização.
- `bauer/autonomous_planner.py` ou novo adaptador coeso em
  `bauer/autopilot_planner.py` — somente a ponte planner → tasks Kanban; não
  duplicar a execução do dispatcher.
- `bauer/config_loader.py` e `config.yaml.example` — seção estrita
  `autopilot`, desligada por padrão.
- `bauer/supervisor.py` e `bauer/commands/runtime_cmd.py` — serviço
  supervisionado, start/stop/status/logs e opção explícita de habilitação.
- `bauer/cli.py` somente se for necessário registrar um novo grupo de comando
  público; prefira subcomando interno de `runtime` para não aumentar a
  superfície da CLI sem necessidade.
- `tests/test_autopilot.py`, `tests/test_goal_tracker.py`,
  `tests/test_runtime_supervisor.py`, testes de configuração e documentação
  operacional mínima no README ou runbook existente.

**Fora de escopo**

- Reescrever `BauerKernel`, `TaskDispatcher`, `serve_loop` ou o protocolo de
  tools.
- Alterar a semântica de `admit()`/`execute()` ou criar chamadas externas que
  fechem runs fora do Kernel.
- Geração irrestrita de metas pelo modelo, autoalteração do config, autoaprovação
  de ferramentas de alto risco ou merge automático de branches.
- Aprovação remota via Slack/Telegram; apenas deixar o estado `blocked`/
  `approval_required` observável e pronto para uma integração posterior.
- Alterar credenciais, auth, TLS, bind do `serve` ou bancos de memória não
  relacionados ao estado do autopilot.
- Limpar processos ou sessões existentes na máquina do operador como parte da
  implementação. Isso é operação separada e exige confirmação explícita.

## Fluxo Git

- Branch sugerida: `codex/056-autopilot-mission-controller`.
- Commits pequenos por fatia, seguindo o estilo existente (`feat(runtime): ...`,
  `test(runtime): ...`).
- Não fazer push, merge ou abrir PR sem autorização do operador.

## Etapas

### Etapa 0: Fixar o contrato e o baseline

Leia os arquivos do “Estado atual”, execute o drift check e rode os testes
focados existentes. Documente no próprio PR quais símbolos ainda existem e
qual backend de tasks (`markdown` ou `sqlite`) o teste está usando. Se o
dispatcher já tiver ganhado uma API de goal/lease desde o SHA planejado,
reaproveite-a e pare para reportar qualquer conflito com este plano.

**Verifique**: comandos de ambiente, testes focados e
`git diff --check` → saída 0; nenhum arquivo de produção alterado nesta etapa.

### Etapa 1: Definir configuração e máquina de estados do autopilot

Adicione uma seção Pydantic estrita com, no mínimo:

- `enabled: bool = False`;
- `workspace: str` ou resolução explícita pelo workspace do runtime;
- `poll_interval_s` com limites razoáveis;
- `max_active_goals` e `max_replans_per_goal`;
- `mission: str` opcional, mas obrigatório quando `enabled=true` e não houver
  objetivos pendentes persistidos;
- `allow_model_proposals: bool = False`;
- `approval_mode` limitado a `threshold | deny_all | yolo`, herdando a mesma
  semântica já usada no loop, sem permitir que o autopilot amplie o budget;
- limites por ciclo/objetivo (`max_minutes`, `max_tool_calls`, `max_cost_usd`)
  que só possam reduzir os limites globais efetivos.

Defina estados persistidos claros: `stopped`, `starting`, `idle`, `planning`,
`dispatching`, `verifying`, `blocked`, `error`, `stopping`. O estado deve
guardar apenas metadados operacionais: objetivo atual, último ciclo, erro,
heartbeat e contadores; nunca prompt completo, segredo ou resposta inteira.

**Verifique**: testes de configuração aceitam o exemplo válido, recusam campo
desconhecido, recusam limites não positivos e recusam `enabled=true` sem
missão/objetivo inicial. `uv run pytest tests/test_config_loader.py -q --tb=short`.

### Etapa 2: Tornar objetivos recuperáveis e reivindicáveis

Estenda `GoalTracker` com operações transacionais e testáveis:

1. `claim_next(session_id, lease_seconds)` seleciona o menor `priority` e o
   objetivo mais antigo entre `pending`/`running` recuperável, grava owner,
   lease e heartbeat numa única transação e retorna um snapshot.
2. `heartbeat(goal_id, session_id)` só atualiza o dono correto.
3. `release_or_requeue_stale(now)` rebaixa objetivos cujo lease expirou para
   `pending`, preservando erro e contador de tentativas.
4. `record_materialized_task(goal_id, task_id, step_key)` impede duplicação
   quando o supervisor reinicia entre a criação da task e a gravação do
   vínculo.

Se o schema atual precisar de colunas, faça migração compatível e idempotente;
não apague objetivos existentes. Use `BEGIN IMMEDIATE`/constraints existentes
do padrão SQLite do runtime e feche conexões em `finally`.

**Verifique**: adicione testes com dois trackers apontando para o mesmo banco,
claim concorrente, lease expirado, heartbeat de owner errado, reinício entre
as duas gravações simuladas e replay idempotente. Rode
`uv run pytest tests/test_goal_tracker.py -q --tb=short`.

### Etapa 3: Implementar o controlador de ciclo

Crie `AutopilotController` com uma função pura/testável `tick()` e um
`run_forever(stop_event)` fino. O `tick()` deve:

1. recusar trabalho quando kill-switch, budget ou config estiverem bloqueando;
2. reconciliar tasks vinculadas ao objetivo atual;
3. recuperar leases stale sem executar duas vezes a mesma task;
4. selecionar ou reivindicar um único objetivo, respeitando
   `max_active_goals`;
5. decompor o objetivo em passos com o planner existente;
6. criar tasks `READY` via `get_workspace_manager(workspace).add_task(...)`,
   com metadata `dispatch=true`, `goal_id` e uma chave estável de passo;
7. deixar o `TaskDispatcher` executar — o controller não deve chamar shell,
   provider ou `complete_run` diretamente;
8. detectar conclusão/falha dos filhos e decidir `DONE`, retry limitado,
   `blocked` ou replanejamento;
9. emitir eventos operacionais resumidos pelo EventBus e gravar heartbeat.

A criação de task deve ser idempotente por `(goal_id, step_key)` e só deve
usar status `READY` quando o passo tiver sido materializado por completo.
Falhas parciais devem permanecer recuperáveis e não podem gerar uma segunda
task no próximo tick.

**Verifique**: testes unitários do `tick()` cobrem idle, objetivo novo,
restart após materialização parcial, task DONE, task FAILED com retry,
replanejamento máximo, bloqueio por aprovação/budget/kill-switch e parada
graceful. `uv run pytest tests/test_autopilot.py -q --tb=short`.

### Etapa 4: Integrar o planner sem criar um segundo executor

Implemente a ponte entre passos do planner e tasks Kanban. Preserve retry,
timeout e eventos do planner, mas faça o `execute_fn` do autopilot apenas
consultar o estado da task governada ou, preferencialmente, mova a
materialização para um adaptador separado e deixe a execução para o
dispatcher.

Antes de escolher a forma final, confirme no código como
`TaskDispatcher._worker_command()` encaminha uma task normal para
`bauer orchestrate run` e como o Kernel é atingido nesse caminho. Se isso não
for verdade no checkout vivo, pare e reporte a topologia real; não invente uma
ponte direta para `/loop`.

**Verifique**: teste de integração com workspace temporário cria um objetivo,
materializa passos uma única vez, produz tasks READY e não chama provider,
subprocesso ou rede. O teste deve provar também que metadata de dispatch e
`goal_id` sobrevivem tanto no backend Markdown quanto no SQLite configurado no
teste. Rode os testes focados de planner/dispatcher.

### Etapa 5: Registrar o autopilot no RuntimeSupervisor

Adicione um serviço filho `autopilot` ao `RuntimeSupervisor` somente quando a
seção estiver habilitada ou quando o usuário passar uma flag explícita. O
serviço deve:

- usar o mesmo workspace/config/models do runtime;
- escrever log e estado em `.bauer_runtime`;
- receber SIGTERM e parar entre ticks;
- ser reiniciado com backoff pelo supervisor;
- não iniciar duas instâncias para o mesmo workspace;
- ficar desligado por padrão para manter compatibilidade com instalações
  existentes.

Exponha `--autopilot/--no-autopilot` em `bauer runtime start`, inclua o estado
no `runtime status` e documente `runtime service install` para o boot do
Windows. Não reutilize os dois `dispatch daemon` diretos como mecanismo de
coordenação; o runtime supervisionado deve ser a única autoridade operacional
recomendada.

**Verifique**: estenda `tests/test_runtime_supervisor.py` para specs, dry-run,
status, parada e restart do serviço; simule processo encerrado e confirme
backoff. `uv run pytest tests/test_runtime_supervisor.py tests/test_autopilot.py -q --tb=short`.

### Etapa 6: Expor operação e observabilidade mínima

Adicione status legível/JSON contendo: estado do controller, heartbeat, missão
identificada sem conteúdo sensível, objetivo atual por título/id público,
tasks pendentes/em execução, último motivo de parada, budget consumido e
último erro. Inclua comandos para pausar/retomar e solicitar replanejamento
seguro; todos devem ser idempotentes.

Eventos devem usar nomes estáveis, por exemplo:
`autopilot.started`, `autopilot.idle`, `autopilot.goal.claimed`,
`autopilot.task.materialized`, `autopilot.goal.completed`,
`autopilot.goal.blocked`, `autopilot.replan`, `autopilot.failed`.
Não publique prompts, respostas completas, tokens, paths de segredo ou
argumentos sensíveis de tools.

**Verifique**: testes de status e eventos com store temporário; confirme que
logs e eventos não contêm segredos de fixtures. Rode o teste de segurança de
redação existente junto dos testes novos.

### Etapa 7: Validação ponta a ponta controlada

Crie um cenário de integração hermético com:

- workspace Git temporário;
- missão simples e objetivo inicial determinístico;
- planner/decomposer fake;
- dispatcher fake ou executor de task que não acesse provider real;
- simulação de reinício entre ticks;
- um caso de falha e um caso de kill-switch.

O cenário deve provar a sequência objetivo → tasks → reconciliação → conclusão
ou bloqueio, sem modificar a raiz do repositório. Só depois desse cenário
passar habilite o teste opcional com o runtime real e limites mínimos.

**Verifique**: teste de integração novo passa isoladamente e em
`uv run pytest tests/ -q --tb=short`; lint crítico, lint amplo, mypy e
`uv lock --check` passam conforme a tabela.

## Test plan

- `tests/test_goal_tracker.py`: concorrência, lease, recovery e idempotência.
- `tests/test_autopilot.py`: máquina de estados, seleção, materialização,
  retry, replan, budget, kill-switch, pausa e restart.
- `tests/test_runtime_supervisor.py`: spec, processo filho, backoff, status,
  stop e ausência de duplicação.
- `tests/test_autonomous_planner.py`: somente regressões da ponte; não remova
  os testes unitários existentes do planner.
- `tests/test_config_loader.py` ou o teste de contrato de configuração já
  existente: seção estrita, defaults e validação de `enabled`.
- Teste de integração: fluxo completo com fakes, sem rede, provider ou
  `BAUER_HOME` real.

Modele os testes de processo pelo padrão de `tests/test_runtime_supervisor.py`
e os de planner/tracker pelo padrão de `tests/test_autonomous_planner.py`.

## Critérios de pronto

- [ ] `bauer runtime start --dry-run` mostra o serviço autopilot somente quando
  solicitado/habilitado; o default existente permanece sem autopilot.
- [ ] Um objetivo persistido pode ser reivindicado por exatamente um
  controlador, retomado após lease expirado e não duplica tasks após restart.
- [ ] O autopilot nunca chama provider, shell ou fechamento de run diretamente;
  tasks passam pelo dispatcher e execuções continuam governadas pelo Kernel.
- [ ] Objetivo concluído, falho, bloqueado, replanejado e pausado aparecem no
  status e nos eventos sem vazar conteúdo sensível.
- [ ] Kill-switch, budget, approval boundary e SIGTERM impedem novo trabalho e
  deixam o estado recuperável.
- [ ] `uv run pytest tests/ -q --tb=short` passa.
- [ ] `uv run ruff check bauer/ --select E9,F63,F7,F82` passa.
- [ ] `uv run mypy bauer/` passa sem ampliar a lista de dívida.
- [ ] `uv lock --check` passa e `git diff --check` não reporta erros.
- [ ] Nenhum arquivo fora do escopo foi alterado sem justificativa; nenhum
  segredo, banco de runtime ou artefato de execução entrou no diff.
- [ ] README/runbook explica que o MVP trabalha sobre missão/objetivos
  declarados; propostas livres do modelo continuam desligadas por padrão.

## Condições de parada

Pare e reporte, sem improvisar, se:

- o caminho atual do dispatcher não passar pelo Kernel ou pelo worktree
  descrito acima;
- for necessário alterar a semântica de custódia do Kernel, liberar uma tool
  de alto risco sem aprovação ou ampliar um budget definido pelo operador;
- o backend de tasks não permitir criação idempotente e transacional;
- a recuperação de um processo não permitir distinguir lease expirado de
  trabalho vivo;
- o planner exigir execução inline para funcionar, em vez de poder produzir
  passos/task governados;
- a configuração estrita exigir migrar arquivos reais fora de fixtures;
- qualquer teste precisar de provider, rede, credencial ou `BAUER_HOME` real;
- a implementação precisar tocar em auth/TLS/credenciais ou em arquivos fora
  do escopo listado.

## Nota de manutenção

O autopilot será um novo consumidor do contrato Kanban/Kernel. Mudanças em
`TaskDispatcher`, `GoalTracker`, `runtime_cmd.py`, `RuntimeSupervisor` ou nos
limites do `/loop` devem atualizar os testes de integração do autopilot. A
geração de propostas pelo modelo, aprovação remota e relatórios por gateway
devem ser planos separados; não os acople silenciosamente ao primeiro MVP.
