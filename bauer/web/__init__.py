"""Web backends plugáveis para o Bauer Agent.

Inspirado na arquitetura do Hermes Agent (NousResearch):
  - Dispatcher central seleciona backend via config.yaml
  - Search e Extract são capacidades independentes e configuráveis
  - Zero config por padrão (ddgs + httpx)
  - Backends avançados ativados por config (searxng, crawl4ai)

Uso:
    from bauer.web.dispatcher import WebDispatcher
    dispatcher = WebDispatcher(web_config)
    results = dispatcher.search("Python 3.13 novidades", max_results=5)
    content = dispatcher.extract("https://docs.python.org/...", max_chars=5000)
"""

from .dispatcher import WebDispatcher

__all__ = ["WebDispatcher"]
