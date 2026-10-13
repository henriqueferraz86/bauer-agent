# Validação — 2026-10-13

- `uv sync --frozen --extra dev`: aprovado.
- `uv run pytest tests/ -q --tb=short`: suíte completa aprovada (exit0).
- 34 regressões focadas projector/SSE/delegação/Agno: aprovadas, incluindo
  member tools e sink acessório indisponível. Nenhum provider real chamado.
- 175 testes de delegação e regressões existentes: aprovados.
- Dois gates Ruff0.17.0 exigidos pelo AGENTS.md: aprovados.
- `uv run mypy bauer/`: aprovado,379 arquivos.
- `npm test`:59 testes aprovados; `npm run build`: TypeScript/Vite aprovados.
- Playwright Chromium156:10 verificações herméticas, sem erros de página e sem
  requests externos;503 intencional valida perda de consulta/recuperação.
  Script `validate_browser.py`, relatório e capturas em `validation/`.
- Serviço local reiniciado sem runs ativos. Smoke somente leitura confirmou
  `/chat/activity` vazio sem sessão, run/estado persistido por sessão e29 tools.

Estados sem informação são explícitos; não se infere sucesso pelo texto ou
pelo fim do stream. A matriz remota CI acompanha a PR separadamente.
