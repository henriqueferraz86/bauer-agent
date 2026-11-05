# ARCHITECTURE — Sprint 53

## Componentes

- `families/browser.py`
  - `BrowserProfile`/`BrowserSettings` (estritos; perfil autenticado recusado).
  - Política pura: `host_allowed`, `url_allowed`, `request_allowed`.
  - `install_guards(page, profile)`: `page.route("**/*")` + `download.cancel`.
  - `close_session(router)`: fecha página/contexto/Playwright na thread do
    browser e encerra o executor.
  - `_inner(router, tool, args)`: executa `router._browser_<tool>` na thread
    dedicada (`router._get_browser_executor()`).
- `bridge.FactoryContext.router_factory`: factories podem compor tools Bauer
  existentes usando o router da sessão do agente.

## Fluxo

```text
modelo → Function bauer_browser__navigate
  → ToolRouter.execute (grant por agente, policy browser.navigate, budget,
    eventos sem args)
  → navigate(): perfil → _ensure_browser (thread do browser) → install_guards
  → url_allowed? (senão ToolError antes do goto)
  → _inner(browser_navigate) → route handler decide cada requisição
  → _current_url via snapshot → ainda no perfil? (senão close_session)
  → policy.evaluated {domain, decision}
```

Interações (`click/type_text/press`) seguem o mesmo caminho com operação
`os.ui_control`; a regra padrão `ask` cria o pedido de aprovação e a ação só
roda quando aprovada.

## Testes

Página fake que imita a API sync do Playwright usada pelo mixin; as
implementações reais `browser_*` rodam sobre ela. Nenhum Chromium é
iniciado.
