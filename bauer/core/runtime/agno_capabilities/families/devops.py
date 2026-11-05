"""Sprint 54 — DevOps em leitura: Docker, AWS/EKS e Kubernetes (plano 057, sprint "22").

Three Bauer-native read-only capabilities. Each one makes mutation
*structurally* impossible instead of trusting a toolkit flag (plan STOP rule):

* ``bauer.docker`` — Docker Engine API over the configured socket through a
  transport that refuses every non-``GET`` request before it leaves the
  process. Container allowlist by name pattern; env values, mount sources and
  labels are never returned; logs are redacted and tail-limited.
* ``bauer.aws`` — boto3 (optional) behind ``ReadOnlyAwsClient``, which exposes
  only an explicit allowlist of operation names; account, region and EKS
  cluster must be allowlisted, and the credential's account is verified with
  STS before any other call (wrong account ⇒ refused).
* ``bauer.kubernetes`` — kubernetes client (optional) for an *explicit*
  kubeconfig context and namespace allowlist; only list/read calls exist.

No implicit default context/profile/region is ever used. ``exec``, ``apply``,
``delete``, deploy, scale and IAM operations are not classified anywhere.
Agno's ``DockerTools`` (mixed read/write), ``AWSLambdaTools`` (invoke runs
code) and ``AWSSESTool`` (sends email) are catalogued as incompatible.
"""

from __future__ import annotations

import fnmatch
import re
from typing import Any, Callable

import httpx
from pydantic import Field, field_validator

from .....logging_config import log_suppressed
from .....privacy import redact_text
from .....tools.base import ToolError
from ..bridge import ActionImpl, register_factory
from ..manifest import ActionPolicy, ToolkitPolicy, register_policy
from ..settings import StrictSettings, register_settings_model

_NAME = re.compile(r"^[a-z][a-z0-9_]{0,40}$")


def _redact_lines(text: str, max_chars: int) -> str:
    try:
        from .....secrets_scanner import scan

        text = scan(text, redact=True).redacted_text
    except Exception as exc:  # noqa: BLE001 - redact_text below still applies
        log_suppressed("agno.devops.secret_scan", exc)
    text = redact_text(text)
    return text[-max_chars:]


def _names(value: dict[str, Any]) -> dict[str, Any]:
    for name in value:
        if not _NAME.match(name):
            raise ValueError("nomes devem ser [a-z][a-z0-9_]*")
    return value


def _factory_for(build: Callable[[Any], dict[str, Callable[..., Any]]], settings_type: type, label: str):
    def factory(resolved, section, context=None) -> dict[str, ActionImpl]:
        from ..bridge import _parameters_for

        if not isinstance(section, settings_type):
            raise ToolError(f"{label} sem configuração")
        actions = build(section)
        return {
            action.name: ActionImpl(
                fn=actions[action.name],
                description=(actions[action.name].__doc__ or "").strip().split("\n\n")[0],
                parameters=_parameters_for(actions[action.name]),
                timeout_seconds=int(getattr(section, "timeout_seconds", 20)) + 10,
            )
            for action in resolved.actions
        }

    return factory


# ══ Docker ═══════════════════════════════════════════════════════════════════


class DockerHost(StrictSettings):
    socket: str = Field(min_length=1)
    containers: list[str] = Field(min_length=1, description="padrões fnmatch de nomes permitidos")


class DockerSettings(StrictSettings):
    hosts: dict[str, DockerHost] = Field(min_length=1)
    timeout_seconds: float = Field(default=10.0, gt=0, le=60)
    max_log_lines: int = Field(default=200, ge=1, le=5_000)
    max_log_chars: int = Field(default=20_000, ge=1_000, le=200_000)

    @field_validator("hosts")
    @classmethod
    def _check_names(cls, value: dict[str, Any]) -> dict[str, Any]:
        return _names(value)


#: Test seam: ``callable(host) -> httpx transport``.
_DOCKER_TRANSPORT: Any = None


class ReadOnlyTransport(httpx.BaseTransport):
    """httpx transport wrapper that refuses any method other than GET."""

    def __init__(self, inner: Any) -> None:
        self._inner = inner

    def handle_request(self, request: Any) -> Any:
        if request.method.upper() != "GET":
            raise ToolError(f"método {request.method} bloqueado: capability somente leitura")
        return self._inner.handle_request(request)

    def close(self) -> None:
        close = getattr(self._inner, "close", None)
        if callable(close):
            close()


