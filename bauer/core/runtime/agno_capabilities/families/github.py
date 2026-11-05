"""Sprint 51 — GitHub somente leitura (plano 057, sprint "19").

Two entries, same scope settings:

* ``bauer.github`` — Bauer-native read-only adapter over the GitHub REST API
  (``httpx``, already a Bauer dependency). Usable without extra packages.
* ``agno.github`` — the SDK ``GithubTools`` restricted to a reviewed read-only
  subset; needs ``pygithub`` (optional). Every call is checked against the
  repository allowlist before the SDK sees it.

Write operations (create/edit/comment/delete/merge…) are not classified here:
they cannot be granted. The first write connector is a separate capability
(sprint 57) with its own approval flow.
"""

from __future__ import annotations

import base64
import re
from typing import Any, Callable

from pydantic import Field, field_validator

from .....tools.base import ToolError
from ..bridge import ActionImpl, register_factory, sdk_toolkit_factory
from ..credentials import resolve_credential, valid_credential_name
from ..manifest import ActionPolicy, ToolkitPolicy, register_policy
from ..settings import StrictSettings, register_settings_model

_REPO = re.compile(r"^[A-Za-z0-9_.\-]{1,100}/[A-Za-z0-9_.\-]{1,100}$")
#: Search qualifiers that could widen the scope beyond the allowlisted repo.
_SCOPE_QUALIFIERS = re.compile(r"\b(?:repo|org|user|owner|is:private|is:public)\s*:\s*\S+", re.IGNORECASE)


class GithubSettings(StrictSettings):
    repositories: list[str] = Field(min_length=1)
    credential: str = "GITHUB_TOKEN"
    api_url: str = "https://api.github.com"
    timeout_seconds: float = Field(default=15.0, gt=0, le=60)
    max_items: int = Field(default=30, ge=1, le=100)
    max_file_bytes: int = Field(default=200_000, ge=1_000, le=2_000_000)

    @field_validator("repositories")
    @classmethod
    def _repos(cls, value: list[str]) -> list[str]:
        normalized = []
        for item in value:
            text = str(item).strip()
            if not _REPO.match(text):
                raise ValueError("repositório deve ser 'owner/repo'")
            normalized.append(text.lower())
        return sorted(set(normalized))

    @field_validator("credential")
    @classmethod
    def _credential(cls, value: str) -> str:
        if not valid_credential_name(value):
            raise ValueError("nome de credencial inválido")
        return value

    @field_validator("api_url")
    @classmethod
    def _api_url(cls, value: str) -> str:
        text = value.strip().rstrip("/")
        if not text.startswith("https://"):
            raise ValueError("api_url deve usar https")
        return text


def ensure_repo_allowed(settings: GithubSettings, repo: Any) -> str:
    text = str(repo or "").strip()
    if not _REPO.match(text) or text.lower() not in settings.repositories:
        raise ToolError("repositório fora da allowlist configurada para esta capability")
    return text.lower()


# ── Bauer-native adapter ─────────────────────────────────────────────────────

#: Test seam: an ``httpx`` transport (e.g. ``MockTransport``). ``None`` = real.
_TRANSPORT: Any = None


def _client(settings: GithubSettings):
    import httpx

    from .....http_shared import shared_ssl_context

    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "bauer-agent",
    }
    token = resolve_credential(settings.credential)
    if token:
        headers["Authorization"] = f"Bearer {token}"
    kwargs: dict[str, Any] = {
        "base_url": settings.api_url,
        "headers": headers,
        "timeout": settings.timeout_seconds,
        "follow_redirects": False,
    }
    if _TRANSPORT is not None:
        kwargs["transport"] = _TRANSPORT
    else:
        kwargs["verify"] = shared_ssl_context()
    return httpx.Client(**kwargs)


def _get(settings: GithubSettings, path: str, params: dict[str, Any] | None = None) -> Any:
    import httpx

    try:
        with _client(settings) as client:
            response = client.get(path, params=params)
    except httpx.TimeoutException:
        raise ToolError("GitHub não respondeu dentro do timeout configurado") from None
    except httpx.HTTPError as exc:
        raise ToolError(f"falha de rede ao consultar GitHub ({type(exc).__name__})") from None
    if response.status_code == 404:
        raise ToolError("recurso não encontrado no GitHub (ou sem permissão de leitura)")
    if response.status_code in {401, 403}:
        raise ToolError(f"GitHub recusou a leitura (HTTP {response.status_code}); verifique a credencial")
    if response.status_code >= 400:
        raise ToolError(f"GitHub respondeu HTTP {response.status_code}")
    try:
        return response.json()
    except ValueError:
        raise ToolError("resposta malformada do GitHub (JSON inválido)") from None


def _pick(item: Any, *fields: str) -> dict[str, Any]:
    if not isinstance(item, dict):
        raise ToolError("resposta malformada do GitHub")
    out: dict[str, Any] = {}
    for field in fields:
        value: Any = item
        for part in field.split("."):
            value = value.get(part) if isinstance(value, dict) else None
        out[field] = value
    return out


