# BACKLOG — Sprint 53: browser governado

- [x] Decidir backend único (Playwright Bauer); Browserbase/AgentQL
      `incompatible`.
- [x] Perfis estritos com allowlist de domínios; perfil autenticado recusado.
- [x] Guardas na página: navegação fora do perfil, SSRF em subrecursos,
      downloads cancelados.
- [x] Re-checagem de URL após ações; saída do perfil fecha a sessão.
- [x] Interações como `write`/`os.ui_control` com fluxo de aprovação.
- [x] Fechamento da sessão em cancelamento/erro.
- [x] Auditoria só com domínio.
- [x] `FactoryContext.router_factory` na ponte.
- [x] Testes `tests/test_agno_browser_capability.py` com página fake.
- [x] Gates: suíte completa, Ruff bloqueante e de estilo.
