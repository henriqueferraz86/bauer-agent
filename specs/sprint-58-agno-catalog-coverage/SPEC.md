# SPEC — Sprint 58: cobertura do catálogo e paridade de integrações (plano 057, sprint "26")

Depende das sprints 50–57. Executada como 58 (última do trilho 50–58).

## Problema

Após as sprints 50–57, ~100 toolkits do Agno 3.0.10 ainda estavam
`unreviewed`: visíveis, mas sem estado justificado. O plano exige que nenhuma
integração da versão travada fique sem estado e justificativa, que bumps do
SDK gerem diff revisável e que o catálogo amplo seja navegável sem carregar
os conectores.

## Escopo

1. `families/catalog_coverage.py` classifica **todos** os toolkits restantes:
   - **leitura revisada** (24 toolkits de APIs públicas fixas: buscas,
     clima/mapas, finanças, mídia de busca), ações uma a uma, operação
     `network.http`; ações que leem URL arbitrária, baixam arquivos ou
     disparam tarefas pagas ficam fora com nota;
   - **`blocked_by_policy`** (novo campo `ToolkitPolicy.blocked`): conectores
     que misturam leitura com escrita/envio (Slack, Gmail, Jira, Notion…),
     até a revisão do piloto de escrita (STOP da sprint 57); SaaS com PII
     (Zendesk, Shopify, Scavio); geração de mídia paga (custo fora do
     orçamento do Kernel);
   - **`incompatible`** com motivo: runtime do SDK que duplica Kernel/
     scheduler/memória/HITL/AgentOS (exigiria ADR), sandboxes de execução
     remota, transações blockchain, acesso a câmera, ferramentas de arquivo
     sem escopo.
2. Credenciais de toolkits SDK: `credential_kwargs` injeta no construtor o
   valor resolvido por nome (env → `bauer credential`); kwargs validados
   contra os parâmetros do `__init__` lidos por AST (`init_args`).
   `enable_*` desligado por padrão é tratado com `all=True` + `include_tools`
   (registra só as ações concedidas).
3. `coverage.py`: matriz reprodutível (por estado e categoria, inclusive
   módulos de infraestrutura do SDK como `not_a_toolkit`) e Markdown;
   `sdk_snapshot.json` versionado + `snapshot_diff()` (toolkits
   adicionados/removidos, funções/env/classes alteradas). Bump do Agno ⇒
   diff no teste; nada novo é habilitado (toolkit novo = `unreviewed`,
   função nova fora do manifesto).
4. CLI `bauer tools capabilities` com `--search`, `--state`, `--category`,
   `--json`, `--coverage [--markdown|--json]`, `--diff`, `--write-snapshot`
   (dev). Desktop: busca e filtro por estado no catálogo.
5. Contratos reutilizáveis por família (parametrizados sobre todo toolkit SDK
   revisado): listagem lazy (sentinela de import), toolkit presente no SDK,
   risco de leitura, ação não classificada rejeitada, kwargs de credencial
   válidos, credencial ausente ⇒ `needs_configuration`, dependência ausente ⇒
   erro tipado, credencial injetada sem vazar para eventos.

## Resultado medido (Agno 3.0.10)

`bauer tools capabilities --coverage`:
`blocked_by_policy=53, dependency_missing=18, incompatible=67,
needs_configuration=17, not_a_toolkit=17, ready=6, unreviewed=0`.

## Fora de escopo

- Liberar escrita em outros conectores (STOP do piloto).
- Instalar dependências opcionais.

## Decisões (sem operador — opção mais segura)

- Em dúvida entre leitura e efeito, o toolkit fica `blocked_by_policy`.
- Toolkits que tocam dados pessoais de terceiros ficam bloqueados até haver
  política de privacidade/escopo específica.
- Snapshot embarcado no pacote (`package-data`); `uv.lock` inalterado.

## Critérios de aceite

- [x] Nenhuma integração da versão fixada sem estado e justificativa.
- [x] Integrações suportadas configuráveis por agente sem editar Python
  (`agno.<id>[:acoes]` + `agno_capabilities.yaml`).
- [x] Incompatíveis nomeadas e explicadas; extras carregados sob demanda.
- [x] Bump do Agno produz diff revisável sem ampliar permissões.
- [x] Suíte e Ruff verdes.
