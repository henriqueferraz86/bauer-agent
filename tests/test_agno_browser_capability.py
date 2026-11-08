"""Plano 057 / sprint 53 — browser governado sobre a sessão Playwright do Bauer.

Herméticos: nenhum Chromium é iniciado. ``_ensure_browser`` devolve uma página
fake que imita a API sync do Playwright usada por ``bauer/tools/browser.py``
(``goto``/``route``/``on``/``locator``/``evaluate``); as tools ``browser_*``
reais do ToolRouter rodam sobre ela, na thread dedicada.
"""

from __future__ import annotations

import json
import textwrap
from pathlib import Path

import pytest

from bauer.core.events.bus import EventBus
from bauer.core.runtime.adapters.agno_adapter import AgnoRuntimeAdapter
from bauer.core.runtime.agno_capabilities.catalog import CapabilityError, catalog_entries, resolve_capability
from bauer.core.runtime.agno_capabilities.families import browser as browser_family
from bauer.core.runtime.agno_capabilities.families.browser import BrowserProfile, request_allowed, url_allowed
from bauer.core.runtime.agno_capabilities.settings import CapabilitySettingsError, parse_settings
from bauer.execution_control import ExecutionControlError
from bauer.tools.base import ToolError
from bauer.tools.browser import BrowserToolsMixin

pytest.importorskip("agno")


class _Request:
    def __init__(self, url: str, navigation: bool):
        self.url = url
        self._navigation = navigation

    def is_navigation_request(self) -> bool:
        return self._navigation


class _Route:
    def __init__(self):
        self.outcome = None

    def continue_(self):
        self.outcome = "continue"

    def abort(self, reason: str = ""):
        self.outcome = "abort"


class _Response:
    status = 200


class _Locator:
    def __init__(self, page: "FakePage", selector: str):
        self.page = page
        self.selector = selector

    @property
    def first(self):
        return self

    def click(self, timeout=None):
        self.page.clicks.append(self.selector)
        target = self.page.links.get(self.selector)
        if target:
            # A link click that bypasses goto (e.g. JS location change the
            # route handler did not see) — post-action check must catch it.
            self.page.url = target

    def fill(self, text, timeout=None):
        self.page.typed.append(text)


class FakePage:
    def __init__(self):
        self.url = "about:blank"
        self.handler = None
        self.events: dict[str, list] = {}
        self.redirects: dict[str, str] = {}
        self.links: dict[str, str] = {}
        self.gotos: list[str] = []
        self.clicks: list[str] = []
        self.typed: list[str] = []
        self.closed = False

    def route(self, pattern, handler):
        self.handler = handler

    def unroute(self, pattern):
        self.handler = None

    def on(self, event, callback):
        self.events.setdefault(event, []).append(callback)

    def _request(self, url: str, navigation: bool) -> bool:
        route = _Route()
        self.handler(route, _Request(url, navigation))
        return route.outcome == "continue"

    def goto(self, url, wait_until="load", timeout=None):
        self.gotos.append(url)
        for hop in (url, self.redirects.get(url)):
            if hop is None:
                continue
            if not self._request(hop, True):
                raise RuntimeError("net::ERR_BLOCKED_BY_CLIENT")
            self.url = hop
        return _Response()

    def title(self):
        return "Página"

    def evaluate(self, script):
        return "body"

    def locator(self, selector):
        return _Locator(self, selector)

    def close(self):
        self.closed = True


@pytest.fixture()
def page(monkeypatch):
    fake = FakePage()

    def ensure(self):
        self._browser_page = fake
        return fake

    monkeypatch.setattr(BrowserToolsMixin, "_ensure_browser", ensure)
    return fake


@pytest.fixture()
def configured(tmp_path, monkeypatch):
    target = tmp_path / "agno_capabilities.yaml"
    target.write_text(textwrap.dedent("""
        capabilities:
          bauer.browser:
            profiles:
              default:
                allowed_domains: [app.example.com, "*.docs.example.org"]
    """), encoding="utf-8")
    monkeypatch.setenv("BAUER_AGNO_CAPABILITIES", str(target))
    monkeypatch.setattr("bauer.core.runtime.agno_capabilities.catalog.module_spec_exists", lambda name: True)


def _adapter(tmp_path: Path, bus: EventBus | None = None) -> AgnoRuntimeAdapter:
    workspace = tmp_path / "workspace"
    workspace.mkdir(exist_ok=True)
    return AgnoRuntimeAdapter(
        adapter_config={"workspace": str(workspace), "db_file": str(tmp_path / "agno.db")},
        runtime_policy_root=tmp_path / "runtime",
        runtime_event_bus=bus,
    )


def _tools(adapter, ref):
    return {fn.name: fn for fn in adapter._map_tools([ref])}


# ── policy pura ──────────────────────────────────────────────────────────────


def test_url_and_request_policy():
    profile = BrowserProfile(allowed_domains=["app.example.com", "*.docs.example.org"])
    assert url_allowed("https://app.example.com/login", profile)
    assert url_allowed("https://docs.example.org/x", profile)
    assert url_allowed("https://a.b.docs.example.org/x", profile)
    assert not url_allowed("http://app.example.com/", profile)  # https only by default
    assert not url_allowed("https://evil.com/?r=app.example.com", profile)
    assert not url_allowed("https://app.example.com.evil.com/", profile)
    assert not url_allowed("https://user:pass@app.example.com/", profile)
    assert not url_allowed("file:///etc/passwd", profile)
    assert not request_allowed("http://169.254.169.254/latest", is_navigation=False, profile=profile)
    assert not request_allowed("http://10.0.0.5/admin", is_navigation=False, profile=profile)
    assert request_allowed("https://cdn.thirdparty.net/app.js", is_navigation=False, profile=profile)


