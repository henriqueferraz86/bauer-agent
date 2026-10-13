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

## Correção do JSON na resposta (compatibilidade)

Suíte Python completa aprovada novamente;39 regressões SSE/static aprovadas.
62 testes SPA + TypeScript/Vite aprovados. Teste de rede usa o parser SSE real
com bytes UTF-8 fragmentados, metadata interna e evento desconhecido, garantindo
resposta somente textual. Snapshot antigo continua aceito. Index no-store
coberto pelo teste que baixa os assets reais. Dois Ruff e mypy379 aprovados.
Browser11 cenários aprovados: histórico contaminado é reparado preservando
resposta e o painel; captura `validation/chat-formatting-fixed.png`.

A SPA atualizada foi confirmada no servidor local. Havia uma execução ativa,
portanto o processo do servidor foi preservado; código backend novo será
carregado no próximo restart normal. A SPA aceita também o wire anterior.
