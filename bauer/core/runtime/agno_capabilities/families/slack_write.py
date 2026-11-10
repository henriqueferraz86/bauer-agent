"""Sprint 75 — postar mensagem no Slack sob aprovação (plano 067).

``bauer.slack_messages:post_message`` follows the sprint 57 pattern
(``bauer.github_issues``):

* separate capability, ``needs_configuration`` until the operator writes a
  ``bauer.slack_messages`` section (channel-ID allowlist, credential name) in
  ``$BAUER_HOME/agno_capabilities.yaml``; never in a default grant;
* risk ``write`` / operation ``integration.write`` ⇒ PolicyEngine floor
  ``ask``; audit mode never auto-approves; policy off refuses (bridge);
* local preflight BEFORE the approval request: ``--local``, channel
  allowlist, size limits, broadcast mentions, unknown args, secret material,
  credential presence;
* approval card: channel, thread, text excerpt and credential *name*;
* idempotent per run: ledger reservation + message ``metadata`` carrying the
  key, looked up in the channel history when a previous attempt is uncertain.

Bot token from ``bauer credential`` or the environment (``resolve_credential``,
by name). ``bauer auth`` holds *model provider* credentials and is not
consulted.
"""

from __future__ import annotations

import re
from typing import Any

from pydantic import Field, field_validator

from .....tools.base import ToolError
from ..bridge import ActionImpl, register_factory
from ..catalog import enable_approval_flow
from ..credentials import credential_available, resolve_credential, valid_credential_name
from ..manifest import ActionPolicy, ToolkitPolicy, register_policy
from ..settings import StrictSettings, register_settings_model
from . import write_common as wc

SERVICE = "Slack"
API_URL = "https://slack.com/api"
API_HOST = "slack.com"
_CHANNEL = re.compile(r"^[CG][A-Z0-9]{6,20}$")
_THREAD_TS = re.compile(r"^\d{9,11}\.\d{1,6}$")
_ERROR_CODE = re.compile(r"^[a-z_]{1,60}$")
#: ``<!channel>``, ``<!here>``, ``<!everyone>`` and user-group pings.
_BROADCAST = re.compile(r"<!(?:channel|here|everyone|subteam\^)[^>]*>", re.IGNORECASE)
_EVENT_TYPE = "bauer_idempotency"
_ARGS = {"channel", "text", "thread_ts"}


class SlackMessageSettings(StrictSettings):
    #: Channel IDs (``C…``/``G…``) — names can be renamed; IDs cannot.
    channels: list[str] = Field(min_length=1)
    credential: str = "SLACK_BOT_TOKEN"
    timeout_seconds: float = Field(default=15.0, gt=0, le=60)
    max_text_chars: int = Field(default=4_000, ge=100, le=40_000)
    allow_broadcast_mentions: bool = False

    @field_validator("channels")
    @classmethod
    def _channels(cls, value: list[str]) -> list[str]:
        normalized = []
        for item in value:
            text = str(item).strip().upper()
            if not _CHANNEL.match(text):
                raise ValueError("canal deve ser um ID do Slack (C…/G…)")
            normalized.append(text)
        return sorted(set(normalized))

    @field_validator("credential")
    @classmethod
    def _credential(cls, value: str) -> str:
        if not valid_credential_name(value):
            raise ValueError("nome de credencial inválido")
        return value


def validate_message_args(settings: SlackMessageSettings, args: dict[str, Any]) -> tuple[str, str, str]:
    """Local validation shared by preflight (before approval) and execution."""
    wc.ensure_local_allowed(SERVICE, API_HOST)
    wc.reject_unknown(args, _ARGS)
    channel = str(args.get("channel") or "").strip().upper()
    if not _CHANNEL.match(channel) or channel not in settings.channels:
        raise ToolError("canal fora da allowlist configurada para esta capability")
    text = str(args.get("text") or "")
    if not text.strip():
        raise ToolError("text é obrigatório")
    if len(text) > settings.max_text_chars:
        raise ToolError("text excede o limite configurado")
    if not settings.allow_broadcast_mentions and _BROADCAST.search(text):
        raise ToolError("menção de broadcast (<!channel>/<!here>/<!everyone>/grupo) não permitida pela configuração")
    thread_ts = str(args.get("thread_ts") or "").strip()
    if thread_ts and not _THREAD_TS.match(thread_ts):
        raise ToolError("thread_ts inválido")
    return channel, text, thread_ts


def preflight(settings: SlackMessageSettings, args: dict[str, Any]) -> None:
    validate_message_args(settings, args)
    wc.refuse_secret_material(args)
    if not credential_available(settings.credential):
        raise ToolError("credencial de escrita não configurada")


def _client(settings: SlackMessageSettings, token: str):
    return wc.http_client(
        base_url=API_URL,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json; charset=utf-8"},
        timeout=settings.timeout_seconds,
    )


def _slack_error(data: Any) -> str:
    code = str(data.get("error") or "") if isinstance(data, dict) else ""
    if code in {"invalid_auth", "not_authed", "account_inactive", "token_revoked", "token_expired", "missing_scope"}:
        return f"Slack recusou a credencial ({code})"
    if code in {"not_in_channel", "channel_not_found", "is_archived"}:
        return f"canal inacessível para o bot ({code})"
    if _ERROR_CODE.match(code):
        return f"Slack recusou a mensagem ({code})"
    return "Slack recusou a mensagem"


