# Plano 067 — Catálogo Agno, fase 2: dependências opcionais e conectores de escrita

**Status:** IN PROGRESS

**Prioridade:** P2

**Esforço total estimado:** M, dividido em sprints 73–75

**Referências:** plano 057 (sprints 50–58), `bauer tools capabilities`,
`specs/sprint-57-agno-write-connector/` (padrão do primeiro conector de
escrita) e `specs/sprint-58-agno-catalog-coverage/` (estados do catálogo).

## Problema e objetivo

O plano 057 deixou o catálogo inteiro revisado, mas pouco utilizável. Em
2026-11-10, `bauer tools capabilities` mostrava:

| Estado | Toolkits | Motivo |
|---|---|---|
| `ready` | 6 | calculator, duckduckgo, hackernews, pubmed, websearch, bauer.data |
| `needs_configuration` | 17 | Credencial ou configuração ausente |
| `dependency_missing` | 18 | Dependência opcional não instalável pelo lock |
| `blocked_by_policy` | 53 | Escrita sem caminho de aprovação, dados pessoais ou mídia paga |

O plano 064 fechou as falhas de aprovação, e agora dá para ampliar a
superfície com segurança. O objetivo é tornar instaláveis as dependências
opcionais de leitura e abrir os primeiros conectores de escrita depois do
GitHub, sempre com aprovação da chamada exata.

## Sprints

### Sprint 73 — Pesquisa somente leitura instalável

- Criar extras opcionais no `pyproject.toml`, com versões travadas no
  `uv.lock`, para os toolkits de leitura que hoje são `dependency_missing`:
  - sem credencial: `wikipedia`, `arxiv` (+ `pypdf`), `yfinance` e
    `youtube_transcript_api`;
  - com credencial: `tavily`, `exa`, `brave`, `serpapi` e `linkup`.
- Com o extra instalado, os toolkits sem credencial ficam `ready` e os com
  credencial ficam `needs_configuration`.
- Rever o bloqueio do `agno.searxng`: é uma busca auto-hospedada e útil no
  `--local`. Ele pode virar somente leitura com host configurado pelo
  operador e as checagens de SSRF já existentes.
- Ações revisadas uma a uma, como no plano 057. Os downloads de PDF do arXiv
  ficam confinados ao workspace.
- Nenhuma dependência nova entra no install padrão. Versões com menos de 2
  semanas de publicação não entram.

### Sprint 74 — DevOps e GitHub instaláveis

- O mesmo tratamento para `bauer.aws` (`boto3`), `bauer.kubernetes`
  (`kubernetes`) e `agno.github` (`pygithub`), num extra próprio.
- Os testes provam que os guards de leitura das sprints 51 e 54 valem com a
  biblioteca real instalada. Um transporte falso substitui a rede.

### Sprint 75 — Conectores de escrita sob aprovação

- Seguir o padrão da sprint 57 em três conectores de escrita de alto valor:
  - postar mensagem no Slack;
  - criar issue no Linear;
  - criar issue no GitLab.
- O padrão inclui:
  - aprovação humana obrigatória, que nenhuma policy custom contorna;
  - aprovação válida só para os argumentos exatos e uma única vez;
  - recusa de chamada fora do escopo antes mesmo de pedir aprovação;
  - idempotência contra retry.
- Os conectores usam httpx direto, sem SDK novo.
- Os demais conectores de escrita, os de dados pessoais (Gmail, Drive,
  Calendar, WhatsApp) e os de mídia paga continuam `blocked_by_policy`.

## Fora de escopo

- Ativar qualquer conector por padrão.
- AgentOS Control Plane ou banco Agno, que exigem ADR conforme o plano 057.

## Validação obrigatória por sprint

```bash
uv sync --frozen --extra dev
uv run pytest tests/ -q --tb=short
uv run --with ruff==0.17.0 ruff check bauer/ --select E9,F63,F7,F82
uv run --with ruff==0.17.0 ruff check bauer/ --select E,F,W --ignore E501,W291,W293,E302,E303
uv run mypy bauer/
```

Quando as sprints 73 e 74 mudarem dependências: `uv lock` versionado e o
install limpo do CI verde.
