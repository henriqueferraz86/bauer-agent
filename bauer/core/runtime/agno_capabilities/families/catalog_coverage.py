"""Sprint 58 — cobertura do catálogo e paridade de integrações (plano 057, sprint "26").

Every toolkit shipped by the Agno version locked in ``uv.lock`` ends this
sprint with an explicit, justified state — reviewed read-only actions, a
Bauer adapter elsewhere, ``blocked_by_policy`` (effects waiting for the write
pilot review) or ``incompatible`` (with reason). Nothing stays silently
``unreviewed``; a future SDK bump that adds a toolkit shows up as
``unreviewed`` and in the snapshot diff, never as enabled.

Read-only SDK toolkits reviewed here read their API key from the environment
(the SDK calls ``getenv``); ``credential_kwargs`` additionally injects the
value resolved from ``bauer credential`` into the toolkit constructor, so a
key kept only in the Bauer vault also works. Credential *names* are public
metadata; values never leave the factory.
"""

from __future__ import annotations

from ..manifest import ActionPolicy, ToolkitPolicy, register_policy


def _read(*names: str, operation: str = "network.http") -> tuple[ActionPolicy, ...]:
    return tuple(ActionPolicy(name, "read", operation) for name in names)


def _sdk_read(
    capability_id: str,
    title: str,
    category: str,
    description: str,
    module: str,
    class_name: str,
    actions: tuple[ActionPolicy, ...],
    *,
    requires: tuple[str, ...] = (),
    install_hint: str = "",
    credentials: tuple[str, ...] = (),
    credential_kwargs: tuple[tuple[str, str], ...] = (),
    notes: str = "",
) -> None:
    register_policy(ToolkitPolicy(
        id=capability_id, title=title, category=category, description=description,
        actions=actions, module=module, class_name=class_name, requires=requires,
        install_hint=install_hint, credentials=credentials, credential_kwargs=credential_kwargs,
        notes=notes,
    ))


def _blocked(capability_id: str, title: str, category: str, reason: str) -> None:
    register_policy(ToolkitPolicy(
        id=capability_id, title=title, category=category,
        description="Toolkit do SDK Agno com efeitos externos.", blocked=reason,
    ))


def _incompatible(capability_id: str, title: str, category: str, reason: str) -> None:
    register_policy(ToolkitPolicy(
        id=capability_id, title=title, category=category,
        description="Toolkit do SDK Agno.", incompatible=reason,
    ))


_SEARCH = "Pesquisa e conteúdo web"
_DATA = "Dados e finanças"
_DEV = "Desenvolvimento"
_MSG = "Mensageria e colaboração"
_MEDIA = "Mídia e voz"
_RUNTIME = "Runtime e memória do SDK"
_EXEC = "Execução remota"
_GEO = "Mapas e clima"
#: Extra opcional que instala as dependências de pesquisa (plano 067, sprint 73).
_RESEARCH_EXTRA = "uv pip install 'bauer-agent[agno-research]'"

# ── read-only, fixed public APIs (reviewed) ──────────────────────────────────

_sdk_read("agno.bravesearch", "Brave Search", _SEARCH, "Busca web Brave.", "agno.tools.bravesearch", "BraveSearchTools",
          _read("brave_search"), requires=("brave",),
          install_hint="sem versão instalável: brave-search 0.2.0 tem metadados inválidos e fixa httpx<0.26/numpy<2",
          credentials=("BRAVE_API_KEY",), credential_kwargs=(("api_key", "BRAVE_API_KEY"),))
_sdk_read("agno.baidusearch", "Baidu Search", _SEARCH, "Busca Baidu.", "agno.tools.baidusearch", "BaiduSearchTools",
          _read("baidu_search"), requires=("baidusearch", "pycountry"), install_hint="uv pip install baidusearch pycountry")
_sdk_read("agno.serper", "Serper", _SEARCH, "Busca Google via Serper (sem scraping).", "agno.tools.serper", "SerperTools",
          _read("search_web", "search_news", "search_scholar"), credentials=("SERPER_API_KEY",),
          credential_kwargs=(("api_key", "SERPER_API_KEY"),),
          notes="scrape_webpage fica fora: busca URL arbitrária (use bauer.web_fetch).")
