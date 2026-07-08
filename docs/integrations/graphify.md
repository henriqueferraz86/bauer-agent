# Integração: Graphify

Repo: https://github.com/safishamsi/graphify (MIT, pacote PyPI `graphifyy`)

Converte um codebase (código + SQL + Terraform + docs/PDFs/imagens) num grafo
de conhecimento consultável — tree-sitter local pra código (sem LLM), Leiden
pra detecção de comunidades. Integra via servidor MCP stdio/HTTP real e
completo — decisão tomada: usar via `mcp.servers` do Bauer (`bauer/config_loader.py::McpSection`,
já suportado pelo `McpManager`/`mcp_call`), zero código Python novo no Bauer.

## Setup (fora do Bauer, uma vez por repo)

```bash
pip install "graphifyy[mcp]"   # extra além do pacote base
cd /caminho/do/seu/repo
graphify .                      # build inicial do grafo -> graph.html/graph.json/GRAPH_REPORT.md
```

## Config no Bauer

Descomentar em `config.yaml` (exemplo já em `config.yaml.example`):

```yaml
mcp:
  servers:
    graphify:
      command: ["graphify", "serve", "--transport", "stdio"]
      cwd: /caminho/do/seu/repo
      timeout: 30
```

## As 9 tools expostas via MCP

Confirmado lendo `graphify/serve.py` diretamente (não só o README):

| Tool | Uso |
|---|---|
| `query_graph` | Busca BFS/DFS por pergunta em texto — `question`, `mode`, `depth`, `token_budget`, `context_filter` |
| `get_node` | Lookup de um nó específico por label/ID |
| `get_neighbors` | Vizinhos de um nó, com `relation_filter` opcional |
| `get_community` | Nós de uma comunidade (cluster arquitetural) por `community_id` |
| `god_nodes` | Nós mais conectados do grafo — `top_n` |
| `graph_stats` | Contagens gerais, comunidades, distribuição de confiança |
| `shortest_path` | Caminho mais curto entre dois nós — `source`, `target`, `max_hops` |
| `list_prs` | PRs abertos no GitHub com impacto no grafo |
| `get_pr_impact` | Raio de impacto (blast radius) de um PR específico |
| `triage_prs` | PRs acionáveis com dados de impacto pra decidir ordem de merge |

## Confirmado na pesquisa: consulta ao grafo já construído **não exige chave de LLM**

Rastreado `_query_graph_text` → `_bfs`/`_dfs`/`_score_nodes` em `serve.py`:
travessia pura de grafo com NetworkX, zero chamada de LLM. LLM só entra em
`graphify label`/`cluster-only` (rotulagem de comunidade) e ingestão de
conteúdo não-código (PDFs/imagens) — não na consulta em si.

## Schema do `graph.json` (caso queira ler direto em vez de via MCP)

Formato `networkx.readwrite.json_graph.node_link_data`: chaves top-level
`nodes`, `links`, `directed`/`multigraph`/`graph`, mais `hyperedges` e
`built_at_commit` (SHA do git) adicionados pelo Graphify.

- **Node**: `id`, `label`, `file_type`, `source_file`, `source_location`
  (`"L<linha>"`), `type` opcional, `metadata` opcional, `community` (int),
  `community_name` opcional, `norm_label`.
- **Edge/link**: `source`, `target`, `relation` (`contains`/`inherits`/
  `imports`/`references`/`imports_from`/`re_exports`/`indirect_call`),
  `confidence` (`EXTRACTED` ou nível inferido por LLM), `weight`, `source_file`,
  `source_location`, `context`/`metadata` opcionais, `confidence_score`.

## Fora de escopo

`graphify hook install` (hook git post-commit/post-checkout pra manter o
grafo sempre atualizado) — opt-in, roda-se manualmente se quiser; o Bauer
não mexe em hooks git de ninguém sem pedido explícito. Instalação/desinstalação
confirmada limpa (bloco delimitado por marcadores no arquivo de hook, não
sobrescreve hooks pré-existentes).

## Como re-verificar

```bash
graphify --version
graphify serve --transport stdio &   # confirma que o servidor MCP sobe
# via Bauer: bauer chat -> mcp_call listando tools do server "graphify"
```

## Versão verificada

`v0.9.4` (release, 2026-07-01 — nota: existe uma tag `v1.0.0` no git sem
release correspondente ainda; conferir antes de assumir 1.0.0 como atual).
Ver `bauer/data/external_integrations.yaml`.
