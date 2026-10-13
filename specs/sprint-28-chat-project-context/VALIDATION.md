# Validação — 2026-10-13

Todos os critérios de aceite atendidos.

## Automação

- `uv sync --frozen --extra dev`: exit 0.
- Backend: 162 testes direcionados de compatibilidade e 14 checks finais,
  incluindo 13 regressões de contexto, ambos engines/transportes e tool real
  controlada; exit 0.
- `uv run pytest tests/ -q --tb=short`: exit 0 após registrar a nova rota no
  mapa de contrato HTTP. Skips existentes e avisos FastAPI `on_event` mantidos.
- Ambos os gates Ruff 0.17.0 de AGENTS.md: exit 0.
- `uv run mypy bauer/`: exit 0, 377 módulos sem erros.
- `npm test` em desktop: 41 testes, 8 arquivos, exit 0.
- `npm run build`: TypeScript e Vite, exit 0; artefatos em `bauer/static`.
- `git diff --check`: exit 0.

## Navegador e serviço

`validate_browser.py` usa Chromium já instalado e API local controlada: projeto
antes do envio, confirmação SSE, mudança do ativo com sessão fixa, reload,
Nova sessão, largura móvel, fallback e indisponibilidade. Nenhum erro JavaScript
ou requisição externa. Resultado e screenshots em `validation/`.

Reiniciado `bauer-serve.service` após confirmar ausência de turnos ativos.
Consulta real `/chat/context` devolve investimentosOS em
`/home/henrique/projetos/investimentosOS`, tanto antes do primeiro envio quanto
para a sessão `b1633939`. Sessão antiga `fce88e25` permanece github-ai-agents e
avisa que Nova sessão usa investimentosOS.

Smoke do navegador contra `localhost:8000` confirmou a faixa de projeto ativo
e o vínculo de `b1633939`, sem erros JavaScript e sem chamadas de provider.
`chat-live-active.png`, `chat-live-session.png` e `live-browser.json` registram
essa evidência. Testes automatizados nunca carregam config/provider reais.

## Integração na master

O primeiro CI do PR #156 passou build e 41 testes da SPA, mas o audit
bloqueou cinco dependências transitivas já presentes no lock. Atualizadas
somente brace-expansion (5.0.12), http-cache-semantics (4.3.0),
ip-address (10.7.4), source-map-js (1.2.2) e undici (6.29.0). O override
de brace-expansion exige agora a versão corrigida. Após `npm ci`,
`npm test` e `npm run build` passaram; `npm audit --audit-level=high`
reportou zero vulnerabilidades. Os artefatos compilados permaneceram iguais.
