"""Tests for bauer.server — CORS, GZip, access log, per-key rate limit."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Skip entire module if fastapi is not installed
pytest.importorskip("fastapi")

from bauer.server import create_app, _RateLimiter


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_app(tmp_path: Path, **kwargs):
    """Create a minimal test FastAPI app."""
    mock_router = MagicMock()
    mock_router.available_tools.return_value = ["list_dir"]
    mock_router.tool_info.side_effect = lambda n: {"name": n}

    mock_client = MagicMock()
    mock_client._provider = "test"
    mock_client.list_models.return_value = []

    return create_app(
        model_name="test-model",
        applied_context=4096,
        router=mock_router,
        client=mock_client,
        system_prompt="",
        sessions_dir=tmp_path / "sessions",
        **kwargs,
    )


def _client(app):
    from fastapi.testclient import TestClient
    return TestClient(app)


# ---------------------------------------------------------------------------
# _RateLimiter unit tests
# ---------------------------------------------------------------------------

class TestRateLimiter:
    def test_allows_within_limit(self):
        rl = _RateLimiter(max_requests=5, window_s=60.0)
        for _ in range(5):
            assert rl.is_allowed("key1") is True

    def test_blocks_when_exceeded(self):
        rl = _RateLimiter(max_requests=3, window_s=60.0)
        for _ in range(3):
            rl.is_allowed("ip1")
        assert rl.is_allowed("ip1") is False

    def test_different_keys_independent(self):
        rl = _RateLimiter(max_requests=2, window_s=60.0)
        rl.is_allowed("a")
        rl.is_allowed("a")
        assert rl.is_allowed("a") is False
        assert rl.is_allowed("b") is True  # different key, fresh window

    def test_disabled_when_zero(self):
        rl = _RateLimiter(max_requests=0, window_s=60.0)
        for _ in range(1000):
            assert rl.is_allowed("any") is True

    def test_retry_after_returns_positive(self):
        rl = _RateLimiter(max_requests=1, window_s=60.0)
        rl.is_allowed("k")
        rl.is_allowed("k")  # blocked
        assert rl.retry_after("k") > 0

    def test_retry_after_unknown_key_is_zero(self):
        rl = _RateLimiter(max_requests=5, window_s=60.0)
        assert rl.retry_after("never_seen") == 0.0


# ---------------------------------------------------------------------------
# Health endpoint (sanity)
# ---------------------------------------------------------------------------

class TestHealthEndpoint:
    def test_health_ok(self, tmp_path):
        app = _make_app(tmp_path)
        resp = _client(app).get("/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"
        assert resp.json()["model"] == "test-model"

    def test_status_endpoint(self, tmp_path):
        app = _make_app(tmp_path)
        resp = _client(app).get("/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["model"] == "test-model"
        assert isinstance(data["tools"], list)


# ---------------------------------------------------------------------------
# CORS
# ---------------------------------------------------------------------------

class TestCORS:
    def test_no_cors_headers_when_disabled(self, tmp_path):
        app = _make_app(tmp_path, cors_origins=None)
        resp = _client(app).get("/health", headers={"Origin": "http://example.com"})
        assert "access-control-allow-origin" not in resp.headers

    def test_cors_wildcard(self, tmp_path):
        app = _make_app(tmp_path, cors_origins=["*"])
        resp = _client(app).get("/health", headers={"Origin": "http://example.com"})
        assert resp.headers.get("access-control-allow-origin") == "*"

    def test_cors_specific_origin(self, tmp_path):
        app = _make_app(tmp_path, cors_origins=["https://myapp.example.com"])
        resp = _client(app).get(
            "/health",
            headers={"Origin": "https://myapp.example.com"},
        )
        assert "https://myapp.example.com" in resp.headers.get(
            "access-control-allow-origin", ""
        )

    def test_cors_preflight_options(self, tmp_path):
        app = _make_app(tmp_path, cors_origins=["*"])
        resp = _client(app).options(
            "/health",
            headers={
                "Origin": "http://localhost:3000",
                "Access-Control-Request-Method": "GET",
            },
        )
        assert resp.status_code in (200, 204)
        assert "access-control-allow-methods" in resp.headers


# ---------------------------------------------------------------------------
# GZip
# ---------------------------------------------------------------------------

class TestGzip:
    def test_gzip_enabled_by_default(self, tmp_path):
        # GZip is transparent — just test that it doesn't break responses
        app = _make_app(tmp_path, enable_gzip=True)
        resp = _client(app).get("/health", headers={"Accept-Encoding": "gzip"})
        assert resp.status_code == 200

    def test_gzip_disabled(self, tmp_path):
        app = _make_app(tmp_path, enable_gzip=False)
        resp = _client(app).get("/health")
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Access log
# ---------------------------------------------------------------------------

class TestAccessLog:
    @pytest.fixture(autouse=True)
    def _ensure_bauer_propagation(self):
        # setup_logging() (chamado por outros testes de CLI) seta
        # logger 'bauer'.propagate=False, impedindo que records de
        # 'bauer.access' cheguem ao caplog (que escuta na raiz). Garante
        # propagação durante estes testes, independente da ordem de execução.
        lg = logging.getLogger("bauer")
        prev = lg.propagate
        lg.propagate = True
        yield
        lg.propagate = prev

    def test_access_log_disabled_by_default(self, tmp_path, caplog):
        app = _make_app(tmp_path, enable_access_log=False)
        with caplog.at_level(logging.INFO, logger="bauer.access"):
            _client(app).get("/health")
        # No bauer.access entries
        access_entries = [r for r in caplog.records if r.name == "bauer.access"]
        assert len(access_entries) == 0

    def test_access_log_enabled(self, tmp_path, caplog):
        app = _make_app(tmp_path, enable_access_log=True)
        with caplog.at_level(logging.INFO, logger="bauer.access"):
            _client(app).get("/health")
        access_entries = [r for r in caplog.records if r.name == "bauer.access"]
        assert len(access_entries) == 1
        record = json.loads(access_entries[0].message)
        assert record["method"] == "GET"
        assert record["path"] == "/health"
        assert record["status"] == 200
        assert "duration_ms" in record
        assert "ts" in record

    def test_access_log_includes_error_status(self, tmp_path, caplog):
        app = _make_app(tmp_path, enable_access_log=True)
        with caplog.at_level(logging.INFO, logger="bauer.access"):
            _client(app).get("/does-not-exist")
        access_entries = [r for r in caplog.records if r.name == "bauer.access"]
        assert len(access_entries) == 1
        record = json.loads(access_entries[0].message)
        assert record["status"] == 404


# ---------------------------------------------------------------------------
# Per-key rate limiting
# ---------------------------------------------------------------------------

class TestPerKeyRateLimit:
    def test_per_ip_rate_limit_default(self, tmp_path):
        app = _make_app(
            tmp_path,
            api_key="secret",
            rate_limit_requests=2,
            rate_limit_per_key=False,
        )
        c = _client(app)
        # Two calls pass, third blocked
        r1 = c.get("/health")
        r2 = c.get("/health")
        r3 = c.get("/health")
        assert r1.status_code == 200
        assert r2.status_code == 200
        assert r3.status_code == 429

    def test_per_key_rate_limit_separate_keys(self, tmp_path):
        """Different API keys get independent rate-limit buckets."""
        app = _make_app(
            tmp_path,
            api_key="",   # no auth required, but test key extraction
            rate_limit_requests=1,
            rate_limit_per_key=True,
        )
        c = _client(app)
        # First call with key A passes
        r1 = c.get("/health", headers={"X-API-Key": "key_a"})
        # Second call with key A blocked
        r2 = c.get("/health", headers={"X-API-Key": "key_a"})
        # First call with key B still passes
        r3 = c.get("/health", headers={"X-API-Key": "key_b"})
        assert r1.status_code == 200
        assert r2.status_code == 429
        assert r3.status_code == 200


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

class TestAuth:
    def test_no_auth_required_when_key_empty(self, tmp_path):
        app = _make_app(tmp_path, api_key="")
        resp = _client(app).get("/health")
        assert resp.status_code == 200

    def test_rejects_wrong_key(self, tmp_path):
        app = _make_app(tmp_path, api_key="correct-key")
        resp = _client(app).get(
            "/sessions", headers={"X-API-Key": "wrong-key"}
        )
        assert resp.status_code == 401

    def test_accepts_correct_key_in_header(self, tmp_path):
        app = _make_app(tmp_path, api_key="mykey")
        resp = _client(app).get("/sessions", headers={"X-API-Key": "mykey"})
        assert resp.status_code == 200

    def test_accepts_bearer_token(self, tmp_path):
        app = _make_app(tmp_path, api_key="mykey")
        resp = _client(app).get(
            "/sessions", headers={"Authorization": "Bearer mykey"}
        )
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# SPA estática — regressão: os assets referenciados pelo index.html devem servir
# (a SPA buildada referencia /assets/*; sem o mount o serve devolve a página em
#  branco com 404 nos chunks). Ver bauer/server.py mount de /assets.
# ---------------------------------------------------------------------------

class TestStaticSpaAssets:
    def _static_dir(self) -> Path:
        return Path(__file__).resolve().parent.parent / "bauer" / "static"

    def test_index_referenced_assets_served(self, tmp_path):
        import re

        static_dir = self._static_dir()
        if not (static_dir / "index.html").exists():
            pytest.skip("SPA build ausente (bauer/static/index.html)")

        client = _client(_make_app(tmp_path))
        html = client.get("/")
        assert html.status_code == 200

        refs = re.findall(r'(?:src|href)="(\.?/assets/[^"]+)"', html.text)
        assert refs, "index.html não referencia nenhum /assets/* (build inesperado)"
        for ref in refs:
            path = ref.lstrip(".")  # './assets/x' -> '/assets/x'
            r = client.get(path)
            assert r.status_code == 200, f"asset {path} retornou {r.status_code}"

    def test_assets_mount_present_when_build_exists(self, tmp_path):
        static_dir = self._static_dir()
        if not (static_dir / "assets").is_dir():
            pytest.skip("diretório de assets ausente")
        app = _make_app(tmp_path)
        mounts = {getattr(r, "path", "") for r in app.routes}
        assert "/assets" in mounts


# ---------------------------------------------------------------------------
# SEC-01: Detalhes de exceção não devem vazar para clientes HTTP
# ---------------------------------------------------------------------------

class TestExceptionDetailHidden:
    """SEC-01: detail=str(exc) não deve vazar para clientes HTTP."""

    def test_chat_500_hides_exception_detail(self, tmp_path):
        from unittest.mock import patch

        with patch("bauer.agent.run_one_turn", side_effect=RuntimeError("path=/home/user/.bauer/auth.json token=sk-abc123")):
            app = _make_app(tmp_path)
            client = _client(app)
            resp = client.post(
                "/chat",
                json={"message": "oi"},
                headers={"X-API-Key": ""},
            )
        assert resp.status_code == 500
        assert "path=" not in resp.json()["detail"]
        assert "token=" not in resp.json()["detail"]
        assert "sk-" not in resp.json()["detail"]
        assert "logs" in resp.json()["detail"].lower()

    def test_v1_completions_500_hides_exception_detail(self, tmp_path):
        from unittest.mock import patch

        with patch("bauer.agent.run_one_turn", side_effect=ValueError("internal api key sk-secret")):
            app = _make_app(tmp_path)
            client = _client(app)
            resp = client.post(
                "/v1/chat/completions",
                json={"model": "test-model", "messages": [{"role": "user", "content": "oi"}]},
                headers={"X-API-Key": ""},
            )
        assert resp.status_code == 500
        body = resp.json()
        assert "sk-secret" not in str(body)


# ---------------------------------------------------------------------------
# SEC-05: comparação de API key deve usar hmac.compare_digest
# ---------------------------------------------------------------------------

class TestApiKeyComparison:
    """SEC-05: comparação de API key deve usar hmac.compare_digest."""

    def test_valid_key_grants_access(self, tmp_path):
        app = _make_app(tmp_path, api_key="secret-key-abc")
        client = _client(app)
        resp = client.get("/health", headers={"X-API-Key": "secret-key-abc"})
        assert resp.status_code == 200

    def test_invalid_key_returns_401(self, tmp_path):
        app = _make_app(tmp_path, api_key="secret-key-abc")
        client = _client(app)
        resp = client.post(
            "/chat",
            json={"message": "oi"},
            headers={"X-API-Key": "wrong-key"},
        )
        assert resp.status_code == 401

    def test_hmac_compare_digest_used(self):
        """Guarda de regressão: _verify_key usa compare_digest, não ==."""
        import inspect
        import bauer.server as srv
        src = inspect.getsource(srv)
        assert "hmac.compare_digest" in src
        assert "incoming != api_key" not in src
