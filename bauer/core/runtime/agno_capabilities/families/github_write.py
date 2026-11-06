"""Sprint 57 — primeiro conector com escrita: criar issue no GitHub (plano 057, sprint "25").

``bauer.github_issues:create_issue`` is a capability **separate** from the
read-only ``bauer.github`` and starts unusable (needs its own settings with a
repository allowlist and a credential name). Its only action:

* has risk ``write`` and policy operation ``integration.write``, which the
  PolicyEngine always resolves to *ask* (a floor that custom policy files
  cannot lower), so every call needs a human approval bound — by the
  ApprovalManager — to the run, session, tool and the **exact arguments**
  (single use; any change of target/title/body needs a new approval;
  expired/denied approvals never authorize);
* shows the approver target, effect summary and identity (credential name)
  through the approval reason;
* is idempotent per run: the same approved call retried (agent retry, Kernel
  retry, network timeout) never creates a second issue — a local ledger and a
  marker searched on GitHub both short-circuit to the existing issue;
* turns every remote failure into a ``ToolError`` (never a fake success).

Per the plan's STOP rule, no other write connector is added until this flow is
reviewed and validated in a test environment.
"""

from __future__ import annotations

import hashlib
import json
import re
import threading
from pathlib import Path
from typing import Any

from pydantic import Field, field_validator

from .....tools.base import ToolError
from ..bridge import ActionImpl, register_factory
from ..catalog import enable_approval_flow
from ..credentials import resolve_credential, valid_credential_name
from ..manifest import ActionPolicy, ToolkitPolicy, register_policy
from ..settings import StrictSettings, register_settings_model
from . import github as gh

_LABEL = re.compile(r"^[A-Za-z0-9 _.:\-]{1,50}$")
_MARKER = "bauer-idempotency"
_LEDGER_LOCK = threading.Lock()


class GithubIssueSettings(StrictSettings):
    repositories: list[str] = Field(min_length=1)
    credential: str = "GITHUB_TOKEN"
    api_url: str = "https://api.github.com"
    allowed_labels: list[str] = Field(default_factory=list)
    timeout_seconds: float = Field(default=15.0, gt=0, le=60)
    max_title_chars: int = Field(default=200, ge=10, le=256)
    max_body_chars: int = Field(default=8_000, ge=100, le=60_000)

    @field_validator("repositories")
    @classmethod
    def _repos(cls, value: list[str]) -> list[str]:
        normalized = []
        for item in value:
            text = str(item).strip()
            if not gh._REPO.match(text):
                raise ValueError("repositório deve ser 'owner/repo'")
            normalized.append(text.lower())
        return sorted(set(normalized))

    @field_validator("api_url")
    @classmethod
    def _api_url(cls, value: str) -> str:
        text = value.strip().rstrip("/")
        if not text.startswith("https://"):
            raise ValueError("api_url deve usar https")
        return text

    @field_validator("credential")
    @classmethod
    def _credential(cls, value: str) -> str:
        if not valid_credential_name(value):
            raise ValueError("nome de credencial inválido")
        return value

    @field_validator("allowed_labels")
    @classmethod
    def _labels(cls, value: list[str]) -> list[str]:
        for label in value:
            if not _LABEL.match(label):
                raise ValueError("label inválida")
        return value

    def as_read_settings(self) -> gh.GithubSettings:
        return gh.GithubSettings(
            repositories=list(self.repositories), credential=self.credential,
            api_url=self.api_url, timeout_seconds=self.timeout_seconds,
        )


def _ledger_path(policy_root: Path | None) -> Path:
    from .....paths import get_bauer_home

    base = Path(policy_root) if policy_root is not None else get_bauer_home() / "memory" / "runtime"
    return base / "agno_capabilities" / "github_issues_ledger.json"


def _ledger_get(path: Path, key: str) -> dict[str, Any] | None:
    with _LEDGER_LOCK:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        entry = data.get(key)
        return entry if isinstance(entry, dict) else None


def _ledger_put(path: Path, key: str, value: dict[str, Any]) -> None:
    with _LEDGER_LOCK:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        data[key] = value
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, sort_keys=True), encoding="utf-8")
        tmp.replace(path)


def _post(settings: GithubIssueSettings, path: str, payload: dict[str, Any]) -> dict[str, Any]:
    import httpx

    read = settings.as_read_settings()
    try:
        with gh._client(read) as client:
            response = client.post(path, json=payload)
    except httpx.TimeoutException:
        raise ToolError(
            "GitHub não respondeu no prazo; o efeito é incerto — repita a MESMA chamada "
            "(idempotente) para verificar antes de qualquer nova ação"
        ) from None
    except httpx.HTTPError as exc:
        raise ToolError(f"falha de rede ao criar issue ({type(exc).__name__})") from None
    if response.status_code in {401, 403}:
        raise ToolError(f"GitHub recusou a escrita (HTTP {response.status_code}); verifique escopo da credencial")
    if response.status_code == 404:
        raise ToolError("repositório não encontrado ou sem permissão de escrita")
    if response.status_code == 422:
        raise ToolError("GitHub rejeitou os dados da issue (HTTP 422)")
    if response.status_code >= 400:
        raise ToolError(f"GitHub respondeu HTTP {response.status_code}")
    try:
        data = response.json()
    except ValueError:
        raise ToolError("resposta malformada do GitHub após criar issue; efeito incerto") from None
    if not isinstance(data, dict) or not data.get("number"):
        raise ToolError("resposta do GitHub sem número da issue; efeito incerto")
    return data


