"""Sprint 75 — infraestrutura comum dos conectores de escrita (plano 067).

Shared by ``bauer.slack_messages``, ``bauer.linear_issues`` and
``bauer.gitlab_issues`` (the sprint 57 pattern, generalized):

* ``WriteLedger`` — per-connector idempotency ledger, one file per key.
  ``reserve`` uses ``O_CREAT|O_EXCL`` so two identical calls (threads or
  processes) can never both reach the remote API. A reservation stays
  ``pending`` while the effect is uncertain (timeout, 5xx, broken connection
  after sending); a retry then asks the remote side (marker lookup) instead of
  sending again.
* ``execute_once`` — the replay/lookup/send state machine on top of it.
* ``http_client`` — ``httpx`` client with redirects **off** (a 3xx is never
  followed, so a credential header can never reach another origin — sprint
  62), ``shared_ssl_context`` and a test seam (``_TRANSPORT``).
* ``ensure_local_allowed`` — ``--local`` (``BAUER_LOCAL_ONLY``) refuses every
  target host that is not this machine (sprint 69 definition).
* ``safe_summary`` — approval card text: single line, truncated, redacted.

Error messages built here are fixed strings plus status codes / validated
API error codes: never exception text, response bodies or headers, so a
token can never leak through a ``ToolError``.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
from pathlib import Path
from typing import Any, Callable

from .....tools.base import ToolError

#: Test seam shared by the three connectors: an ``httpx`` transport
#: (``MockTransport``). ``None`` = real network.
_TRANSPORT: Any = None

_KEY = re.compile(r"^[0-9a-f]{16,64}$")
_LOCK = threading.Lock()


class NotSent(Exception):
    """The remote side certainly did not apply the write (safe to retry)."""


class Uncertain(Exception):
    """The write may or may not have been applied (never resend blindly)."""


def http_client(*, base_url: str, headers: dict[str, str], timeout: float):
    import httpx

    from .....http_shared import shared_ssl_context

    kwargs: dict[str, Any] = {
        "base_url": base_url,
        "headers": {"User-Agent": "bauer-agent", **headers},
        "timeout": timeout,
        "follow_redirects": False,
    }
    if _TRANSPORT is not None:
        kwargs["transport"] = _TRANSPORT
    else:
        kwargs["verify"] = shared_ssl_context()
    return httpx.Client(**kwargs)


def send(client: Any, method: str, path: str, *, service: str, **kwargs: Any) -> Any:
    """Perform the *write* request, classifying transport failures."""
    import httpx

    try:
        response = client.request(method, path, **kwargs)
    except (httpx.ConnectError, httpx.ConnectTimeout):
        raise NotSent(f"{service}: falha de conexão; nada foi enviado") from None
    except httpx.TimeoutException:
        raise Uncertain(
            f"{service} não respondeu no prazo; o efeito é incerto — repita a MESMA chamada "
            "para verificar antes de qualquer nova ação"
        ) from None
    except httpx.HTTPError as exc:
        raise Uncertain(f"{service}: falha de rede ({type(exc).__name__}); o efeito é incerto") from None
    if 300 <= response.status_code < 400:
        raise NotSent(f"{service} respondeu redirect (HTTP {response.status_code}); não seguido por segurança")
    return response


def read(client: Any, method: str, path: str, *, service: str, **kwargs: Any) -> Any:
    """Perform a *lookup* request; every failure is a plain ``ToolError``."""
    import httpx

    try:
        response = client.request(method, path, **kwargs)
    except httpx.TimeoutException:
        raise ToolError(f"{service} não respondeu no prazo ao verificar a chamada anterior") from None
    except httpx.HTTPError as exc:
        raise ToolError(f"{service}: falha de rede ao verificar a chamada anterior ({type(exc).__name__})") from None
    if 300 <= response.status_code < 400:
        raise ToolError(f"{service} respondeu redirect (HTTP {response.status_code}); não seguido por segurança")
    return response


def classify_status(status: int, *, service: str) -> None:
    """Raise for a non-success write response (``NotSent``/``Uncertain``)."""
    if status < 400:
        return
    if status in {401, 403}:
        raise NotSent(f"{service} recusou a escrita (HTTP {status}); verifique o escopo da credencial")
    if status == 404:
        raise NotSent(f"{service}: alvo não encontrado ou sem permissão de escrita (HTTP 404)")
    if status == 429:
        raise NotSent(f"{service}: limite de requisições atingido (HTTP 429); nada foi criado")
    if status in {408} or status >= 500:
        raise Uncertain(f"{service} respondeu HTTP {status}; o efeito é incerto")
    raise NotSent(f"{service} rejeitou os dados (HTTP {status})")


def json_body(response: Any, *, service: str, uncertain: bool = True) -> Any:
    try:
        return response.json()
    except ValueError:
        message = f"resposta malformada de {service}"
        if uncertain:
            raise Uncertain(message + "; o efeito é incerto") from None
        raise ToolError(message) from None


# ── --local ─────────────────────────────────────────────────────────────────


def host_is_local(host: str) -> bool:
    from .....model_router import _HOSTS_LOCAIS

    return (host or "").strip().strip("[]").rstrip(".").lower() in _HOSTS_LOCAIS


def ensure_local_allowed(service: str, host: str) -> None:
    """Under ``--local`` only a target on this machine may receive the write."""
    from .....model_router import local_only_active

    if local_only_active() and not host_is_local(host):
        raise ToolError(
            f"modo --local: {service} ({host}) fica fora desta máquina; escrita recusada antes de pedir aprovação"
        )


# ── approval card / preflight helpers ───────────────────────────────────────


def one_line(value: Any, limit: int = 80) -> str:
    text = re.sub(r"[\x00-\x1f\x7f]+", " ", str(value or "")).strip()
    return text[:limit] + ("…" if len(text) > limit else "")


def safe_summary(text: str) -> str:
    from .....privacy import redact_text

    return redact_text(text)[:300]


def refuse_secret_material(args: dict[str, Any]) -> None:
    """Same check the bridge applies before sending, moved before approval:
    the approval card and the approval store never see a secret."""
    from ..bridge import _refuse_secret_material

    _refuse_secret_material(args)


def reject_unknown(args: dict[str, Any], allowed: set[str]) -> None:
    unknown = set(args) - allowed
    if unknown:
        raise ToolError(f"argumentos desconhecidos: {', '.join(sorted(unknown))}")


# ── idempotency ─────────────────────────────────────────────────────────────


def call_scope(router: Any) -> str:
    """Retry scope: the run (or session) of the call; never model-supplied."""
    return str(getattr(router, "_runtime_run_id", None) or getattr(router, "_runtime_session_id", None) or "no-run")


def idempotency_key(router: Any, connector: str, *parts: Any) -> str:
    payload = json.dumps([call_scope(router), connector, *parts], ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]


class WriteLedger:
    """``<policy_root>/agno_capabilities/write_ledger/<connector>/<key>.json``."""

    def __init__(self, policy_root: Path | None, connector: str) -> None:
        from .....paths import get_bauer_home

        base = Path(policy_root) if policy_root is not None else get_bauer_home() / "memory" / "runtime"
        self.root = base / "agno_capabilities" / "write_ledger" / connector

    def _path(self, key: str) -> Path:
        if not _KEY.match(key):
            raise ToolError("chave de idempotência inválida")
        return self.root / f"{key}.json"

    def get(self, key: str) -> dict[str, Any] | None:
        try:
            data = json.loads(self._path(key).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return data if isinstance(data, dict) else None

    def reserve(self, key: str) -> bool:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            return False
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump({"status": "pending", "reserved_at": time.time()}, handle)
        return True

    def complete(self, key: str, result: dict[str, Any]) -> None:
        path = self._path(key)
        with _LOCK:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps({"status": "done", "result": result}, ensure_ascii=False, sort_keys=True),
                           encoding="utf-8")
            tmp.replace(path)

    def release(self, key: str) -> None:
        try:
            self._path(key).unlink()
        except FileNotFoundError:
            return


def execute_once(
    ledger: WriteLedger,
    key: str,
    *,
    service: str,
    perform: Callable[[], dict[str, Any]],
    lookup: Callable[[float], dict[str, Any] | None],
    resend_safe: bool = False,
) -> dict[str, Any]:
    """Run ``perform`` at most once per ``key``.

    ``lookup(reserved_at)`` asks the remote side whether a previous attempt
    landed (idempotency marker). ``resend_safe`` is only true when the remote
    API itself deduplicates (Linear: client-chosen issue id).
    """
    entry = ledger.get(key)
    if entry and entry.get("status") == "done" and isinstance(entry.get("result"), dict):
        return {**entry["result"], "idempotent_replay": True}
    if entry is not None:
        found = lookup(float(entry.get("reserved_at") or 0.0))
        if found:
            ledger.complete(key, found)
            return {**found, "idempotent_replay": True}
        if not resend_safe:
            raise ToolError(
                f"{service}: uma tentativa anterior desta mesma chamada teve efeito incerto e o marcador "
                "não foi encontrado; não reenviada para evitar duplicata — verifique manualmente"
            )
    elif not ledger.reserve(key):
        raise ToolError(f"{service}: chamada idêntica já em andamento; repita a mesma chamada para verificar")
    try:
        result = perform()
    except NotSent as exc:
        ledger.release(key)
        raise ToolError(str(exc)) from None
    except Uncertain as exc:
        raise ToolError(str(exc)) from None
    except ToolError:
        raise
    except Exception as exc:  # noqa: BLE001 - unknown failure: keep the reservation (uncertain)
        raise ToolError(f"{service}: falha inesperada ({type(exc).__name__}); o efeito é incerto") from None
    ledger.complete(key, result)
    return result


def router_of(router_factory: Any) -> Any:
    return router_factory() if router_factory is not None else None
