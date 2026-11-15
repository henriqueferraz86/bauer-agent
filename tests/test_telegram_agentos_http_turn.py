from types import SimpleNamespace

import jwt
import pytest

from bauer.agentos_capabilities import capability_proof
from bauer.telegram_agentos_http import TelegramAgentOSError, execute_http_turn
from bauer.tool_gateway_protocol import TOOL_CONTINUATION_VERSION


def _execution(user="42", chat="42", project="p1", update="123"):
    return SimpleNamespace(channel="telegram", private_chat=True, update_id=update, inbox_persisted=True,
                           owner=f"telegram:{chat}:{user}", chat_id=chat, project_id=project)


@pytest.fixture
def setup(monkeypatch, tmp_path):
    import bauer.telegram_agentos_http as module

    key = "k" * 40
    monkeypatch.setenv("TEST_AGENTOS_KEY", key)
    calls = []
    client_options = []

    class Client:
        def __init__(self, _url, token, **kwargs):
            self.claims = jwt.decode(token, options={"verify_signature": False})
            self.trust_env = kwargs.get("trust_env", True)
            client_options.append(kwargs)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def channel_capabilities(self, challenge):
            caps = {"contract_version": 1, "mode": "read_only", "user_isolation": True,
                    "catalog_tools": False, "continuation": False, "cancel_requires_terminal": True,
                    "identity": {"subject": self.claims["sub"], "scopes": self.claims["scopes"]}}
            return {"capabilities": caps, "proof": capability_proof(key, challenge, caps)}

        def stream_run(self, _entity, agent, message, session, owner):
            calls.append((agent, message, session, owner))
            yield {"event": "RunStarted", "run_id": f"r{len(calls)}", "session_id": session}
            yield {"event": "RunCompleted", "run_id": f"r{len(calls)}", "session_id": session, "content": "ok"}

    monkeypatch.setattr(module, "AgentOSHttpClient", Client)
    settings = {"enabled": True, "experimental_read_only": True, "agent_id": "bauer.dev",
                "base_url": "http://127.0.0.1:7777", "jwt_issuer": "gateway",
                "jwt_audience": "agentos", "jwt_key_env": "TEST_AGENTOS_KEY"}
    return settings, calls, tmp_path, Client, client_options


def test_governed_turn_dedup_and_isolated_sessions(setup):
    settings, calls, state, _, client_options = setup
    assert execute_http_turn(settings=settings, execution=_execution(), gateway_run_id="g1",
                             text="olá", state_dir=state).startswith("ok")
    with pytest.raises(TelegramAgentOSError):
        execute_http_turn(settings=settings, execution=_execution(), gateway_run_id="g2",
                          text="olá", state_dir=state)
    assert len(calls) == 1
    assert execute_http_turn(settings=settings, execution=_execution(user="43", chat="43", update="124"),
                             gateway_run_id="g3", text="olá", state_dir=state).startswith("ok")
    assert calls[0][2] != calls[1][2]
    assert client_options and all(options["trust_env"] is False for options in client_options)


def test_untrusted_capabilities_never_submit(setup, monkeypatch):
    import bauer.telegram_agentos_http as module

    settings, calls, state, client, _ = setup
    monkeypatch.setattr(client, "channel_capabilities", lambda self, challenge: {
        "capabilities": {"contract_version": 1, "mode": "read_only"}, "proof": "forged",
    })
    with pytest.raises(TelegramAgentOSError):
        execute_http_turn(settings=settings, execution=_execution(), gateway_run_id="g1",
                          text="olá", state_dir=state)
    assert not calls


def test_non_loopback_http_is_rejected_even_without_external_tools(setup):
    settings, calls, state, _, client_options = setup
    settings["base_url"] = "http://agentos:7777"
    with pytest.raises(TelegramAgentOSError, match="HTTPS verificado ou endereço loopback"):
        execute_http_turn(settings=settings, execution=_execution(), gateway_run_id="g1",
                          text="olá", state_dir=state)
    assert not calls and not client_options


def test_requires_private_telegram_and_update_id(setup):
    settings, calls, state, _, _ = setup
    execution = _execution(update=None)
    with pytest.raises(TelegramAgentOSError):
        execute_http_turn(settings=settings, execution=execution, gateway_run_id="g1",
                          text="olá", state_dir=state)
    assert not calls


