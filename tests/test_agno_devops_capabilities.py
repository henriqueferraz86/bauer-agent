"""Plano 057 / sprint 54 — Docker, AWS/EKS e Kubernetes somente leitura.

Herméticos: Docker via ``httpx.MockTransport``; AWS e Kubernetes via fábricas
fake (boto3/kubernetes não estão no lock e nunca são chamados de verdade).
"""

from __future__ import annotations

import json
import textwrap
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from bauer.core.runtime.adapters.agno_adapter import AgnoRuntimeAdapter
from bauer.core.runtime.agno_capabilities import catalog
from bauer.core.runtime.agno_capabilities.catalog import CapabilityError, catalog_entries, resolve_capability
from bauer.core.runtime.agno_capabilities.families import devops
from bauer.core.runtime.agno_capabilities.settings import CapabilitySettingsError, parse_settings
from bauer.tools.base import ToolError

pytest.importorskip("agno")


def _settings(tmp_path: Path, monkeypatch, body: str) -> None:
    target = tmp_path / "agno_capabilities.yaml"
    target.write_text(textwrap.dedent(body), encoding="utf-8")
    monkeypatch.setenv("BAUER_AGNO_CAPABILITIES", str(target))


def _tools(tmp_path: Path, ref: str) -> dict:
    workspace = tmp_path / "workspace"
    workspace.mkdir(exist_ok=True)
    adapter = AgnoRuntimeAdapter(
        adapter_config={"workspace": str(workspace), "db_file": str(tmp_path / "agno.db")},
        runtime_policy_root=tmp_path / "runtime",
    )
    return {fn.name: fn for fn in adapter._map_tools([ref])}


# ── Docker ───────────────────────────────────────────────────────────────────


class _DockerApi:
    def __init__(self):
        self.methods: list[str] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.methods.append(request.method)
        path = request.url.path
        if path == "/containers/json":
            return httpx.Response(200, json=[
                {"Names": ["/web-1"], "Image": "nginx", "State": "running", "Status": "Up", "Ports": []},
                {"Names": ["/db-secret"], "Image": "postgres", "State": "running", "Status": "Up", "Ports": []},
            ])
        if path == "/containers/web-1/json":
            return httpx.Response(200, json={
                "Id": "abc", "Name": "/web-1", "RestartCount": 0,
                "Config": {"Image": "nginx", "Env": ["DB_PASSWORD=hunter2", "MODE=prod"]},
                "State": {"Status": "running", "Running": True},
                "Mounts": [{"Source": "/home/user/.ssh", "Destination": "/root/.ssh"}],
            })
        if path == "/containers/db-secret/json":
            return httpx.Response(200, json={"Id": "def", "Name": "/db-secret"})
        if path == "/containers/abc/logs":
            line = b"2026-01-01 token=ghp_abcdefghijklmnopqrstuvwxyz0123456789AB started\n"
            frame = b"\x01\x00\x00\x00" + len(line).to_bytes(4, "big") + line
            return httpx.Response(200, content=frame)
        return httpx.Response(404)


@pytest.fixture()
def docker_api(tmp_path, monkeypatch):
    api = _DockerApi()
    monkeypatch.setattr(devops, "_DOCKER_TRANSPORT", lambda host: httpx.MockTransport(api))
    _settings(tmp_path, monkeypatch, """
        capabilities:
          bauer.docker:
            hosts:
              local:
                socket: /var/run/docker.sock
                containers: ["web-*"]
    """)
    return api


def test_docker_inventory_only_shows_allowlisted_containers(tmp_path, docker_api):
    tools = _tools(tmp_path, "bauer.docker")
    listed = json.loads(tools["bauer_docker__list_containers"].entrypoint(host="local"))
    assert [item["name"] for item in listed] == ["web-1"]
    with pytest.raises(ToolError, match="fora da allowlist"):
        tools["bauer_docker__inspect_container"].entrypoint(host="local", container="db-secret")
    with pytest.raises(ToolError, match="host Docker desconhecido"):
        tools["bauer_docker__list_containers"].entrypoint(host="prod")


