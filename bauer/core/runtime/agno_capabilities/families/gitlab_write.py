"""Sprint 75 — criar issue no GitLab sob aprovação (plano 067).

``bauer.gitlab_issues:create_issue`` follows the sprint 57 pattern
(``bauer.github_issues``); see ``slack_write`` for the shared guarantees.

GitLab specifics:

* ``base_url`` is chosen by the **operator** (gitlab.com or self-hosted, even
  under a sub-path); the model only names a project of the allowlist, so it
  can never choose the host. The URL is validated when the settings load:
  ``https`` (plain ``http`` only for this machine), no user-info, query or
  fragment, and never a metadata / link-local address (sprint 62 SSRF list).
  Private-network hosts are accepted on purpose: a self-hosted GitLab usually
  lives there, and the operator wrote the address.
* Redirects are never followed (``write_common``): a 3xx is an error, so the
  ``PRIVATE-TOKEN`` header can never reach another origin.
* ``--local``: allowed only when ``base_url`` points at this machine.
* Idempotency: ledger + ``<!-- bauer-idempotency: … -->`` marker in the
  description, searched in the project when an attempt is uncertain.
"""

from __future__ import annotations

import ipaddress
import re
from typing import Any
from urllib.parse import quote, urlsplit

from pydantic import Field, field_validator

from .....tools.base import ToolError
from ..bridge import ActionImpl, register_factory
from ..catalog import enable_approval_flow
from ..credentials import credential_available, resolve_credential, valid_credential_name
from ..manifest import ActionPolicy, ToolkitPolicy, register_policy
from ..settings import StrictSettings, register_settings_model
from . import write_common as wc

SERVICE = "GitLab"
_PROJECT = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.\-]{0,99}(?:/[A-Za-z0-9_][A-Za-z0-9_.\-]{0,99}){1,19}$")
_LABEL = re.compile(r"^[A-Za-z0-9 _.:\-]{1,50}$")
_MARKER = "bauer-idempotency"
_ARGS = {"project", "title", "description", "labels"}
_METADATA_HOSTS = frozenset({"169.254.169.254", "fd00:ec2::254", "metadata.google.internal", "metadata.goog"})


def _blocked_literal(host: str) -> bool:
    if host in _METADATA_HOSTS:
        return True
    try:
        addr = ipaddress.ip_address(host)
    except ValueError:
        return False
    return addr.is_link_local or addr.is_multicast or addr.is_unspecified or addr.is_reserved


class GitlabIssueSettings(StrictSettings):
    base_url: str = "https://gitlab.com"
    #: ``group/[subgroup/…]project`` paths (case-insensitive).
    projects: list[str] = Field(min_length=1)
    credential: str = "GITLAB_TOKEN"
    allowed_labels: list[str] = Field(default_factory=list)
    timeout_seconds: float = Field(default=15.0, gt=0, le=60)
    max_title_chars: int = Field(default=200, ge=10, le=255)
    max_description_chars: int = Field(default=20_000, ge=100, le=1_000_000)

    @field_validator("base_url")
    @classmethod
    def _base_url(cls, value: str) -> str:
        text = str(value).strip().rstrip("/")
        try:
            parts = urlsplit(text)
            host = (parts.hostname or "").rstrip(".").lower()
            parts.port  # noqa: B018 - raises ValueError on an invalid port
        except ValueError:
            raise ValueError("base_url inválida") from None
        if not host:
            raise ValueError("base_url sem host")
        if parts.username or parts.password or parts.query or parts.fragment:
            raise ValueError("base_url não pode ter credencial, query ou fragmento")
        if parts.scheme != "https" and not (parts.scheme == "http" and wc.host_is_local(host)):
            raise ValueError("base_url deve usar https (http só para esta máquina)")
        if _blocked_literal(host):
            raise ValueError("base_url aponta para endereço de metadata/link-local")
        if any(segment in {".", ".."} for segment in parts.path.split("/")):
            raise ValueError("base_url com caminho inválido")
        return text

    @field_validator("projects")
    @classmethod
    def _projects(cls, value: list[str]) -> list[str]:
        out = []
        for item in value:
            text = str(item).strip().strip("/")
            if not _PROJECT.match(text) or any(seg in {".", ".."} for seg in text.split("/")):
                raise ValueError("projeto deve ser 'grupo/projeto'")
            out.append(text.lower())
        return sorted(set(out))

    @field_validator("allowed_labels")
    @classmethod
    def _labels(cls, value: list[str]) -> list[str]:
        for label in value:
            if not _LABEL.match(label) or "," in label:
                raise ValueError("label inválida")
        return value

    @field_validator("credential")
    @classmethod
    def _credential(cls, value: str) -> str:
        if not valid_credential_name(value):
            raise ValueError("nome de credencial inválido")
        return value

    @property
    def host(self) -> str:
        return (urlsplit(self.base_url).hostname or "").rstrip(".").lower()


def validate_issue_args(settings: GitlabIssueSettings, args: dict[str, Any]) -> tuple[str, str, str, list[str]]:
    wc.ensure_local_allowed(SERVICE, settings.host)
    wc.reject_unknown(args, _ARGS)
    project = str(args.get("project") or "").strip().strip("/").lower()
    if project not in settings.projects:
        raise ToolError("projeto fora da allowlist configurada para esta capability")
    title = str(args.get("title") or "").strip()
    if not title:
        raise ToolError("title é obrigatório")
    if len(title) > settings.max_title_chars:
        raise ToolError("title excede o limite configurado")
    description = str(args.get("description") or "")
    if len(description) > settings.max_description_chars:
        raise ToolError("description excede o limite configurado")
    labels = args.get("labels") or []
    if not isinstance(labels, list):
        raise ToolError("labels deve ser uma lista")
    chosen = sorted({str(label) for label in labels})
    if any(label not in settings.allowed_labels for label in chosen):
        raise ToolError("label fora da lista permitida")
    return project, title, description, chosen