@pytest.mark.parametrize("continuation_version", [None, 1, TOOL_CONTINUATION_VERSION])
def test_external_tool_turn_keeps_kernel_control_and_closes_client(setup, monkeypatch, continuation_version):
    import bauer.tool_gateway_bridge as module

    settings, _, state, client, _ = setup
    key = "z" * 40
    monkeypatch.setenv("BAUER_AGENTOS_JWT_KEY", key)
    settings.update(external_read_tools=True, jwt_key_env="BAUER_AGENTOS_JWT_KEY", base_url="http://127.0.0.1:7777")

    def capabilities(self, challenge):
        assert not self.trust_env
        caps = {"contract_version": 1, "mode": "read_only", "user_isolation": True,
                "catalog_tools": False, "continuation": True, "cancel_requires_terminal": True,
                "external_read_tools": True,
                "identity": {"subject": self.claims["sub"], "scopes": self.claims["scopes"]}}
        if continuation_version is not None:
            caps["tool_continuation_version"] = continuation_version
        return {"capabilities": caps, "proof": capability_proof(key, challenge, caps)}

    def stream(self, _kind, _agent, _message, session, _owner):
        events.append("submitted")
        yield {"event": "RunStarted", "run_id": "r", "session_id": session}
        yield {"event": "RunPaused", "run_id": "r", "session_id": session}

    monkeypatch.setattr(client, "channel_capabilities", capabilities)
    monkeypatch.setattr(client, "stream_run", stream)
    events = []

    class Bridge:
        def invoke(self, capability, action, args):
            events.append("invoke")
            return "read"

        def continue_paused(self, **kwargs):
            assert kwargs["authenticated_user_id"] == "42"
            assert self.invoke("token", "read_file", {}) == "read"
            return {"status": "COMPLETED", "content": "done"}

    http = SimpleNamespace(close=lambda: events.append("closed"))
    monkeypatch.setattr(module, "build_telegram_tool_bridge", lambda **kwargs: (Bridge(), http))
    execution = _execution()
    execution.control = SimpleNamespace(before_tool=lambda: events.append("budget"),
                                        checkpoint=lambda: events.append("checkpoint"))
    if continuation_version != TOOL_CONTINUATION_VERSION:
        with pytest.raises(TelegramAgentOSError, match="não comprovou o perfil"):
            execute_http_turn(settings=settings, execution=execution, gateway_run_id="g1",
                              text="read", state_dir=state, kernel=object())
        assert not events
        return
    result = execute_http_turn(settings=settings, execution=execution, gateway_run_id="g1",
                               text="read", state_dir=state, kernel=object())
    assert result == "done"
    assert events == ["submitted", "checkpoint", "budget", "invoke", "checkpoint", "closed", "checkpoint"]


def test_cancel_after_gateway_read_blocks_agentos_continuation(setup, monkeypatch):
    import bauer.telegram_agentos_http as module
    from bauer.provider_retry import ProviderRetryInterrupted

    settings, _, state, client, _ = setup
    monkeypatch.setenv("BAUER_AGENTOS_JWT_KEY", "z" * 40)
    settings.update(external_read_tools=True, jwt_key_env="BAUER_AGENTOS_JWT_KEY",
                    base_url="http://127.0.0.1:7787")
    events = []

    def capabilities(self, challenge):
        caps = {"contract_version": 1, "mode": "read_only", "user_isolation": True,
                "catalog_tools": False, "continuation": True, "cancel_requires_terminal": True,
                "external_read_tools": True, "tool_continuation_version": TOOL_CONTINUATION_VERSION,
                "identity": {"subject": self.claims["sub"], "scopes": self.claims["scopes"]}}
        return {"capabilities": caps, "proof": capability_proof("z" * 40, challenge, caps)}

    def stream(self, _kind, _agent, _message, session, _owner):
        events.append("submitted")
        yield {"event": "RunStarted", "run_id": "cancel-run", "session_id": session}
        yield {"event": "RunPaused", "run_id": "cancel-run", "session_id": session}

    monkeypatch.setattr(client, "channel_capabilities", capabilities)
    monkeypatch.setattr(client, "stream_run", stream)
    canceled = {"value": False}

    class Bridge:
        def invoke(self, _capability, _action, _args):
            events.append("read")
            canceled["value"] = True
            return "marker"

        def continue_paused(self, **_kwargs):
            self.invoke("capability", "read_file", {})
            events.append("continue")
            return {"status": "COMPLETED", "content": "unexpected"}

    gateway = SimpleNamespace(close=lambda: events.append("closed"))
    import bauer.tool_gateway_bridge as bridge_module
    monkeypatch.setattr(bridge_module, "build_telegram_tool_bridge", lambda **_kwargs: (Bridge(), gateway))
    cancel_calls = []
    monkeypatch.setattr(client, "cancel_run", lambda *args: (cancel_calls.append(args) or {"status": "accepted"}), raising=False)
    control = SimpleNamespace(
        before_tool=lambda: events.append("before_tool"),
        checkpoint=lambda: (_ for _ in ()).throw(ProviderRetryInterrupted("cancelled"))
        if canceled["value"] else events.append("checkpoint"),
    )
    execution = _execution()
    execution.control = control

    with pytest.raises(ProviderRetryInterrupted):
        execute_http_turn(settings=settings, execution=execution, gateway_run_id="gateway-run",
                          text="read", state_dir=state, kernel=object())

    assert "read" in events and "continue" not in events
    assert cancel_calls
    assert events[-1] == "closed"