def test_docker_inspect_hides_secret_values_and_logs_are_redacted(tmp_path, docker_api):
    tools = _tools(tmp_path, "bauer.docker")
    info = tools["bauer_docker__inspect_container"].entrypoint(host="local", container="web-1")
    assert "hunter2" not in info and "/home/user/.ssh" not in info
    assert json.loads(info)["env_keys"] == ["DB_PASSWORD", "MODE"]

    logs = tools["bauer_docker__container_logs"].entrypoint(host="local", container="web-1", tail=10)
    assert "started" in logs and "ghp_abcdefghijklmnopqrstuvwxyz" not in logs
    assert set(docker_api.methods) == {"GET"}


def test_docker_transport_refuses_mutating_methods_before_network():
    sent: list[str] = []
    transport = devops.ReadOnlyTransport(httpx.MockTransport(lambda r: sent.append(r.method) or httpx.Response(200)))
    with httpx.Client(transport=transport, base_url="http://docker") as client:
        for method in ("POST", "DELETE", "PUT"):
            with pytest.raises(ToolError, match="bloqueado"):
                client.request(method, "/containers/web-1/kill")
    assert sent == []


def test_docker_mutations_are_not_classified(docker_api):
    for action in ("run_container", "stop_container", "exec", "remove_container"):
        with pytest.raises(CapabilityError) as exc:
            resolve_capability(f"bauer.docker:{action}")
        assert exc.value.state == "blocked_by_policy"


# ── AWS / EKS ────────────────────────────────────────────────────────────────


class _FakeAws:
    def __init__(self, account: str = "123456789012"):
        self.account = account
        self.calls: list[str] = []

    def factory(self, profile, region, timeout):
        def client(service):
            return SimpleNamespace(
                get_caller_identity=lambda: self._rec("sts.get_caller_identity", {"Account": self.account, "Arn": "arn:aws:iam::x:role/ro"}),
                list_clusters=lambda **kw: self._rec("eks.list_clusters", {"clusters": ["prod", "secret"]}),
                describe_cluster=lambda name: self._rec("eks.describe_cluster", {"cluster": {"name": name, "status": "ACTIVE", "version": "1.30"}}),
                list_nodegroups=lambda clusterName: self._rec("eks.list_nodegroups", {"nodegroups": ["ng1"]}),
                describe_nodegroup=lambda clusterName, nodegroupName: self._rec("eks.describe_nodegroup", {"nodegroup": {"status": "ACTIVE"}}),
                describe_instances=lambda **kw: self._rec("ec2.describe_instances", {"Reservations": []}),
                delete_cluster=lambda **kw: self._rec("eks.delete_cluster", {}),
                terminate_instances=lambda **kw: self._rec("ec2.terminate_instances", {}),
            )

        return client

    def _rec(self, name, value):
        self.calls.append(name)
        return value


@pytest.fixture()
def aws(tmp_path, monkeypatch):
    fake = _FakeAws()
    monkeypatch.setattr(devops, "_AWS_SESSION_FACTORY", fake.factory)
    monkeypatch.setattr(catalog, "module_spec_exists", lambda name: True)
    _settings(tmp_path, monkeypatch, """
        capabilities:
          bauer.aws:
            accounts:
              main:
                profile: bauer-readonly
                account_id: "123456789012"
                regions: [us-east-1]
                eks_clusters: [prod]
    """)
    return fake


def test_aws_reads_only_allowlisted_scope(tmp_path, aws):
    tools = _tools(tmp_path, "bauer.aws")
    clusters = json.loads(tools["bauer_aws__list_eks_clusters"].entrypoint(account="main", region="us-east-1"))
    assert clusters == {"clusters": ["prod"]}
    described = json.loads(tools["bauer_aws__describe_eks_cluster"].entrypoint(account="main", region="us-east-1", cluster="prod"))
    assert described["status"] == "ACTIVE"
    with pytest.raises(ToolError, match="cluster EKS fora da allowlist"):
        tools["bauer_aws__describe_eks_cluster"].entrypoint(account="main", region="us-east-1", cluster="secret")
    with pytest.raises(ToolError, match="região fora da allowlist"):
        tools["bauer_aws__list_eks_clusters"].entrypoint(account="main", region="eu-west-1")
    assert aws.calls[0] == "sts.get_caller_identity"


def test_aws_wrong_account_credential_is_refused(tmp_path, aws):
    aws.account = "999999999999"
    tools = _tools(tmp_path, "bauer.aws:list_eks_clusters")
    with pytest.raises(ToolError, match="outra conta"):
        tools["bauer_aws__list_eks_clusters"].entrypoint(account="main", region="us-east-1")
    assert "eks.list_clusters" not in aws.calls