def _docker_client(settings: DockerSettings, host: DockerHost):
    if _DOCKER_TRANSPORT is not None:
        inner = _DOCKER_TRANSPORT(host)
    else:
        inner = httpx.HTTPTransport(uds=host.socket)
    return httpx.Client(
        transport=ReadOnlyTransport(inner),
        base_url="http://docker",
        timeout=settings.timeout_seconds,
    )


def _docker_get(settings: DockerSettings, host: DockerHost, path: str, params: dict[str, Any] | None = None, raw: bool = False) -> Any:
    import httpx

    try:
        with _docker_client(settings, host) as client:
            response = client.get(path, params=params)
    except ToolError:
        raise
    except httpx.TimeoutException:
        raise ToolError("Docker não respondeu dentro do timeout") from None
    except (httpx.HTTPError, OSError) as exc:
        raise ToolError(f"Docker indisponível ({type(exc).__name__})") from None
    if response.status_code == 404:
        raise ToolError("recurso Docker não encontrado")
    if response.status_code >= 400:
        raise ToolError(f"Docker respondeu HTTP {response.status_code}")
    if raw:
        return response.content
    try:
        return response.json()
    except ValueError:
        raise ToolError("resposta malformada do Docker") from None


def _demux_logs(data: bytes) -> str:
    """Decode Docker's multiplexed log stream (8-byte frame headers) or raw TTY."""
    out: list[bytes] = []
    index = 0
    if len(data) >= 8 and data[0] in (0, 1, 2) and data[1:4] == b"\x00\x00\x00":
        while index + 8 <= len(data):
            size = int.from_bytes(data[index + 4:index + 8], "big")
            out.append(data[index + 8:index + 8 + size])
            index += 8 + size
        return b"".join(out).decode("utf-8", errors="replace")
    return data.decode("utf-8", errors="replace")


def _docker_actions(settings: DockerSettings) -> dict[str, Callable[..., Any]]:
    def _host(name: str) -> DockerHost:
        host = settings.hosts.get(str(name or ""))
        if host is None:
            raise ToolError(f"host Docker desconhecido; configurados: {', '.join(sorted(settings.hosts))}")
        return host

    def _allowed(host: DockerHost, container_name: str) -> bool:
        name = container_name.lstrip("/")
        return any(fnmatch.fnmatchcase(name, pattern) for pattern in host.containers)

    def _resolve_container(host: DockerHost, container: str) -> dict[str, Any]:
        if not re.match(r"^[A-Za-z0-9][A-Za-z0-9_.\-]{0,127}$", str(container or "")):
            raise ToolError("nome/ID de container inválido")
        info = _docker_get(settings, host, f"/containers/{container}/json")
        if not isinstance(info, dict) or not _allowed(host, str(info.get("Name") or "")):
            raise ToolError("container fora da allowlist configurada")
        return info

    def list_containers(host: str) -> list[dict[str, Any]]:
        """Lista containers permitidos de um host Docker configurado.

        Args:
            host: Nome do host Docker configurado.
        """
        target = _host(host)
        items = _docker_get(settings, target, "/containers/json", {"all": "1"})
        if not isinstance(items, list):
            raise ToolError("resposta malformada do Docker")
        result = []
        for item in items:
            names = [str(n).lstrip("/") for n in item.get("Names") or []]
            if any(_allowed(target, n) for n in names):
                result.append({
                    "name": names[0] if names else None,
                    "image": item.get("Image"),
                    "state": item.get("State"),
                    "status": item.get("Status"),
                    "ports": [
                        {k: p.get(k) for k in ("PrivatePort", "PublicPort", "Type")}
                        for p in item.get("Ports") or [] if isinstance(p, dict)
                    ],
                })
        return result

    def inspect_container(host: str, container: str) -> dict[str, Any]:
        """Estado e configuração não sensível de um container permitido (env só com nomes).

        Args:
            host: Nome do host Docker configurado.
            container: Nome ou ID do container.
        """
        info = _resolve_container(_host(host), container)
        config = info.get("Config") or {}
        state = info.get("State") or {}
        return {
            "name": str(info.get("Name") or "").lstrip("/"),
            "image": config.get("Image"),
            "created": info.get("Created"),
            "restart_count": info.get("RestartCount"),
            "state": {k: state.get(k) for k in ("Status", "Running", "ExitCode", "StartedAt", "FinishedAt", "OOMKilled")},
            "health": (state.get("Health") or {}).get("Status"),
            "env_keys": sorted(str(item).split("=", 1)[0] for item in config.get("Env") or []),
            "mount_destinations": sorted(str(m.get("Destination")) for m in info.get("Mounts") or [] if isinstance(m, dict)),
            "exposed_ports": sorted((config.get("ExposedPorts") or {}).keys()),
        }

    def container_logs(host: str, container: str, tail: int = 100) -> str:
        """Últimas linhas de log (redigidas) de um container permitido.

        Args:
            host: Nome do host Docker configurado.
            container: Nome ou ID do container.
            tail: Número de linhas (limitado pela configuração).
        """
        target = _host(host)
        info = _resolve_container(target, container)
        lines = max(1, min(int(tail), settings.max_log_lines))
        data = _docker_get(
            settings, target, f"/containers/{info.get('Id') or container}/logs",
            {"stdout": "1", "stderr": "1", "tail": str(lines), "timestamps": "1"}, raw=True,
        )
        return _redact_lines(_demux_logs(bytes(data)), settings.max_log_chars)

    def list_images(host: str) -> list[dict[str, Any]]:
        """Lista imagens (tags e tamanho) de um host Docker configurado.

        Args:
            host: Nome do host Docker configurado.
        """
        items = _docker_get(settings, _host(host), "/images/json")
        if not isinstance(items, list):
            raise ToolError("resposta malformada do Docker")
        return [{"tags": item.get("RepoTags"), "size": item.get("Size"), "created": item.get("Created")} for item in items]

    def docker_info(host: str) -> dict[str, Any]:
        """Resumo do daemon (versão, contagens, recursos) de um host configurado.

        Args:
            host: Nome do host Docker configurado.
        """
        info = _docker_get(settings, _host(host), "/info")
        if not isinstance(info, dict):
            raise ToolError("resposta malformada do Docker")
        keys = ("ServerVersion", "Containers", "ContainersRunning", "ContainersStopped", "Images", "NCPU", "MemTotal", "OperatingSystem", "Architecture")
        return {key: info.get(key) for key in keys}

    return {
        "list_containers": list_containers,
        "inspect_container": inspect_container,
        "container_logs": container_logs,
        "list_images": list_images,
        "docker_info": docker_info,
    }


