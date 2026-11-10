"""Plano 067 / sprint 74 — guards DevOps/GitHub com as bibliotecas REAIS.

Só roda com o extra ``agno-devops`` (``uv sync --frozen --extra dev --extra
agno-devops``). Fora dele, pula; com ``BAUER_REQUIRE_AGNO_EXTRAS=1`` (job
``install-check``) a ausência vira falha.

Herméticos: qualquer ``socket.connect`` falha o teste. AWS usa clientes boto3
reais com ``botocore.stub.Stubber``; Kubernetes usa o cliente real montado a
partir de um kubeconfig temporário, com ``urllib3.PoolManager.request``
falso; PyGithub usa ``requests`` com ``HTTPAdapter.send`` falso.
"""

from __future__ import annotations

import importlib.util
import json
import os
import socket
import textwrap
from pathlib import Path

import pytest

_MODULES = ("agno", "boto3", "botocore", "kubernetes", "github")
_MISSING = [name for name in _MODULES if importlib.util.find_spec(name) is None]
if _MISSING:
    if os.environ.get("BAUER_REQUIRE_AGNO_EXTRAS") == "1":
        raise RuntimeError(f"extra agno-devops incompleto: {', '.join(_MISSING)}")
    pytest.skip(f"extra agno-devops não instalado ({', '.join(_MISSING)})", allow_module_level=True)

import boto3  # noqa: E402
import requests  # noqa: E402
import urllib3  # noqa: E402
from botocore import xform_name  # noqa: E402
from botocore.stub import Stubber  # noqa: E402

from bauer.core.runtime.agno_capabilities.catalog import CapabilityError, catalog_entries, resolve_capability  # noqa: E402
from bauer.core.runtime.agno_capabilities.families import devops  # noqa: E402
from bauer.tools.base import ToolError  # noqa: E402

ACCOUNT = "123456789012"


@pytest.fixture(autouse=True)
def no_network(monkeypatch, tmp_path):
    def refuse(*args, **kwargs):
        raise AssertionError("conexão de rede real em teste hermético")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket.socket, "connect_ex", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    # boto3 never reads the developer's real AWS files or IMDS
    monkeypatch.setenv("AWS_CONFIG_FILE", str(tmp_path / "aws-config"))
    monkeypatch.setenv("AWS_SHARED_CREDENTIALS_FILE", str(tmp_path / "aws-credentials"))
    monkeypatch.setenv("AWS_EC2_METADATA_DISABLED", "true")
    monkeypatch.delenv("AWS_PROFILE", raising=False)
    monkeypatch.setenv("BAUER_AGNO_CAPABILITIES", str(tmp_path / "no-settings.yaml"))


def _settings(tmp_path: Path, monkeypatch, body: str) -> None:
    target = tmp_path / "agno_capabilities.yaml"
    target.write_text(textwrap.dedent(body), encoding="utf-8")
    monkeypatch.setenv("BAUER_AGNO_CAPABILITIES", str(target))


def _tools(tmp_path: Path, ref: str) -> dict:
    from bauer.core.runtime.adapters.agno_adapter import AgnoRuntimeAdapter

    workspace = tmp_path / "workspace"
    workspace.mkdir(exist_ok=True)
    adapter = AgnoRuntimeAdapter(adapter_config={"workspace": str(workspace)}, runtime_policy_root=tmp_path / "rt")
    return {fn.name: fn for fn in adapter._map_tools([ref])}


def test_devops_capabilities_need_configuration_once_installed():
    rows = {row["id"]: row for row in catalog_entries()}
    for capability_id in ("bauer.aws", "bauer.kubernetes", "agno.github"):
        assert rows[capability_id]["state"] == "needs_configuration", (capability_id, rows[capability_id]["reason"])


# ══ AWS (boto3 real + Stubber) ═══════════════════════════════════════════════


def _real_client(service: str):
    session = boto3.session.Session(aws_access_key_id="AKIAFAKEFAKEFAKEFAKE",
                                    aws_secret_access_key="fake-secret", region_name="us-east-1")
    return session.client(service)


