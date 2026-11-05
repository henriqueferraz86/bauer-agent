# BACKLOG — Sprint 51: pesquisa e desenvolvimento read-only

- [x] Políticas de busca web (websearch, duckduckgo, hackernews, pubmed,
      wikipedia, arxiv) com ações de leitura somente.
- [x] Extratores de URL arbitrária `incompatible` → `bauer.web_fetch`;
      `bauer.web_fetch` no catálogo Bauer.
- [x] Ponte: recusa de segredo em argumentos de operações de saída.
- [x] `bauer.github` nativo: 8 ações de leitura, allowlist, sanitização de
      busca e caminho, erros tipados, credencial por nome.
- [x] `agno.github` SDK: subconjunto de leitura + guarda de `repo_name`.
- [x] `ToolkitPolicy.dynamic_actions` para ações enumeradas pelo admin.
- [x] `bauer.mcp`: settings estritos, ações dinâmicas, ambiente mínimo,
      start-on-call, saída não confiável, timeout/malformado tipados;
      `agno.mcp`/`agno.mcp_toolbox` `incompatible`.
- [x] Testes: `tests/test_agno_research_capabilities.py` (GitHub com
      MockTransport, servidor MCP stdio fake, busca com SDK monkeypatchado) e
      contrato `tests/test_agno_capability_manifest.py` (toda ação revisada
      existe na classe instalada; efeito ⇒ aprovação; operações conhecidas).
- [x] Gates: suíte completa, Ruff bloqueante e de estilo.