def validate_issue_args(settings: GithubIssueSettings, args: dict[str, Any]) -> tuple[str, str, str, list[str]]:
    """Local validation shared by preflight (before approval) and execution."""
    name = gh.ensure_repo_allowed(settings.as_read_settings(), args.get("repo"))
    clean_title = str(args.get("title") or "").strip()
    if not clean_title:
        raise ToolError("title é obrigatório")
    if len(clean_title) > settings.max_title_chars:
        raise ToolError("title excede o limite configurado")
    text = str(args.get("body") or "")
    if len(text) > settings.max_body_chars:
        raise ToolError("body excede o limite configurado")
    chosen = sorted(set(args.get("labels") or []))
    if any(label not in settings.allowed_labels for label in chosen):
        raise ToolError("label fora da lista permitida")
    unknown = set(args) - {"repo", "title", "body", "labels"}
    if unknown:
        raise ToolError(f"argumentos desconhecidos: {', '.join(sorted(unknown))}")
    return name, clean_title, text, chosen


def _actions(settings: GithubIssueSettings, router_factory: Any) -> dict[str, Any]:
    def create_issue(repo: str, title: str, body: str = "", labels: list[str] | None = None) -> dict[str, Any]:
        """Cria UMA issue num repositório permitido — exige aprovação humana desta chamada exata.

        Args:
            repo: owner/repo da allowlist de escrita.
            title: Título da issue.
            body: Corpo em Markdown (opcional).
            labels: Labels permitidas pela configuração (opcional).
        """
        read = settings.as_read_settings()
        name, clean_title, text, chosen = validate_issue_args(
            settings, {"repo": repo, "title": title, "body": body, "labels": labels or []},
        )
        if not resolve_credential(settings.credential):
            raise ToolError("credencial de escrita não configurada")

        router = router_factory() if router_factory is not None else None
        run_id = getattr(router, "_runtime_run_id", None) or "no-run"
        digest = hashlib.sha256(
            json.dumps([run_id, name, clean_title, text, chosen], ensure_ascii=False).encode("utf-8")
        ).hexdigest()[:32]
        ledger = _ledger_path(getattr(router, "_policy_root", None))
        existing = _ledger_get(ledger, digest)
        if existing:
            return {**existing, "idempotent_replay": True}
        marker = f"<!-- {_MARKER}: {digest} -->"
        found = gh._get(read, "/search/issues", {"q": f'"{digest}" repo:{name} in:body', "per_page": 1})
        items = found.get("items") if isinstance(found, dict) else None
        if isinstance(items, list) and items:
            hit = items[0]
            result = {"repo": name, "number": hit.get("number"), "url": hit.get("html_url")}
            _ledger_put(ledger, digest, result)
            return {**result, "idempotent_replay": True}
        created = _post(settings, f"/repos/{name}/issues", {
            "title": clean_title,
            "body": (text + "\n\n" if text else "") + marker,
            "labels": chosen,
        })
        result = {"repo": name, "number": created.get("number"), "url": created.get("html_url")}
        _ledger_put(ledger, digest, result)
        return result

    return {"create_issue": create_issue}


def approval_summary(args: dict[str, Any], settings: GithubIssueSettings | None = None) -> str:
    """Human-facing summary shown with the approval request (target/effect/identity)."""
    repo = str(args.get("repo") or "?")
    title = str(args.get("title") or "")[:80]
    identity = settings.credential if settings is not None else "credencial configurada"
    return f"Criar issue em {repo}: \"{title}\" (identidade: {identity})"


def _factory(resolved, section, context=None) -> dict[str, ActionImpl]:
    from ..bridge import _parameters_for

    if not isinstance(section, GithubIssueSettings):
        raise ToolError("bauer.github_issues sem configuração")
    actions = _actions(section, getattr(context, "router_factory", None))
    fn = actions["create_issue"]
    return {
        "create_issue": ActionImpl(
            fn=fn,
            description=(fn.__doc__ or "").strip().split("\n\n")[0],
            parameters=_parameters_for(fn),
            timeout_seconds=int(section.timeout_seconds) * 2 + 10,
            approval_summary=lambda args: approval_summary(args, section),
            preflight=lambda args: validate_issue_args(section, args) and None,
        )
    }


register_settings_model("bauer.github_issues", GithubIssueSettings)
register_policy(
    ToolkitPolicy(
        id="bauer.github_issues",
        title="GitHub — criar issue (escrita com aprovação)",
        category="Desenvolvimento",
        description="Cria uma issue num repositório permitido, somente após aprovação humana da chamada exata.",
        source="bauer",
        actions=(ActionPolicy("create_issue", "write", "integration.write", approval_required=True),),
        requires=("httpx",),
        settings_section="bauer.github_issues",
        notes="Piloto de escrita (plano 057): idempotente por run; nenhum outro conector de escrita até revisão.",
    )
)
register_factory("bauer.github_issues", _factory)
enable_approval_flow("bauer.github_issues", "create_issue")