def test_telegram_http_toggle_does_not_route_discord(tmp_path):
    from bauer.channel_base import AgentBackend

    backend = AgentBackend(tmp_path / "missing.yaml", tmp_path / "sessions")
    backend._router = SimpleNamespace(workspace=tmp_path)
    backend._resolver_kernel = lambda *args, **kwargs: None
    backend._configure_channel_router = lambda *args: None
    backend._resolve_telegram_runtime_adapter = lambda: "agentos_http"
    backend._turno_cru = lambda *args, **kwargs: "native-discord"
    execution = SimpleNamespace(channel="discord", owner="discord:1:2", control=None, phase="")
    assert backend._execute_turn(None, "dc:1", None, "model", "hello", execution=execution) == "native-discord"


def test_cancel_only_confirms_terminal_state(setup, monkeypatch):
    import bauer.telegram_agentos_http as module
    from bauer.agentos_jwt_issuer import AgentOSJWTIssuer
    from bauer.remote_run_store import RemoteRunStore
    from bauer.remote_session_store import RemoteSessionStore

    settings, _, state, client, _ = setup
    config = state / "config.yaml"
    config.write_text("model:\n  provider: ollama\n  name: fake\nruntime:\n  telegram_default_adapter: agentos_http\n  adapters:\n"
                      "    agentos_http:\n      enabled: true\n      experimental_read_only: true\n"
                      "      agent_id: bauer.dev\n      base_url: http://127.0.0.1:7777\n"
                      "      jwt_issuer: gateway\n      jwt_audience: agentos\n"
                      "      jwt_key_env: TEST_AGENTOS_KEY\n", encoding="utf-8")
    issuer = AgentOSJWTIssuer(key="k" * 40, issuer="gateway", audience="agentos",
                              allowed_scopes=["agents:bauer.dev:run"])
    owner = jwt.decode(issuer.issue(authenticated_user_id="42", scopes=["agents:bauer.dev:run"]),
                       options={"verify_signature": False})["sub"]
    path = state / "telegram-agentos.sqlite3"
    session = RemoteSessionStore(path).current(channel="telegram", owner_id=owner, chat_id="42")
    store = RemoteRunStore(path, require_managed_session=True)
    store.claim(gateway_run_id="g1", channel="telegram", update_id="1", generation=0,
                owner_id=owner, session_id=session.session_id, entity_type="agent", entity_id="bauer.dev")
    store.transition("g1", owner_id=owner, state="SUBMITTING")
    store.transition("g1", owner_id=owner, state="RUNNING", remote_run_id="r1")
    state_remote = {"status": "RUNNING"}
    cancel_calls = []
    stop_ready = {"value": False}
    monkeypatch.setattr(client, "cancel_run", lambda *args: (cancel_calls.append(args) or {"status": "accepted"}), raising=False)
    monkeypatch.setattr(client, "get_run", lambda *args: {
        "run_id": "r1", "session_id": session.session_id, "status": state_remote["status"]
    }, raising=False)
    from bauer.core.runtime.state_store import SqliteStateStore
    barrier = SimpleNamespace(request_stop=lambda **kwargs: stop_ready["value"])
    monkeypatch.setattr(SqliteStateStore, "tool_gateway_barrier", lambda _self: barrier)
    msg = SimpleNamespace(user_id="42", chat_id="42", project_snapshot=None)
    response = module.cancel_remote_session(config, state, msg)
    assert "aguardando a ferramenta" in response
    assert len(cancel_calls) == 0  # never cancel AgentOS while a tool effect is still in flight
    stop_ready["value"] = True
    response = module.cancel_remote_session(config, state, msg)
    assert len(cancel_calls) == 1
    assert "solicitado" in response
    assert store.get("g1", owner_id=owner).state == "RUNNING"
    state_remote["status"] = "CANCELLED"
    assert "confirmado" in module.cancel_remote_session(config, state, msg)
    assert len(cancel_calls) == 2
    assert store.get("g1", owner_id=owner).state == "CANCELLED"


