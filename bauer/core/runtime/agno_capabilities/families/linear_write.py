"""Sprint 75 — criar issue no Linear sob aprovação (plano 067).

``bauer.linear_issues:create_issue`` follows the sprint 57 pattern
(``bauer.github_issues``); see ``slack_write`` for the shared guarantees.

Linear specifics:

* GraphQL API at a fixed host (``api.linear.app``), mutation ``issueCreate``.
* Scope is a mapping configured by the operator: team key → team UUID,
  optional project name → UUID and label name → UUID. The model only names
  keys of those maps; it can never supply a raw id.
* Idempotency is native: the issue id is a UUID derived from the call key and
  sent as ``IssueCreateInput.id``. Linear refuses a second issue with the same
  id, so an uncertain attempt is first looked up by id and, if absent,
  resent safely (``resend_safe``).
"""

from __future__ import annotations

import hashlib
import re
import uuid
from typing import Any, Literal

from pydantic import Field, field_validator

from .....tools.base import ToolError
from ..bridge import ActionImpl, register_factory
from ..catalog import enable_approval_flow
from ..credentials import credential_available, resolve_credential, valid_credential_name
from ..manifest import ActionPolicy, ToolkitPolicy, register_policy
from ..settings import StrictSettings, register_settings_model
from . import write_common as wc

SERVICE = "Linear"
API_URL = "https://api.linear.app"
API_HOST = "api.linear.app"
_TEAM_KEY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_\-]{0,19}$")
_NAME = re.compile(r"^[A-Za-z0-9 _.:\-]{1,80}$")
_ARGS = {"team", "title", "description", "project", "labels"}

_CREATE = (
    "mutation BauerIssueCreate($input: IssueCreateInput!) {"
    " issueCreate(input: $input) { success issue { id identifier url } } }"
)
_LOOKUP = "query BauerIssueLookup($id: String!) { issue(id: $id) { id identifier url } }"


def _uuid(value: str) -> str:
    try:
        return str(uuid.UUID(str(value).strip()))
    except ValueError:
        raise ValueError("id deve ser um UUID do Linear") from None


class LinearIssueSettings(StrictSettings):
    #: team key (e.g. ``ENG``) → team UUID.
    teams: dict[str, str] = Field(min_length=1)
    #: project name → project UUID (optional scope).
    projects: dict[str, str] = Field(default_factory=dict)
    #: label name → label UUID (only these may be applied).
    labels: dict[str, str] = Field(default_factory=dict)
    credential: str = "LINEAR_API_KEY"
    #: ``api_key`` = personal key (``Authorization: <key>``); ``bearer`` = OAuth.
    auth_scheme: Literal["api_key", "bearer"] = "api_key"
    timeout_seconds: float = Field(default=15.0, gt=0, le=60)
    max_title_chars: int = Field(default=200, ge=10, le=255)
    max_description_chars: int = Field(default=20_000, ge=100, le=100_000)

    @field_validator("teams")
    @classmethod
    def _teams(cls, value: dict[str, str]) -> dict[str, str]:
        out: dict[str, str] = {}
        for key, team_id in value.items():
            name = str(key).strip().upper()
            if not _TEAM_KEY.match(name):
                raise ValueError("chave de time inválida")
            out[name] = _uuid(team_id)
        return out

    @field_validator("projects", "labels")
    @classmethod
    def _named(cls, value: dict[str, str]) -> dict[str, str]:
        out: dict[str, str] = {}
        for key, item_id in value.items():
            name = str(key).strip()
            if not _NAME.match(name):
                raise ValueError("nome inválido")
            out[name] = _uuid(item_id)
        return out

    @field_validator("credential")
    @classmethod
    def _credential(cls, value: str) -> str:
        if not valid_credential_name(value):
            raise ValueError("nome de credencial inválido")
        return value