_sdk_read("agno.serply", "Serply", _SEARCH, "Busca web/notícias/acadêmica.", "agno.tools.serply", "SerplyTools",
          _read("search_web", "search_news", "search_scholar"), credentials=("SERPLY_API_KEY",))
_sdk_read("agno.serpapi", "SerpApi", _SEARCH, "Busca Google/YouTube via SerpApi.", "agno.tools.serpapi", "SerpApiTools",
          _read("search_google", "search_youtube"), requires=("serpapi",), install_hint=_RESEARCH_EXTRA,
          credentials=("SERP_API_KEY",), credential_kwargs=(("api_key", "SERP_API_KEY"),))
_sdk_read("agno.searchapi", "SearchApi", _SEARCH, "Busca Google/notícias/imagens/YouTube.", "agno.tools.searchapi", "SearchApiTools",
          _read("search_google", "search_news", "search_images", "search_youtube"), credentials=("SEARCHAPI_API_KEY",),
          credential_kwargs=(("api_key", "SEARCHAPI_API_KEY"),))
_sdk_read("agno.tavily", "Tavily", _SEARCH, "Busca Tavily (sem extração de URL).", "agno.tools.tavily", "TavilyTools",
          _read("web_search_using_tavily", "web_search_with_tavily"), requires=("tavily",), install_hint=_RESEARCH_EXTRA,
          credentials=("TAVILY_API_KEY",), credential_kwargs=(("api_key", "TAVILY_API_KEY"),),
          notes="extract_url_content fica fora: busca URL arbitrária.")
_sdk_read("agno.exa", "Exa", _SEARCH, "Busca e similares Exa (sem leitura de URLs).", "agno.tools.exa", "ExaTools",
          _read("search_exa", "find_similar", "exa_answer"), requires=("exa_py",), install_hint=_RESEARCH_EXTRA,
          credentials=("EXA_API_KEY",), credential_kwargs=(("api_key", "EXA_API_KEY"),),
          notes="get_contents/research ficam fora: buscam conteúdo de URLs arbitrárias / tarefas longas pagas.")
_sdk_read("agno.linkup", "Linkup", _SEARCH, "Busca Linkup.", "agno.tools.linkup", "LinkupTools",
          _read("web_search_with_linkup"), requires=("linkup",), install_hint=_RESEARCH_EXTRA,
          credentials=("LINKUP_API_KEY",), credential_kwargs=(("api_key", "LINKUP_API_KEY"),))
_sdk_read("agno.perplexity", "Perplexity", _SEARCH, "Busca Perplexity.", "agno.tools.perplexity", "PerplexitySearch",
          _read("search"), credentials=("PERPLEXITY_API_KEY",), credential_kwargs=(("api_key", "PERPLEXITY_API_KEY"),))
_sdk_read("agno.youcom", "You.com", _SEARCH, "Busca You.com.", "agno.tools.youcom", "YouTools",
          _read("you_search"), credentials=("YDC_API_KEY",), credential_kwargs=(("api_key", "YDC_API_KEY"),))
_sdk_read("agno.valyu", "Valyu", _SEARCH, "Busca acadêmica/web Valyu.", "agno.tools.valyu", "ValyuTools",
          _read("search_academic_sources", "search_web", "search_within_paper"), requires=("valyu",), install_hint="uv pip install valyu",
          credentials=("VALYU_API_KEY",), credential_kwargs=(("api_key", "VALYU_API_KEY"),))
_sdk_read("agno.seltz", "Seltz", _SEARCH, "Busca Seltz.", "agno.tools.seltz", "SeltzTools",
          _read("search_seltz"), requires=("seltz",), install_hint="uv pip install seltz",
          credentials=("SELTZ_API_KEY",), credential_kwargs=(("api_key", "SELTZ_API_KEY"),))
_sdk_read("agno.giphy", "Giphy", _MEDIA, "Busca de GIFs.", "agno.tools.giphy", "GiphyTools",
          _read("search_gifs"), credentials=("GIPHY_API_KEY",), credential_kwargs=(("api_key", "GIPHY_API_KEY"),))
