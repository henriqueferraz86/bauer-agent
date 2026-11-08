# ARCHITECTURE — Sprint 64

## Setup do turno governado

```text
AgentBackend.process ─▶ worker ─▶ _execute_turn ─▶ run_governed ─▶ _turno_cru
                                                     │
                                                     ├─ resolve_team_intent ─▶ TeamRegistry.list
                                                     └─ decide_route ─▶ decision_catalog ─▶ agents/teams .list
```

Antes, `TeamRegistry.list` fazia o seguinte para cada time:

```text
for agent in team.agents:
    agent_registry.get(agent)    # list() inteiro: lê e parseia todos os specs
```

Agora:

```text
known = {id, name de agent_registry.list()}   # uma vez por listagem
missing = [a for a in team.agents if a not in known]
```

`load_spec_yaml(path)` em `bauer/core/runtime/agent_registry.py`:

- lê os bytes do arquivo e calcula o `sha256`;
- consulta um cache de módulo protegido por lock e limitado a 512 entradas
  (é limpo ao encher);
- na falta, faz `yaml.load(..., Loader=CSafeLoader|SafeLoader)`;
- devolve `copy.deepcopy`, porque `agent_spec_from_mapping` e
  `team_spec_from_mapping` não podem compartilhar estado mutável entre
  chamadas.

O `TeamRegistry._read` e o `RuntimeAgentRegistry._read_specs` usam essa
função. A validação continua a mesma: agent desconhecido ainda levanta
`TeamRegistryError`.

## Inventário Docker no boot

`desktop_api.build_desktop_router` → `sync_discovered_docker_targets` →
`discover_docker_containers(runner=None)`. Com `BAUER_DOCKER_DISCOVERY` em
`0/false/off/no`, a função devolve `None` antes de qualquer subprocesso. Os
callers já tratam `None` como "daemon indisponível, não mexer no cadastro".
Um `runner` explícito sempre é honrado.

## Testes

| Arquivo | Mudança |
|---|---|
| `tests/conftest.py` | `BAUER_DOCKER_DISCOVERY=0` no nível do módulo |
| `tests/test_channel_execution.py` | `charge_generation_from`; esperas de 30s |
| `tests/test_approval_chat_e2e.py` | `ANTI_HANG = 30` em `pending()` e nos joins; budget de 15s |
| `tests/test_server_accounting_producers.py` | `terminal_run` e polling de `/loop` com 30s |
| `tests/test_kernel_serve_stream.py` | polling de aprovação por deadline de 30s |
| `tests/test_sprint64_runtime_registry_cost.py` | regressões: contagem de parses, edição visível, cópia isolada, toggle Docker |