def test_authenticated_profiles_and_bad_domains_are_rejected():
    with pytest.raises(CapabilitySettingsError):
        parse_settings({"capabilities": {"bauer.browser": {"profiles": {"default": {
            "allowed_domains": ["app.example.com"], "authenticated": True}}}}})
    with pytest.raises(CapabilitySettingsError):
        parse_settings({"capabilities": {"bauer.browser": {"profiles": {"default": {
            "allowed_domains": ["https://app.example.com/path"]}}}}})


def test_agno_browser_toolkits_are_incompatible():
    rows = {row["id"]: row for row in catalog_entries()}
    for capability_id in ("agno.browserbase", "agno.agentql", "agno.webbrowser"):
        assert rows[capability_id]["state"] == "incompatible"


# ── sessão governada ─────────────────────────────────────────────────────────


def test_navigate_allowed_domain_records_only_domain(tmp_path, page, configured):
    bus = EventBus(root=tmp_path / "events")
    tools = _tools(_adapter(tmp_path, bus), "bauer.browser")
    assert set(tools) == {"bauer_browser__navigate", "bauer_browser__snapshot", "bauer_browser__close"}

    out = tools["bauer_browser__navigate"].entrypoint(url="https://app.example.com/dashboard?token=abc")
    assert "status HTTP 200" in out
    domain_events = [e for e in bus.list_events() if e.data.get("capability") == "bauer.browser" and "domain" in e.data]
    assert domain_events[-1].data == {"capability": "bauer.browser", "domain": "app.example.com", "decision": "allowed"}
    assert "token=abc" not in json.dumps([e.data for e in bus.list_events()])
    assert page.events.get("download")  # download guard installed


def test_navigate_outside_profile_never_reaches_the_page(tmp_path, page, configured):
    navigate = _tools(_adapter(tmp_path), "bauer.browser:navigate")["bauer_browser__navigate"]
    with pytest.raises(ToolError, match="fora dos domínios"):
        navigate.entrypoint(url="https://evil.example.net/")
    assert page.gotos == []


def test_redirect_out_of_profile_is_aborted_in_the_page(tmp_path, page, configured):
    page.redirects["https://app.example.com/out"] = "https://evil.example.net/phish"
    navigate = _tools(_adapter(tmp_path), "bauer.browser:navigate")["bauer_browser__navigate"]
    with pytest.raises(ToolError, match="ERR_BLOCKED_BY_CLIENT"):
        navigate.entrypoint(url="https://app.example.com/out")
    assert page.url == "https://app.example.com/out"


def test_downloads_are_cancelled(tmp_path, page, configured):
    _tools(_adapter(tmp_path), "bauer.browser:navigate")["bauer_browser__navigate"].entrypoint(
        url="https://app.example.com/"
    )

    class Download:
        cancelled = False

        def cancel(self):
            self.cancelled = True

    download = Download()
    for callback in page.events["download"]:
        callback(download)
    assert download.cancelled


def test_interaction_requires_policy_approval(tmp_path, page, configured):
    tools = _tools(_adapter(tmp_path), "bauer.browser:navigate,click")
    tools["bauer_browser__navigate"].entrypoint(url="https://app.example.com/")
    with pytest.raises(ToolError, match="waiting_approval"):
        tools["bauer_browser__click"].entrypoint(selector="#delete")
    assert page.clicks == []


def test_leaving_profile_after_action_closes_session(tmp_path, page, configured):
    page.links["#external"] = "https://evil.example.net/"
    adapter = _adapter(tmp_path)
    adapter.runtime_policy_mode = "off"  # isolate the post-action check from approvals
    tools = _tools(adapter, "bauer.browser:navigate,click")
    tools["bauer_browser__navigate"].entrypoint(url="https://app.example.com/")
    with pytest.raises(ToolError, match="saiu do perfil"):
        tools["bauer_browser__click"].entrypoint(selector="#external")
    assert page.closed


def test_cancellation_closes_session(tmp_path, page, configured, monkeypatch):
    tools = _tools(_adapter(tmp_path), "bauer.browser:navigate,snapshot")
    tools["bauer_browser__navigate"].entrypoint(url="https://app.example.com/")

    def cancelled(self, args):
        raise ExecutionControlError("cancelled")

    original = BrowserToolsMixin._browser_snapshot
    calls = {"n": 0}

    def snapshot(self, args):
        calls["n"] += 1
        if calls["n"] > 1:  # first call is the in-profile URL check
            return cancelled(self, args)
        return original(self, args)

    monkeypatch.setattr(BrowserToolsMixin, "_browser_snapshot", snapshot)
    with pytest.raises(BaseException):
        tools["bauer_browser__snapshot"].entrypoint()
    assert page.closed


def test_click_without_approval_flow_registration_would_be_blocked(configured, monkeypatch):
    monkeypatch.setattr("bauer.core.runtime.agno_capabilities.catalog._APPROVAL_FLOWS", set())
    with pytest.raises(CapabilityError) as exc:
        resolve_capability("bauer.browser:click")
    assert exc.value.state == "blocked_by_policy"


def test_browser_without_settings_is_not_ready(tmp_path, monkeypatch):
    monkeypatch.setenv("BAUER_AGNO_CAPABILITIES", str(tmp_path / "none.yaml"))
    row = next(row for row in catalog_entries() if row["id"] == "bauer.browser")
    assert row["state"] in {"needs_configuration", "dependency_missing"}
    assert browser_family.BrowserSettings  # module importable without Playwright browsers