# ══ AWS / EKS ════════════════════════════════════════════════════════════════

_AWS_READ_OPERATIONS: dict[str, frozenset[str]] = {
    "sts": frozenset({"get_caller_identity"}),
    "eks": frozenset({"list_clusters", "describe_cluster", "list_nodegroups", "describe_nodegroup"}),
    "ec2": frozenset({"describe_instances"}),
}


class AwsAccount(StrictSettings):
    profile: str = Field(min_length=1, description="perfil AWS (credencial de menor privilégio)")
    account_id: str
    regions: list[str] = Field(min_length=1)
    eks_clusters: list[str] = Field(default_factory=list)

    @field_validator("account_id")
    @classmethod
    def _account(cls, value: str) -> str:
        if not re.fullmatch(r"\d{12}", value):
            raise ValueError("account_id deve ter 12 dígitos")
        return value

    @field_validator("regions")
    @classmethod
    def _regions(cls, value: list[str]) -> list[str]:
        for region in value:
            if not re.fullmatch(r"[a-z]{2}(-gov)?-[a-z]+-\d", region):
                raise ValueError("região AWS inválida")
        return value


class AwsSettings(StrictSettings):
    accounts: dict[str, AwsAccount] = Field(min_length=1)
    timeout_seconds: float = Field(default=15.0, gt=0, le=60)
    max_items: int = Field(default=50, ge=1, le=500)

    @field_validator("accounts")
    @classmethod
    def _check_names(cls, value: dict[str, Any]) -> dict[str, Any]:
        return _names(value)


class ReadOnlyAwsClient:
    """Exposes only allowlisted read operations of a boto3 client."""

    def __init__(self, service: str, client: Any) -> None:
        self._service = service
        self._client = client
        self._allowed = _AWS_READ_OPERATIONS.get(service, frozenset())

    def __getattr__(self, name: str) -> Any:
        if name.startswith("_") or name not in self._allowed:
            raise ToolError(f"operação AWS '{self._service}.{name}' bloqueada: capability somente leitura")
        return getattr(self._client, name)


def _default_session_factory(profile: str, region: str, timeout: float) -> Any:
    import boto3
    from botocore.config import Config

    session = boto3.session.Session(profile_name=profile, region_name=region)

    def client(service: str) -> Any:
        return session.client(service, config=Config(connect_timeout=timeout, read_timeout=timeout, retries={"max_attempts": 2}))

    return client


