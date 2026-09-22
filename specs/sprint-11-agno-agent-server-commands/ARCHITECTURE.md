# Arquitetura

O `bauer agent` recebe o Kernel já construído por `agent_cmd.py`. O novo
handler de slash recebe esse objeto e deriva o diretório de runtime do
`RunManager` do Kernel. Ele constrói uma única instância de
`AgnoTeamOrchestrator` por chamada, lista as specs por `TeamRegistry` e executa
com `run()`. Assim, o comando não cria um caminho paralelo de execução.

O server passa o mesmo Kernel, EventBus e diretório de runtime para uma camada
de rotas de times montada dentro do router desktop `/api`. As rotas de consulta
usam registries e stores; a execução usa `AgnoTeamOrchestrator.run()`; o
streaming usa `AgnoTeamOrchestrator.stream()`; cancelamento chama
`kernel.cancel()` e o estado persistido continua sendo a fonte de verdade.

Contrato HTTP:

- `GET /api/teams`
- `GET /api/teams/{team_id}`
- `GET /api/teams/{team_id}/budget`
- `POST /api/teams/{team_id}/runs` com `{task, session_id?, user_id?}`
- `GET /api/teams/runs/{run_id}`
- `GET /api/teams/runs/{run_id}/events`
- `POST /api/teams/runs/{run_id}/cancel`

O frontend ganha uma tela `/teams` e usa polling curto para o estado e eventos
da run. A chamada é deliberadamente síncrona na primeira versão para preservar
o contrato completo do Kernel e simplificar a recuperação após refresh; a
timeline persiste no backend e pode ser reaberta pelo run id.

## Riscos e mitigação

- Provider indisponível: o erro volta como `failed` e fica registrado na run.
- Kernel bloqueia por política/budget: a API retorna o resultado governado com
  status correspondente, sem chamar o adapter.
- Frontend desconecta: a run e os eventos já persistidos continuam consultáveis.
- Configuração antiga sem Agno: o runtime mantém o fallback compatível já
  coberto na sprint 10; a tela sinaliza erro de execução sem corromper estado.