def _actions(settings: SlackMessageSettings, router_factory: Any) -> dict[str, Any]:
    def post_message(channel: str, text: str, thread_ts: str = "") -> dict[str, Any]:
        """Posta UMA mensagem num canal permitido do Slack — exige aprovação humana desta chamada exata.

        Args:
            channel: ID do canal (C…/G…) da allowlist configurada.
            text: Texto da mensagem (mrkdwn do Slack).
            thread_ts: Timestamp da mensagem-pai para responder em thread (opcional).
        """
        clean_channel, clean_text, clean_ts = validate_message_args(
            settings, {"channel": channel, "text": text, "thread_ts": thread_ts},
        )
        token = resolve_credential(settings.credential)
        if not token:
            raise ToolError("credencial de escrita não configurada")
        router = wc.router_of(router_factory)
        key = wc.idempotency_key(router, "slack_messages", clean_channel, clean_text, clean_ts)
        ledger = wc.WriteLedger(getattr(router, "_policy_root", None), "slack_messages")

        def perform() -> dict[str, Any]:
            payload: dict[str, Any] = {
                "channel": clean_channel,
                "text": clean_text,
                "unfurl_links": False,
                "unfurl_media": False,
                "metadata": {"event_type": _EVENT_TYPE, "event_payload": {"idempotency_key": key}},
            }
            if clean_ts:
                payload["thread_ts"] = clean_ts
            with _client(settings, token) as client:
                response = wc.send(client, "POST", "/chat.postMessage", service=SERVICE, json=payload)
            wc.classify_status(response.status_code, service=SERVICE)
            data = wc.json_body(response, service=SERVICE)
            if not isinstance(data, dict):
                raise wc.Uncertain("resposta malformada do Slack; o efeito é incerto")
            if data.get("ok") is not True:
                if str(data.get("error") or "") == "ratelimited":
                    raise wc.NotSent("Slack: limite de requisições atingido; nada foi enviado")
                raise wc.NotSent(_slack_error(data))
            if not data.get("ts"):
                raise wc.Uncertain("resposta do Slack sem ts da mensagem; o efeito é incerto")
            return {"channel": clean_channel, "ts": str(data.get("ts")), "thread_ts": clean_ts or None}

        def lookup(reserved_at: float) -> dict[str, Any] | None:
            params: dict[str, Any] = {"channel": clean_channel, "limit": 200, "include_all_metadata": "true"}
            if clean_ts:
                method, params["ts"] = "/conversations.replies", clean_ts
            else:
                method = "/conversations.history"
                if reserved_at:
                    params["oldest"] = f"{max(0.0, reserved_at - 60):.6f}"
            with _client(settings, token) as client:
                response = wc.read(client, "GET", method, service=SERVICE, params=params)
            if response.status_code >= 400:
                raise ToolError(f"Slack respondeu HTTP {response.status_code} ao verificar a chamada anterior")
            data = wc.json_body(response, service=SERVICE, uncertain=False)
            if not isinstance(data, dict) or data.get("ok") is not True:
                raise ToolError(_slack_error(data) + " ao verificar a chamada anterior (precisa de channels:history)")
            for message in data.get("messages") or []:
                metadata = message.get("metadata") if isinstance(message, dict) else None
                event_payload = metadata.get("event_payload") if isinstance(metadata, dict) else None
                if isinstance(event_payload, dict) and event_payload.get("idempotency_key") == key:
                    return {"channel": clean_channel, "ts": str(message.get("ts")), "thread_ts": clean_ts or None}
            return None

        return wc.execute_once(ledger, key, service=SERVICE, perform=perform, lookup=lookup)

    return {"post_message": post_message}


def approval_summary(args: dict[str, Any], settings: SlackMessageSettings | None = None) -> str:
    """Approval card: target, effect, identity — never the token."""
    channel = wc.one_line(args.get("channel") or "?", 30)
    thread = wc.one_line(args.get("thread_ts") or "", 20)
    identity = settings.credential if settings is not None else "credencial configurada"
    where = f"{channel} (thread {thread})" if thread else channel
    return wc.safe_summary(
        f"Postar mensagem no Slack em {where}: \"{wc.one_line(args.get('text'))}\" (identidade: {identity})"
    )


def _factory(resolved, section, context=None) -> dict[str, ActionImpl]:
    from ..bridge import _parameters_for

    if not isinstance(section, SlackMessageSettings):
        raise ToolError("bauer.slack_messages sem configuração")
    fn = _actions(section, getattr(context, "router_factory", None))["post_message"]
    return {
        "post_message": ActionImpl(
            fn=fn,
            description=(fn.__doc__ or "").strip().split("\n\n")[0],
            parameters=_parameters_for(fn),
            timeout_seconds=int(section.timeout_seconds) * 2 + 10,
            approval_summary=lambda args: approval_summary(args, section),
            preflight=lambda args: preflight(section, args),
        )
    }


register_settings_model("bauer.slack_messages", SlackMessageSettings)
register_policy(
    ToolkitPolicy(
        id="bauer.slack_messages",
        title="Slack — postar mensagem (escrita com aprovação)",
        category="Mensageria e colaboração",
        description="Posta uma mensagem num canal permitido do Slack, somente após aprovação humana da chamada exata.",
        source="bauer",
        actions=(ActionPolicy("post_message", "write", "integration.write", approval_required=True),),
        requires=("httpx",),
        settings_section="bauer.slack_messages",
        notes=(
            "Sprint 75 (plano 067): allowlist por ID de canal; idempotente por run (ledger + metadata da "
            "mensagem); broadcast bloqueado por padrão; recusado em --local (serviço em nuvem)."
        ),
    )
)
register_factory("bauer.slack_messages", _factory)
enable_approval_flow("bauer.slack_messages", "post_message")