#: Test seam: ``(profile, region, timeout) -> client_factory(service)``.
_AWS_SESSION_FACTORY: Callable[[str, str, float], Callable[[str], Any]] = _default_session_factory


def _aws_actions(settings: AwsSettings) -> dict[str, Callable[..., Any]]:
    verified: set[tuple[str, str]] = set()

    def _clients(account: str, region: str) -> Callable[[str], ReadOnlyAwsClient]:
        config = settings.accounts.get(str(account or ""))
        if config is None:
            raise ToolError(f"conta AWS desconhecida; configuradas: {', '.join(sorted(settings.accounts))}")
        if region not in config.regions:
            raise ToolError("região fora da allowlist da conta")
        try:
            raw = _AWS_SESSION_FACTORY(config.profile, region, settings.timeout_seconds)
        except ImportError:
            raise ToolError("boto3 não instalado") from None
        except Exception as exc:  # noqa: BLE001
            raise ToolError(f"credencial AWS indisponível ({type(exc).__name__})") from None

        def factory(service: str) -> ReadOnlyAwsClient:
            return ReadOnlyAwsClient(service, raw(service))

        if (account, region) not in verified:
            identity = _call(lambda: factory("sts").get_caller_identity())
            if str(identity.get("Account")) != config.account_id:
                raise ToolError("a credencial configurada pertence a outra conta AWS; chamada recusada")
            verified.add((account, region))
        return factory

    def _cluster(account: str, cluster: str) -> str:
        allowed = settings.accounts[account].eks_clusters
        if cluster not in allowed:
            raise ToolError("cluster EKS fora da allowlist")
        return cluster

    def caller_identity(account: str, region: str) -> dict[str, Any]:
        """Confirma a identidade/conta da credencial configurada (STS).

        Args:
            account: Nome da conta configurada.
            region: Região permitida.
        """
        clients = _clients(account, region)
        identity = _call(lambda: clients("sts").get_caller_identity())
        return {"account": identity.get("Account"), "arn": identity.get("Arn")}

    def list_eks_clusters(account: str, region: str) -> dict[str, Any]:
        """Lista clusters EKS permitidos numa conta/região configurada.

        Args:
            account: Nome da conta configurada.
            region: Região permitida.
        """
        clients = _clients(account, region)
        names = _call(lambda: clients("eks").list_clusters(maxResults=min(100, settings.max_items))).get("clusters") or []
        allowed = settings.accounts[account].eks_clusters
        return {"clusters": [name for name in names if name in allowed]}

    def describe_eks_cluster(account: str, region: str, cluster: str) -> dict[str, Any]:
        """Estado, versão e rede de um cluster EKS permitido.

        Args:
            account: Nome da conta configurada.
            region: Região permitida.
            cluster: Nome do cluster (allowlist).
        """
        clients = _clients(account, region)
        name = _cluster(account, cluster)
        data = (_call(lambda: clients("eks").describe_cluster(name=name)).get("cluster") or {})
        return {
            "name": data.get("name"),
            "status": data.get("status"),
            "version": data.get("version"),
            "platform_version": data.get("platformVersion"),
            "endpoint_public_access": (data.get("resourcesVpcConfig") or {}).get("endpointPublicAccess"),
            "created_at": str(data.get("createdAt")),
        }

    def list_eks_nodegroups(account: str, region: str, cluster: str) -> dict[str, Any]:
        """Nodegroups e seu estado num cluster EKS permitido.

        Args:
            account: Nome da conta configurada.
            region: Região permitida.
            cluster: Nome do cluster (allowlist).
        """
        clients = _clients(account, region)
        name = _cluster(account, cluster)
        eks = clients("eks")
        groups = _call(lambda: eks.list_nodegroups(clusterName=name)).get("nodegroups") or []
        result = []
        for group in groups[: settings.max_items]:
            info = _call(lambda g=group: eks.describe_nodegroup(clusterName=name, nodegroupName=g)).get("nodegroup") or {}
            result.append({
                "name": group,
                "status": info.get("status"),
                "instance_types": info.get("instanceTypes"),
                "scaling": info.get("scalingConfig"),
            })
        return {"nodegroups": result}

    def describe_ec2_instances(account: str, region: str) -> dict[str, Any]:
        """Inventário de instâncias EC2 (id, tipo, estado, AZ) numa conta/região permitida.

        Args:
            account: Nome da conta configurada.
            region: Região permitida.
        """
        clients = _clients(account, region)
        pages = _call(lambda: clients("ec2").describe_instances(MaxResults=min(1000, max(5, settings.max_items))))
        instances = []
        for reservation in pages.get("Reservations") or []:
            for item in reservation.get("Instances") or []:
                instances.append({
                    "id": item.get("InstanceId"),
                    "type": item.get("InstanceType"),
                    "state": (item.get("State") or {}).get("Name"),
                    "az": (item.get("Placement") or {}).get("AvailabilityZone"),
                })
        return {"instances": instances[: settings.max_items], "truncated": len(instances) > settings.max_items}

    return {
        "caller_identity": caller_identity,
        "list_eks_clusters": list_eks_clusters,
        "describe_eks_cluster": describe_eks_cluster,
        "list_eks_nodegroups": list_eks_nodegroups,
        "describe_ec2_instances": describe_ec2_instances,
    }


