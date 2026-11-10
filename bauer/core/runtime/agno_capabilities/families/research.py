"""Sprint 51 — pesquisa em modo de leitura (plano 057, sprint "19").

Search toolkits whose network target is a *fixed public API* (DuckDuckGo via
``ddgs``, Hacker News, Wikipedia, arXiv search, PubMed) are reviewed as
read-only and evaluated under the ``network.http`` policy operation, which
already denies payloads carrying secret material.

Toolkits that fetch *arbitrary URLs* would bypass ``bauer.url_safety`` (SSRF,
private ranges, metadata endpoints) and Bauer's extraction limits. They are
catalogued as ``incompatible`` pointing to ``bauer.web_fetch`` — the Bauer
service that already enforces those guards.
"""

from __future__ import annotations

from ..manifest import ActionPolicy, ToolkitPolicy, register_policy


def _search(name: str, description: str = "") -> ActionPolicy:
    return ActionPolicy(name, "read", "network.http", description)


def _url_fetcher(capability_id: str, title: str) -> ToolkitPolicy:
    return ToolkitPolicy(
        id=capability_id,
        title=title,
        category="Pesquisa e conteúdo web",
        description="Busca/extrai conteúdo de URLs arbitrárias.",
        incompatible=(
            "Acessa URL arbitrária fora do url_safety do Bauer (SSRF/IPs privados/metadata). "
            "Use bauer.web_fetch (extração) ou bauer.web_search (busca)."
        ),
    )


register_policy(
    ToolkitPolicy(
        id="agno.websearch",
        title="Busca web (ddgs)",
        category="Pesquisa e conteúdo web",
        description="Busca web e notícias via ddgs; somente leitura de resultados.",
        actions=(_search("web_search"), _search("search_news")),
        module="agno.tools.websearch",
        class_name="WebSearchTools",
        requires=("ddgs",),
        install_hint="uv pip install 'bauer-agent[web]'",
    )
)
register_policy(
    ToolkitPolicy(
        id="agno.duckduckgo",
        title="DuckDuckGo",
        category="Pesquisa e conteúdo web",
        description="Busca DuckDuckGo via ddgs; somente leitura de resultados.",
        actions=(_search("web_search"), _search("search_news")),
        module="agno.tools.duckduckgo",
        class_name="DuckDuckGoTools",
        requires=("ddgs",),
        install_hint="uv pip install 'bauer-agent[web]'",
    )
)
register_policy(
    ToolkitPolicy(
        id="agno.hackernews",
        title="Hacker News",
        category="Pesquisa e conteúdo web",
        description="Top stories e perfis públicos da API oficial do Hacker News.",
        actions=(_search("get_top_hackernews_stories"), _search("get_user_details")),
        module="agno.tools.hackernews",
        class_name="HackerNewsTools",
        requires=("httpx",),
    )
)
register_policy(
    ToolkitPolicy(
        id="agno.wikipedia",
        title="Wikipedia",
        category="Pesquisa e conteúdo web",
        description="Busca de artigos na Wikipedia (sem atualizar knowledge base).",
        actions=(_search("search_wikipedia"),),
        module="agno.tools.wikipedia",
        class_name="WikipediaTools",
        requires=("wikipedia",),
        install_hint="uv pip install 'bauer-agent[agno-research]'",
        notes=(
            "search_wikipedia_and_update_knowledge_base fica fora: escreve em knowledge base. "
            "A API é forçada para HTTPS a cada chamada (sprint 73)."
        ),
    )
)
register_policy(
    ToolkitPolicy(
        id="agno.arxiv",
        title="arXiv",
        category="Pesquisa e conteúdo web",
        description="Busca de artigos no arXiv e leitura do texto de artigos por id.",
        actions=(_search("search_arxiv_and_return_articles"), _search("read_arxiv_papers")),
        module="agno.tools.arxiv",
        class_name="ArxivTools",
        requires=("arxiv", "pypdf"),
        install_hint="uv pip install 'bauer-agent[agno-research]'",
        notes=(
            "read_arxiv_papers é implementação Bauer (sprint 73): ids validados, PDF só de "
            "https://arxiv.org com tamanho limitado e lido em memória, sem gravar em disco."
        ),
    )
)
register_policy(
    ToolkitPolicy(
        id="agno.pubmed",
        title="PubMed",
        category="Pesquisa e conteúdo web",
        description="Busca de artigos na API pública do PubMed.",
        actions=(_search("search_pubmed"),),
        module="agno.tools.pubmed",
        class_name="PubmedTools",
        requires=("httpx",),
    )
)

for _capability_id, _title in (
    ("agno.website", "Website"),
    ("agno.webtools", "Web tools"),
    ("agno.webbrowser", "Abrir navegador local"),
    ("agno.jina", "Jina Reader"),
    ("agno.newspaper", "Newspaper"),
    ("agno.newspaper4k", "Newspaper4k"),
    ("agno.trafilatura", "Trafilatura"),
    ("agno.crawl4ai", "Crawl4AI"),
    ("agno.spider", "Spider"),
    ("agno.firecrawl", "Firecrawl"),
    ("agno.api", "API HTTP genérica"),
    ("agno.llms_txt", "llms.txt"),
    ("agno.brightdata", "Bright Data"),
    ("agno.oxylabs", "Oxylabs"),
    ("agno.scrapegraph", "ScrapeGraph"),
):
    register_policy(_url_fetcher(_capability_id, _title))
