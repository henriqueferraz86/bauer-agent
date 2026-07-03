"""Discord Bridge — canal Discord do Bauer Gateway.

Bot conversacional completo via Discord Gateway WebSocket v10 — implementado
direto sobre a lib ``websockets`` (extra ``[gateway]``), sem discord.py.

Setup::

    pip install 'bauer-agent[gateway]'
    bauer gateway init            # wizard: token, allowlist
    bauer discord start           # só este canal
    bauer gateway start           # todos os canais habilitados

Requisitos no Discord Developer Portal (https://discord.com/developers):
1. Criar Application → Bot → copiar o token (DISCORD_BOT_TOKEN no .env).
2. Aba Bot → habilitar **MESSAGE CONTENT INTENT** (sem isso o bot recebe
   mensagens com content vazio).
3. Convidar o bot: OAuth2 URL Generator → scopes ``bot`` → permissões
   "Send Messages" + "Read Message History".

Comportamento: responde DMs sempre; em servidores (guilds) responde apenas
quando mencionado (``mention_only: true``, default). Allowlists de usuário/
guild/canal no config.yaml. Allowlist de usuários vazia NEGA todo mundo.

Protocolo (resumo): HELLO (op 10) → IDENTIFY (op 2) → heartbeats (op 1) ↔
ACK (op 11); eventos via DISPATCH (op 0). Reconexão: RECONNECT (op 7) →
RESUME (op 6); INVALID_SESSION (op 9) → re-IDENTIFY com backoff.
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
import re
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

logger = logging.getLogger("bauer.discord")

DISCORD_API = "https://discord.com/api/v10"
MAX_MESSAGE_CHARS = 2000

# Gateway opcodes
OP_DISPATCH = 0
OP_HEARTBEAT = 1
OP_IDENTIFY = 2
OP_RESUME = 6
OP_RECONNECT = 7
OP_INVALID_SESSION = 9
OP_HELLO = 10
OP_HEARTBEAT_ACK = 11


def compute_intents() -> int:
    """GUILDS | GUILD_MESSAGES | DIRECT_MESSAGES | MESSAGE_CONTENT."""
    return (1 << 0) | (1 << 9) | (1 << 12) | (1 << 15)


def is_dm(event_data: dict) -> bool:
    """MESSAGE_CREATE sem guild_id é mensagem direta."""
    return "guild_id" not in event_data


def mentions_bot(event_data: dict, bot_id: str) -> bool:
    if any(m.get("id") == bot_id for m in event_data.get("mentions", [])):
        return True
    # Menção crua no texto (<@id> ou <@!id>) — alguns clients não populam mentions
    content = event_data.get("content", "") or ""
    return bool(re.search(rf"<@!?{re.escape(bot_id)}>", content))


def strip_bot_mention(content: str, bot_id: str) -> str:
    """Remove a menção ao bot do início/corpo da mensagem."""
    return re.sub(rf"<@!?{re.escape(bot_id)}>", "", content or "").strip()


def should_respond(
    event_data: dict,
    bot_id: str,
    *,
    mention_only: bool = True,
    allowed_guilds: set[str] | None = None,
    allowed_channels: set[str] | None = None,
) -> bool:
    """Filtro de MESSAGE_CREATE — ANTES da allowlist de usuário.

    Regras: ignora bots (inclusive a si mesmo); DM sempre passa; em guild,
    respeita allowlists de guild/canal e (se mention_only) exige menção.
    """
    author = event_data.get("author") or {}
    if author.get("bot") or author.get("id") == bot_id:
        return False
    if is_dm(event_data):
        return True
    if allowed_guilds and str(event_data.get("guild_id")) not in allowed_guilds:
        return False
    if allowed_channels and str(event_data.get("channel_id")) not in allowed_channels:
        return False
    if mention_only and not mentions_bot(event_data, bot_id):
        return False
    return True


class DiscordBridge(BaseBridge):
    """Canal Discord via Gateway WebSocket — implementação de BaseBridge."""

    name = "discord"

    def __init__(
        self,
        token: str,
        backend: AgentBackend,
        allowed_users: list[str] | None = None,
        allowed_guilds: list[str] | None = None,
        allowed_channels: list[str] | None = None,
        allow_all: bool = False,
        mention_only: bool = True,
        max_msgs_per_minute: int = 20,
    ) -> None:
        super().__init__(backend, RateLimiter(max_msgs_per_minute))
        self.token = token
        self.allowed_users = {str(u) for u in (allowed_users or [])}
        self.allowed_guilds = {str(g) for g in (allowed_guilds or [])}
        self.allowed_channels = {str(c) for c in (allowed_channels or [])}
        self.allow_all = allow_all
        self.mention_only = mention_only
        self._http = httpx.Client(
            timeout=30,
            headers={"Authorization": f"Bot {self.token}"},
        )
        self.bot_id: str = ""
        self.bot_name: str = ""
        # Estado do gateway (para RESUME)
        self._session_id: str = ""
        self._resume_url: str = ""
        self._seq: int | None = None

    # ── REST ───────────────────────────────────────────────────────────────

    def get_me(self) -> dict:
        """Valida o token e retorna info do bot (wizard/doctor)."""
        resp = self._http.get(f"{DISCORD_API}/users/@me")
        resp.raise_for_status()
        return resp.json()

    def send_text(self, chat_id: str, text: str) -> None:
        for chunk in chunk_text(text, MAX_MESSAGE_CHARS):
            try:
                resp = self._http.post(
                    f"{DISCORD_API}/channels/{chat_id}/messages",
                    json={"content": chunk},
                )
                resp.raise_for_status()
            except Exception as exc:  # noqa: BLE001
                self.last_error = f"send: {exc}"
                logger.error("Falha enviando para canal %s: %s", chat_id, exc)

    def _send_typing(self, chat_id: str) -> None:
        try:
            self._http.post(f"{DISCORD_API}/channels/{chat_id}/typing")
        except Exception:  # noqa: BLE001 — cosmético
            pass

    # ── Auth ───────────────────────────────────────────────────────────────

    def _is_authorized(self, msg: ChannelMessage) -> bool:
        if self.allow_all:
            return True
        return msg.user_id in self.allowed_users

    # ── Gateway WebSocket ──────────────────────────────────────────────────

    def start(self) -> None:
        """Conecta ao Gateway e processa eventos até stop()."""
        if not self.token:
            raise RuntimeError(
                "Token do Discord ausente. Defina DISCORD_BOT_TOKEN no .env "
                "ou rode `bauer gateway init`."
            )
        try:
            import websockets  # noqa: F401
        except ImportError as exc:
            raise RuntimeError(
                "Discord bridge requer a lib websockets: "
                "pip install 'bauer-agent[gateway]'"
            ) from exc
        if not self.allowed_users and not self.allow_all:
            logger.warning(
                "discord.allowed_users vazio — NENHUM usuário será atendido. "
                "Rode `bauer gateway init` ou defina allow_all: true (cuidado)."
            )
        me = self.get_me()
        self.bot_id = str(me.get("id", ""))
        self.bot_name = me.get("username", "")
        logger.info("Discord bridge online como %s (%s)", self.bot_name, self.bot_id)
        asyncio.run(self._run_forever())

    async def _run_forever(self) -> None:
        """Loop de (re)conexão: cada queda decide RESUME vs re-IDENTIFY."""
        backoff = 2.0
        resume = False
        while not self.stopped:
            try:
                resume = await self._run_connection(resume=resume)
                backoff = 2.0
            except asyncio.CancelledError:
                break
            except Exception as exc:  # noqa: BLE001
                if self.stopped:
                    break
                self.last_error = str(exc)
                logger.warning("Gateway caiu (%s) — reconectando em %.0fs", exc, backoff)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60.0)
        logger.info("Discord bridge parado.")

    def _gateway_url(self, resume: bool) -> str:
        if resume and self._resume_url:
            base = self._resume_url
        else:
            resp = self._http.get(f"{DISCORD_API}/gateway/bot")
            resp.raise_for_status()
            base = resp.json()["url"]
        return f"{base}?v=10&encoding=json"

    async def _run_connection(self, resume: bool) -> bool:
        """Uma conexão WS completa. Retorna True se a próxima deve dar RESUME."""
        import websockets

        url = self._gateway_url(resume)
        async with websockets.connect(url, max_size=10 * 1024 * 1024) as ws:
            # 1. HELLO
            hello = json.loads(await ws.recv())
            if hello.get("op") != OP_HELLO:
                raise RuntimeError(f"Esperava HELLO, recebi op={hello.get('op')}")
            interval_s = hello["d"]["heartbeat_interval"] / 1000.0

            # 2. IDENTIFY ou RESUME
            if resume and self._session_id:
                await ws.send(json.dumps({
                    "op": OP_RESUME,
                    "d": {"token": self.token, "session_id": self._session_id,
                          "seq": self._seq},
                }))
            else:
                await ws.send(json.dumps({
                    "op": OP_IDENTIFY,
                    "d": {
                        "token": self.token,
                        "intents": compute_intents(),
                        "properties": {"os": "bauer", "browser": "bauer", "device": "bauer"},
                    },
                }))

            # 3. Heartbeat + watcher de stop em paralelo ao recv loop
            acked = {"ok": True}

            async def heartbeats() -> None:
                await asyncio.sleep(interval_s * random.random())
                while True:
                    if not acked["ok"]:
                        raise RuntimeError("Heartbeat sem ACK — conexão zumbi")
                    acked["ok"] = False
                    await ws.send(json.dumps({"op": OP_HEARTBEAT, "d": self._seq}))
                    await asyncio.sleep(interval_s)

            async def stop_watcher() -> None:
                while not self.stopped:
                    await asyncio.sleep(0.5)
                await ws.close()

            hb_task = asyncio.create_task(heartbeats())
            watch_task = asyncio.create_task(stop_watcher())
            try:
                async for raw in ws:
                    payload = json.loads(raw)
                    op = payload.get("op")
                    if payload.get("s") is not None:
                        self._seq = payload["s"]
                    if op == OP_HEARTBEAT_ACK:
                        acked["ok"] = True
                    elif op == OP_HEARTBEAT:
                        await ws.send(json.dumps({"op": OP_HEARTBEAT, "d": self._seq}))
                    elif op == OP_RECONNECT:
                        return True  # servidor pediu reconexão — RESUME
                    elif op == OP_INVALID_SESSION:
                        resumable = bool(payload.get("d"))
                        await asyncio.sleep(random.uniform(1, 5))
                        return resumable
                    elif op == OP_DISPATCH:
                        await self._on_dispatch(payload)
            finally:
                hb_task.cancel()
                watch_task.cancel()
        return True  # fechamento limpo → tenta RESUME

    async def _on_dispatch(self, payload: dict) -> None:
        event = payload.get("t")
        data = payload.get("d") or {}
        if event == "READY":
            self._session_id = data.get("session_id", "")
            self._resume_url = data.get("resume_gateway_url", "")
            logger.info("Gateway READY (session %s)", self._session_id[:8])
        elif event == "RESUMED":
            logger.info("Gateway RESUMED")
        elif event == "MESSAGE_CREATE":
            if not should_respond(
                data, self.bot_id,
                mention_only=self.mention_only,
                allowed_guilds=self.allowed_guilds,
                allowed_channels=self.allowed_channels,
            ):
                return
            author = data.get("author") or {}
            channel_id = str(data.get("channel_id", ""))
            text = strip_bot_mention(data.get("content", ""), self.bot_id)
            if not text:
                return
            msg = ChannelMessage(
                channel="discord",
                user_id=str(author.get("id", "")),
                chat_id=channel_id,
                text=text,
                user_name=author.get("username", ""),
                raw=data,
            )
            self._send_typing(channel_id)
            # backend.process é bloqueante (LLM + tools) — vai para thread
            # para não congelar heartbeats do gateway.
            response = await asyncio.to_thread(self.handle_message, msg)
            if response:
                await asyncio.to_thread(self.send_text, channel_id, response)

    def stop(self) -> None:
        super().stop()
        try:
            self._http.close()
        except Exception:  # noqa: BLE001
            pass


def build_bridge_from_config(cfg, backend: AgentBackend | None = None) -> DiscordBridge:
    """Monta o DiscordBridge a partir de um BauerConfig validado."""
    token = resolve_token(cfg.discord.bot_token, "DISCORD_BOT_TOKEN")
    return DiscordBridge(
        token=token,
        backend=backend or AgentBackend(),
        allowed_users=cfg.discord.allowed_users,
        allowed_guilds=cfg.discord.allowed_guilds,
        allowed_channels=cfg.discord.allowed_channels,
        allow_all=cfg.discord.allow_all,
        mention_only=cfg.discord.mention_only,
        max_msgs_per_minute=cfg.discord.max_msgs_per_minute,
    )


def run_bridge(config_path: str | Path = "config.yaml") -> None:
    """Entry point standalone: python -m bauer.discord_bridge."""
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
