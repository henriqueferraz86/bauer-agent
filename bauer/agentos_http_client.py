"""HTTP transport for AgentOS. This module does not depend on the Agno SDK.

POSTs are deliberately never retried: a timeout or disconnect can occur after
the remote service accepted a run. The caller must persist and reconcile that
uncertain outcome rather than submit the request again.
"""
from __future__ import annotations

import codecs
import json
from collections.abc import Callable, Iterator, Mapping
from typing import Any
from urllib.parse import quote, urlsplit

import httpx

from .http_shared import shared_ssl_context


class AgentOSHttpError(Exception):
    """A sanitized transport, protocol, or HTTP error."""

    def __init__(self, message: str, *, status_code: int | None = None, uncertain: bool = False):
        super().__init__(message)
        self.status_code = status_code
        self.uncertain = uncertain


class AgentOSAuthError(AgentOSHttpError):
    pass


class AgentOSNotFoundError(AgentOSHttpError):
    pass


class AgentOSRateLimitError(AgentOSHttpError):
    pass


class AgentOSServerError(AgentOSHttpError):
    pass


class AgentOSProtocolError(AgentOSHttpError):
    pass


def _entity_path(entity_type: str, entity_id: str) -> str:
    if entity_type not in {"agents", "teams"}:
        raise ValueError("entity_type must be agents or teams")
    if not entity_id or "/" in entity_id or "\\" in entity_id or entity_id in {".", ".."}:
        raise ValueError("invalid entity ID")
    return f"/{entity_type}/{quote(entity_id, safe='')}"


def _run_path(entity_type: str, entity_id: str, run_id: str) -> str:
    if not run_id or "/" in run_id or "\\" in run_id or run_id in {".", ".."}:
        raise ValueError("invalid run ID")
    return f"{_entity_path(entity_type, entity_id)}/runs/{quote(run_id, safe='')}"


def _multipart(fields: Mapping[str, Any]) -> dict[str, tuple[None, str]]:
    result: dict[str, tuple[None, str]] = {}
    for name, value in fields.items():
        if value is None:
            continue
        if isinstance(value, bool):
            value = str(value).lower()
        elif isinstance(value, (dict, list)):
            value = json.dumps(value, ensure_ascii=False)
        result[name] = (None, str(value))
    return result


