# ARCHITECTURE — Sprint 73

| Peça | Arquivo |
|---|---|
| Extra `agno-research` | `pyproject.toml`, `uv.lock` |
| Factories/guards (wikipedia, arxiv, yfinance, tavily, searxng) | `agno_capabilities/families/research_extras.py` |
| Políticas wikipedia/arxiv (dica de instalação, `read_arxiv_papers`) | `families/research.py` |
| Dicas de instalação, `brave` explicado, `searxng` desbloqueado | `families/catalog_coverage.py` |
| Testes sempre ativos | `tests/test_agno_research_extras.py` |
| Testes com libs reais | `tests/test_agno_research_extras_installed.py` |
| CI (pendente, ver BACKLOG) | `.github/workflows/ci.yml` (`install-check`) |

```text
agno.arxiv:read_arxiv_papers(id_list, pages_to_read)
  → ponte (network.http, scanner de segredo) → validate_arxiv_ids
  → arxiv.Client (export.arxiv.org, parser lxml sem entidades) → metadados
  → fetch_arxiv_pdf: https://arxiv.org/pdf/<id>, sem follow automático,
    redirect só https + host arXiv, stream com teto → %PDF?
  → pypdf.PdfReader(BytesIO) → páginas (teto 40) → JSON (ponte trunca)

agno.searxng:<categoria>(query, max_results)
  → settings (host do operador, allow_private_network, engines, limites)
  → ensure_searxng_target: url_safety.check_url + bloqueio fixo de
    metadata/link-local no host e nos IPs resolvidos
  → httpx (sem redirect, verify=shared_ssl_context, stream com teto)
  → resultados recortados
```

Costuras de teste: `_SEARXNG_TRANSPORT`, `_ARXIV_PDF_TRANSPORT`,
`_ARXIV_CLIENT_FACTORY`. Nos testes com libs reais, `requests` é atendido por
um `HTTPAdapter.send` falso e qualquer `socket.connect` falha o teste.

Nenhuma factory importa o SDK ou a lib opcional no import do módulo: a
listagem do catálogo continua lazy (contrato da sprint 58).
