# BACKLOG — Sprint 50: catálogo Agno completo e ponte ToolRouter

- [x] Verificar entrega da sprint 18 (`specs/sprint-18-agno-capability-catalog/`)
      e não refazer: endpoint, painel, rejeição de nome desconhecido mantidos.
- [x] `discovery.py`: AST de `agno/tools`, imports opcionais (sem stdlib),
      dicas `pip install`, env vars, funções registradas, subclasses indiretas.
- [x] `manifest.py`: tipos, `default_actions` só leitura, `agno.calculator`
      revisado, duplicatas de tools Bauer `incompatible`.
- [x] `settings.py` + `credentials.py`: arquivo próprio estrito, só leitura,
      erros sem valores.
- [x] `catalog.py`: estados, refs `id[:acoes]`, resolução fail-closed,
      fluxo de aprovação obrigatório para ações com efeito.
- [x] `bridge.py`: factories por allowlist, SDK com `include_tools`,
      `Function` → `router.execute_native_call`, saída limitada, erro tipado.
- [x] `tool_router.py`: registro por instância, operação de policy,
      telemetria sem valores, grant por agente no contexto.
- [x] `agno_adapter.py`: ramo governado em `_map_tools`.
- [x] `agno_catalog.py`: toolkits individuais no lugar de famílias.
- [x] `bauer doctor`: painel "Capabilities Agno".
- [x] Desktop: rótulos de estado e "faltando".
- [x] Testes herméticos: `tests/test_agno_capability_bridge.py`; ajustes de
      semântica em `test_agno_catalog.py`/`test_agno_runtime_adapter.py`.
- [x] Gates: `uv sync --frozen --extra dev`, suíte completa, Ruff bloqueante e
      de estilo.

## Riscos conhecidos

- A heurística AST de funções registradas é informativa; o manifesto é a
  autoridade (uma função nova do SDK nunca é habilitada sozinha).
- `npm run build` do Desktop não roda neste ambiente (sem dependências
  instaladas); a mudança é de rótulos e tipos opcionais.
