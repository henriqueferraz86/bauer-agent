# SPEC — Sprint 50: catálogo Agno completo, capabilities e ponte ToolRouter (plano 057, sprint "18")

O plano 057 numera suas sprints de 18 a 26; esses números já foram usados por
outras specs. Este trilho usa 50–58, na mesma ordem do plano. Esta sprint
corresponde à "Sprint 18 — Catálogo Agno completo, capacidades e ponte para o
ToolRouter".

## O que já existia (não refeito)

`specs/sprint-18-agno-capability-catalog/` entregou a fundação:
`bauer/core/runtime/agno_catalog.py` (7 tools Bauer + **pastas** de família do
SDK, sem import), rejeição de nome desconhecido em `_map_tools`, endpoint
`/api/agno/catalog` e o painel no Desktop. Lacunas medidas no `origin/master`:

- o catálogo listava só 7 diretórios (`code`, `file`, `google`, …); os ~150
  toolkits em módulos (`github.py`, `docker.py`, `sql.py`…) eram invisíveis;
- nenhum toolkit do SDK podia ser materializado — não havia manifesto de
  ações/risco nem ponte para o ToolRouter;
- não havia estados `incompatible`/`unreviewed`/`blocked_by_policy`, nem
  declaração de ações por agente, nem diagnóstico no `bauer doctor`.

## Objetivo

Tornar **todo** o catálogo do Agno instalado (3.0.10 no `uv.lock`) descobrível
com estado e requisitos, e permitir habilitar por agente apenas ações
**revisadas**, executadas sempre através do ToolRouter do agente.

## Escopo

1. Descoberta estática (AST) de cada módulo de `agno/tools`: classes Toolkit,
   imports opcionais, dicas de instalação, nomes de variáveis de ambiente e
   funções registradas — sem importar SDK nem conectores.
2. Manifesto revisado em código (`ToolkitPolicy`/`ActionPolicy`): ações
   explícitas, risco por ação, operação do PolicyEngine, dependências,
   seção de configuração, credenciais (nomes) e motivo de incompatibilidade.
   Integração sem manifesto = `unreviewed`: visível, inerte.
3. Estados públicos: `enabled`, `ready`, `needs_configuration`,
   `dependency_missing`, `blocked_by_policy`, `incompatible`, `unreviewed`.
4. Referência por agente: `agno.<id>` (só ações `read` padrão) ou
   `agno.<id>:acao1,acao2`. ID/ação desconhecidos, não revisados, não
   configurados ou com efeito sem fluxo de aprovação falham **antes** de montar
   o Agent/Team (RuntimeAdapterError).
5. Ponte: cada ação vira tool **da instância** do ToolRouter do agente
   (`register_capability_tool`), com operação de policy, risco e timeout do
   manifesto. A `Function` entregue ao Agno só chama
   `router.execute_native_call` — contexto, PolicyEngine, aprovação, budget,
   timeout, scanner de segredos e eventos valem como para tools Bauer.
6. Telemetria de tools de capability: só nomes de argumentos, capability,
   ação, operação e duração; sem valores, sem `result_preview`, erro só com
   tipo.
7. Configuração do operador em arquivo próprio,
   `$BAUER_HOME/agno_capabilities.yaml` (`BAUER_AGNO_CAPABILITIES` sobrepõe),
   estrito (`extra=forbid`), só leitura — nunca criado/reescrito por
   install/update. Credenciais por **nome** (env → `bauer credential`).
8. `bauer doctor` mostra contagem por estado, arquivo de config, o que falta
   para habilitar e declarações inválidas de agentes; Desktop exibe os novos
   estados e o que falta.
9. Primeiro toolkit SDK ligado pela ponte: `agno.calculator` (puro). Toolkits
   que duplicariam tools Bauer fora do sandbox (`shell`, `python`,
   `local_file_system`, `file`, `coding`, `memory`) ficam `incompatible` com o
   substituto Bauer indicado.

## Fora de escopo

- Classificar os demais ~140 toolkits (sprints 51–58; a 58 fecha cobertura).
- Qualquer escrita externa (fica `blocked_by_policy` até a sprint 57).
- AgentOS control plane/DB Agno (exige ADR — plano 057).
- Seção nova no `config.yaml` (estrito e compartilhado; decisão abaixo).

## Decisões tomadas (sem operador disponível — opção mais segura)

- **Arquivo próprio em vez de seção no `config.yaml`:** evita `ValidationError`
  de boot em máquinas com código antigo e elimina migração; ausência do arquivo
  = nada habilitado = comportamento anterior idêntico.
- **Grant por agente substitui o allowlist de contexto só para tools de
  capability**: o contexto `worker` é allowlist e não conhece nomes
  `agno_*`; a tool só existe no router do agente que a declarou. `deny` por
  nome no `tool_policy.yaml` continua valendo.
- **Ações com efeito exigem fluxo de aprovação registrado** em código
  (`enable_approval_flow`); sem ele, `blocked_by_policy`, mesmo com regra
  `allow` em policy customizada.

## Critérios de aceite

- [x] Catálogo lista todos os toolkits do Agno 3.0.10 com estado; não importa
  `agno.tools.*` nem conectores (teste sentinela).
- [x] Integração não classificada fica visível e inativa; materializá-la falha.
- [x] Dois agentes com declarações diferentes recebem só suas ações.
- [x] Chamada negada (contexto ou PolicyEngine) não executa o conector.
- [x] Eventos/audit de capability não contêm argumentos, resultados ou texto
  de erro do conector.
- [x] Dependência opcional ausente marca só aquela capability; boot segue.
- [x] Sem `agno_capabilities.yaml`, comportamento e tools Bauer inalterados; o
  arquivo nunca é criado.
- [x] Suíte, Ruff bloqueante e de estilo verdes.

## Plano de validação

`tests/test_agno_capability_bridge.py` (+ ajustes em `test_agno_catalog.py` e
`test_agno_runtime_adapter.py` para a semântica de toolkit individual);
`uv sync --frozen --extra dev`; suíte completa; Ruff E9/F63/F7/F82 e E/F/W.
Build do Desktop: mudança mínima de TSX (labels); `npm run build` não
executado neste ambiente (sem `node_modules`/rede) — registrar no PR.
