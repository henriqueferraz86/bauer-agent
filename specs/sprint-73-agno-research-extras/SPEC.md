# SPEC — Sprint 73: pesquisa somente leitura instalável (plano 067)

Depende das sprints 51 e 58 (políticas de pesquisa revisadas, catálogo com
estados) e das guardas de rede da sprint 62 (`bauer.url_safety`).

## Problema

Em 2026-11-10 o catálogo tinha 18 toolkits `dependency_missing`. Nove deles
são de pesquisa somente leitura já revisados; faltava um caminho instalável
pelo lock. `agno.searxng` estava `blocked_by_policy` por não ter escopo de
host, mas é a busca natural do modo `--local`.

## Escopo

1. Extra opcional `agno-research` no `pyproject.toml`, travado no `uv.lock`:
   `wikipedia`, `arxiv`, `pypdf`, `yfinance`, `youtube-transcript-api`
   (sem chave) e `tavily-python`, `exa-py`, `google-search-results`,
   `linkup-sdk` (com chave).
2. Com o extra: sem chave ⇒ `ready`; com chave ⇒ `needs_configuration`.
3. Revisão ação a ação com a biblioteca real:
   - `agno.wikipedia`: só `search_wikipedia`; a API (hardcoded `http://` na
     lib) é forçada para HTTPS antes de cada chamada.
   - `agno.arxiv`: busca do SDK com `num_articles` limitado a 25;
     `read_arxiv_papers` passa a ser implementação Bauer (ids validados por
     regex, no máximo 3; PDF só de `https://arxiv.org` com redirect só para
     hosts arXiv; tamanho limitado; lido **em memória**, nada gravado).
   - `agno.yfinance`: caches do yfinance em `$BAUER_HOME/cache/yfinance`.
   - `agno.youtube`: a URL do modelo só escolhe o id do vídeo; a rede vai
     sempre para youtube.com (provado em teste).
   - `agno.tavily`: as duas variantes de busca, cada uma numa instância
     restrita; `extract_url_content` continua fora.
   - `agno.exa`/`agno.serpapi`/`agno.linkup`: ações da sprint 58 mantidas;
     `get_contents`/`research` (Exa) continuam fora.
4. `agno.searxng` sai de `blocked_by_policy` e vira cliente Bauer somente
   leitura com host configurado pelo operador.
5. CI (**pendente**): `install-check`, que já instala todos os extras, deve
   rodar os testes com as libs reais com `BAUER_REQUIRE_AGNO_EXTRAS=1`
   (pular vira falha). O token de push desta sprint não tem escopo
   `workflow`; o step fica para o coordenador aplicar (ver BACKLOG).

## Fora de escopo

- `agno.bravesearch`: `brave-search` 0.2.0 tem metadados inválidos e fixa
  `httpx<0.26`, `numpy<2` e `pytest-asyncio<0.24` como dependências de
  runtime — incompatível com o lock. Continua `dependency_missing`, com o
  motivo na dica.
- `baidusearch`, `googlemaps`, `openbb`, `valyu`, `seltz` (fora do plano).
- Instalar qualquer extra por padrão.

## Decisões (sem operador — opção mais segura)

- **PDF do arXiv em memória.** O plano pede download confinado ao workspace;
  não gravar nada é confinamento mais forte. Se um dia o PDF precisar ser
  guardado, o caminho é `.bauer/artifacts/arxiv/` via `confined_files`, como
  ação `write` sob `filesystem.write` com aprovação.
  A URL do PDF é montada pelo Bauer a partir do id; o `pdf_url` do feed é
  ignorado. `arxiv` 4 removeu `Result.download_pdf`, então a ação do SDK nem
  funcionaria.
- **SearXNG:** `host` obrigatório em `agno_capabilities.yaml`
  (`http(s)`, sem credenciais/query/fragmento/`..`); `url_safety.check_url`
  a cada chamada (DNS reavaliado). Rede privada (localhost/LAN) **só com
  `allow_private_network: true`** explícito — é auto-hospedado, então o
  caso é legítimo, mas não o default. Metadata de nuvem, link-local e
  endereço não especificado são negados mesmo com o opt-in (no host
  configurado e no IP resolvido). Sem redirect, resposta limitada em bytes,
  resultados recortados e limitados por `max_results`. O modelo não escolhe
  host, categoria nem engines. Risco residual: TOCTOU de DNS entre a checagem
  e a conexão do httpx (mesmo modelo do `web_fetch`).
- **Idade mínima de 14 dias** (corte 2026-10-27): o lock foi gerado com
  `--exclude-newer-package` para cada pacote novo (diretos e transitivos) e
  regenerado sem as flags, mantendo as versões por preferência — assim o
  `uv.lock` não grava a opção e `uv lock --check` continua passando sem
  flags. Excluídos por idade: `pypdf` 7.0.0, `yfinance` 1.8.0, `exa-py`
  2.27/2.28, `youtube-transcript-api` 1.2.5, `pandas` 3.1.1, `curl-cffi`
  0.17.0 (que também forçaria `certifi`/`cffi` novos no install padrão),
  `pytz`/`tzdata` 2026.6.
- **Install padrão inalterado:** `uv pip freeze` após
  `uv sync --frozen --extra dev` idêntico ao de antes; teste trava o fecho
  das dependências padrão no `uv.lock`.
- CI não ganha um job novo: `install-check` já faz `--all-extras`; basta um step.

## Critérios de aceite

- [x] Extra instalável pelo lock; nenhuma versão com menos de 14 dias.
- [x] Sem chave `ready`, com chave `needs_configuration` (extra instalado).
- [x] Nenhuma ação que grave em disco ou leia URL arbitrária concedível.
- [x] SearXNG somente leitura com host do operador e SSRF.
- [x] Install padrão idêntico.
- [x] Testes herméticos (socket bloqueado, transportes falsos).
- [x] Gates verdes.
