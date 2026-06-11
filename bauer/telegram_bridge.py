"""Telegram Bridge — canal Telegram do Bauer Gateway.

Bot conversacional via Telegram Bot API com long-polling (getUpdates),
sem dependência de python-telegram-bot — só httpx, que já é core.

Setup::

    bauer gateway init          # wizard: token, allowlist, .env
    bauer telegram start        # só este canal
    bauer gateway start         # todos os canais habilitados

Config (config.yaml)::

    telegram:
      enabled: true
      allowed_users: [123456789]   # seu user id (o wizard descobre)
      # token preferencialmente em TELEGRAM_BOT_TOKEN no .env

Segurança: allowlist vazia NEGA todo mundo (allow_all: true para liberar,
não recomendado). Offset persistido em workspace/.bauer_gateway/ — restart
não reprocessa mensagens antigas.
"""

from __future__ import annotations

import html as _html
import json
import logging
import re
import threading
import time
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

logger = logging.getLogger("bauer.telegram")

TELEGRAM_API = "https://api.telegram.org"
MAX_MESSAGE_CHARS = 4096
POLL_TIMEOUT_S = 30  # long-polling do getUpdates

# Menu "/" do Telegram (setMyCommands) — igual Hermes/OpenClaw: ao digitar /
# o cliente mostra estas opções com descrição. Mantenha em sincronia com os
# handlers de AgentBackend.process() e com o HELP_TEXT do channel_base.
BOT_COMMANDS = [
    {"command": "start", "description": "Menu inicial"},
    {"command": "help", "description": "Ajuda e comandos disponíveis"},
    {"command": "status", "description": "Modelo, contexto e sessão atual"},
    {"command": "model", "description": "Listar modelos e trocar o desta conversa"},
    {"command": "tasks", "description": "Tarefas do kanban do workspace"},
    {"command": "new", "description": "Conversa nova (apaga o histórico)"},
    {"command": "clear", "description": "O mesmo que /new"},
]


def md_to_telegram_html(text: str) -> str:
    """Converte markdown comum do modelo para HTML do Telegram.

    O Telegram não renderiza markdown cru — ``**negrito**`` chega como
    asteriscos literais. Suporta: ```blocos```, `inline`, **negrito**,
    *itálico*, [link](url). Conteúdo é escapado antes (sem injeção de HTML).
    """
    parts = re.split(r"```(?:\w*\n)?(.*?)```", text or "", flags=re.DOTALL)
    out: list[str] = []
    for i, part in enumerate(parts):
        if i % 2 == 1:  # conteúdo de bloco de código — só escapa
            out.append(f"<pre>{_html.escape(part.rstrip())}</pre>")
            continue
        seg = _html.escape(part)
        seg = re.sub(r"`([^`\n]+)`", r"<code>\1</code>", seg)
        seg = re.sub(r"\*\*([^*\n]+)\*\*", r"<b>\1</b>", seg)
        seg = re.sub(r"(?<!\*)\*([^*\n]+)\*(?!\*)", r"<i>\1</i>", seg)
        seg = re.sub(
            r"\[([^\]\n]+)\]\((https?://[^)\s]+)\)", r'<a href="\2">\1</a>', seg
        )
        out.append(seg)
    return "".join(out)


