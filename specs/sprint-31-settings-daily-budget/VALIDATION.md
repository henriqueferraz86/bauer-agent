# Validação — 2026-10-14

uv sync --frozen --extra dev aprovado. Suíte completa uv run pytest tests/ -q
--tb=short aprovada (exit0) após isolar scorecard do histórico local.31testes
novos API/Kernel passaram, incl requestinvalid/auth/CSRF/persistência/consumo
preservado, Kernel cacheado liberado ao elevarcap e zero prístino denied.
Ambos gates Ruff0.17.0 aprovados; mypy379 arquivos aprovado.
77testes SPA e TypeScript/Vitebuild aprovados, artefatos index-B_KeN5XJ.js.
Playwright Chromium156:10cenários aprovados, desktop/390px/save/load failures,
sem erros de página ou requests externos;2 respostas503intencionais testamretry.
Script validate_browser.py, relatório browser.json e capturas em validation/.

Nenhum limite real alterado, nenhum provider real chamado. O usuário decide
valor em Settings. API mantém consumo acumulado; intervalo é últimas24horas,
não meia-noite. Matriz remota CI acompanha PR separadamente.

Beelink: serviço reiniciado sem runs ativos, healthOK e bundle confirmado.
GET daily manteve limite2 e uso9.694501. PUTinválido -1 retornou422 antesescrita;
perfil/consumo inteiros iguais antes/depois. Nenhum limite real elevado.
