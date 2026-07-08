"""Slack Bridge — canal Slack do Bauer Gateway.

Bot conversacional completo via **Socket Mode** — conexão WebSocket que o
Bauer abre PARA FORA do Slack (mesmo princípio do Discord Gateway e do
long-polling do Telegram): não precisa de URL pública/ngrok, funciona atrás
de NAT/firewall.

Setup::

    pip install 'bauer-agent[gateway]'
    bauer gateway init            # wizard: tokens, allowlist
    bauer gateway start           # todos os canais habilitados

Requisitos no Slack (https://api.slack.com/apps → Create New App):
1. **Socket Mode** (menu lateral) → habilitar. Gera um App-Level Token
   (``xapp-…``) com escopo ``connections:write`` — vai em SLACK_APP_TOKEN.
2. **OAuth & Permissions** → Bot Token Scopes: ``chat:write``, ``im:history``,
   ``im:read``, ``channels:history``, ``app_mentions:read``. Instalar o app
   no workspace gera o Bot Token (``xoxb-…``) — vai em SLACK_BOT_TOKEN.
3. **Event Subscriptions** → habilitar, inscrever em ``message.im`` (DMs) e
   ``app_mention`` (menção em canal). Opcional: ``message.channels`` se quiser
   responder em canal sem ser mencionado (``mention_only: false``).

Comportamento: responde DMs sempre; em canais responde apenas quando
mencionado (``mention_only: true``, default). Allowlists de usuário/canal no
config.yaml. Allowlist de usuários vazia NEGA todo mundo.

Protocolo (resumo): POST ``apps.connections.open`` (Bearer app token) → URL
wss de uso único → conecta → cada envelope recebido precisa de ACK
(``{"envelope_id": ...}``) em até 3s, senão o Slack reentrega. Um
``disconnect`` do servidor (ou queda da conexão) pede nova ``connections.open``.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from collections import deque
from pathlib import Path

import httpx

from .channel_base import (
    AgentBackend,
    BaseBridge,
    ChannelMessage,
    RateLimiter,
    chunk_text,
    resolve_token,
)

logger = logging.getLogger("bauer.slack")

SLACK_API = "https://slack.com/api"
MAX_MESSAGE_CHARS = 3000

# Envelopes já processados (dedup) — Slack reentrega se o ACK atrasar >3s.
_MAX_SEEN_ENVELOPES = 500


def strip_bot_mention(text: str, bot_user_id: str) -> str:
    """Remove a menção ao bot (``<@U…>``) do início/corpo da mensagem."""
    return re.sub(rf"<@{re.escape(bot_user_id)}>", "", text or "").strip()


def should_respond(
    event: dict,
    bot_user_id: str,
    *,
    mention_only: bool = True,
    allowed_channels: set[str] | None = None,
) -> bool:
    """Filtro de evento ``message``/``app_mention`` — ANTES da allowlist de usuário.

    Regras: ignora bots (inclusive a si mesmo) e subtypes (edição, join, etc.
    — só mensagem de texto plana); DM (``channel_type == "im"``) sempre passa;
    ``app_mention`` sempre passa; mensagem normal em canal só passa se
    ``mention_only`` for False (senão espera o evento ``app_mention``).
    """
    if event.get("bot_id") or event.get("user") == bot_user_id:
        return False
    if event.get("subtype"):
        return False
    if allowed_channels and str(event.get("channel", "")) not in allowed_channels:
        return False
    if event.get("type") == "app_mention":
        return True
    if event.get("channel_type") == "im":
        return True
    return not mention_only


class SlackBridge(BaseBridge):
    """Canal Slack via Socket Mode — implementação de BaseBridge."""

    name = "slack"

    def __init__(
        self,
        bot_token: str,
        app_token: str,
        backend: AgentBackend,
        allowed_users: list[str] | None = None,
        allowed_channels: list[str] | None = None,
        allow_all: bool = False,
        mention_only: bool = True,
        max_msgs_per_minute: int = 20,
    ) -> None:
        super().__init__(backend, RateLimiter(max_msgs_per_minute))
        self.bot_token = bot_token
        self.app_token = app_token
        self.allowed_users = {str(u) for u in (allowed_users or [])}
        self.allowed_channels = {str(c) for c in (allowed_channels or [])}
        self.allow_all = allow_all
        self.mention_only = mention_only
        self._http = httpx.Client(
            timeout=30,
            headers={"Authorization": f"Bearer {self.bot_token}"},
        )
        self.bot_user_id: str = ""
        self.bot_name: str = ""
        self._seen_envelopes: deque = deque(maxlen=_MAX_SEEN_ENVELOPES)

    # ── REST ───────────────────────────────────────────────────────────────

    def auth_test(self) -> dict:
        """Valida o bot token e retorna info do bot (wizard/doctor)."""
        resp = self._http.post(f"{SLACK_API}/auth.test")
        resp.raise_for_status()
        data = resp.json()
        if not data.get("ok"):
            raise RuntimeError(f"auth.test falhou: {data.get('error')}")
        return data

    def send_text(self, chat_id: str, text: str) -> None:
        for chunk in chunk_text(text, MAX_MESSAGE_CHARS):
            try:
                resp = self._http.post(
                    f"{SLACK_API}/chat.postMessage",
                    json={"channel": chat_id, "text": chunk},
                )
                resp.raise_for_status()
                data = resp.json()
                if not data.get("ok"):
                    raise RuntimeError(data.get("error", "erro desconhecido"))
            except Exception as exc:  # noqa: BLE001
                self.last_error = f"send: {exc}"
                logger.error("Falha enviando para canal %s: %s", chat_id, exc)

    # ── Auth ───────────────────────────────────────────────────────────────

    def _is_authorized(self, msg: ChannelMessage) -> bool:
        if self.allow_all:
            return True
        return msg.user_id in self.allowed_users

    # ── Socket Mode ────────────────────────────────────────────────────────

    def start(self) -> None:
        """Abre a conexão Socket Mode e processa eventos até stop()."""
        if not self.bot_token or not self.app_token:
            raise RuntimeError(
                "Token do Slack ausente. Defina SLACK_BOT_TOKEN e SLACK_APP_TOKEN "
                "no .env ou rode `bauer gateway init`."
            )
        try:
            import websockets  # noqa: F401
        except ImportError as exc:
            raise RuntimeError(
                "Slack bridge requer a lib websockets: "
                "pip install 'bauer-agent[gateway]'"
            ) from exc
        if not self.allowed_users and not self.allow_all:
            logger.warning(
                "slack.allowed_users vazio — NENHUM usuário será atendido. "
                "Rode `bauer gateway init` ou defina allow_all: true (cuidado)."
            )
        me = self.auth_test()
        self.bot_user_id = str(me.get("user_id", ""))
        self.bot_name = me.get("user", "")
        logger.info("Slack bridge online como %s (%s)", self.bot_name, self.bot_user_id)
        asyncio.run(self._run_forever())

    async def _run_forever(self) -> None:
        """Loop de (re)conexão: cada queda pede uma nova URL Socket Mode."""
        backoff = 2.0
        while not self.stopped:
            try:
                await self._run_connection()
                backoff = 2.0
            except asyncio.CancelledError:
                break
            except Exception as exc:  # noqa: BLE001
                if self.stopped:
                    break
                self.last_error = str(exc)
                logger.warning("Socket Mode caiu (%s) — reconectando em %.0fs", exc, backoff)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60.0)
        logger.info("Slack bridge parado.")

    def _open_connection_url(self) -> str:
        resp = self._http.post(f"{SLACK_API}/apps.connections.open",
                                headers={"Authorization": f"Bearer {self.app_token}"})
        resp.raise_for_status()
        data = resp.json()
        if not data.get("ok"):
            raise RuntimeError(f"apps.connections.open falhou: {data.get('error')}")
        return data["url"]

    async def _run_connection(self) -> None:
        import websockets

        url = await asyncio.to_thread(self._open_connection_url)
        async with websockets.connect(url, max_size=10 * 1024 * 1024) as ws:

            async def stop_watcher() -> None:
                while not self.stopped:
                    await asyncio.sleep(0.5)
                await ws.close()

            watch_task = asyncio.create_task(stop_watcher())
            try:
                async for raw in ws:
                    payload = json.loads(raw)
                    ptype = payload.get("type")
                    envelope_id = payload.get("envelope_id")
                    if envelope_id:
                        await ws.send(json.dumps({"envelope_id": envelope_id}))
                    if ptype == "disconnect":
                        return  # servidor pediu reconexão — pega URL nova
                    if ptype == "events_api":
                        await self._on_events_api(payload, envelope_id)
            finally:
                watch_task.cancel()

    async def _on_events_api(self, payload: dict, envelope_id: str | None) -> None:
        if envelope_id:
            if envelope_id in self._seen_envelopes:
                return  # reentrega do Slack (ACK anterior demorou) — já tratado
            self._seen_envelopes.append(envelope_id)

        event = (payload.get("payload") or {}).get("event") or {}
        if event.get("type") not in ("message", "app_mention"):
            return
        if not should_respond(
            event, self.bot_user_id,
            mention_only=self.mention_only,
            allowed_channels=self.allowed_channels,
        ):
            return
        channel = str(event.get("channel", ""))
        text = strip_bot_mention(event.get("text", ""), self.bot_user_id)
        if not text:
            return
        msg = ChannelMessage(
            channel="slack",
            user_id=str(event.get("user", "")),
            chat_id=channel,
            text=text,
            raw=event,
        )
        # backend.process é bloqueante (LLM + tools) — vai para thread para não
        # segurar o loop de eventos (o ACK já foi mandado acima).
        response = await asyncio.to_thread(self.handle_message, msg)
        if response:
            await asyncio.to_thread(self.send_text, channel, response)

    def stop(self) -> None:
        super().stop()
        try:
            self._http.close()
        except Exception:  # noqa: BLE001
            pass


def build_bridge_from_config(cfg, backend: AgentBackend | None = None) -> SlackBridge:
    """Monta o SlackBridge a partir de um BauerConfig validado."""
    bot_token = resolve_token(cfg.slack.bot_token, "SLACK_BOT_TOKEN")
    app_token = resolve_token(cfg.slack.app_token, "SLACK_APP_TOKEN")
    return SlackBridge(
        bot_token=bot_token,
        app_token=app_token,
        backend=backend or AgentBackend(),
        allowed_users=cfg.slack.allowed_users,
        allowed_channels=cfg.slack.allowed_channels,
        allow_all=cfg.slack.allow_all,
        mention_only=cfg.slack.mention_only,
        max_msgs_per_minute=cfg.slack.max_msgs_per_minute,
    )


def run_bridge(config_path: str | Path = "config.yaml") -> None:
    """Entry point standalone: python -m bauer.slack_bridge."""
    from .config_loader import load_config

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    cfg = load_config(config_path)
    bridge = build_bridge_from_config(cfg)
    try:
        bridge.start()
    except KeyboardInterrupt:
        bridge.stop()


if __name__ == "__main__":
    run_bridge()