@pytest.mark.parametrize("service", sorted(devops._AWS_READ_OPERATIONS))
def test_real_boto3_client_exposes_only_allowlisted_read_operations(service):
    raw = _real_client(service)
    client = devops.ReadOnlyAwsClient(service, raw)
    operations = {xform_name(name) for name in raw.meta.service_model.operation_names}
    allowed = devops._AWS_READ_OPERATIONS[service]
    assert allowed <= operations  # allowlist matches the real service model
    assert all(name.split("_")[0] in {"get", "list", "describe"} for name in allowed)
    with Stubber(raw) as stub:  # any call reaching botocore would fail: nothing is queued
        for name in sorted(operations - allowed):
            with pytest.raises(ToolError, match="bloqueada"):
                getattr(client, name)
        for name in ("meta", "get_paginator", "get_waiter", "generate_presigned_url", "_make_api_call", "close"):
            with pytest.raises(ToolError, match="bloqueada"):
                getattr(client, name)
        stub.assert_no_pending_responses()


class _StubbedAws:
    """``_AWS_SESSION_FACTORY`` over real boto3 clients wrapped by Stubber."""

    def __init__(self, account: str = ACCOUNT):
        self.clients = {service: _real_client(service) for service in ("sts", "eks", "ec2")}
        self.stubs = {service: Stubber(client) for service, client in self.clients.items()}
        self.stubs["sts"].add_response("get_caller_identity", {
            "Account": account, "Arn": f"arn:aws:iam::{account}:user/ro", "UserId": "AIDAFAKE"})
        for stub in self.stubs.values():
            stub.activate()
        self.seen: list[tuple[str, str, float]] = []

    def __call__(self, profile: str, region: str, timeout: float):
        self.seen.append((profile, region, timeout))
        return lambda service: self.clients[service]


@pytest.fixture()
def aws_settings(tmp_path, monkeypatch):
    _settings(tmp_path, monkeypatch, f"""
        capabilities:
          bauer.aws:
            accounts:
              main: {{profile: readonly, account_id: "{ACCOUNT}", regions: [us-east-1], eks_clusters: [prod]}}
    """)


def test_aws_reads_through_real_client_after_sts_check(tmp_path, monkeypatch, aws_settings):
    aws = _StubbedAws()
    aws.stubs["eks"].add_response("list_clusters", {"clusters": ["prod", "secret-cluster"]}, {"maxResults": 50})
    monkeypatch.setattr(devops, "_AWS_SESSION_FACTORY", aws)
    tools = _tools(tmp_path, "bauer.aws")

    assert json.loads(tools["bauer_aws__list_eks_clusters"].entrypoint(account="main", region="us-east-1")) == {"clusters": ["prod"]}
    assert aws.seen == [("readonly", "us-east-1", 15.0)]
    for stub in aws.stubs.values():
        stub.assert_no_pending_responses()

    with pytest.raises(ToolError, match="região fora"):
        tools["bauer_aws__list_eks_clusters"].entrypoint(account="main", region="eu-west-1")
    with pytest.raises(ToolError, match="cluster EKS fora"):
        tools["bauer_aws__describe_eks_cluster"].entrypoint(account="main", region="us-east-1", cluster="secret-cluster")


def test_aws_credential_of_another_account_is_refused_before_any_read(tmp_path, monkeypatch, aws_settings):
    aws = _StubbedAws(account="999999999999")
    monkeypatch.setattr(devops, "_AWS_SESSION_FACTORY", aws)
    tools = _tools(tmp_path, "bauer.aws:describe_ec2_instances")
    with pytest.raises(ToolError, match="outra conta"):
        tools["bauer_aws__describe_ec2_instances"].entrypoint(account="main", region="us-east-1")
    aws.stubs["ec2"].assert_no_pending_responses()  # EC2 never called