def test_recovery_never_bypasses_kernel_gates(setup):
    import bauer.telegram_agentos_http as module
    from bauer.agentos_jwt_issuer import AgentOSJWTIssuer
    from bauer.remote_run_store import RemoteRunStore
    from bauer.remote_session_store import RemoteSessionStore

    _, _, state, _, _ = setup
    config = state / "config.yaml"
    config.write_text("model:\n  provider: ollama\n  name: fake\nruntime:\n"
                      "  telegram_default_adapter: agentos_http\n  adapters:\n    agentos_http:\n"
                      "      enabled: true\n      experimental_read_only: true\n      agent_id: bauer.dev\n"
                      "      base_url: http://127.0.0.1:7777\n      jwt_issuer: gateway\n"
                      "      jwt_audience: agentos\n      jwt_key_env: TEST_AGENTOS_KEY\n", encoding="utf-8")
    signer = AgentOSJWTIssuer(key="k" * 40, issuer="gateway", audience="agentos",
                              allowed_scopes=["agents:bauer.dev:run"])
    owner = jwt.decode(signer.issue(authenticated_user_id="42", scopes=["agents:bauer.dev:run"]),
                       options={"verify_signature": False})["sub"]
    path = state / "telegram-agentos.sqlite3"
    session = RemoteSessionStore(path).current(channel="telegram", owner_id=owner, chat_id="42")
    store = RemoteRunStore(path, require_managed_session=True)
    store.claim(gateway_run_id="g1", channel="telegram", update_id="1", generation=0,
                owner_id=owner, session_id=session.session_id, entity_type="agent", entity_id="bauer.dev")
    store.transition("g1", owner_id=owner, state="SUBMITTING")
    store.transition("g1", owner_id=owner, state="COMPLETED", remote_run_id="r1")
    run = SimpleNamespace(status="failed", agent_id="channel.telegram", runtime_adapter="agentos_http",
                          session_id="tg:42", input={"operator": "telegram:42:42", "project_id": None,
                                                      "workspace": str(state)}, output={"output": "raw"})
    kernel = SimpleNamespace(runs=SimpleNamespace(get_run=lambda _: run))
    msg = SimpleNamespace(user_id="42", chat_id="42", channel="telegram", project_snapshot=None,
                          raw={"update_id": 1})
    assert module.recover_remote_update(config, state, msg, kernel=kernel, session_key="tg:42", workspace=state) is None
    run.status = "completed"
    assert module.recover_remote_update(config, state, msg, kernel=kernel, session_key="tg:42", workspace=state) == "raw"

    store.claim(gateway_run_id="g2", channel="telegram", update_id="2", generation=0,
                owner_id=owner, session_id=session.session_id, entity_type="agent", entity_id="bauer.dev")
    store.transition("g2", owner_id=owner, state="SUBMITTING")
    store.transition("g2", owner_id=owner, state="CANCELLED", remote_run_id="r2")
    run.status = "cancelled"
    msg.raw["update_id"] = 2
    assert "cancelada" in module.recover_remote_update(
        config, state, msg, kernel=kernel, session_key="tg:42", workspace=state,
    )

    store.claim(gateway_run_id="g3", channel="telegram", update_id="3", generation=0,
                owner_id=owner, session_id=session.session_id, entity_type="agent", entity_id="bauer.dev")
    store.transition("g3", owner_id=owner, state="SUBMITTING")
    store.transition("g3", owner_id=owner, state="ERROR", remote_run_id="r3")
    run.status = "failed"
    msg.raw["update_id"] = 3
    assert "terminou com erro" in module.recover_remote_update(
        config, state, msg, kernel=kernel, session_key="tg:42", workspace=state,
    )
