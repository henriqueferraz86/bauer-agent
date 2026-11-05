"""Sprint 53 — browser e testes de interface (plano 057, sprint "21").

Decision: Bauer's own Playwright session (``bauer/tools/browser.py``, one
thread-affine page per ToolRouter) is the **single** browser backend. Agno's
browser toolkits (Browserbase cloud sessions, AgentQL's own Playwright,
``webbrowser`` opening the desktop browser) would create concurrent sessions
and duplicate credentials, so they are catalogued as incompatible.

``bauer.browser`` wraps the existing ``browser_*`` implementations (each inner
Playwright step runs on the router's dedicated browser thread, after the
outer capability call passed the router's grant/policy/budget) and adds what
they lack:

* a domain allowlist per configured profile, enforced *in the page* with a
  Playwright route handler — every navigation (typed, clicked or redirected)
  to a host outside the profile is aborted, and every request to a private,
  loopback, link-local or metadata address is aborted (SSRF);
* downloads are cancelled;
* after each action the current URL is re-checked; leaving the profile
  closes the page;
* interactions (click/type/press) are ``write`` actions under the
  ``os.ui_control`` policy operation (default rule: ask for approval);
* cancellation or error closes the browser session;
* only the *domain* is recorded (``policy.evaluated``) — never DOM, text,
  screenshots or typed values.

Authenticated profiles (stored cookies) are refused for now: there is no
isolated per-profile browser context yet, and reusing the shared page would
leak the session between profiles.
"""

from __future__ import annotations

import ipaddress
import logging
import re
from typing import Any, Callable
from urllib.parse import urlsplit

from pydantic import Field, field_validator

from .....logging_config import log_suppressed
from .....tools.base import ToolError
from ..bridge import ActionImpl, register_factory
from ..catalog import enable_approval_flow
from ..manifest import ActionPolicy, ToolkitPolicy, register_policy
from ..settings import StrictSettings, register_settings_model

logger = logging.getLogger("bauer.agno_capabilities.browser")

_DOMAIN = re.compile(r"^(?:\*\.)?[a-z0-9](?:[a-z0-9\-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9\-]{0,61}[a-z0-9])?)*$")
_METADATA_HOSTS = frozenset({"metadata.google.internal", "metadata.goog", "169.254.169.254", "fd00:ec2::254"})


class BrowserProfile(StrictSettings):
    allowed_domains: list[str] = Field(min_length=1)
    #: Explicitly allow http:// (default https only).
    allow_http: bool = False
    #: Local development servers (localhost/127.0.0.1) — off by default.
    allow_localhost: bool = False
    authenticated: bool = False

    @field_validator("allowed_domains")
    @classmethod
    def _domains(cls, value: list[str]) -> list[str]:
        normalized = []
        for item in value:
            text = str(item).strip().lower().rstrip(".")
            if not _DOMAIN.match(text):
                raise ValueError("domínio inválido (use example.com ou *.example.com)")
            normalized.append(text)
        return sorted(set(normalized))

    @field_validator("authenticated")
    @classmethod
    def _no_auth(cls, value: bool) -> bool:
        if value:
            raise ValueError("perfis autenticados ainda não são suportados (sem contexto isolado por perfil)")
        return value


class BrowserSettings(StrictSettings):
    profiles: dict[str, BrowserProfile] = Field(min_length=1)
    default_profile: str = "default"

    @field_validator("default_profile")
    @classmethod
    def _name(cls, value: str) -> str:
        if not re.match(r"^[a-z][a-z0-9_]{0,40}$", value):
            raise ValueError("nome de perfil inválido")
        return value

    def profile(self, name: str | None) -> tuple[str, BrowserProfile]:
        key = str(name or self.default_profile)
        if key not in self.profiles:
            raise ToolError(f"perfil de browser desconhecido; disponíveis: {', '.join(sorted(self.profiles))}")
        return key, self.profiles[key]