class TelegramBridge(BaseBridge):
    """Canal Telegram via long-polling — implementação de BaseBridge."""

    name = "telegram"

    def __init__(
        self,
        token: str,
        backend: AgentBackend,
        allowed_users: list[int] | None = None,
        allow_all: bool = False,
        poll_interval: float = 2.0,
        max_msgs_per_minute: int = 20,
        state_dir: str | Path = "workspace/.bauer_gateway",
    ) -> None:
        super().__init__(backend, RateLimiter(max_msgs_per_minute))
        self.token = token
        self.allowed_users = {int(u) for u in (allowed_users or [])}
        self.allow_all = allow_all
        self.poll_interval = poll_interval
        self.state_dir = Path(state_dir)
        self._offset_path = self.state_dir / "telegram_offset.json"
        self._offset = self._load_offset()
        self._http = httpx.Client(timeout=POLL_TIMEOUT_S + 10)

    # ── API Telegram ───────────────────────────────────────────────────────

    def _api(self, method: str, **params) -> dict:
        """POST num método da Bot API; retorna o `result` ou levanta erro."""
        url = f"{TELEGRAM_API}/bot{self.token}/{method}"
        resp = self._http.post(url, json=params)
        if resp.status_code == 409:
            # Dois processos consumindo o MESMO bot: o Telegram só permite um
            # getUpdates por token. Causa clássica: bridge antigo ainda vivo.
            raise RuntimeError(
                "Telegram 409: outro processo já está consumindo este bot. "
                "Pare-o com `bauer telegram stop` (ou mate o processo antigo) "
                "e tente de novo."
            )
        resp.raise_for_status()
        data = resp.json()
        if not data.get("ok"):
            raise RuntimeError(f"Telegram API {method}: {data.get('description')}")
        return data.get("result")

    def get_me(self) -> dict:
        """Valida o token e retorna info do bot (usado pelo wizard/doctor)."""
        return self._api("getMe")

    def register_commands(self) -> None:
        """Registra o menu '/' do bot (setMyCommands). Falha não é fatal."""
        try:
            self._api("setMyCommands", commands=BOT_COMMANDS)
            logger.info("Menu de comandos registrado (%d comandos)", len(BOT_COMMANDS))
        except Exception as exc:  # noqa: BLE001 — menu é cosmético
            logger.warning("setMyCommands falhou: %s", exc)

    def send_text(self, chat_id: str, text: str) -> None:
        """Envia em HTML (markdown convertido); cai para texto puro se o
        Telegram rejeitar o parse — nunca perde a mensagem por formatação."""
        for chunk in chunk_text(text, MAX_MESSAGE_CHARS):
            try:
                self._api(
                    "sendMessage", chat_id=chat_id,
                    text=md_to_telegram_html(chunk), parse_mode="HTML",
                )
            except Exception:  # noqa: BLE001 — fallback plain text
                try:
                    self._api("sendMessage", chat_id=chat_id, text=chunk)
                except Exception as exc:  # noqa: BLE001
                    self.last_error = f"sendMessage: {exc}"
                    logger.error("Falha enviando para %s: %s", chat_id, exc)

    def _send_typing(self, chat_id: str) -> None:
        try:
            self._api("sendChatAction", chat_id=chat_id, action="typing")
        except Exception:  # noqa: BLE001 — typing é cosmético
            pass

    # ── Offset (não reprocessar updates após restart) ──────────────────────

    def _load_offset(self) -> int:
        try:
            data = json.loads(self._offset_path.read_text(encoding="utf-8"))
            return int(data.get("offset", 0))
        except Exception:
            return 0

    def _save_offset(self) -> None:
        try:
            self.state_dir.mkdir(parents=True, exist_ok=True)
            self._offset_path.write_text(
                json.dumps({"offset": self._offset}), encoding="utf-8"
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Falha persistindo offset: %s", exc)

    # ── Auth ───────────────────────────────────────────────────────────────

    def _is_authorized(self, msg: ChannelMessage) -> bool:
        if self.allow_all:
            return True
        try:
            return int(msg.user_id) in self.allowed_users
        except (TypeError, ValueError):
            return False

    # ── Loop principal ─────────────────────────────────────────────────────

    def start(self) -> None:
        """Long-polling até stop(). Erros de rede são re-tentados com backoff."""
        if not self.token:
            raise RuntimeError(
                "Token do Telegram ausente. Defina TELEGRAM_BOT_TOKEN no .env "
                "ou rode `bauer gateway init`."
            )
        if not self.allowed_users and not self.allow_all:
            logger.warning(
                "telegram.allowed_users vazio — NENHUM usuário será atendido. "
                "Rode `bauer gateway init` ou defina allow_all: true (cuidado)."
            )
        me = self.get_me()
        logger.info("Telegram bridge online como @%s", me.get("username"))
        self.register_commands()  # menu "/" no cliente Telegram

        backoff = 2.0
        while not self.stopped:
            try:
                updates = self._api(
                    "getUpdates",
                    offset=self._offset + 1,
                    timeout=POLL_TIMEOUT_S,
                    allowed_updates=["message"],
                )
                backoff = 2.0
                for update in updates or []:
                    self._offset = max(self._offset, int(update.get("update_id", 0)))
                    self._handle_update(update)
                if updates:
                    self._save_offset()
            except (httpx.HTTPError, RuntimeError) as exc:
                if self.stopped:
                    break
                self.last_error = str(exc)
                logger.warning("Polling falhou (%s) — retry em %.0fs", exc, backoff)
                self._stop_event.wait(backoff)
                backoff = min(backoff * 2, 60.0)
        logger.info("Telegram bridge parado.")

    def _handle_update(self, update: dict) -> None:
        message = update.get("message") or {}
        text = message.get("text", "")
        chat_id = str((message.get("chat") or {}).get("id", ""))
        from_user = message.get("from") or {}
        if not text or not chat_id:
            return
        msg = ChannelMessage(
            channel="telegram",
            user_id=str(from_user.get("id", "")),
            chat_id=chat_id,
            text=text,
            user_name=from_user.get("username", "") or from_user.get("first_name", ""),
            raw=update,
        )
        if self._is_authorized(msg):  # não mostrar "digitando…" a estranhos
            self._send_typing(chat_id)
        response = self.handle_message(msg)
        if response:
            self.send_text(chat_id, response)

    def stop(self) -> None:
        super().stop()
        try:
            self._http.close()
        except Exception:  # noqa: BLE001
            pass


def build_bridge_from_config(cfg, backend: AgentBackend | None = None) -> TelegramBridge:
    """Monta o TelegramBridge a partir de um BauerConfig validado."""
    token = resolve_token(cfg.telegram.bot_token, "TELEGRAM_BOT_TOKEN")
    workspace = Path(cfg.agent.workspace)
    return TelegramBridge(
        token=token,
        backend=backend or AgentBackend(),
        allowed_users=cfg.telegram.allowed_users,
        allow_all=cfg.telegram.allow_all,
        poll_interval=cfg.telegram.poll_interval,
        max_msgs_per_minute=cfg.telegram.max_msgs_per_minute,
        state_dir=workspace / ".bauer_gateway",
    )


def run_bridge(config_path: str | Path = "config.yaml") -> None:
    """Entry point standalone: python -m bauer.telegram_bridge."""
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
