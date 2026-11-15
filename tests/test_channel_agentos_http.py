from concurrent.futures import ThreadPoolExecutor
from threading import Event

import httpx
import pytest

from bauer.agentos_http_client import AgentOSHttpClient, AgentOSHttpError
from bauer.channel_agentos_http import AgentOSChannelExecutor
from bauer.remote_run_store import RemoteRunStore


def make_executor(tmp_path, handler):
    client = AgentOSHttpClient("http://agentos", "token", transport=httpx.MockTransport(handler))
    return AgentOSChannelExecutor(client, RemoteRunStore(tmp_path / "runs.db"))


def submit(executor, *, run="g1", update="u1", owner="owner"):
    return executor.submit(gateway_run_id=run, channel="telegram", update_id=update, generation=0,
                           owner_id=owner, session_id="session", entity_type="agent",
                           entity_id="a", message="hello")


def test_completed_then_replay_does_not_repeat_post(tmp_path):
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(200, json={"run_id": "r1", "status": "COMPLETED", "content": "ok"})
    executor = make_executor(tmp_path, handler)
    first = submit(executor)
    second = submit(executor, run="new-gateway-id")
    assert first.run.state == "COMPLETED" and first.content == "ok"
    assert second.replay and second.run.gateway_run_id == "g1"
    assert second.content is None and len(calls) == 1


@pytest.mark.parametrize("body", [
    {"run_id": "r", "content": "not evidence"},
    {"status": "COMPLETED", "content": "no remote id"},
    {"run_id": "r", "status": "RUNNING", "content": "partial"},
])
def test_no_false_completion(tmp_path, body):
    executor = make_executor(tmp_path, lambda request: httpx.Response(200, json=body))
    result = submit(executor)
    assert result.run.state in {"UNKNOWN", "RUNNING"}
    assert result.content is None


def test_202_is_not_completion(tmp_path):
    executor = make_executor(tmp_path, lambda request: httpx.Response(
        202, json={"run_id": "r", "status": "COMPLETED", "content": "not yet"}))
    result = submit(executor)
    assert result.run.state == "UNKNOWN" and result.run.remote_run_id == "r" and result.content is None


def test_response_from_another_session_is_not_associated_or_completed(tmp_path):
    executor = make_executor(tmp_path, lambda request: httpx.Response(
        200, json={"run_id": "wrong-run", "session_id": "other-session",
                   "status": "COMPLETED", "content": "wrong user's content"}))
    result = submit(executor)
    assert result.run.state == "UNKNOWN"
    assert result.run.remote_run_id is None and result.content is None


@pytest.mark.parametrize("status", [400, 401, 403, 404, 422, 429, 500])
def test_http_rejection_does_not_prematurely_release_session(tmp_path, status):
    executor = make_executor(tmp_path, lambda request: httpx.Response(status, json={"detail": "error"}))
    with pytest.raises(AgentOSHttpError):
        submit(executor)
    assert executor.store.get("g1", owner_id="owner").state == "UNKNOWN"
    with pytest.raises(ValueError):
        submit(executor, run="g2", update="u2")


def test_timeout_is_uncertain_and_replay_does_not_submit(tmp_path):
    calls = []
    def handler(request):
        calls.append(request)
        raise httpx.ReadTimeout("unknown", request=request)
    executor = make_executor(tmp_path, handler)
    with pytest.raises(AgentOSHttpError):
        submit(executor)
    replay = submit(executor)
    assert replay.replay and replay.run.state == "UNKNOWN" and len(calls) == 1


def test_concurrent_replay_never_submits_twice(tmp_path):
    started, release = Event(), Event()
    calls = []
    def handler(request):
        calls.append(request)
        started.set()
        assert release.wait(5)
        return httpx.Response(200, json={"run_id": "r", "status": "COMPLETED"})
    executor = make_executor(tmp_path, handler)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(submit, executor)
        assert started.wait(5)
        second = pool.submit(submit, executor)
        result = second.result(timeout=5)
        release.set()
        first.result(timeout=5)
    assert result.replay and result.run.state == "SUBMITTING" and len(calls) == 1


def test_reconcile_gets_known_run_and_records_terminal_success(tmp_path):
    calls = []
    def handler(request):
        calls.append(request)
        if request.method == "POST":
            return httpx.Response(202, json={"run_id": "r"})
        return httpx.Response(200, json={"run_id": "r", "session_id": "session",
                                        "status": "COMPLETED", "content": "done"})
    executor = make_executor(tmp_path, handler)
    assert submit(executor).run.state == "UNKNOWN"
    result = executor.reconcile("g1", owner_id="owner")
    assert result.run.state == "COMPLETED" and result.content == "done"
    assert [request.method for request in calls] == ["POST", "GET"]
    assert calls[1].url.path == "/agents/a/runs/r"
    assert calls[1].url.params["session_id"] == "session"
    executor.reconcile("g1", owner_id="owner")
    assert len(calls) == 2


