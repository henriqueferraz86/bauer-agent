# BACKLOG — Sprint 73: pesquisa somente leitura instalável

- [x] Medir o estado inicial (`--coverage`: ready=6, needs_configuration=17,
      dependency_missing=18, blocked_by_policy=53).
- [x] Conferir imports dos toolkits no Agno 3.0.10 e nomes de pacote.
- [x] Escolher versões (≥14 dias, licença) e travar no `uv.lock`; provar
      install padrão idêntico.
- [x] `brave-search` descartado (metadados inválidos, httpx<0.26).
- [x] Wikipedia HTTPS; arXiv leitura em memória; yfinance cache; Tavily por
      variante.
- [x] SearXNG nativo com host do operador + SSRF; desbloqueado.
- [x] Testes sempre ativos e testes com libs reais (socket bloqueado).
- [ ] Step no `install-check` com `BAUER_REQUIRE_AGNO_EXTRAS=1` — pendente:
      push de `.github/workflows/` recusado (token sem escopo `workflow`).
      Step proposto, após "Coleta de testes sem erro de plugin":

      ```yaml
      - name: Extras Agno com as bibliotecas reais (plano 067)
        env:
          BAUER_REQUIRE_AGNO_EXTRAS: "1"
        run: uv run pytest tests/test_agno_research_extras_installed.py tests/test_agno_devops_extras_installed.py -q -n 0
      ```
- [x] Gates: lock, sync, pytest (dev e dev+agno-research), ruff, mypy.

## Resultado medido (Agno 3.0.10)

| Ambiente | ready | needs_configuration | dependency_missing | blocked_by_policy |
|---|---|---|---|---|
| antes | 6 | 17 | 18 | 53 |
| depois, sem extra | 6 | 18 | 18 | 52 |
| depois, com `agno-research` | 10 | 23 | 9 | 52 |

`agno.openweather` também sai de `dependency_missing` com o extra (`requests`
vem junto) e fica `needs_configuration`.

## Versões travadas (upload no PyPI, licença)

| Pacote | Versão | Upload | Licença |
|---|---|---|---|
| wikipedia | 1.4.0 | 2014-11-15 | MIT |
| arxiv | 4.0.2 | 2026-10-26 | MIT |
| pypdf | 6.21.2 | 2026-10-21 | BSD-3-Clause |
| yfinance | 1.7.0 | 2026-08-26 | Apache-2.0 |
| youtube-transcript-api | 1.2.4 | 2026-01-29 | MIT |
| tavily-python | 0.8.5 | 2026-10-06 | MIT |
| exa-py | 2.26.0 | 2026-10-19 | MIT |
| google-search-results | 2.4.2 | 2023-03-10 | MIT |
| linkup-sdk | 0.24.0 | 2026-10-22 | MIT |
| transitivos novos | curl-cffi 0.16.3 (MIT), pandas 3.1.0 (BSD-3), peewee 4.5.3 (MIT), multitasking 0.0.13 (Apache-2.0), tiktoken 0.14.0 (MIT), defusedxml 0.7.1 (PSF), pytz 2026.5 (MIT), tzdata 2026.5 (Apache-2.0) | ≤ 2026-10-22 | — |

## Riscos

- `wikipedia` (2014) não usa timeout em `requests.get`; o timeout do
  ToolRouter corta a chamada.
- `yfinance`/`curl-cffi` são pesados (pandas, binário curl): ficam só no
  extra.
- TOCTOU de DNS no SearXNG (documentado no SPEC).