def test_aws_client_exposes_only_read_operations():
    client = devops.ReadOnlyAwsClient("eks", SimpleNamespace(delete_cluster=lambda **kw: "deleted", list_clusters=lambda: {}))
    with pytest.raises(ToolError, match="bloqueada"):
        client.delete_cluster(name="prod")
    ec2 = devops.ReadOnlyAwsClient("ec2", SimpleNamespace(terminate_instances=lambda **kw: "x"))
    with pytest.raises(ToolError, match="bloqueada"):
        ec2.terminate_instances(InstanceIds=["i-1"])


def test_aws_without_boto3_is_dependency_missing(tmp_path, monkeypatch):
    _settings(tmp_path, monkeypatch, """
        capabilities:
          bauer.aws:
            accounts:
              main: {profile: ro, account_id: "123456789012", regions: [us-east-1]}
    """)
    monkeypatch.setattr(catalog, "module_spec_exists", lambda name: name != "boto3")
    row = next(r for r in catalog_entries() if r["id"] == "bauer.aws")
    assert row["state"] == "dependency_missing"


def test_aws_settings_reject_ambiguous_scope():
    with pytest.raises(CapabilitySettingsError):
        parse_settings({"capabilities": {"bauer.aws": {"accounts": {"main": {"profile": "ro", "account_id": "123", "regions": ["us-east-1"]}}}}})
    with pytest.raises(CapabilitySettingsError):
        parse_settings({"capabilities": {"bauer.aws": {"accounts": {"main": {"profile": "ro", "account_id": "123456789012", "regions": []}}}}})


# ── Kubernetes ───────────────────────────────────────────────────────────────


def test_kubernetes_requires_explicit_context_and_namespace(tmp_path, monkeypatch):
    calls: list[tuple] = []

    def pod(name, restarts):
        return SimpleNamespace(
            metadata=SimpleNamespace(name=name), spec=SimpleNamespace(node_name="n1"),
            status=SimpleNamespace(phase="Running", container_statuses=[SimpleNamespace(restart_count=restarts)]),
        )

    core = SimpleNamespace(
        list_namespaced_pod=lambda ns, **kw: calls.append(("pods", ns)) or SimpleNamespace(items=[pod("api-1", 2)]),
        read_namespaced_pod_log=lambda name, ns, **kw: "ok password=supersecret123 done",
        delete_namespaced_pod=lambda *a, **kw: calls.append(("delete",)),
    )
    apps = SimpleNamespace(list_namespaced_deployment=lambda ns, **kw: SimpleNamespace(items=[]))
    monkeypatch.setattr(devops, "_KUBE_API_FACTORY", lambda context: (core, apps))
    monkeypatch.setattr(catalog, "module_spec_exists", lambda name: True)
    _settings(tmp_path, monkeypatch, """
        capabilities:
          bauer.kubernetes:
            contexts:
              prod:
                kubeconfig_context: eks-prod-readonly
                namespaces: [shop]
    """)
    tools = _tools(tmp_path, "bauer.kubernetes")

    pods = json.loads(tools["bauer_kubernetes__list_pods"].entrypoint(context="prod", namespace="shop"))
    assert pods["pods"][0]["restarts"] == 2
    with pytest.raises(ToolError, match="namespace fora da allowlist"):
        tools["bauer_kubernetes__list_pods"].entrypoint(context="prod", namespace="kube-system")
    with pytest.raises(ToolError, match="contexto desconhecido"):
        tools["bauer_kubernetes__list_pods"].entrypoint(context="default", namespace="shop")
    logs = tools["bauer_kubernetes__pod_logs"].entrypoint(context="prod", namespace="shop", pod="api-1")
    assert "supersecret123" not in logs
    assert ("delete",) not in calls
    with pytest.raises(CapabilityError):
        resolve_capability("bauer.kubernetes:delete_pod")


def test_sdk_devops_toolkits_are_incompatible():
    rows = {row["id"]: row for row in catalog_entries()}
    for capability_id in ("agno.docker", "agno.aws_lambda", "agno.aws_ses"):
        assert rows[capability_id]["state"] == "incompatible"