# ── pure policy ──────────────────────────────────────────────────────────────


def _is_internal_host(host: str) -> bool:
    if host in _METADATA_HOSTS or host == "localhost" or host.endswith(".localhost"):
        return True
    try:
        address = ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        return False
    return (
        address.is_private or address.is_loopback or address.is_link_local
        or address.is_multicast or address.is_reserved or address.is_unspecified
    )


def host_allowed(host: str, profile: BrowserProfile) -> bool:
    host = (host or "").lower().rstrip(".")
    if not host:
        return False
    if profile.allow_localhost and host in {"localhost", "127.0.0.1", "::1"}:
        return True
    if _is_internal_host(host):
        return False
    for pattern in profile.allowed_domains:
        if pattern.startswith("*."):
            base = pattern[2:]
            if host == base or host.endswith("." + base):
                return True
        elif host == pattern:
            return True
    return False


def url_allowed(url: str, profile: BrowserProfile) -> bool:
    try:
        parts = urlsplit(str(url))
    except ValueError:
        return False
    if parts.scheme == "about" and parts.path == "blank":
        return True
    if parts.scheme not in ({"https", "http"} if profile.allow_http else {"https"}):
        return False
    if parts.username or parts.password:
        return False
    return host_allowed(parts.hostname or "", profile)


def request_allowed(url: str, *, is_navigation: bool, profile: BrowserProfile) -> bool:
    """Route-handler decision: navigations must be in-profile; any request to
    an internal address is refused (SSRF through subresources)."""
    if is_navigation:
        return url_allowed(url, profile)
    try:
        parts = urlsplit(str(url))
    except ValueError:
        return False
    if parts.scheme in {"data", "blob"}:
        return True
    host = (parts.hostname or "").lower()
    if profile.allow_localhost and host in {"localhost", "127.0.0.1", "::1"}:
        return True
    return parts.scheme in {"http", "https", "ws", "wss"} and not _is_internal_host(host)


# ── session plumbing (runs on the router's browser thread) ───────────────────


def install_guards(page: Any, profile: BrowserProfile) -> None:
    """Install route + download guards on a Playwright page (idempotent)."""
    if getattr(page, "_bauer_guard_profile", None) is profile:
        return
    if getattr(page, "_bauer_guard_installed", False):
        page.unroute("**/*")

    def handle(route: Any, request: Any) -> None:
        if request_allowed(request.url, is_navigation=request.is_navigation_request(), profile=profile):
            route.continue_()
        else:
            route.abort("blockedbyclient")

    page.route("**/*", handle)
    if not getattr(page, "_bauer_guard_installed", False):
        page.on("download", lambda download: download.cancel())
    page._bauer_guard_installed = True
    page._bauer_guard_profile = profile


def _on_browser_thread(router: Any, fn: Callable[[], Any]) -> Any:
    from contextvars import copy_context

    future = router._get_browser_executor().submit(copy_context().run, fn)
    return future.result(timeout=60)


def close_session(router: Any) -> None:
    """Close page/context/Playwright on the browser thread, then stop the thread."""
    def _close() -> None:
        for attr, method in (("_browser_page", "close"), ("_browser_ctx", "close")):
            obj = getattr(router, attr, None)
            if obj is not None:
                try:
                    getattr(obj, method)()
                except Exception as exc:  # noqa: BLE001 - best-effort teardown
                    log_suppressed("agno.browser.close_page", exc)
                setattr(router, attr, None)
        pw = getattr(router, "_browser_pw", None)
        if pw is not None:
            try:
                pw.__exit__(None, None, None)
            except Exception as exc:  # noqa: BLE001
                log_suppressed("agno.browser.close_playwright", exc)
            router._browser_pw = None

    try:
        if getattr(router, "_browser_executor", None) is not None:
            _on_browser_thread(router, _close)
    except Exception as exc:  # noqa: BLE001
        logger.debug("browser close failed: %s", type(exc).__name__)
    finally:
        try:
            router.close_browser_executor()
        except Exception as exc:  # noqa: BLE001
            log_suppressed("agno.browser.close_executor", exc)


