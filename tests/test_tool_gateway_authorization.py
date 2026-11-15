"""Hermetic authorization-only Tool Gateway tests; no network or tool effects."""
from __future__ import annotations

import hashlib
import hmac
import time

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from bauer import projects_registry
from bauer.channel_projects import ChannelProjectStore
from bauer.core.runtime.run_manager import RunManager
from bauer.remote_run_store import RemoteRunStore
from bauer.tool_gateway_authorization import ToolGatewayAuthorizer, ToolGatewayDenied
from bauer.tool_gateway_protocol import call_digest


@pytest.fixture
def env(tmp_path):
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = private.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                        serialization.NoEncryption()).decode()
    public_pem = private.public_key().public_bytes(serialization.Encoding.PEM,
                                                   serialization.PublicFormat.SubjectPublicKeyInfo).decode()
    identity_key = "identity-test-secret-with-over-32-bytes"
    workspace = tmp_path / "projects" / "project"
    workspace.mkdir(parents=True)
    registry_path = tmp_path / "projects.json"
    project = projects_registry.add_project(workspace, registry_path=registry_path)
    bindings = ChannelProjectStore(tmp_path / "bindings.db")
    bindings.bind("telegram:123:123", project["id"], workspace)
    runs = RunManager(root=tmp_path / "runtime")
    run = runs.create_run(session_id="gateway-session", agent_id="channel.telegram",
                          runtime_adapter="agentos_http", status="running", input={
                              "endpoint": "canal:telegram", "operator": "telegram:123:123",
                              "project_id": project["id"], "workspace": str(workspace),
                          })
    subject = "tg-" + hmac.new(identity_key.encode(), b"bauer-agentos-telegram-subject-v1\0" + b"123",
                               hashlib.sha256).hexdigest()
    remote = RemoteRunStore(tmp_path / "remote.db")
    remote.claim(gateway_run_id=run.id, channel="telegram", update_id="42", generation=0,
                 owner_id=subject, session_id="remote-session", entity_type="agent", entity_id="bauer.dev")
    remote.transition(run.id, owner_id=subject, state="SUBMITTING")
    remote.transition(run.id, owner_id=subject, state="RUNNING", remote_run_id="remote-run")
    auth = ToolGatewayAuthorizer(public_key=public_pem, identity_key=identity_key, issuer="gateway",
                                 audience="tool-gateway", runs=runs, remote_runs=remote,
                                 bindings=bindings, registry_path=registry_path)
    now = int(time.time())
    claims = {"sub": subject, "iss": "gateway", "aud": "tool-gateway", "iat": now, "exp": now + 30,
              "jti": "token-1", "scopes": ["tool_gateway:invoke"], "channel": "telegram",
              "gateway_run_id": run.id, "gateway_session_id": run.session_id, "project_id": project["id"],
              "remote_run_id": "remote-run", "remote_session_id": "remote-session", "agent_id": "bauer.dev",
              "tool_call_id": "call-1", "call_digest": call_digest("read_file", {"path": "example.txt"})}

    def token(changes=None, *, signing_key=private_pem, algorithm="RS256"):
        return "Bearer " + jwt.encode({**claims, **(changes or {})}, signing_key, algorithm=algorithm)

    return auth, token, claims, runs, run, remote, bindings, workspace, registry_path


def test_valid_context_is_derived_only_from_authoritative_stores(env):
    auth, token, claims, _, run, _, _, workspace, _ = env
    context = auth.authorize(token())
    assert context.workspace == workspace.resolve()
    assert context.gateway_run_id == run.id and context.owner_subject == claims["sub"]
    assert context.project_id == projects_registry.project_id(workspace)


def test_paused_owned_run_still_authorized_but_terminal_denied(env):
    auth, token, claims, _, run, remote, *_ = env
    remote.transition(run.id, owner_id=claims["sub"], state="PAUSED")
    assert auth.authorize(token()).gateway_run_id == run.id
    remote.transition(run.id, owner_id=claims["sub"], state="CANCELLED")
    with pytest.raises(ToolGatewayDenied):
        auth.authorize(token())


@pytest.mark.parametrize("change", [
    {"sub": "tg-attacker"}, {"channel": "discord"}, {"gateway_run_id": "missing"},
    {"gateway_session_id": "other"}, {"remote_run_id": "other"}, {"remote_session_id": "other"},
    {"project_id": "other"}, {"agent_id": "bauer.qa"}, {"scopes": ["tool_gateway:admin"]},
    {"call_digest": "bad"}, {"tool_call_id": ""},
    {"aud": "agentos"}, {"iss": "attacker"}, {"workspace": "/tmp/forged"},
    {"tool_args": {"path": "/tmp/forged"}}, {"exp": 0},
])
def test_spoofed_claims_are_denied(env, change):
    auth, token, *_ = env
    with pytest.raises(ToolGatewayDenied):
        auth.authorize(token(change))


def test_forged_key_replay_lifetime_and_malformed_bearer_denied(env):
    auth, token, claims, *_ = env
    other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    other_pem = other.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                    serialization.NoEncryption()).decode()
    for bearer in (token(signing_key=other_pem), token({"exp": claims["iat"] + 61}), "", "Bearer x y"):
        with pytest.raises(ToolGatewayDenied):
            auth.authorize(bearer)


def test_changed_binding_removed_project_or_terminal_run_are_denied(env):
    auth, token, claims, runs, run, _, bindings, workspace, registry = env
    bindings.bind("telegram:123:123", claims["project_id"], workspace / "other")
    with pytest.raises(ToolGatewayDenied):
        auth.authorize(token())
    bindings.bind("telegram:123:123", claims["project_id"], workspace)
    projects_registry.remove_project(claims["project_id"], registry_path=registry)
    with pytest.raises(ToolGatewayDenied):
        auth.authorize(token())
    projects_registry.add_project(workspace, registry_path=registry)
    runs.complete_run(run.id, output={})
    with pytest.raises(ToolGatewayDenied):
        auth.authorize(token())


def test_wrong_project_run_workspace_and_missing_remote_are_denied(env, tmp_path):
    auth, token, claims, runs, run, remote, _, _, _ = env
    runs.update_run(run.id, input={**run.input, "workspace": str(tmp_path)})
    with pytest.raises(ToolGatewayDenied):
        auth.authorize(token())
    runs.update_run(run.id, input=run.input)
    remote.transition(run.id, owner_id=claims["sub"], state="UNKNOWN")
    with pytest.raises(ToolGatewayDenied):
        auth.authorize(token())