def _call(fn: Callable[[], Any]) -> dict[str, Any]:
    try:
        result = fn()
    except ToolError:
        raise
    except Exception as exc:  # noqa: BLE001 - botocore errors carry request ids/ARNs
        raise ToolError(f"AWS recusou ou falhou ({type(exc).__name__})") from None
    if not isinstance(result, dict):
        raise ToolError("resposta malformada da AWS")
    return result


# ══ Kubernetes ═══════════════════════════════════════════════════════════════


class KubeContext(StrictSettings):
    kubeconfig_context: str = Field(min_length=1)
    namespaces: list[str] = Field(min_length=1)
    kubeconfig_path: str | None = None


class KubernetesSettings(StrictSettings):
    contexts: dict[str, KubeContext] = Field(min_length=1)
    timeout_seconds: float = Field(default=15.0, gt=0, le=60)
    max_items: int = Field(default=100, ge=1, le=1_000)
    max_log_lines: int = Field(default=200, ge=1, le=5_000)
    max_log_chars: int = Field(default=20_000, ge=1_000, le=200_000)

    @field_validator("contexts")
    @classmethod
    def _check_names(cls, value: dict[str, Any]) -> dict[str, Any]:
        return _names(value)


def _default_kube_api(context: KubeContext) -> tuple[Any, Any]:
    from kubernetes import client, config

    api_client = config.new_client_from_config(
        config_file=context.kubeconfig_path, context=context.kubeconfig_context, persist_config=False,
    )
    return client.CoreV1Api(api_client), client.AppsV1Api(api_client)


#: Test seam: ``(context) -> (CoreV1Api, AppsV1Api)``.
_KUBE_API_FACTORY: Callable[[KubeContext], tuple[Any, Any]] = _default_kube_api


def _kube_actions(settings: KubernetesSettings) -> dict[str, Callable[..., Any]]:
    def _scope(context: str, namespace: str) -> tuple[Any, Any]:
        config = settings.contexts.get(str(context or ""))
        if config is None:
            raise ToolError(f"contexto desconhecido; configurados: {', '.join(sorted(settings.contexts))}")
        if namespace not in config.namespaces:
            raise ToolError("namespace fora da allowlist do contexto")
        try:
            return _KUBE_API_FACTORY(config)
        except ImportError:
            raise ToolError("cliente kubernetes não instalado") from None
        except Exception as exc:  # noqa: BLE001
            raise ToolError(f"kubeconfig/contexto indisponível ({type(exc).__name__})") from None

    def _kcall(fn: Callable[[], Any]) -> Any:
        try:
            return fn()
        except ToolError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise ToolError(f"API Kubernetes recusou ou falhou ({type(exc).__name__})") from None

    def list_pods(context: str, namespace: str) -> dict[str, Any]:
        """Pods (fase, reinícios, nó) de um namespace permitido.

        Args:
            context: Nome do contexto configurado.
            namespace: Namespace permitido.
        """
        core, _apps = _scope(context, namespace)
        items = _kcall(lambda: core.list_namespaced_pod(namespace, limit=settings.max_items, _request_timeout=settings.timeout_seconds)).items
        return {"pods": [
            {
                "name": pod.metadata.name,
                "phase": pod.status.phase,
                "node": getattr(pod.spec, "node_name", None),
                "restarts": sum((cs.restart_count or 0) for cs in (pod.status.container_statuses or [])),
            }
            for pod in items
        ]}

    def list_deployments(context: str, namespace: str) -> dict[str, Any]:
        """Deployments e réplicas prontas de um namespace permitido.

        Args:
            context: Nome do contexto configurado.
            namespace: Namespace permitido.
        """
        _core, apps = _scope(context, namespace)
        items = _kcall(lambda: apps.list_namespaced_deployment(namespace, limit=settings.max_items, _request_timeout=settings.timeout_seconds)).items
        return {"deployments": [
            {"name": d.metadata.name, "replicas": d.spec.replicas, "ready": d.status.ready_replicas}
            for d in items
        ]}

    def pod_logs(context: str, namespace: str, pod: str, tail: int = 100) -> str:
        """Últimas linhas de log (redigidas) de um pod num namespace permitido.

        Args:
            context: Nome do contexto configurado.
            namespace: Namespace permitido.
            pod: Nome do pod.
            tail: Número de linhas (limitado pela configuração).
        """
        if not re.match(r"^[a-z0-9]([-a-z0-9.]{0,251}[a-z0-9])?$", str(pod or "")):
            raise ToolError("nome de pod inválido")
        core, _apps = _scope(context, namespace)
        lines = max(1, min(int(tail), settings.max_log_lines))
        text = _kcall(lambda: core.read_namespaced_pod_log(pod, namespace, tail_lines=lines, _request_timeout=settings.timeout_seconds))
        return _redact_lines(str(text or ""), settings.max_log_chars)

    return {"list_pods": list_pods, "list_deployments": list_deployments, "pod_logs": pod_logs}


