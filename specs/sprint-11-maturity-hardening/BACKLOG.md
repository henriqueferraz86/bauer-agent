# Backlog — Sprint 11

- [x] S11-01 Criar fachada de memória e testes de dual-write/busca.
- [x] S11-02 Integrar a fachada aos comandos de memória.
- [x] S11-03 Extrair decisão e payload de rota compartilhados.
- [x] S11-04 Endurecer servidor Kanban para execução paralela no Windows.
- [x] S11-05 Rodar validações completas e registrar o resultado.

## Validação

- `uv sync --frozen --extra dev` — passou.
- `uv run pytest tests/ -n 0 -q --tb=short` — passou integralmente.
- `uv run pytest tests/ -q --tb=short` — passou integralmente com `xdist`.
- `uv run ruff check bauer/ --select E9,F63,F7,F82` — passou.
- `uv run mypy bauer/` — passou.
- Ruff informativo — mantém 55 avisos preexistentes fora do gate bloqueante.
