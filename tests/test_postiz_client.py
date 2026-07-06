"""Testes do PostizClient — httpx.MockTransport, sem rede."""

from __future__ import annotations

import httpx
import pytest

from bauer.postiz_client import PostizClient, PostizError


def _make_client(handler) -> PostizClient:
    client = PostizClient(api_key="test-key")
    client._http = httpx.Client(
        base_url=client.api_url,
        headers={"Authorization": "test-key"},
        transport=httpx.MockTransport(handler),
    )
    return client


class TestConstrucao:
    def test_sem_api_key_falha_claro(self):
        with pytest.raises(PostizError, match="POSTIZ_API_KEY"):
            PostizClient(api_key="")

    def test_api_url_default(self):
        client = PostizClient(api_key="x")
        assert client.api_url == "https://api.postiz.com"

    def test_api_url_customizado_self_hosted(self):
        client = PostizClient(api_key="x", api_url="http://localhost:4007/")
        assert client.api_url == "http://localhost:4007"


class TestListIntegrations:
    def test_lista_integracoes(self):
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.path == "/public/v1/integrations"
            assert request.headers["Authorization"] == "test-key"
            return httpx.Response(200, json=[{"id": "ig-1", "provider": "instagram"}])

        client = _make_client(handler)
        result = client.list_integrations()
        assert result == [{"id": "ig-1", "provider": "instagram"}]

    def test_filtra_por_group(self):
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.params["group"] == "acme"
            return httpx.Response(200, json=[])

        client = _make_client(handler)
        client.list_integrations(group="acme")

    def test_erro_http_levanta_postiz_error(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(401, text="Unauthorized")

        client = _make_client(handler)
        with pytest.raises(PostizError, match="401"):
            client.list_integrations()


class TestCreatePost:
    def test_monta_body_correto(self):
        captured = {}

        def handler(request: httpx.Request) -> httpx.Response:
            import json
            captured["body"] = json.loads(request.content)
            return httpx.Response(200, json={"posts": [{"id": "p1"}]})

        client = _make_client(handler)
        client.create_post(
            "Olá mundo!", ["ig-1", "x-1"],
            media_urls=["https://cdn.postiz.com/a.png"],
            schedule_at="2026-01-01T12:00:00Z",
        )
        body = captured["body"]
        assert body["date"] == "2026-01-01T12:00:00Z"
        assert body["type"] == "schedule"
        assert len(body["posts"]) == 2
        assert body["posts"][0]["integration"]["id"] == "ig-1"
        assert body["posts"][0]["value"][0]["content"] == "Olá mundo!"
        assert body["posts"][0]["value"][0]["image"][0]["path"] == "https://cdn.postiz.com/a.png"

    def test_sem_schedule_at_usa_agora(self):
        captured = {}

        def handler(request: httpx.Request) -> httpx.Response:
            import json
            captured["body"] = json.loads(request.content)
            return httpx.Response(200, json={})

        client = _make_client(handler)
        client.create_post("oi", ["ig-1"])
        assert captured["body"]["date"]  # preenchido, não vazio

    def test_draft(self):
        captured = {}

        def handler(request: httpx.Request) -> httpx.Response:
            import json
            captured["body"] = json.loads(request.content)
            return httpx.Response(200, json={})

        client = _make_client(handler)
        client.create_post("oi", ["ig-1"], post_type="draft")
        assert captured["body"]["type"] == "draft"


class TestUpload:
    def test_upload_arquivo_inexistente(self):
        client = PostizClient(api_key="x")
        with pytest.raises(PostizError, match="não encontrado"):
            client.upload("/caminho/que/nao/existe.png")

    def test_upload_ok(self, tmp_path):
        f = tmp_path / "img.png"
        f.write_bytes(b"fake-png-bytes")

        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.path == "/public/v1/upload"
            return httpx.Response(200, json={"path": "https://cdn.postiz.com/img.png"})

        client = _make_client(handler)
        result = client.upload(f)
        assert result["path"] == "https://cdn.postiz.com/img.png"


class TestDeleteEList:
    def test_delete_post(self):
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.method == "DELETE"
            assert request.url.path == "/public/v1/posts/p1"
            return httpx.Response(200, json={"ok": True})

        client = _make_client(handler)
        client.delete_post("p1")

    def test_list_posts(self):
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.params["startDate"] == "2026-01-01"
            assert request.url.params["endDate"] == "2026-02-01"
            return httpx.Response(200, json=[])

        client = _make_client(handler)
        client.list_posts("2026-01-01", "2026-02-01")