def preflight(settings: GitlabIssueSettings, args: dict[str, Any]) -> None:
    validate_issue_args(settings, args)
    wc.refuse_secret_material(args)
    if not credential_available(settings.credential):
        raise ToolError("credencial de escrita não configurada")


def _client(settings: GitlabIssueSettings, token: str):
    return wc.http_client(
        base_url=settings.base_url + "/api/v4",
        headers={"PRIVATE-TOKEN": token, "Accept": "application/json"},
        timeout=settings.timeout_seconds,
    )


def _issues_path(project: str) -> str:
    return f"/projects/{quote(project, safe='')}/issues"


def _actions(settings: GitlabIssueSettings, router_factory: Any) -> dict[str, Any]:
    def create_issue(project: str, title: str, description: str = "", labels: list[str] | None = None) -> dict[str, Any]:
        """Cria UMA issue num projeto permitido do GitLab — exige aprovação humana desta chamada exata.

        Args:
            project: Caminho grupo/projeto da allowlist configurada.
            title: Título da issue.
            description: Descrição em Markdown (opcional).
            labels: Labels permitidas pela configuração (opcional).
        """
        clean_project, clean_title, text, chosen = validate_issue_args(settings, {
            "project": project, "title": title, "description": description, "labels": labels or [],
        })
        token = resolve_credential(settings.credential)
        if not token:
            raise ToolError("credencial de escrita não configurada")
        router = wc.router_of(router_factory)
        key = wc.idempotency_key(router, "gitlab_issues", settings.host, clean_project, clean_title, text, chosen)
        ledger = wc.WriteLedger(getattr(router, "_policy_root", None), "gitlab_issues")
        marker = f"<!-- {_MARKER}: {key} -->"

        def result_of(item: Any) -> dict[str, Any] | None:
            if not isinstance(item, dict) or not item.get("iid"):
                return None
            return {"project": clean_project, "iid": item.get("iid"), "url": item.get("web_url")}

        def lookup(_reserved_at: float) -> dict[str, Any] | None:
            with _client(settings, token) as client:
                response = wc.read(client, "GET", _issues_path(clean_project), service=SERVICE,
                                   params={"search": key, "in": "description", "per_page": 5})
            if response.status_code >= 400:
                raise ToolError(f"GitLab respondeu HTTP {response.status_code} ao verificar a chamada anterior")
            items = wc.json_body(response, service=SERVICE, uncertain=False)
            for item in items if isinstance(items, list) else []:
                if isinstance(item, dict) and key in str(item.get("description") or ""):
                    return result_of(item)
            return None

        def perform() -> dict[str, Any]:
            payload: dict[str, Any] = {
                "title": clean_title,
                "description": (text + "\n\n" if text else "") + marker,
            }
            if chosen:
                payload["labels"] = ",".join(chosen)
            with _client(settings, token) as client:
                response = wc.send(client, "POST", _issues_path(clean_project), service=SERVICE, json=payload)
            wc.classify_status(response.status_code, service=SERVICE)
            created = result_of(wc.json_body(response, service=SERVICE))
            if not created:
                raise wc.Uncertain("resposta do GitLab sem o número da issue; o efeito é incerto")
            return created

        return wc.execute_once(ledger, key, service=SERVICE, perform=perform, lookup=lookup)

    return {"create_issue": create_issue}


def approval_summary(args: dict[str, Any], settings: GitlabIssueSettings | None = None) -> str:
    project = wc.one_line(args.get("project") or "?", 120)
    host = settings.host if settings is not None else "?"
    identity = settings.credential if settings is not None else "credencial configurada"
    return wc.safe_summary(
        f"Criar issue no GitLab {host}/{project}: \"{wc.one_line(args.get('title'))}\" (identidade: {identity})"
    )


def _factory(resolved, section, context=None) -> dict[str, ActionImpl]:
    from ..bridge import _parameters_for

    if not isinstance(section, GitlabIssueSettings):
        raise ToolError("bauer.gitlab_issues sem configuração")
    fn = _actions(section, getattr(context, "router_factory", None))["create_issue"]
    return {
        "create_issue": ActionImpl(
            fn=fn,
            description=(fn.__doc__ or "").strip().split("\n\n")[0],
            parameters=_parameters_for(fn),
            timeout_seconds=int(section.timeout_seconds) * 2 + 10,
            approval_summary=lambda args: approval_summary(args, section),
            preflight=lambda args: preflight(section, args),
        )
    }


register_settings_model("bauer.gitlab_issues", GitlabIssueSettings)
register_policy(
    ToolkitPolicy(
        id="bauer.gitlab_issues",
        title="GitLab — criar issue (escrita com aprovação)",
        category="Desenvolvimento",
        description="Cria uma issue num projeto permitido do GitLab (gitlab.com ou self-hosted), somente após aprovação humana da chamada exata.",
        source="bauer",
        actions=(ActionPolicy("create_issue", "write", "integration.write", approval_required=True),),
        requires=("httpx",),
        settings_section="bauer.gitlab_issues",
        notes=(
            "Sprint 75 (plano 067): base_url do operador (redirect nunca seguido); allowlist de projetos; "
            "idempotente por run (ledger + marcador); em --local só com GitLab nesta máquina."
        ),
    )
)
register_factory("bauer.gitlab_issues", _factory)
enable_approval_flow("bauer.gitlab_issues", "create_issue")
