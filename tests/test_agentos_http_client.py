import httpx
import pytest
from urllib.parse import parse_qs

from bauer.agentos_http_client import (
    AgentOSAuthError,
    AgentOSHttpClient,
    AgentOSHttpError,
    AgentOSNotFoundError,
    AgentOSProtocolError,
    AgentOSRateLimitError,
    AgentOSServerError,
)


def client(handler, **kwargs):
    return AgentOSHttpClient("http://agentos:7777", "private-token", transport=httpx.MockTransport(handler), **kwargs)


def test_run_uses_multipart_and_bearer():
    def handler(request):
        assert request.url.path == "/agents/bauer.dev/runs"
        assert request.headers["authorization"] == "Bearer private-token"
        assert request.headers["content-type"].startswith("multipart/form-data;")
        assert b'name="stream"' in request.content and b"false" in request.content
        return httpx.Response(200, json={"run_id": "r1", "content": "ok"})

    with client(handler) as c:
        assert c.run_agent("bauer.dev", "hello", "s1", "u1")["run_id"] == "r1"


def test_stream_and_followup_use_fresh_bearer_tokens():
    tokens = iter(["stream-token", "requirement-token"])
    requests = []

    def handler(request):
        requests.append(request)
        if request.url.path.endswith("/runs"):
            return httpx.Response(200, headers={"content-type": "text/event-stream"},
                                  content=b'data: {"event":"RunPaused"}\n\n')
        return httpx.Response(200, json={"statement": {}})

    with client(handler, token_factory=lambda: next(tokens)) as c:
        assert list(c.stream_run("agents", "bauer.dev", "read", "s1", "u1"))[0]["event"] == "RunPaused"
        c.tool_requirement(agent_id="bauer.dev", run_id="r1", session_id="s1", challenge="test-challenge")
    assert [request.headers["authorization"] for request in requests] == [
        "Bearer stream-token", "Bearer requirement-token",
    ]


def test_team_and_cancel_use_scoped_paths():
    requests = []
    def handler(request):
        requests.append(request)
        return httpx.Response(200, json={})
    with client(handler) as c:
        c.run_team("team", "hello", "session", "user")
        c.cancel_run("teams", "team", "run", "session")
        c.get_run("teams", "team", "run", "session")
    assert requests[0].url.path == "/teams/team/runs"
    assert requests[1].url.path == "/teams/team/runs/run/cancel"
    assert requests[1].url.params["session_id"] == "session"
    assert requests[2].method == "GET"


@pytest.mark.parametrize("status,kind", [(401, AgentOSAuthError), (403, AgentOSAuthError),
                                        (404, AgentOSNotFoundError), (429, AgentOSRateLimitError),
                                        (500, AgentOSServerError), (422, AgentOSHttpError)])
def test_http_errors_are_typed_and_do_not_expose_body(status, kind):
    with client(lambda r: httpx.Response(status, text="sensitive secret")) as c:
        with pytest.raises(kind) as caught:
            c.run_agent("a", "m", "s", "u")
    assert caught.value.status_code == status
    assert "sensitive" not in str(caught.value)
    assert caught.value.uncertain == (status >= 500)


def test_timeout_does_not_retry_post():
    calls = []
    def handler(request):
        calls.append(request)
        raise httpx.ReadTimeout("possibly accepted", request=request)
    with client(handler) as c:
        with pytest.raises(AgentOSHttpError) as caught:
            c.run_agent("a", "m", "s", "u")
    assert caught.value.uncertain and len(calls) == 1


def test_accepted_run_exposes_202_and_continue_form():
    requests = []
    def handler(request):
        requests.append(request)
        return httpx.Response(202, json={"run_id": "r"})
    with client(handler) as c:
        result = c.continue_run("agents", "a", "r", "session", tools=[{"id": "t", "approved": True}])
    assert result == {"run_id": "r", "_http_status": 202}
    assert requests[0].url.path == "/agents/a/runs/r/continue"
    assert requests[0].headers["content-type"].startswith("application/x-www-form-urlencoded")
    fields = parse_qs(requests[0].content.decode())
    assert fields["session_id"] == ["session"]
    assert '"approved": true' in fields["tools"][0]


def test_team_continue_uses_requirements_and_cancel_accepts_empty():
    requests = []
    def handler(request):
        requests.append(request)
        return httpx.Response(200, json={}) if request.url.path.endswith("continue") else httpx.Response(204)
    with client(handler) as c:
        c.continue_run("teams", "t", "r", "s", tools=[{"id": "x"}])
        assert c.cancel_run("teams", "t", "r", "s") is None
    fields = parse_qs(requests[0].content.decode())
    assert "requirements" in fields and "tools" not in fields


class Chunks(httpx.SyncByteStream):
    def __iter__(self):
        yield b'event: RunContent\ndata: {"content":"ol\xc3'
        yield b'\xa1"}\n\nevent: RunCompleted\ndata: {"run_id":"r"}\n\n'


def test_sse_handles_split_unicode_and_events():
    with client(lambda r: httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=Chunks())) as c:
        assert list(c.stream_run("agents", "a", "m", "s", "u")) == [
            {"event": "RunContent", "content": "olá"}, {"event": "RunCompleted", "run_id": "r"}]


def test_truncated_stream_is_uncertain():
    with client(lambda r: httpx.Response(200, headers={"content-type": "text/event-stream"},
                                        content=b'data: {"run_id":"r"}')) as c:
        with pytest.raises(AgentOSProtocolError) as caught:
            list(c.stream_run("agents", "a", "m", "s", "u"))
    assert caught.value.uncertain


def test_stream_disconnect_preserves_delta_and_never_resubmits():
    calls = []
    class BrokenStream(httpx.SyncByteStream):
        def __iter__(self):
            yield b'data: {"content":"partial"}\n\n'
            raise httpx.ReadError("disconnected")
    def handler(request):
        calls.append(request)
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=BrokenStream())
    with client(handler) as c:
        events = c.stream_run("agents", "a", "m", "s", "u")
        assert next(events) == {"content": "partial"}
        with pytest.raises(AgentOSHttpError) as caught:
            next(events)
    assert caught.value.uncertain and len(calls) == 1


def test_bounded_response_and_invalid_id():
    with client(lambda r: httpx.Response(200, json={"content": "123456"}), max_response_bytes=4) as c:
        with pytest.raises(AgentOSProtocolError) as caught:
            c.run_agent("a", "m", "s", "u")
        assert caught.value.uncertain
        with pytest.raises(ValueError):
            c.run_agent("../x", "m", "s", "u")
