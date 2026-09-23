from pathlib import Path

from bauer.config_loader import BauerConfig, validate_config_file
from bauer.env_loader import apply_env_to_config


ROOT = Path(__file__).resolve().parent.parent


def test_serve_web_auth_defaults_are_safe():
    serve = BauerConfig(model={"name": "qwen3:0.6b"}).serve
    assert serve.web_auth_enabled is True
    assert serve.auth_session_hours == 168
    assert serve.auth_google_client_id == ""


def test_google_client_id_can_come_from_environment(monkeypatch):
    cfg = BauerConfig(model={"name": "qwen3:0.6b"})
    monkeypatch.setenv("BAUER_AUTH_GOOGLE_CLIENT_ID", "client.apps.googleusercontent.com")
    apply_env_to_config(cfg)
    assert cfg.serve.auth_google_client_id == "client.apps.googleusercontent.com"


def test_documented_example_accepts_web_auth_fields():
    ok, message = validate_config_file(ROOT / "config.yaml.example")
    assert ok, message


def test_frontend_does_not_persist_api_key_anymore():
    client_source = (ROOT / "desktop" / "src" / "api" / "client.ts").read_text(encoding="utf-8")
    config_source = (ROOT / "desktop" / "src" / "screens" / "Config.tsx").read_text(encoding="utf-8")
    assert 'setItem("bauer.apiKey"' not in client_source
    assert "X-API-Key do serve" not in config_source
    assert "clearLegacyApiKey" in client_source
