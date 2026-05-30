"""Testes do WebDispatcher — backends de search e extract."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from bauer.web.dispatcher import WebDispatcher, WebError, SearchResult


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _cfg(search_backend="ddgs", extract_backend="httpx", **kwargs):
    """Config mínima para o dispatcher."""
    cfg = MagicMock()
    cfg.search_backend = search_backend
    cfg.extract_backend = extract_backend
    cfg.searxng_url = kwargs.get("searxng_url", "http://localhost:8080")
    cfg.brave_api_key = kwargs.get("brave_api_key", "")
    cfg.max_results = kwargs.get("max_results", 5)
    cfg.max_chars = kwargs.get("max_chars", 5000)
    cfg.timeout_seconds = kwargs.get("timeout_seconds", 15)
    return cfg


# ---------------------------------------------------------------------------
# WebDispatcher — básicos
# ---------------------------------------------------------------------------

def test_defaults_sem_config():
    d = WebDispatcher(None)
    assert d.search_backend == "ddgs"
    assert d.extract_backend == "httpx"
    assert d.max_results == 5
    assert d.max_chars == 5000


def test_usa_config_quando_fornecida():
    cfg = _cfg(search_backend="searxng", extract_backend="crawl4ai", max_results=3)
    d = WebDispatcher(cfg)
    assert d.search_backend == "searxng"
    assert d.extract_backend == "crawl4ai"
    assert d.max_results == 3


def test_backend_desconhecido_levanta_webError():
    d = WebDispatcher(_cfg(search_backend="inexistente"))
    with pytest.raises(WebError, match="desconhecido"):
        d.search("query")


def test_extract_backend_desconhecido_levanta_webError():
    d = WebDispatcher(_cfg(extract_backend="inexistente"))
    with pytest.raises(WebError, match="desconhecido"):
        d.extract("https://example.com")


# ---------------------------------------------------------------------------
# URL safety
# ---------------------------------------------------------------------------

def test_bloqueia_localhost():
    d = WebDispatcher(None)
    with pytest.raises(WebError, match="interno"):
        d.extract("http://localhost/secret")


def test_bloqueia_127():
    d = WebDispatcher(None)
    with pytest.raises(WebError, match="interno"):
        d.extract("http://127.0.0.1/secret")


def test_bloqueia_ip_privado():
    d = WebDispatcher(None)
    with pytest.raises(WebError, match="privado"):
        d.extract("http://192.168.1.1/secret")


def test_url_sem_http_levanta():
    d = WebDispatcher(None)
    with pytest.raises(WebError, match="http"):
        d.extract("ftp://example.com")


# ---------------------------------------------------------------------------
# Backend ddgs — mock
# ---------------------------------------------------------------------------

def test_search_ddgs_retorna_resultados():
    import sys
    import types

    d = WebDispatcher(_cfg(search_backend="ddgs"))

    mock_result = [{"title": "Título", "href": "https://ex.com", "body": "Resumo"}]

    # Cria módulo ddgs falso com classe DDGS que funciona como context manager
    mock_ddgs_instance = MagicMock()
    mock_ddgs_instance.__enter__ = lambda s: s
    mock_ddgs_instance.__exit__ = MagicMock(return_value=False)
    mock_ddgs_instance.text = MagicMock(return_value=iter(mock_result))

    fake_ddgs_module = types.ModuleType("ddgs")
    fake_ddgs_module.DDGS = MagicMock(return_value=mock_ddgs_instance)

    with patch.dict(sys.modules, {"ddgs": fake_ddgs_module}):
        results = d._search_ddgs("python", 5)

    assert len(results) == 1
    assert results[0].title == "Título"
    assert results[0].url == "https://ex.com"
    assert results[0].engine == "ddgs"


def test_search_ddgs_sem_modulo_levanta():
    d = WebDispatcher(_cfg(search_backend="ddgs"))
    import sys
    # Remove ddgs do sys.modules para simular não instalado
    ddgs_backup = sys.modules.pop("ddgs", None)
    try:
        with pytest.raises(WebError, match="ddgs"):
            d._search_ddgs("query", 5)
    finally:
        if ddgs_backup:
            sys.modules["ddgs"] = ddgs_backup


# ---------------------------------------------------------------------------
# Backend searxng — mock httpx
# ---------------------------------------------------------------------------

def test_search_searxng_retorna_resultados():
    d = WebDispatcher(_cfg(search_backend="searxng", searxng_url="http://localhost:8080"))

    mock_response = MagicMock()
    mock_response.json.return_value = {
        "results": [
            {"title": "Resultado", "url": "https://ex.com", "content": "Conteúdo", "engine": "google"},
        ]
    }
    mock_response.raise_for_status = MagicMock()

    with patch("httpx.get", return_value=mock_response):
        results = d._search_searxng("python", 5)

    assert len(results) == 1
    assert results[0].title == "Resultado"
    assert results[0].engine == "google"


def test_search_searxng_offline_levanta():
    import httpx
    d = WebDispatcher(_cfg(search_backend="searxng"))
    with patch("httpx.get", side_effect=httpx.ConnectError("offline")):
        with pytest.raises(WebError, match="SearXNG"):
            d._search_searxng("query", 5)


# ---------------------------------------------------------------------------
# Backend brave — mock httpx
# ---------------------------------------------------------------------------

def test_search_brave_sem_key_levanta():
    d = WebDispatcher(_cfg(search_backend="brave", brave_api_key=""))
    with pytest.raises(WebError, match="BRAVE_API_KEY"):
        d._search_brave("query", 5)


def test_search_brave_com_key_retorna():
    d = WebDispatcher(_cfg(search_backend="brave", brave_api_key="test-key"))

    mock_response = MagicMock()
    mock_response.json.return_value = {
        "web": {"results": [{"title": "T", "url": "https://ex.com", "description": "D"}]}
    }
    mock_response.raise_for_status = MagicMock()

    with patch("httpx.get", return_value=mock_response):
        results = d._search_brave("query", 5)

    assert len(results) == 1
    assert results[0].engine == "brave"


# ---------------------------------------------------------------------------
# Backend httpx extract — mock
# ---------------------------------------------------------------------------

def test_extract_httpx_retorna_texto():
    d = WebDispatcher(_cfg(extract_backend="httpx"))

    mock_response = MagicMock()
    mock_response.text = "<html><body><p>Hello world</p></body></html>"
    mock_response.headers = {"content-type": "text/html"}
    mock_response.raise_for_status = MagicMock()

    with patch("httpx.get", return_value=mock_response):
        result = d._extract_httpx("https://example.com", 5000)

    assert "Hello world" in result


def test_extract_httpx_trunca_texto_longo():
    d = WebDispatcher(_cfg())

    mock_response = MagicMock()
    mock_response.text = "x" * 10000
    mock_response.headers = {"content-type": "text/plain"}
    mock_response.raise_for_status = MagicMock()

    with patch("httpx.get", return_value=mock_response):
        result = d._extract_httpx("https://example.com", 100)

    assert len(result) < 10000
    assert "truncado" in result


def test_extract_binario_retorna_aviso():
    d = WebDispatcher(_cfg())

    mock_response = MagicMock()
    mock_response.headers = {"content-type": "application/octet-stream"}
    mock_response.raise_for_status = MagicMock()

    with patch("httpx.get", return_value=mock_response):
        result = d._extract_httpx("https://example.com/file.bin", 5000)

    assert "binário" in result or "binario" in result


# ---------------------------------------------------------------------------
# search_as_text — formato de saída
# ---------------------------------------------------------------------------

def test_search_as_text_formato():
    d = WebDispatcher(_cfg(search_backend="ddgs"))

    fake_results = [
        SearchResult("Título A", "https://a.com", "Resumo A", "ddgs"),
        SearchResult("Título B", "https://b.com", "Resumo B", "ddgs"),
    ]

    with patch.object(d, "search", return_value=fake_results):
        text = d.search_as_text("query")

    assert "1." in text
    assert "2." in text
    assert "Título A" in text
    assert "https://a.com" in text
    assert "[ddgs]" in text


def test_search_as_text_sem_resultados():
    d = WebDispatcher(_cfg())
    with patch.object(d, "search", return_value=[]):
        text = d.search_as_text("query inexistente")
    assert "Nenhum resultado" in text