class AgentOSHttpClient:
    """Synchronous bounded HTTP client. No credential or response body is logged."""

    def __init__(self, base_url: str, token: str, *, connect_timeout_s: float = 5,
                 read_timeout_s: float = 180, max_response_bytes: int = 4 * 1024 * 1024,
                 transport: httpx.BaseTransport | None = None, trust_env: bool = True,
                 token_factory: Callable[[], str] | None = None):
        parsed = urlsplit(base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.username or parsed.password:
            raise ValueError("invalid AgentOS base URL")
        if parsed.query or parsed.fragment or not token.strip() or max_response_bytes <= 0:
            raise ValueError("invalid AgentOS client configuration")
        self.max_response_bytes = max_response_bytes
        self._token_factory = token_factory
        self._client = httpx.Client(
            base_url=base_url.rstrip("/") + "/", headers={"Authorization": f"Bearer {token}"},
            timeout=httpx.Timeout(read_timeout_s, connect=connect_timeout_s),
            verify=shared_ssl_context(), transport=transport, follow_redirects=False, trust_env=trust_env,
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> AgentOSHttpClient:
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()

    def _request_headers(self, headers: Mapping[str, str] | None = None) -> Mapping[str, str] | None:
        if self._token_factory is None:
            return headers
        return {**(headers or {}), "Authorization": f"Bearer {self._token_factory()}"}

    @staticmethod
    def _check(response: httpx.Response) -> None:
        status = response.status_code
        if 200 <= status < 300:
            return
        error = (AgentOSAuthError if status in (401, 403) else
                 AgentOSNotFoundError if status == 404 else
                 AgentOSRateLimitError if status == 429 else
                 AgentOSServerError if status >= 500 else AgentOSHttpError)
        raise error(f"AgentOS returned HTTP {status}", status_code=status,
                    uncertain=status >= 500)

    def _read(self, response: httpx.Response, *, allow_empty: bool = False) -> Any:
        chunks: list[bytes] = []
        total = 0
        for chunk in response.iter_bytes():
            total += len(chunk)
            if total > self.max_response_bytes:
                raise AgentOSProtocolError("AgentOS response exceeds size limit")
            chunks.append(chunk)
        raw = b"".join(chunks)
        if not raw and (allow_empty or response.status_code == 204):
            return None
        try:
            return json.loads(raw)
        except (ValueError, UnicodeDecodeError) as exc:
            raise AgentOSProtocolError("AgentOS returned invalid JSON") from exc

    def _request(self, method: str, path: str, *, params: Mapping[str, str] | None = None,
                 fields: Mapping[str, Any] | None = None, urlencoded: bool = False,
                 allow_empty: bool = False, headers: Mapping[str, str] | None = None,
                 json_body: Mapping[str, Any] | None = None) -> Any:
        try:
            if fields is not None and json_body is not None:
                raise ValueError("only one request body is allowed")
            values = _multipart(fields) if fields is not None else None
            data = {key: value[1] for key, value in values.items()} if values and urlencoded else None
            with self._client.stream(method, path, params=params, headers=self._request_headers(headers),
                                     data=data, files=values if not urlencoded else None,
                                     json=json_body) as response:
                self._check(response)
                result = self._read(response, allow_empty=allow_empty)
                if response.status_code == 202:
                    if not isinstance(result, dict):
                        raise AgentOSProtocolError("AgentOS acceptance response is not an object")
                    result = {**result, "_http_status": 202}
                return result
        except AgentOSProtocolError as exc:
            if method != "GET":
                exc.uncertain = True
            raise
        except httpx.RequestError as exc:
            raise AgentOSHttpError("AgentOS transport error", uncertain=method != "GET") from exc

    def health(self) -> Any:
        return self._request("GET", "/health")

    def channel_capabilities(self, challenge: str) -> Any:
        return self._request("GET", "/bauer/channel-capabilities", headers={"X-Bauer-Challenge": challenge})

    def tool_requirement(self, *, agent_id: str, run_id: str, session_id: str, challenge: str) -> Any:
        return self._request("POST", "/bauer/tool-requirement", json_body={"agent_id": agent_id,
                             "run_id": run_id, "session_id": session_id, "challenge": challenge})

    def tool_continue(self, *, agent_id: str, run_id: str, session_id: str,
                      tool_call_id: str, call_digest: str, result: str, proof: str) -> Any:
        return self._request("POST", "/bauer/tool-continue", json_body={"agent_id": agent_id,
                             "run_id": run_id, "session_id": session_id, "tool_call_id": tool_call_id,
                             "call_digest": call_digest, "result": result, "proof": proof})

    def discover(self) -> dict[str, Any]:
        return {name: self._request("GET", f"/{name}") for name in ("info", "config", "agents", "teams")}

    def run_agent(self, agent_id: str, message: str, session_id: str, user_id: str) -> Any:
        return self._run("agents", agent_id, message, session_id, user_id)

    def run_team(self, team_id: str, message: str, session_id: str, user_id: str) -> Any:
        return self._run("teams", team_id, message, session_id, user_id)

    def _run(self, entity_type: str, entity_id: str, message: str, session_id: str, user_id: str) -> Any:
        return self._request("POST", f"{_entity_path(entity_type, entity_id)}/runs",
                             fields={"message": message, "session_id": session_id,
                                     "user_id": user_id, "stream": False})

    def get_run(self, entity_type: str, entity_id: str, run_id: str, session_id: str) -> Any:
        return self._request("GET", _run_path(entity_type, entity_id, run_id),
                             params={"session_id": session_id})

    def cancel_run(self, entity_type: str, entity_id: str, run_id: str, session_id: str) -> Any:
        return self._request("POST", _run_path(entity_type, entity_id, run_id) + "/cancel",
                             params={"session_id": session_id}, allow_empty=True)

    def continue_run(self, entity_type: str, entity_id: str, run_id: str, session_id: str,
                     *, tools: list[dict[str, Any]]) -> Any:
        return self._request("POST", _run_path(entity_type, entity_id, run_id) + "/continue",
                             fields={"session_id": session_id,
                                     "requirements" if entity_type == "teams" else "tools": tools,
                                     "stream": False}, urlencoded=True)

    def stream_run(self, entity_type: str, entity_id: str, message: str, session_id: str,
                   user_id: str) -> Iterator[dict[str, Any]]:
        """Yield decoded SSE events; EOF is not proof of terminal success."""
        fields = {"message": message, "session_id": session_id, "user_id": user_id, "stream": True}
        try:
            with self._client.stream("POST", f"{_entity_path(entity_type, entity_id)}/runs",
                                     files=_multipart(fields), headers=self._request_headers()) as response:
                self._check(response)
                if "text/event-stream" not in response.headers.get("content-type", "").lower():
                    raise AgentOSProtocolError("AgentOS did not return an SSE stream", uncertain=True)
                yield from self._events(response)
        except httpx.RequestError as exc:
            raise AgentOSHttpError("AgentOS transport error", uncertain=True) from exc

    def _events(self, response: httpx.Response) -> Iterator[dict[str, Any]]:
        decoder = codecs.getincrementaldecoder("utf-8")("strict")
        buffer = ""
        data: list[str] = []
        event = ""
        size = 0
        try:
            for chunk in response.iter_bytes():
                size += len(chunk)
                if size > self.max_response_bytes:
                    raise AgentOSProtocolError("AgentOS stream exceeds size limit", uncertain=True)
                buffer += decoder.decode(chunk)
                while "\n" in buffer:
                    line, buffer = buffer.split("\n", 1)
                    line = line.rstrip("\r")
                    if not line:
                        if data:
                            payload = json.loads("\n".join(data))
                            if not isinstance(payload, dict):
                                raise ValueError("SSE payload must be an object")
                            if event and "event" not in payload:
                                payload["event"] = event
                            yield payload
                        data, event = [], ""
                    elif line.startswith("data:"):
                        data.append(line[5:].removeprefix(" "))
                    elif line.startswith("event:"):
                        event = line[6:].removeprefix(" ")
            buffer += decoder.decode(b"", final=True)
            if buffer or data or event:
                raise AgentOSProtocolError("AgentOS stream ended mid-event", uncertain=True)
        except (UnicodeDecodeError, ValueError) as exc:
            raise AgentOSProtocolError("AgentOS returned invalid SSE", uncertain=True) from exc