_sdk_read("agno.unsplash", "Unsplash", _MEDIA, "Busca de fotos (sem download).", "agno.tools.unsplash", "UnsplashTools",
          _read("search_photos", "get_photo", "get_random_photo"), credentials=("UNSPLASH_ACCESS_KEY",),
          credential_kwargs=(("access_key", "UNSPLASH_ACCESS_KEY"),),
          notes="download_photo fica fora: dispara contagem de download na API e grava arquivo.")
_sdk_read("agno.youtube", "YouTube", _SEARCH, "Metadados, legendas e timestamps de vídeos.", "agno.tools.youtube", "YouTubeTools",
          _read("get_youtube_video_data", "get_youtube_video_captions", "get_video_timestamps"),
          requires=("youtube_transcript_api",), install_hint=_RESEARCH_EXTRA)
_sdk_read("agno.openweather", "OpenWeather", _GEO, "Clima atual, previsão e poluição.", "agno.tools.openweather", "OpenWeatherTools",
          _read("geocode_location", "get_current_weather", "get_forecast", "get_air_pollution"), requires=("requests",),
          credentials=("OPENWEATHER_API_KEY",), credential_kwargs=(("api_key", "OPENWEATHER_API_KEY"),))
_sdk_read("agno.openrouteservice", "OpenRouteService", _GEO, "Geocodificação, rotas e matriz de distância.",
          "agno.tools.openrouteservice", "OpenRouteServiceTools",
          _read("geocode_location", "get_directions", "get_distance_matrix"), credentials=("ORS_API_KEY",),
          credential_kwargs=(("api_key", "ORS_API_KEY"),))
_sdk_read("agno.google.maps", "Google Maps", _GEO, "Lugares, rotas, geocodificação.", "agno.tools.google.maps", "GoogleMapTools",
          _read("search_places", "get_directions", "validate_address", "geocode_address", "reverse_geocode",
                "get_distance_matrix", "get_elevation", "get_timezone"),
          requires=("googlemaps",), install_hint="uv pip install googlemaps",
          credentials=("GOOGLE_MAPS_API_KEY",), credential_kwargs=(("key", "GOOGLE_MAPS_API_KEY"),))
_sdk_read("agno.yfinance", "Yahoo Finance", _DATA, "Cotações, fundamentos e notícias.", "agno.tools.yfinance", "YFinanceTools",
          _read("get_current_stock_price", "get_company_info", "get_historical_stock_prices", "get_stock_fundamentals",
                "get_income_statements", "get_key_financial_ratios", "get_analyst_recommendations", "get_company_news",
                "get_technical_indicators"),
          requires=("yfinance",), install_hint=_RESEARCH_EXTRA,
          notes="Caches de cookie/fuso do yfinance ficam em $BAUER_HOME/cache/yfinance (sprint 73).")
_sdk_read("agno.financial_datasets", "Financial Datasets", _DATA, "Demonstrativos, preços e filings.",
          "agno.tools.financial_datasets", "FinancialDatasetsTools",
          _read("get_income_statements", "get_balance_sheets", "get_cash_flow_statements", "get_segmented_financials",
                "get_financial_metrics", "get_company_info", "get_crypto_prices", "get_earnings", "get_insider_trades",
                "get_institutional_ownership", "get_news", "get_stock_prices", "search_tickers", "get_sec_filings"),
          credentials=("FINANCIAL_DATASETS_API_KEY",), credential_kwargs=(("api_key", "FINANCIAL_DATASETS_API_KEY"),))
_sdk_read("agno.openbb", "OpenBB", _DATA, "Dados de mercado OpenBB.", "agno.tools.openbb", "OpenBBTools",
          _read("get_stock_price", "search_company_symbol", "get_company_news", "get_company_profile", "get_price_targets"),
          requires=("openbb",), install_hint="uv pip install openbb")
_sdk_read("agno.adanos", "Adanos", _DATA, "Sentimento de mercado/cripto.", "agno.tools.adanos", "AdanosTools",
          _read("get_stock_sentiment", "get_crypto_sentiment", "get_market_sentiment", "get_trending"),
          credentials=("ADANOS_API_KEY",), credential_kwargs=(("api_key", "ADANOS_API_KEY"),))
