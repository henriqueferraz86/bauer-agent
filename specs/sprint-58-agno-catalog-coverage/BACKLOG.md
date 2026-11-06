# BACKLOG — Sprint 58: cobertura do catálogo

- [x] `blocked`/`credential_kwargs` no manifesto; estado `blocked_by_policy`.
- [x] `init_args` na descoberta AST.
- [x] Injeção de credencial por nome e fallback `all=True` na factory SDK.
- [x] Classificação de todos os toolkits restantes (leitura revisada,
      bloqueados, incompatíveis) com justificativa.
- [x] Matriz de cobertura reprodutível, Markdown, snapshot + diff.
- [x] CLI `bauer tools capabilities` (busca/filtros/cobertura/diff).
- [x] Desktop: busca e filtro de estado.
- [x] `package-data` inclui o snapshot.
- [x] Contratos por família em `tests/test_agno_catalog_coverage.py`.
- [x] Gates: suíte completa, Ruff bloqueante e de estilo.

## Riscos

- Nomes de ações/kwargs revisados contra o AST do SDK instalado; o contrato
  falha se um bump mudar a assinatura.
- `npm run build` do Desktop não executado neste ambiente (sem
  `node_modules`); mudança restrita a estado React e filtros.