def test_aws_mutations_are_not_grantable(aws_settings):
    for ref in ("bauer.aws:delete_cluster", "bauer.aws:terminate_instances", "bauer.aws:create_nodegroup"):
        with pytest.raises(CapabilityError) as exc:
            resolve_capability(ref)
        assert exc.value.state == "blocked_by_policy"


def test_aws_missing_named_profile_never_falls_back_to_env_credentials(tmp_path, monkeypatch, aws_settings):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "AKIAENVFALLBACKFAKE1")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "env-secret")
    monkeypatch.setattr(devops, "_AWS_SESSION_FACTORY", devops._default_session_factory)  # real boto3 session
    tools = _tools(tmp_path, "bauer.aws:caller_identity")
    with pytest.raises(ToolError, match="credencial AWS indisponível"):
        tools["bauer_aws__caller_identity"].entrypoint(account="main", region="us-east-1")


# ══ Kubernetes (cliente real + pool falso) ═══════════════════════════════════

_KUBECONFIG = """
apiVersion: v1
kind: Config
current-context: prod
clusters:
- name: prod
  cluster: {server: "https://prod.k8s.example:6443", insecure-skip-tls-verify: true}
- name: staging
  cluster: {server: "https://staging.k8s.example:6443", insecure-skip-tls-verify: true}
users:
- name: viewer
  user: {token: kube-token-fake-123}
contexts:
- name: prod
  context: {cluster: prod, user: viewer}
- name: staging
  context: {cluster: staging, user: viewer}
"""


class _FakeKubePool:
    def __init__(self, monkeypatch):
        self.calls: list[tuple[str, str]] = []
        fake = self

        def request(pool, method, url, *args, **kwargs):
            fake.calls.append((method, url))
            if "/log" in url:
                body = b"line1\nAuthorization: Bearer sk-ant-api03-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA\n"
                ctype = "text/plain"
            else:
                body = json.dumps({"kind": "PodList", "apiVersion": "v1", "metadata": {}, "items": [
                    {"metadata": {"name": "api-1"}, "spec": {"nodeName": "n1", "containers": [{"name": "c", "image": "i"}]},
                     "status": {"phase": "Running", "containerStatuses": [
                         {"name": "c", "image": "i", "imageID": "", "ready": True, "restartCount": 2}]}},
                ]}).encode()
                ctype = "application/json"
            return urllib3.response.HTTPResponse(body=body, status=200, headers={"Content-Type": ctype},
                                                 preload_content=True)

        monkeypatch.setattr(urllib3.PoolManager, "request", request)


@pytest.fixture()
def kube(tmp_path, monkeypatch):
    kubeconfig = tmp_path / "kubeconfig"
    kubeconfig.write_text(_KUBECONFIG, encoding="utf-8")
    _settings(tmp_path, monkeypatch, f"""
        capabilities:
          bauer.kubernetes:
            contexts:
              stg: {{kubeconfig_context: staging, namespaces: [web], kubeconfig_path: "{kubeconfig.as_posix()}"}}
    """)
    return _FakeKubePool(monkeypatch)


def test_kubernetes_uses_only_the_named_context_and_get(tmp_path, kube):
    tools = _tools(tmp_path, "bauer.kubernetes")
    pods = json.loads(tools["bauer_kubernetes__list_pods"].entrypoint(context="stg", namespace="web"))
    assert pods == {"pods": [{"name": "api-1", "phase": "Running", "node": "n1", "restarts": 2}]}
    logs = tools["bauer_kubernetes__pod_logs"].entrypoint(context="stg", namespace="web", pod="api-1", tail=5)
    assert "sk-ant-api03" not in logs
    assert kube.calls and all(method == "GET" for method, _ in kube.calls)
    # explicit context wins over kubeconfig current-context (prod)
    assert all(url.startswith("https://staging.k8s.example:6443/api/v1/namespaces/web/") for _, url in kube.calls)