_sdk_read("agno.brandfetch", "Brandfetch", _DATA, "Dados públicos de marcas.", "agno.tools.brandfetch", "BrandfetchTools",
          _read("search_by_identifier", "search_by_brand"), credentials=("BRANDFETCH_API_KEY",),
          credential_kwargs=(("api_key", "BRANDFETCH_API_KEY"),))

# ── write-capable or mixed SaaS: blocked until the write pilot is reviewed ──

_WRITE_REVIEW = (
    "Mistura leitura com escrita/envio externo. Pelo STOP do plano 057 (sprint 25/57), nenhum outro "
    "conector de escrita é aberto antes da revisão do piloto bauer.github_issues; leitura isolada "
    "exigirá policy própria por ação."
)
for _cid, _title, _cat in (
    ("agno.discord", "Discord", _MSG), ("agno.telegram", "Telegram", _MSG),
    ("agno.whatsapp", "WhatsApp", _MSG), ("agno.webex", "Webex", _MSG), ("agno.email", "E-mail SMTP", _MSG),
    ("agno.resend", "Resend", _MSG), ("agno.atomic_mail", "Atomic Mail", _MSG), ("agno.twilio", "Twilio", _MSG),
    ("agno.plivo", "Plivo", _MSG), ("agno.x", "X (Twitter)", _MSG), ("agno.reddit", "Reddit", _MSG),
    ("agno.notion", "Notion", _MSG), ("agno.confluence", "Confluence", _MSG), ("agno.jira", "Jira", _DEV),
    ("agno.clickup", "ClickUp", _DEV), ("agno.trello", "Trello", _DEV),
    ("agno.todoist", "Todoist", _DEV), ("agno.redmine", "Redmine", _DEV), ("agno.bitbucket", "Bitbucket", _DEV),
    ("agno.salesforce", "Salesforce", _DATA), ("agno.calcom", "Cal.com", _MSG), ("agno.zoom", "Zoom", _MSG),
    ("agno.spotify", "Spotify", _MEDIA), ("agno.google.calendar", "Google Calendar", _MSG),
    ("agno.google.drive", "Google Drive", _MSG), ("agno.google.gmail", "Gmail", _MSG),
    ("agno.google.sheets", "Google Sheets", _DATA), ("agno.google.slides", "Google Slides", _MSG),
    ("agno.parallel", "Parallel", _SEARCH), ("agno.apify", "Apify", _SEARCH),
):
    _blocked(_cid, _title, _cat, _WRITE_REVIEW)

# Sprint 75 opened one approved write action per connector as Bauer adapters;
# the SDK toolkits (mixed read/write, no per-call approval) stay blocked.
for _cid, _title, _cat, _adapter in (
    ("agno.slack", "Slack", _MSG, "bauer.slack_messages"),
    ("agno.linear", "Linear", _DEV, "bauer.linear_issues"),
    ("agno.gitlab", "GitLab", _DEV, "bauer.gitlab_issues"),
):
    _blocked(_cid, _title, _cat, (
        f"Toolkit do SDK mistura leitura com escrita sem aprovação por chamada. Use {_adapter} "
        "(escrita sob aprovação humana da chamada exata, plano 067 sprint 75)."
    ))

for _cid, _title, _cat, _reason in (
    ("agno.zendesk", "Zendesk", _MSG, "Busca em tickets de clientes (dados pessoais): exige política de privacidade/escopo antes de habilitar."),
    ("agno.shopify", "Shopify", _DATA, "Dados de pedidos/clientes (PII e financeiros): exige escopo por loja e redação antes de habilitar."),
    ("agno.scavio", "Scavio", _SEARCH, "Coleta perfis/seguidores de redes sociais (dados pessoais de terceiros): fora da política de privacidade."),
    ("agno.sofya", "Sofya", _SEARCH, "Mistura busca com extração de URL arbitrária e tarefas de pesquisa pagas."),
):
    _blocked(_cid, _title, _cat, _reason)

