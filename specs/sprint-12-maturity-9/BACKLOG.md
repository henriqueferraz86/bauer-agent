# Backlog — Sprint 12

- [x] S12-01 Adaptar `UnifiedMemory` ao contrato `MemoryProvider`.
- [x] S12-02 Integrar o provider unificado ao provider local padrão.
- [x] S12-03 Criar diagnóstico `bauer maturity` com score por dimensão.
- [x] S12-04 Reduzir avisos Ruff informativos e adicionar gate de contagem.
- [x] S12-05 Rodar suíte, gates e atualizar documentação.

## Validação

- `uv sync --frozen --extra dev` — passou.
- `uv lock --check` — passou.
- `uv run pytest tests/ -n 0 -q --tb=short` — passou integralmente.
- `uv run pytest tests/ -q --tb=short` — passou integralmente com `xdist`.
- Ruff crítico e Ruff informativo — passaram; 0 ocorrências informativas.
- `uv run mypy bauer/` — passou em 369 módulos.
- `uv run bauer maturity --json` — score 9.2/10, todos os gates verdes.
