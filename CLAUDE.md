# CLAUDE.md

Leia [`AGENTS.md`](AGENTS.md) integralmente antes de trabalhar: ele é a
instrução canônica e obrigatória deste repositório.

Bauer Agent é um runtime adaptativo para LLMs locais e cloud. Consulte o
[`README.md`](README.md) para produto, instalação e modos de uso.

## Verificação

```bash
uv sync --frozen --extra dev
uv run pytest tests/ -q --tb=short
uv run ruff check bauer/ --select E9,F63,F7,F82
```

Use `uv` e o lockfile versionado; nunca substitua esse fluxo por `pip`.

## Regras de trabalho

- A suíte é hermética: preserve o isolamento de config/home definido em
  `tests/conftest.py` e nunca permita provider real nos testes.
- Siga `AGENTS.md` para escopo, ferramentas, convenções, segurança e commits.
- Leia o plano aplicável em `plans/` antes de executá-lo e atualize seu status
  em `plans/README.md` quando concluído.