def _record_domain(router: Any, host: str, decision: str) -> None:
    bus = getattr(router, "_event_bus", None)
    if bus is None:
        return
    try:
        bus.publish(
            "policy.evaluated",
            run_id=getattr(router, "_runtime_run_id", None),
            session_id=getattr(router, "_runtime_session_id", None),
            tool_name="bauer.browser",
            status=decision,
            message=f"browser domain {decision}",
            data={"capability": "bauer.browser", "domain": host, "decision": decision},
        )
    except Exception as exc:  # noqa: BLE001 - telemetry is best-effort
        log_suppressed("agno.browser.record_domain", exc)


_SNAPSHOT_URL = re.compile(r"^\[browser_snapshot\][^\n|]*\|\s*(\S+)")


def _inner(router: Any, tool: str, args: dict[str, Any]) -> str:
    """Run an existing ``browser_*`` implementation on the dedicated thread.

    The *outer* capability call already went through the ToolRouter (grant,
    policy operation, budget, events); the inner Playwright step reuses the
    same session without a second, conflicting context check.
    """
    method = getattr(router, f"_{tool}")
    return str(_on_browser_thread(router, lambda: method(dict(args))))


def _current_url(router: Any) -> str:
    snapshot = _inner(router, "browser_snapshot", {})
    match = _SNAPSHOT_URL.match(str(snapshot))
    return match.group(1) if match else ""


def _actions(settings: BrowserSettings, router_factory: Callable[[], Any]) -> dict[str, Callable[..., Any]]:
    state: dict[str, Any] = {"profile": None}

    def _session(profile_name: str | None = None) -> tuple[Any, BrowserProfile]:
        router = router_factory()
        name, profile = settings.profile(profile_name or state["profile"])
        state["profile"] = name
        page = _on_browser_thread(router, router._ensure_browser)
        _on_browser_thread(router, lambda: install_guards(page, profile))
        return router, profile

    def _verify_still_in_profile(router: Any, profile: BrowserProfile) -> str:
        url = _current_url(router)
        host = (urlsplit(url).hostname or "") if url else ""
        if url and not url_allowed(url, profile):
            _record_domain(router, host, "blocked")
            close_session(router)
            raise ToolError("a página saiu do perfil permitido; sessão de browser encerrada")
        return host

    def _guarded(call: Callable[[], str], router: Any) -> str:
        try:
            return call()
        except ToolError:
            raise
        except BaseException:
            close_session(router)
            raise

    def navigate(url: str, profile: str = "") -> str:
        """Abre uma URL permitida pelo perfil de browser configurado.

        Args:
            url: URL https completa dentro dos domínios do perfil.
            profile: Perfil configurado (vazio = padrão).
        """
        router, active = _session(profile or None)
        host = (urlsplit(str(url)).hostname or "").lower()
        if not url_allowed(url, active):
            _record_domain(router, host, "blocked")
            raise ToolError("URL fora dos domínios permitidos pelo perfil de browser")
        result = _guarded(lambda: _inner(router, "browser_navigate", {"url": url}), router)
        _record_domain(router, _verify_still_in_profile(router, active) or host, "allowed")
        return result

    def snapshot() -> str:
        """Lê a árvore de acessibilidade/texto da página atual (perfil permitido)."""
        router, active = _session()
        _verify_still_in_profile(router, active)
        return _guarded(lambda: _inner(router, "browser_snapshot", {}), router)

    def click(selector: str, by: str = "css") -> str:
        """Clica num elemento da página atual (interação de teste; requer aprovação por policy).

        Args:
            selector: Seletor CSS, XPath, texto ou role.
            by: css, xpath, text ou role.
        """
        router, active = _session()
        _verify_still_in_profile(router, active)
        result = _guarded(lambda: _inner(router, "browser_click", {"selector": selector, "by": by}), router)
        _verify_still_in_profile(router, active)
        return result

    def type_text(selector: str, text: str) -> str:
        """Digita texto num campo da página atual (requer aprovação por policy).

        Args:
            selector: Seletor CSS do campo.
            text: Texto a digitar (não é registrado em telemetria).
        """
        router, active = _session()
        _verify_still_in_profile(router, active)
        return _guarded(lambda: _inner(router, "browser_type", {"selector": selector, "text": text}), router)

    def press(key: str) -> str:
        """Pressiona uma tecla na página atual (requer aprovação por policy).

        Args:
            key: Tecla (Enter, Tab, Control+A…).
        """
        router, active = _session()
        _verify_still_in_profile(router, active)
        result = _guarded(lambda: _inner(router, "browser_press", {"key": key}), router)
        _verify_still_in_profile(router, active)
        return result

    def close() -> str:
        """Fecha a sessão de browser (página, contexto e thread dedicada)."""
        close_session(router_factory())
        return "[browser] sessão encerrada"

    return {
        "navigate": navigate,
        "snapshot": snapshot,
        "click": click,
        "type_text": type_text,
        "press": press,
        "close": close,
    }