def test_kubernetes_scope_refused_before_any_request(tmp_path, kube):
    tools = _tools(tmp_path, "bauer.kubernetes")
    with pytest.raises(ToolError, match="namespace fora"):
        tools["bauer_kubernetes__list_pods"].entrypoint(context="stg", namespace="kube-system")
    with pytest.raises(ToolError, match="contexto desconhecido"):
        tools["bauer_kubernetes__list_pods"].entrypoint(context="prod", namespace="web")
    with pytest.raises(ToolError, match="nome de pod"):
        tools["bauer_kubernetes__pod_logs"].entrypoint(context="stg", namespace="web", pod="../secrets")
    assert kube.calls == []
    for ref in ("bauer.kubernetes:delete_namespaced_pod", "bauer.kubernetes:exec", "bauer.kubernetes:patch_deployment"):
        with pytest.raises(CapabilityError):
            resolve_capability(ref)


# ══ GitHub (PyGithub real + requests falso) ══════════════════════════════════


class _FakeGithub:
    def __init__(self, monkeypatch):
        self.requests: list[requests.PreparedRequest] = []
        fake = self

        def send(adapter, request, **kwargs):
            fake.requests.append(request)
            response = requests.Response()
            if request.url.rstrip("/").endswith("/repos/acme/app"):
                response.status_code = 200
                response._content = json.dumps({
                    "full_name": "acme/app", "name": "app", "owner": {"login": "acme"}, "description": "d",
                    "html_url": "https://github.com/acme/app", "stargazers_count": 1, "forks_count": 0,
                    "open_issues_count": 0, "language": "Python", "license": None, "default_branch": "main",
                    "url": "https://api.github.com/repos/acme/app",
                }).encode()
            else:
                response.status_code = 404
                response._content = b'{"message": "Not Found"}'
            response.headers["Content-Type"] = "application/json"
            response.url = request.url
            response.request = request
            return response

        monkeypatch.setattr(requests.adapters.HTTPAdapter, "send", send)


def _origin(url: str) -> tuple[str, str]:
    parsed = requests.utils.urlparse(url)
    return parsed.scheme, parsed.hostname or ""


@pytest.fixture()
def github(tmp_path, monkeypatch):
    monkeypatch.setenv("BAUER_TEST_GH_TOKEN", "ghp_testtokenvalue1234567890")
    _settings(tmp_path, monkeypatch, """
        capabilities:
          agno.github:
            repositories: [acme/app]
            credential: BAUER_TEST_GH_TOKEN
    """)
    return _FakeGithub(monkeypatch)


def test_sdk_github_real_pygithub_reads_only_allowlisted_repo(tmp_path, github):
    tools = _tools(tmp_path, "agno.github:get_repository")
    repo = json.loads(tools["agno_github__get_repository"].entrypoint(repo_name="acme/app"))
    assert repo["name"] == "acme/app"
    sent = len(github.requests)
    assert sent and all(r.method == "GET" and _origin(r.url) == ("https", "api.github.com") for r in github.requests)
    assert github.requests[0].headers["Authorization"] == "token ghp_testtokenvalue1234567890"

    with pytest.raises(ToolError, match="fora da allowlist"):
        tools["agno_github__get_repository"].entrypoint(repo_name="other/secret")
    assert len(github.requests) == sent  # denied before PyGithub touches the network


def test_sdk_github_every_reviewed_action_exists_and_writes_are_not_grantable(tmp_path, github):
    from agno.tools.github import GithubTools

    from bauer.core.runtime.agno_capabilities.families.github import _SDK_READ_ACTIONS

    sdk_functions = set(GithubTools(access_token="x").get_functions())
    assert set(_SDK_READ_ACTIONS) <= sdk_functions
    assert set(_tools(tmp_path, "agno.github")) == {f"agno_github__{name}" for name in _SDK_READ_ACTIONS}
    writes = {"create_issue", "create_repository", "delete_repository", "create_pull_request", "merge_pull_request",
              "create_file", "update_file", "delete_file", "create_branch", "comment_on_issue", "edit_issue"}
    for name in sorted(writes & sdk_functions):
        with pytest.raises(CapabilityError) as exc:
            resolve_capability(f"agno.github:{name}")
        assert exc.value.state == "blocked_by_policy"
    assert github.requests == []