@pytest.mark.parametrize("response", [
    {"run_id": "other", "status": "COMPLETED"},
    {"run_id": "r", "session_id": "other", "status": "COMPLETED"},
    {"run_id": "r", "status": "unknown", "content": "bad"},
    {"run_id": "r", "content": "bad"},
    [],
])
def test_reconcile_rejects_mismatched_or_invalid_response(tmp_path, response):
    def handler(request):
        if request.method == "POST":
            return httpx.Response(202, json={"run_id": "r"})
        return httpx.Response(200, json=response)
    executor = make_executor(tmp_path, handler)
    submit(executor)
    result = executor.reconcile("g1", owner_id="owner")
    assert result.run.state == "UNKNOWN" and result.content is None


def test_reconcile_without_id_never_sends_another_request(tmp_path):
    calls = []
    def handler(request):
        calls.append(request)
        raise httpx.ReadTimeout("unknown", request=request)
    executor = make_executor(tmp_path, handler)
    with pytest.raises(AgentOSHttpError):
        submit(executor)
    with pytest.raises(ValueError, match="without a remote ID"):
        executor.reconcile("g1", owner_id="owner")
    assert len(calls) == 1
def test_stream_records_started_id_and_requires_terminal_event(tmp_path):
    from bauer.channel_agentos_http import AgentOSChannelExecutor
    from bauer.remote_run_store import RemoteRunStore

    store = RemoteRunStore(tmp_path / "runs.sqlite")

    class Client:
        def stream_run(self, *_args):
            yield {"event": "RunStarted", "run_id": "remote-1", "session_id": "s"}
            assert store.get("gateway-1", owner_id="owner").remote_run_id == "remote-1"
            yield {"event": "RunContent", "content": "partial"}

    deltas = []
    result = AgentOSChannelExecutor(Client(), store).submit_stream(
        gateway_run_id="gateway-1", channel="telegram", update_id="42", generation=0,
        owner_id="owner", session_id="s", entity_type="agent", entity_id="a",
        message="hello", on_delta=deltas.append,
    )
    assert result.run.state == "UNKNOWN" and result.run.remote_run_id == "remote-1"
    assert result.content is None and deltas == ["partial"]


def test_stream_never_reports_completion_on_wrong_session(tmp_path):
    from bauer.channel_agentos_http import AgentOSChannelExecutor
    from bauer.remote_run_store import RemoteRunStore

    store = RemoteRunStore(tmp_path / "runs.sqlite")

    class Client:
        def stream_run(self, *_args):
            yield {"event": "RunStarted", "run_id": "remote-1", "session_id": "other"}

    import pytest
    with pytest.raises(ValueError):
        AgentOSChannelExecutor(Client(), store).submit_stream(
            gateway_run_id="gateway-1", channel="telegram", update_id="42", generation=0,
            owner_id="owner", session_id="s", entity_type="agent", entity_id="a", message="hello",
        )
    assert store.get("gateway-1", owner_id="owner").state == "UNKNOWN"


@pytest.mark.parametrize("outcome", ["complete", "uncertain", "limit", "disabled"])
def test_stream_pause_is_persisted_and_continuation_is_bounded(tmp_path, outcome):
    from bauer.channel_agentos_http import AgentOSChannelExecutor
    from bauer.remote_run_store import RemoteRunStore

    store = RemoteRunStore(tmp_path / "runs.sqlite")

    class Client:
        def stream_run(self, *_args):
            yield {"event": "RunStarted", "run_id": "r", "session_id": "s"}
            yield {"event": "RunPaused", "run_id": "r", "session_id": "s"}

    calls = []

    def resume(record):
        assert store.get("g", owner_id="owner").state == record.state == "PAUSED"
        calls.append(record.remote_run_id)
        return {"status": {"complete": "COMPLETED", "uncertain": "RUNNING", "limit": "PAUSED"}[outcome],
                "content": "done"}

    args = dict(gateway_run_id="g", channel="telegram", update_id="42", generation=0,
                owner_id="owner", session_id="s", entity_type="agent", entity_id="a", message="hi",
                on_pause=None if outcome == "disabled" else resume)
    executor = AgentOSChannelExecutor(Client(), store)
    if outcome in {"uncertain", "limit"}:
        with pytest.raises(ValueError):
            executor.submit_stream(**args)
        assert store.get("g", owner_id="owner").state == "UNKNOWN"
    else:
        result = executor.submit_stream(**args)
        assert result.run.state == ("COMPLETED" if outcome == "complete" else "PAUSED")
        assert result.content == ("done" if outcome == "complete" else None)
    assert len(calls) == {"complete": 1, "uncertain": 1, "limit": 16, "disabled": 0}[outcome]
