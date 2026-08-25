"""SSRF boundaries for user-configured gateway webhooks."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from bauer.gateway_adapters import GatewayDeliveryAdapter
from bauer.url_safety import UrlSafetyError


def _adapter(tmp_path, **kwargs) -> GatewayDeliveryAdapter:
    return GatewayDeliveryAdapter(tmp_path, **kwargs)


def test_private_webhook_is_blocked_before_urlopen(tmp_path):
    adapter = _adapter(tmp_path)
    with patch("bauer.gateway_adapters.urllib.request.urlopen") as urlopen:
        with pytest.raises(UrlSafetyError, match="SSRF blocked"):
            adapter.deliver(
                channel="webhook",
                target="http://127.0.0.1/internal",
                payload={"message": "blocked"},
            )
    urlopen.assert_not_called()


def test_public_webhook_proceeds_to_urlopen(tmp_path):
    adapter = _adapter(tmp_path)
    response = MagicMock(status=200)
    response.__enter__.return_value = response
    with patch("bauer.gateway_adapters.urllib.request.urlopen", return_value=response) as urlopen:
        adapter.deliver(
            channel="webhook",
            target="https://8.8.8.8/hook",
            payload={"message": "delivered"},
        )
    urlopen.assert_called_once()


def test_internal_webhook_requires_explicit_constructor_opt_in(tmp_path):
    adapter = _adapter(tmp_path, allow_internal_webhooks=True)
    response = MagicMock(status=200)
    response.__enter__.return_value = response
    with patch("bauer.gateway_adapters.urllib.request.urlopen", return_value=response) as urlopen:
        adapter.deliver(
            channel="webhook",
            target="http://127.0.0.1/trusted-receiver",
            payload={"message": "allowed explicitly"},
        )
    urlopen.assert_called_once()


def test_internal_webhook_config_opt_in_is_applied(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "bauer.config_loader.load_config",
        lambda: SimpleNamespace(gateway=SimpleNamespace(allow_internal_webhooks=True)),
    )
    adapter = _adapter(tmp_path)
    response = MagicMock(status=200)
    response.__enter__.return_value = response
    with patch("bauer.gateway_adapters.urllib.request.urlopen", return_value=response) as urlopen:
        adapter.deliver(
            channel="webhook",
            target="http://127.0.0.1/configured-receiver",
            payload={"message": "allowed by config"},
        )
    urlopen.assert_called_once()


def test_private_whatsapp_api_base_is_blocked_before_urlopen(tmp_path, monkeypatch):
    monkeypatch.setenv("WHATSAPP_ACCESS_TOKEN", "test-token")
    adapter = _adapter(tmp_path)
    with patch("bauer.gateway_adapters.urllib.request.urlopen") as urlopen:
        with pytest.raises(UrlSafetyError, match="SSRF blocked"):
            adapter.deliver(
                channel="whatsapp",
                target="recipient",
                payload={"message": "blocked"},
                metadata={
                    "phone_number_id": "123",
                    "api_base": "http://127.0.0.1:8080",
                },
            )
    urlopen.assert_not_called()


def test_internal_whatsapp_api_base_requires_explicit_opt_in(tmp_path, monkeypatch):
    monkeypatch.setenv("WHATSAPP_ACCESS_TOKEN", "test-token")
    adapter = _adapter(tmp_path, allow_internal_webhooks=True)
    response = MagicMock(status=200)
    response.__enter__.return_value = response
    with patch("bauer.gateway_adapters.urllib.request.urlopen", return_value=response) as urlopen:
        adapter.deliver(
            channel="whatsapp",
            target="recipient",
            payload={"message": "allowed explicitly"},
            metadata={
                "phone_number_id": "123",
                "api_base": "http://127.0.0.1:8080",
            },
        )
    urlopen.assert_called_once()


@pytest.mark.parametrize("channel", ["discord", "slack"])
def test_provider_webhook_target_is_also_checked_before_urlopen(tmp_path, channel: str):
    adapter = _adapter(tmp_path)
    with patch("bauer.gateway_adapters.urllib.request.urlopen") as urlopen:
        with pytest.raises(UrlSafetyError, match="SSRF blocked"):
            adapter.deliver(
                channel=channel,
                target="http://10.0.0.9/hook",
                payload={"message": "blocked"},
            )
    urlopen.assert_not_called()