def _factory(resolved, section, context=None) -> dict[str, ActionImpl]:
    from ..bridge import _parameters_for

    if not isinstance(section, BrowserSettings):
        raise ToolError("bauer.browser sem configuração")
    router_factory = getattr(context, "router_factory", None)
    if router_factory is None:
        raise ToolError("bauer.browser exige o ToolRouter do agente")
    actions = _actions(section, router_factory)
    return {
        action.name: ActionImpl(
            fn=actions[action.name],
            description=(actions[action.name].__doc__ or "").strip().split("\n\n")[0],
            parameters=_parameters_for(actions[action.name]),
            timeout_seconds=0,  # inner browser_* tools carry their own timeouts
        )
        for action in resolved.actions
    }


register_settings_model("bauer.browser", BrowserSettings)
register_policy(
    ToolkitPolicy(
        id="bauer.browser",
        title="Browser governado (Playwright Bauer)",
        category="Browser e testes de UI",
        description="Navegação e interação de teste em domínios permitidos por perfil, sobre a sessão Playwright única do Bauer.",
        source="bauer",
        actions=(
            ActionPolicy("navigate", "read", "browser.navigate"),
            ActionPolicy("snapshot", "read", "browser.read"),
            ActionPolicy("close", "read", "browser.read"),
            ActionPolicy("click", "write", "os.ui_control", approval_required=True),
            ActionPolicy("type_text", "write", "os.ui_control", approval_required=True),
            ActionPolicy("press", "write", "os.ui_control", approval_required=True),
        ),
        requires=("playwright",),
        install_hint="uv pip install playwright && playwright install chromium",
        settings_section="bauer.browser",
        notes="Downloads cancelados; navegação fora do perfil abortada na página; só o domínio vai para a auditoria.",
    )
)
register_factory("bauer.browser", _factory)
for _action in ("click", "type_text", "press"):
    # os.ui_control: default policy rule asks for human approval per call.
    enable_approval_flow("bauer.browser", _action)

for _capability_id, _title, _reason in (
    ("agno.browserbase", "Browserbase", "abre sessão de browser remota na nuvem com credenciais próprias"),
    ("agno.agentql", "AgentQL", "inicia sua própria sessão Playwright, concorrente à sessão Bauer"),
):
    register_policy(
        ToolkitPolicy(
            id=_capability_id,
            title=_title,
            category="Browser e testes de UI",
            description="Toolkit de browser do SDK Agno.",
            incompatible=(
                f"O toolkit {_reason}. O Bauer usa um único backend de browser governado: bauer.browser."
            ),
        )
    )