def _limit(settings: GithubSettings, value: Any) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = settings.max_items
    return max(1, min(number, settings.max_items))


def _list(payload: Any) -> list[Any]:
    if not isinstance(payload, list):
        raise ToolError("resposta malformada do GitHub (lista esperada)")
    return payload


def _native_actions(settings: GithubSettings) -> dict[str, Callable[..., Any]]:
    def get_repository(repo: str) -> dict[str, Any]:
        """Metadados públicos de um repositório da allowlist.

        Args:
            repo: Repositório no formato owner/repo (precisa estar na allowlist).
        """
        name = ensure_repo_allowed(settings, repo)
        return _pick(
            _get(settings, f"/repos/{name}"),
            "full_name", "description", "default_branch", "visibility", "language",
            "stargazers_count", "open_issues_count", "updated_at", "html_url",
        )

    def list_issues(repo: str, state: str = "open", limit: int = 20) -> list[dict[str, Any]]:
        """Lista issues (sem PRs) de um repositório da allowlist.

        Args:
            repo: owner/repo da allowlist.
            state: open, closed ou all.
            limit: Máximo de itens (limitado pela configuração).
        """
        name = ensure_repo_allowed(settings, repo)
        if state not in {"open", "closed", "all"}:
            raise ToolError("state deve ser open, closed ou all")
        items = _list(_get(settings, f"/repos/{name}/issues", {"state": state, "per_page": _limit(settings, limit)}))
        return [
            _pick(item, "number", "title", "state", "user.login", "labels", "updated_at", "html_url")
            for item in items
            if isinstance(item, dict) and "pull_request" not in item
        ]

    def get_issue(repo: str, number: int) -> dict[str, Any]:
        """Lê uma issue de um repositório da allowlist.

        Args:
            repo: owner/repo da allowlist.
            number: Número da issue.
        """
        name = ensure_repo_allowed(settings, repo)
        return _pick(
            _get(settings, f"/repos/{name}/issues/{int(number)}"),
            "number", "title", "state", "body", "user.login", "comments", "created_at", "updated_at", "html_url",
        )

    def list_pull_requests(repo: str, state: str = "open", limit: int = 20) -> list[dict[str, Any]]:
        """Lista pull requests de um repositório da allowlist.

        Args:
            repo: owner/repo da allowlist.
            state: open, closed ou all.
            limit: Máximo de itens (limitado pela configuração).
        """
        name = ensure_repo_allowed(settings, repo)
        if state not in {"open", "closed", "all"}:
            raise ToolError("state deve ser open, closed ou all")
        items = _list(_get(settings, f"/repos/{name}/pulls", {"state": state, "per_page": _limit(settings, limit)}))
        return [_pick(item, "number", "title", "state", "user.login", "head.ref", "base.ref", "html_url") for item in items]

    def get_pull_request(repo: str, number: int) -> dict[str, Any]:
        """Lê um pull request de um repositório da allowlist.

        Args:
            repo: owner/repo da allowlist.
            number: Número do PR.
        """
        name = ensure_repo_allowed(settings, repo)
        return _pick(
            _get(settings, f"/repos/{name}/pulls/{int(number)}"),
            "number", "title", "state", "body", "user.login", "head.ref", "base.ref", "merged",
            "additions", "deletions", "changed_files", "html_url",
        )

    def list_commits(repo: str, ref: str = "", path: str = "", limit: int = 20) -> list[dict[str, Any]]:
        """Lista commits recentes de um repositório da allowlist.

        Args:
            repo: owner/repo da allowlist.
            ref: Branch/tag/SHA opcional.
            path: Filtra commits que tocam este caminho (opcional).
            limit: Máximo de itens (limitado pela configuração).
        """
        name = ensure_repo_allowed(settings, repo)
        params: dict[str, Any] = {"per_page": _limit(settings, limit)}
        if ref:
            params["sha"] = ref
        if path:
            params["path"] = _safe_path(path)
        items = _list(_get(settings, f"/repos/{name}/commits", params))
        return [_pick(item, "sha", "commit.message", "commit.author.name", "commit.author.date", "html_url") for item in items]

    def get_file(repo: str, path: str, ref: str = "") -> dict[str, Any]:
        """Lê um arquivo de texto de um repositório da allowlist.

        Args:
            repo: owner/repo da allowlist.
            path: Caminho do arquivo dentro do repositório.
            ref: Branch/tag/SHA opcional.
        """
        name = ensure_repo_allowed(settings, repo)
        payload = _get(settings, f"/repos/{name}/contents/{_safe_path(path)}", {"ref": ref} if ref else None)
        if not isinstance(payload, dict) or payload.get("type") != "file":
            raise ToolError("caminho não é um arquivo (use list_commits ou leia arquivos individuais)")
        size = int(payload.get("size") or 0)
        if size > settings.max_file_bytes:
            raise ToolError(f"arquivo excede o limite configurado ({settings.max_file_bytes} bytes)")
        try:
            content = base64.b64decode(str(payload.get("content") or ""), validate=False).decode("utf-8")
        except (ValueError, UnicodeDecodeError):
            raise ToolError("arquivo não é texto UTF-8") from None
        return {"path": payload.get("path"), "sha": payload.get("sha"), "size": size, "content": content}

    def search_issues(repo: str, query: str, limit: int = 20) -> list[dict[str, Any]]:
        """Busca issues/PRs dentro de UM repositório da allowlist.

        Args:
            repo: owner/repo da allowlist.
            query: Termos de busca (qualificadores de escopo são removidos).
            limit: Máximo de itens (limitado pela configuração).
        """
        name = ensure_repo_allowed(settings, repo)
        terms = _SCOPE_QUALIFIERS.sub(" ", str(query or "")).strip()[:256]
        if not terms:
            raise ToolError("query vazia")
        payload = _get(settings, "/search/issues", {"q": f"{terms} repo:{name}", "per_page": _limit(settings, limit)})
        items = payload.get("items") if isinstance(payload, dict) else None
        return [_pick(item, "number", "title", "state", "html_url") for item in _list(items)]

    return {
        "get_repository": get_repository,
        "list_issues": list_issues,
        "get_issue": get_issue,
        "list_pull_requests": list_pull_requests,
        "get_pull_request": get_pull_request,
        "list_commits": list_commits,
        "get_file": get_file,
        "search_issues": search_issues,
    }


