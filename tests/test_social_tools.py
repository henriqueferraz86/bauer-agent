"""Testes das tools social_list_channels / social_post do ToolRouter."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from bauer.tool_router import ToolError, ToolRouter


@pytest.fixture
def ws(tmp_path: Path) -> Path:
    return tmp_path / "workspace"


@pytest.fixture
def router(ws: Path) -> ToolRouter:
    return ToolRouter(workspace=ws, postiz_api_key="test-key")


class TestPostizClientAusente:
    def test_sem_api_key_erro_claro(self, ws):
        router = ToolRouter(workspace=ws)  # sem postiz_api_key
        with pytest.raises(ToolError, match="POSTIZ_API_KEY"):
            router.execute({"action": "social_list_channels", "args": {}})


class TestSocialListChannels:
    def test_lista_contas(self, router):
        fake_client = MagicMock()
        fake_client.list_integrations.return_value = [
            {"id": "ig-1", "identifier": "instagram-standalone", "name": "bauercorp",
             "profile": "bauercorp", "disabled": False},
        ]
        with patch.object(router, "_postiz_client", return_value=fake_client):
            out = router.execute({"action": "social_list_channels", "args": {}})
        assert "ig-1" in out and "instagram-standalone" in out and "bauercorp" in out

    def test_lista_vazia_orienta(self, router):
        fake_client = MagicMock()
        fake_client.list_integrations.return_value = []
        with patch.object(router, "_postiz_client", return_value=fake_client):
            out = router.execute({"action": "social_list_channels", "args": {}})
        assert "Nenhuma conta" in out

    def test_erro_da_api_vira_tool_error(self, router):
        fake_client = MagicMock()
        fake_client.list_integrations.side_effect = RuntimeError("boom")
        with patch.object(router, "_postiz_client", return_value=fake_client):
            with pytest.raises(ToolError, match="Falha ao listar"):
                router.execute({"action": "social_list_channels", "args": {}})


class TestSocialPost:
    def test_args_obrigatorios(self, router):
        with pytest.raises(ToolError, match="content"):
            router.execute({"action": "social_post", "args": {"channels": ["ig-1"]}})
        with pytest.raises(ToolError, match="channels"):
            router.execute({"action": "social_post", "args": {"content": "oi"}})

    def test_post_type_invalido(self, router):
        with pytest.raises(ToolError, match="post_type"):
            router.execute({
                "action": "social_post",
                "args": {"content": "oi", "channels": ["ig-1"], "post_type": "agora"},
            })

    def test_publica_com_sucesso(self, router):
        fake_client = MagicMock()
        with patch.object(router, "_postiz_client", return_value=fake_client):
            out = router.execute({
                "action": "social_post",
                "args": {"content": "Olá mundo!", "channels": ["ig-1", "x-1"]},
            })
        assert "agendado" in out
        fake_client.create_post.assert_called_once()
        _, kwargs = fake_client.create_post.call_args
        assert kwargs["media_urls"] is None

    def test_draft(self, router):
        fake_client = MagicMock()
        with patch.object(router, "_postiz_client", return_value=fake_client):
            out = router.execute({
                "action": "social_post",
                "args": {"content": "rascunho", "channels": ["ig-1"], "post_type": "draft"},
            })
        assert "rascunho" in out.lower() or "salvo como rascunho" in out

    def test_upload_de_midia_antes_do_post(self, router, tmp_path):
        img = tmp_path / "foto.png"
        img.write_bytes(b"fake")
        fake_client = MagicMock()
        fake_client.upload.return_value = {"path": "https://cdn.postiz.com/foto.png"}
        with patch.object(router, "_postiz_client", return_value=fake_client):
            router.execute({
                "action": "social_post",
                "args": {
                    "content": "com foto",
                    "channels": ["ig-1"],
                    "media_paths": [str(img)],
                },
            })
        fake_client.upload.assert_called_once_with(str(img))
        _, kwargs = fake_client.create_post.call_args
        assert kwargs["media_urls"] == ["https://cdn.postiz.com/foto.png"]

    def test_falha_na_api_vira_tool_error(self, router):
        fake_client = MagicMock()
        fake_client.create_post.side_effect = RuntimeError("rate limited")
        with patch.object(router, "_postiz_client", return_value=fake_client):
            with pytest.raises(ToolError, match="Falha ao publicar"):
                router.execute({
                    "action": "social_post",
                    "args": {"content": "oi", "channels": ["ig-1"]},
                })


class TestMetadata:
    def test_tools_aparecem_no_available(self, router):
        tools = router.available_tools()
        assert "social_list_channels" in tools
        assert "social_post" in tools

    def test_risk_levels(self):
        from bauer.tool_router import _TOOL_SECURITY

        assert _TOOL_SECURITY["social_post"]["risk"] == "high"
        assert _TOOL_SECURITY["social_post"]["approval"] is True
        assert _TOOL_SECURITY["social_list_channels"]["risk"] == "low"