# ══ registration ═════════════════════════════════════════════════════════════


def _read(name: str) -> ActionPolicy:
    return ActionPolicy(name, "read", "infra.read")


register_settings_model("bauer.docker", DockerSettings)
register_policy(ToolkitPolicy(
    id="bauer.docker",
    title="Docker (leitura)",
    category="DevOps",
    description="Inventário, inspeção sem segredos e logs redigidos de containers permitidos via Docker Engine API (somente GET).",
    source="bauer",
    actions=tuple(_read(n) for n in ("list_containers", "inspect_container", "container_logs", "list_images", "docker_info")),
    requires=("httpx",),
    settings_section="bauer.docker",
    notes="Transporte recusa qualquer método diferente de GET antes da rede. Socket unix (Linux/macOS).",
))
register_factory("bauer.docker", _factory_for(_docker_actions, DockerSettings, "bauer.docker"))

register_settings_model("bauer.aws", AwsSettings)
register_policy(ToolkitPolicy(
    id="bauer.aws",
    title="AWS/EKS (leitura)",
    category="DevOps",
    description="Identidade, clusters/nodegroups EKS e inventário EC2 de contas e regiões permitidas.",
    source="bauer",
    actions=tuple(_read(n) for n in ("caller_identity", "list_eks_clusters", "describe_eks_cluster", "list_eks_nodegroups", "describe_ec2_instances")),
    requires=("boto3",),
    install_hint="uv pip install boto3",
    settings_section="bauer.aws",
    notes="Conta verificada via STS antes de cada sessão; cliente só expõe operações de leitura listadas.",
))
register_factory("bauer.aws", _factory_for(_aws_actions, AwsSettings, "bauer.aws"))

register_settings_model("bauer.kubernetes", KubernetesSettings)
register_policy(ToolkitPolicy(
    id="bauer.kubernetes",
    title="Kubernetes (leitura)",
    category="DevOps",
    description="Pods, deployments e logs redigidos de namespaces permitidos num contexto kubeconfig explícito.",
    source="bauer",
    actions=tuple(_read(n) for n in ("list_pods", "list_deployments", "pod_logs")),
    requires=("kubernetes",),
    install_hint="uv pip install kubernetes",
    settings_section="bauer.kubernetes",
    notes="Sem contexto padrão: cada contexto é nomeado e mapeado explicitamente.",
))
register_factory("bauer.kubernetes", _factory_for(_kube_actions, KubernetesSettings, "bauer.kubernetes"))

for _capability_id, _title, _reason in (
    ("agno.docker", "DockerTools", "mistura leitura com run/stop/remove/pull/build no mesmo toolkit e devolve env/logs sem redação; use bauer.docker"),
    ("agno.aws_lambda", "AWSLambdaTools", "invoke executa código remoto; sem capability de leitura equivalente segura; use bauer.aws para inventário"),
    ("agno.aws_ses", "AWSSESTool", "envia e-mail (efeito externo) e está fora do piloto de escrita da sprint 57"),
):
    register_policy(ToolkitPolicy(
        id=_capability_id,
        title=_title,
        category="DevOps",
        description="Toolkit DevOps/cloud do SDK Agno.",
        incompatible=f"O toolkit {_reason}.",
    ))
