"""The gateway refuses cleartext credentials to non-loopback destinations."""
import pytest

from bauer.tool_gateway_bridge import secure_service_url, ToolGatewayHTTPClient


@pytest.mark.parametrize(("url", "expected"), [
    ("http://127.0.0.1:8000", True),
    ("http://[::1]:8000", True),
    ("https://gateway.example:8000", True),
    ("http://localhost:8000", False),
    ("http://192.168.3.65:8000", False),
    ("https://user:pass@gateway.example", False),
    ("http://127.0.0.1:8000/?token=secret", False),
    ("http://[broken", False),
])
def test_secure_url(url, expected):
    assert secure_service_url(url) is expected


def test_client_requires_secure_transport_and_secret():
    with pytest.raises(ValueError):
        ToolGatewayHTTPClient(base_url="http://192.168.3.65:8000", service_key="x" * 40)
    with pytest.raises(ValueError):
        ToolGatewayHTTPClient(base_url="http://127.0.0.1:8000", service_key="short")