def validate_issue_args(
    settings: LinearIssueSettings, args: dict[str, Any],
) -> tuple[str, str, str, str, list[str]]:
    wc.ensure_local_allowed(SERVICE, API_HOST)
    wc.reject_unknown(args, _ARGS)
    team = str(args.get("team") or "").strip().upper()
    if team not in settings.teams:
        raise ToolError("time fora da allowlist configurada para esta capability")
    title = str(args.get("title") or "").strip()
    if not title:
        raise ToolError("title é obrigatório")
    if len(title) > settings.max_title_chars:
        raise ToolError("title excede o limite configurado")
    description = str(args.get("description") or "")
    if len(description) > settings.max_description_chars:
        raise ToolError("description excede o limite configurado")
    project = str(args.get("project") or "").strip()
    if project and project not in settings.projects:
        raise ToolError("projeto fora da allowlist configurada")
    labels = args.get("labels") or []
    if not isinstance(labels, list):
        raise ToolError("labels deve ser uma lista")
    chosen = sorted({str(label) for label in labels})
    if any(label not in settings.labels for label in chosen):
        raise ToolError("label fora da lista permitida")
    return team, title, description, project, chosen


def preflight(settings: LinearIssueSettings, args: dict[str, Any]) -> None:
    validate_issue_args(settings, args)
    wc.refuse_secret_material(args)
    if not credential_available(settings.credential):
        raise ToolError("credencial de escrita não configurada")


def issue_uuid(key: str) -> str:
    """Deterministic UUID (v4 layout) for an idempotency key."""
    return str(uuid.UUID(bytes=hashlib.sha256(key.encode("ascii")).digest()[:16], version=4))


def _client(settings: LinearIssueSettings, token: str):
    auth = f"Bearer {token}" if settings.auth_scheme == "bearer" else token
    return wc.http_client(
        base_url=API_URL,
        headers={"Authorization": auth, "Content-Type": "application/json"},
        timeout=settings.timeout_seconds,
    )


def _issue_result(team: str, issue: Any) -> dict[str, Any] | None:
    if not isinstance(issue, dict) or not issue.get("id"):
        return None
    return {"team": team, "id": issue.get("id"), "identifier": issue.get("identifier"), "url": issue.get("url")}