# ── paid/external media generation: costs outside Kernel budget ─────────────

_MEDIA_REASON = (
    "Gera mídia em serviço pago de terceiros: custo fora do orçamento do Kernel (sem medição por chamada) "
    "e envio de conteúdo do usuário; requer integração de custo antes de habilitar."
)
for _cid, _title in (
    ("agno.dalle", "DALL·E"), ("agno.fal", "fal.ai"), ("agno.lumalab", "Luma"), ("agno.minimax", "MiniMax"),
    ("agno.models_labs", "ModelsLab"), ("agno.replicate", "Replicate"), ("agno.wavespeed", "WaveSpeed"),
    ("agno.nano_banana", "Nano Banana"), ("agno.openai", "OpenAI media"), ("agno.eleven_labs", "ElevenLabs"),
    ("agno.cartesia", "Cartesia"), ("agno.desi_vocal", "DesiVocal"), ("agno.gandr", "Gandr"),
    ("agno.smallest", "Smallest"), ("agno.twelvelabs", "TwelveLabs"),
):
    _blocked(_cid, _title, _MEDIA, _MEDIA_REASON)

# ── incompatible with Bauer's architecture ──────────────────────────────────

_RUNTIME_REASON = (
    "Duplica runtime do Bauer (Kernel, scheduler, memória, HITL ou AgentOS); usar exigiria ADR "
    "(plano 057: sem AgentOS/db Agno sem ADR)."
)
for _cid, _title in (
    ("agno.agentos", "AgentOS"), ("agno.studio", "Studio"), ("agno.studio_runner", "Studio runner"),
    ("agno.scheduler", "Scheduler"), ("agno.workflow", "Workflow tools"), ("agno.advisor", "Advisor"),
    ("agno.knowledge", "Knowledge"), ("agno.knowledge.management", "Knowledge management"),
    ("agno.mem0", "Mem0"), ("agno.zep", "Zep"), ("agno.reasoning", "Reasoning"),
    ("agno.user_control_flow", "User control flow"), ("agno.user_feedback", "User feedback"),
    ("agno.workspace", "Workspace"), ("agno.code.code_mode", "Code mode"), ("agno.finance", "Finance base"),
    ("agno.google.base", "Google base"), ("agno.sleep", "Sleep"),
):
    _incompatible(_cid, _title, _RUNTIME, _RUNTIME_REASON)

for _cid, _title, _cat, _reason in (
    ("agno.e2b", "E2B", _EXEC, "Executa código/comandos em sandbox remota fora do shell governado do Bauer."),
    ("agno.daytona", "Daytona", _EXEC, "Executa código/comandos em sandbox remota fora do shell governado do Bauer."),
    ("agno.superserve", "Superserve", _EXEC, "Executa código e anexa segredos em sandbox remota."),
    ("agno.antigravity", "Antigravity", _EXEC, "Cria/executa/apaga agentes remotos."),
    ("agno.evm", "EVM", _DATA, "Assina e envia transações em blockchain (transferência de fundos): nunca habilitável."),
    ("agno.xai_auth", "xAI auth", _RUNTIME, "Fluxo de login de terceiros; credenciais são do cofre Bauer."),
    ("agno.opencv", "OpenCV", _MEDIA, "Captura câmera/vídeo do dispositivo."),
    ("agno.moviepy_video", "MoviePy", _MEDIA, "Lê/grava arquivos de vídeo em caminhos livres, fora do workspace isolado."),
    ("agno.mlx_transcribe", "MLX Transcribe", _MEDIA, "Lê arquivos de áudio de caminhos livres; Bauer já tem transcrição própria (/transcribe)."),
    ("agno.docling", "Docling", _DATA, "Converte arquivos de caminhos livres/URLs sem escopo; use bauer.data/bauer.web_fetch."),
    ("agno.file.generation", "File generation", _DATA, "Grava arquivos em diretório arbitrário; use bauer.data (artefatos) ou bauer.write_file."),
    ("agno.airflow", "Airflow", _DEV, "Lê/grava arquivos de DAG em caminho livre; use bauer.read_file/write_file."),
):
    _incompatible(_cid, _title, _cat, _reason)