def _safe_path(path: Any) -> str:
    text = str(path or "").strip().lstrip("/")
    parts = text.split("/")
    if not text or any(part in {"", ".", ".."} for part in parts) or "\\" in text:
        raise ToolError("caminho inválido dentro do repositório")
    return text


def _native_factory(resolved, section) -> dict[str, ActionImpl]:
    from ..bridge import _parameters_for

    if not isinstance(section, GithubSettings):
        raise ToolError("bauer.github sem configuração")
    actions = _native_actions(section)
    impls: dict[str, ActionImpl] = {}
    for action in resolved.actions:
        fn = actions[action.name]
        doc = (fn.__doc__ or "").strip().split("\n\n")[0]
        impls[action.name] = ActionImpl(
            fn=fn, description=doc, parameters=_parameters_for(fn),
            timeout_seconds=int(section.timeout_seconds) + 5,
        )
    return impls


def _read(name: str, description: str = "") -> ActionPolicy:
    return ActionPolicy(name, "read", "network.http", description)


register_settings_model("bauer.github", GithubSettings)
register_policy(
    ToolkitPolicy(
        id="bauer.github",
        title="GitHub (leitura)",
        category="Desenvolvimento",
        description="Leitura de repositórios, issues, PRs, commits e arquivos de uma allowlist de repositórios.",
        source="bauer",
        actions=tuple(
            _read(name)
            for name in (
                "get_repository", "list_issues", "get_issue", "list_pull_requests",
                "get_pull_request", "list_commits", "get_file", "search_issues",
            )
        ),
        requires=("httpx",),
        settings_section="bauer.github",
        notes="Credencial opcional (nome em 'credential'; env ou bauer credential). Sem ela, só repositórios públicos.",
    )
)
register_factory("bauer.github", _native_factory)


# ── SDK GithubTools, read-only subset ───────────────────────────────────────

_SDK_READ_ACTIONS = (
    "get_repository",
    "get_repository_languages",
    "get_pull_request",
    "get_pull_request_changes",
    "get_pull_requests",
    "get_pull_request_comments",
    "list_issues",
    "get_issue",
    "list_issue_comments",
    "list_branches",
    "get_file_content",
    "get_directory_content",
)


def _sdk_factory(resolved, section) -> dict[str, ActionImpl]:
    if not isinstance(section, GithubSettings):
        raise ToolError("agno.github sem configuração")
    token = resolve_credential(section.credential)
    base_url = None if section.api_url == "https://api.github.com" else section.api_url
    impls = sdk_toolkit_factory(resolved, section, init_kwargs={"access_token": token, "base_url": base_url})
    for name, impl in impls.items():
        impl.fn = _repo_guard(section, impl.fn)
        impl.timeout_seconds = int(section.timeout_seconds) + 5
    return impls


def _repo_guard(settings: GithubSettings, fn: Callable[..., Any]) -> Callable[..., Any]:
    def guarded(**kwargs: Any) -> Any:
        if "repo_name" not in kwargs:
            raise ToolError("repo_name é obrigatório")
        ensure_repo_allowed(settings, kwargs["repo_name"])
        return fn(**kwargs)

    return guarded


register_settings_model("agno.github", GithubSettings)
register_policy(
    ToolkitPolicy(
        id="agno.github",
        title="GitHub (SDK Agno, leitura)",
        category="Desenvolvimento",
        description="GithubTools do Agno restrito a leitura e à allowlist de repositórios.",
        actions=tuple(_read(name) for name in _SDK_READ_ACTIONS),
        module="agno.tools.github",
        class_name="GithubTools",
        requires=("github",),
        install_hint="uv pip install pygithub",
        settings_section="agno.github",
        notes="Escrita (create/edit/comment/delete) não é classificada: indisponível por construção.",
    )
)
register_factory("agno.github", _sdk_factory)