def _actions(settings: LinearIssueSettings, router_factory: Any) -> dict[str, Any]:
    def create_issue(
        team: str, title: str, description: str = "", project: str = "", labels: list[str] | None = None,
    ) -> dict[str, Any]:
        """Cria UMA issue num time permitido do Linear — exige aprovação humana desta chamada exata.

        Args:
            team: Chave do time (ex.: ENG) da allowlist configurada.
            title: Título da issue.
            description: Descrição em Markdown (opcional).
            project: Nome de projeto da allowlist configurada (opcional).
            labels: Nomes de labels permitidas pela configuração (opcional).
        """
        clean_team, clean_title, text, clean_project, chosen = validate_issue_args(settings, {
            "team": team, "title": title, "description": description, "project": project, "labels": labels or [],
        })
        token = resolve_credential(settings.credential)
        if not token:
            raise ToolError("credencial de escrita não configurada")
        router = wc.router_of(router_factory)
        key = wc.idempotency_key(router, "linear_issues", clean_team, clean_title, text, clean_project, chosen)
        ledger = wc.WriteLedger(getattr(router, "_policy_root", None), "linear_issues")
        issue_id = issue_uuid(key)

        def lookup(_reserved_at: float) -> dict[str, Any] | None:
            with _client(settings, token) as client:
                response = wc.read(client, "POST", "/graphql", service=SERVICE,
                                   json={"query": _LOOKUP, "variables": {"id": issue_id}})
            if response.status_code in {401, 403}:
                raise ToolError(f"Linear recusou a credencial (HTTP {response.status_code}) ao verificar a chamada anterior")
            if response.status_code >= 500:
                raise ToolError(f"Linear respondeu HTTP {response.status_code} ao verificar a chamada anterior")
            data = wc.json_body(response, service=SERVICE, uncertain=False)
            issue = ((data or {}).get("data") or {}).get("issue") if isinstance(data, dict) else None
            return _issue_result(clean_team, issue)

        def perform() -> dict[str, Any]:
            issue_input: dict[str, Any] = {
                "id": issue_id,
                "teamId": settings.teams[clean_team],
                "title": clean_title,
            }
            if text:
                issue_input["description"] = text
            if clean_project:
                issue_input["projectId"] = settings.projects[clean_project]
            if chosen:
                issue_input["labelIds"] = [settings.labels[name] for name in chosen]
            with _client(settings, token) as client:
                response = wc.send(client, "POST", "/graphql", service=SERVICE,
                                   json={"query": _CREATE, "variables": {"input": issue_input}})
            if response.status_code != 400:
                wc.classify_status(response.status_code, service=SERVICE)
            data = wc.json_body(response, service=SERVICE)
            if not isinstance(data, dict):
                raise wc.Uncertain("resposta malformada do Linear; o efeito é incerto")
            created = ((data.get("data") or {}).get("issueCreate") or {}) if isinstance(data.get("data"), dict) else {}
            result = _issue_result(clean_team, created.get("issue")) if created.get("success") else None
            if result:
                return result
            if data.get("errors") or response.status_code == 400:
                # A GraphQL error after a resend may be "id already exists":
                # the id is ours, so look it up before reporting failure.
                existing = lookup(0.0)
                if existing:
                    return existing
                raise wc.NotSent("Linear rejeitou a criação da issue (erro GraphQL)")
            raise wc.Uncertain("resposta do Linear sem a issue criada; o efeito é incerto")

        return wc.execute_once(ledger, key, service=SERVICE, perform=perform, lookup=lookup, resend_safe=True)

    return {"create_issue": create_issue}


def approval_summary(args: dict[str, Any], settings: LinearIssueSettings | None = None) -> str:
    team = wc.one_line(args.get("team") or "?", 20)
    project = wc.one_line(args.get("project") or "", 40)
    identity = settings.credential if settings is not None else "credencial configurada"
    where = f"time {team}" + (f", projeto {project}" if project else "")
    return wc.safe_summary(
        f"Criar issue no Linear ({where}): \"{wc.one_line(args.get('title'))}\" (identidade: {identity})"
    )


def _factory(resolved, section, context=None) -> dict[str, ActionImpl]:
    from ..bridge import _parameters_for

    if not isinstance(section, LinearIssueSettings):
        raise ToolError("bauer.linear_issues sem configuração")
    fn = _actions(section, getattr(context, "router_factory", None))["create_issue"]
    return {
        "create_issue": ActionImpl(
            fn=fn,
            description=(fn.__doc__ or "").strip().split("\n\n")[0],
            parameters=_parameters_for(fn),
            timeout_seconds=int(section.timeout_seconds) * 3 + 10,
            approval_summary=lambda args: approval_summary(args, section),
            preflight=lambda args: preflight(section, args),
        )
    }


register_settings_model("bauer.linear_issues", LinearIssueSettings)
register_policy(
    ToolkitPolicy(
        id="bauer.linear_issues",
        title="Linear — criar issue (escrita com aprovação)",
        category="Desenvolvimento",
        description="Cria uma issue num time permitido do Linear, somente após aprovação humana da chamada exata.",
        source="bauer",
        actions=(ActionPolicy("create_issue", "write", "integration.write", approval_required=True),),
        requires=("httpx",),
        settings_section="bauer.linear_issues",
        notes=(
            "Sprint 75 (plano 067): times/projetos/labels por mapa do operador; id da issue derivado da chave "
            "de idempotência (Linear deduplica); recusado em --local (serviço em nuvem)."
        ),
    )
)
register_factory("bauer.linear_issues", _factory)
enable_approval_flow("bauer.linear_issues", "create_issue")
