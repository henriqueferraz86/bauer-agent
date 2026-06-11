"""Channel base — backend de agent compartilhado entre canais de chat.

O Bauer Gateway conecta canais de chat (Telegram, Discord, …) ao pipeline
do agent. Este módulo concentra o que é comum a todos os canais:

- ``AgentBackend`` — UM pipeline (client + router + sessões) compartilhado;
  cada chat tem sua própria sessão persistida no SqliteSessionStore.
- ``BaseBridge`` — contrato que todo canal implementa; um canal novo
  (Slack, WhatsApp…) é só um adaptador fino sobre esta base.
- ``ChannelMessage`` — envelope normalizado de mensagem inbound.
- ``resolve_token`` / ``chunk_text`` — helpers compartilhados.

Princípios:
- Secrets env-first: token vem de env var antes do config.yaml.
- Seguro por default: bridges negam usuários fora da allowlist.
- Sessões por chat: chave ``tg:{chat_id}`` / ``dc:{channel_id}`` no
  SqliteSessionStore — o histórico sobrevive a restarts e aparece no
  ``bauer memory search``.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from abc import ABC, abstractmethod
from collections import OrderedDict, deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger("bauer.channels")

# Sessões com ContextManager vivo em memória (as demais ficam só no SQLite)
_MAX_ACTIVE_SESSIONS = 50

HELP_TEXT = (
    "🤖 *Bauer Agent*\n\n"
    "Mande qualquer mensagem e eu respondo usando o modelo configurado.\n\n"
    "Comandos:\n"
    "/status — modelo, contexto e sessão atual\n"
    "/model — modelo ativo do agent\n"
    "/clear — apaga o histórico desta conversa\n"
    "/help — esta mensagem"
)


def resolve_token(config_value: str, env_var: str) -> str:
    """Token de canal: env var tem precedência sobre o valor do config.yaml."""
    return os.environ.get(env_var, "").strip() or (config_value or "").strip()


def chunk_text(text: str, limit: int) -> list[str]:
    """Divide texto em pedaços <= limit, preferindo quebras de linha.

    Telegram corta em 4096 chars, Discord em 2000 — mandar inteiro perde
    conteúdo silenciosamente, então todo bridge usa este helper.
    """
    text = text or ""
    if len(text) <= limit:
        return [text] if text else []
    chunks: list[str] = []
    remaining = text
    while remaining:
        if len(remaining) <= limit:
            chunks.append(remaining)
            break
        cut = remaining.rfind("\n", 0, limit)
        if cut < limit // 2:  # sem quebra razoável — corta seco
            cut = limit
        chunks.append(remaining[:cut])
        remaining = remaining[cut:].lstrip("\n")
    return chunks


@dataclass
class ChannelMessage:
    """Mensagem inbound normalizada — formato único para todos os canais."""

    channel: str            # "telegram" | "discord" | ...
    user_id: str
    chat_id: str
    text: str
    user_name: str = ""
    raw: dict = field(default_factory=dict)

    @property
    def session_key(self) -> str:
        prefix = {"telegram": "tg", "discord": "dc"}.get(self.channel, self.channel)
        return f"{prefix}:{self.chat_id}"


class RateLimiter:
    """Sliding window por usuário — protege o modelo de flood num canal público."""

    def __init__(self, max_per_minute: int = 20) -> None:
        self.max_per_minute = max(1, int(max_per_minute))
        self._hits: dict[str, deque] = {}
        self._lock = threading.Lock()

    def allow(self, user_id: str) -> bool:
        now = time.monotonic()
        with self._lock:
            dq = self._hits.setdefault(user_id, deque())
            while dq and now - dq[0] > 60.0:
                dq.popleft()
            if len(dq) >= self.max_per_minute:
                return False
            dq.append(now)
            return True


class AgentBackend:
    """Pipeline do agent compartilhado por todos os canais do gateway.

    Um único client/router para o processo; uma sessão (ContextManager +
    persistência SQLite) por chat. Thread-safe: bridges rodam em threads
    próprias e podem processar mensagens concorrentes de chats diferentes.
    """

    def __init__(
        self,
        config_path: str | Path = "config.yaml",
        sessions_dir: str | Path = "memory/sessions",
    ) -> None:
        self.config_path = Path(config_path)
        self.sessions_dir = Path(sessions_dir)
        self._init_lock = threading.Lock()
        self._sessions_lock = threading.Lock()
        # session_key -> (ContextManager, Lock); LRU dos ContextManagers vivos
        self._sessions: "OrderedDict[str, tuple[Any, threading.Lock]]" = OrderedDict()
        self._client: Any = None
        self._model_name: str = ""
        self._provider: str = ""
        self._applied_context: int = 0
        self._router: Any = None
        self._store: Any = None
        self._system_prompt: str = ""
        self._init_error: str = ""
        self._config_mtime: float = 0.0  # hot-reload: detecta `bauer model` etc.
        # Injetável (testes / embedding): callable(cfg) -> client.
        # Default None → usa bauer.cli._build_client (import tardio).
        self._client_builder: Any = None
        self.msgs_processed = 0
        self.errors = 0

    def _build_client_fn(self):
        if self._client_builder is not None:
            return self._client_builder
        from .cli import _build_client  # tardio: cli é pesado e importa este módulo
        return _build_client

    # ── Inicialização ──────────────────────────────────────────────────────

    @property
    def is_ready(self) -> bool:
        return self._client is not None

    def initialize(self) -> None:
        """Monta client + router + store a partir do config.yaml.

        Idempotente; thread-safe. Falha vira ``_init_error`` (mensagem
        amigável devolvida ao usuário do canal) — nunca traceback no chat.
        """
        if self._client is not None:
            return
        with self._init_lock:
            if self._client is not None:
                return
            # Imports tardios: bauer.cli é pesado e importá-lo no topo
            # criaria ciclo (cli importa os bridges para os comandos).
            from .config_loader import load_config
            from .agent import _build_system_prompt
            from .context_manager import ContextManager  # noqa: F401 (valida import)
            from .provider_profile import get_default_context
            from .sqlite_session_store import SqliteSessionStore
            from .tool_router import ToolRouter

            try:
                cfg = load_config(self.config_path)
                self._client = self._build_client_fn()(cfg)
                self._model_name = cfg.model.name
                self._provider = cfg.model.provider
                default_ctx = get_default_context(self._provider)
                if self._provider == "ollama":
                    self._applied_context = cfg.model.requested_context
                else:
                    self._applied_context = max(cfg.model.requested_context, default_ctx)
                workspace = Path(cfg.agent.workspace)
                workspace.mkdir(parents=True, exist_ok=True)
                self._router = ToolRouter(
                    workspace=workspace,
                    llm_client=self._client,
                    session_id="gateway",
                )
                self._store = SqliteSessionStore(self.sessions_dir)
                self._system_prompt = _build_system_prompt(self._router)
                self._init_error = ""
                try:
                    self._config_mtime = self.config_path.stat().st_mtime
                except OSError:
                    self._config_mtime = 0.0
                logger.info(
                    "AgentBackend pronto: %s/%s ctx=%d",
                    self._provider, self._model_name, self._applied_context,
                )
            except Exception as exc:  # noqa: BLE001 — vira mensagem p/ usuário
                self._client = None
                self._init_error = f"{type(exc).__name__}: {exc}"
                logger.error("AgentBackend falhou ao inicializar: %s", self._init_error)
                raise

    def _maybe_reload(self) -> None:
        """Hot-reload do client/modelo quando o config.yaml muda no disco.

        Sem isto, `bauer model` (ou edição manual) no servidor não tem efeito
        até reiniciar o gateway — o usuário troca o modelo e o bot continua
        respondendo com o antigo. Sessões são preservadas; só o client troca.
        Falha de reload mantém o client anterior (não derruba o canal).
        """
        if not self.is_ready:
            return
        try:
            mtime = self.config_path.stat().st_mtime
        except OSError:
            return
        if mtime == self._config_mtime:
            return
        with self._init_lock:
            if mtime == self._config_mtime:  # outro thread já recarregou
                return
            try:
                from .config_loader import load_config
                from .provider_profile import get_default_context

                cfg = load_config(self.config_path)
                new_client = self._build_client_fn()(cfg)
                self._client = new_client
                self._model_name = cfg.model.name
                self._provider = cfg.model.provider
                default_ctx = get_default_context(self._provider)
                if self._provider == "ollama":
                    self._applied_context = cfg.model.requested_context
                else:
                    self._applied_context = max(cfg.model.requested_context, default_ctx)
                # Sessões vivas passam a comprimir/conversar com o client novo
                with self._sessions_lock:
                    for ctx, _lock in self._sessions.values():
                        ctx.set_llm(new_client, self._model_name)
                self._config_mtime = mtime
                logger.info(
                    "config.yaml mudou — gateway agora usa %s/%s",
                    self._provider, self._model_name,
                )
            except Exception as exc:  # noqa: BLE001 — mantém client anterior
                self._config_mtime = mtime  # não tenta de novo a cada msg
                logger.error("Reload do config falhou (mantendo modelo atual): %s", exc)

    # ── Sessões ────────────────────────────────────────────────────────────

    def _get_session(self, key: str) -> tuple[Any, threading.Lock]:
        """ContextManager + lock da sessão; carrega histórico do SQLite."""
        from .context_manager import ContextManager

        with self._sessions_lock:
            if key in self._sessions:
                self._sessions.move_to_end(key)
                return self._sessions[key]
            ctx = ContextManager(
                applied_context=self._applied_context,
                system_prompt=self._system_prompt,
                provider=self._provider,
            )
            ctx.set_llm(self._client, self._model_name)
            saved = self._store.load(key)
            if saved:
                ctx.messages = saved
            entry = (ctx, threading.Lock())
            self._sessions[key] = entry
            # Evict LRU: o histórico já está persistido a cada turno —
            # derrubar do cache só libera RAM.
            while len(self._sessions) > _MAX_ACTIVE_SESSIONS:
                self._sessions.popitem(last=False)
            return entry

    def _clear_session(self, key: str) -> None:
        with self._sessions_lock:
            self._sessions.pop(key, None)
        try:
            self._store.delete(key)
        except Exception:
            logger.warning("Falha ao apagar sessão %s do store", key)

    # ── Processamento ──────────────────────────────────────────────────────

    def process(self, msg: ChannelMessage) -> str:
        """Processa uma mensagem inbound e retorna o texto de resposta.

        Nunca propaga exceção — qualquer falha vira mensagem amigável
        (o bridge só precisa entregar a string ao usuário).
        """
        if not self.is_ready:
            try:
                self.initialize()
            except Exception:
                return (
                    "⚠️ O agent não conseguiu inicializar.\n"
                    f"Detalhe: {self._init_error}\n"
                    "Verifique o config.yaml e rode `bauer doctor` no servidor."
                )

        self._maybe_reload()  # `bauer model` no servidor vale na próxima msg

        text = (msg.text or "").strip()
        if not text:
            return ""

        command = text.split()[0].lower() if text.startswith("/") else ""
        # Telegram em grupo manda "/status@MeuBot" — normaliza
        command = command.split("@")[0]
        if command in ("/start", "/help"):
            return HELP_TEXT
        if command == "/status":
            return self._cmd_status(msg)
        if command == "/model":
            return (
                f"🧠 Modelo ativo: *{self._model_name}* ({self._provider})\n"
                f"Contexto: {self._applied_context} tokens\n\n"
                "Para trocar: rode `bauer model` no servidor — "
                "o gateway recarrega sozinho na próxima mensagem."
            )
        if command == "/clear":
            self._clear_session(msg.session_key)
            return "🧹 Histórico desta conversa apagado."

        try:
            return self._run_turn(msg, text)
        except Exception as exc:  # noqa: BLE001
            self.errors += 1
            logger.error("Erro processando msg de %s/%s: %s", msg.channel, msg.user_id, exc)
            return self._friendly_provider_error(exc)

    @staticmethod
    def _friendly_provider_error(exc: Exception) -> str:
        """Traduz a falha do provider para o usuário do canal.

        Antes: qualquer erro virava um genérico "tente novamente" — rate
        limit, key inválida e provider fora do ar ficavam indistinguíveis
        e o usuário não sabia se era para esperar, corrigir ou desistir.
        """
        detail = str(exc)[:200]
        try:
            from .error_classifier import FailReason, classify_api_error
            reason = classify_api_error(exc).reason
        except Exception:  # noqa: BLE001
            return f"⚠️ Erro ao processar sua mensagem: {detail}"

        if reason in (FailReason.RATE_LIMIT, FailReason.QUOTA_EXCEEDED):
            return (
                "⏳ O provider atingiu o limite de uso (rate limit/quota). "
                "Aguarde alguns minutos e tente de novo.\n"
                f"Detalhe: {detail}"
            )
        if reason in (FailReason.AUTH_ERROR, FailReason.AUTH_PERMANENT):
            return (
                "🔑 Falha de autenticação no provider — a API key pode ter "
                "expirado. No servidor, rode `bauer doctor -p` para verificar.\n"
                f"Detalhe: {detail}"
            )
        if reason == FailReason.CONTEXT_OVERFLOW:
            return (
                "📚 O contexto desta conversa estourou o limite do modelo. "
                "Use /clear para começar do zero.\n"
                f"Detalhe: {detail}"
            )
        if reason == FailReason.PROVIDER_DOWN:
            return (
                "🔌 O provider parece fora do ar (5xx/timeout). "
                "Tente novamente em instantes.\n"
                f"Detalhe: {detail}"
            )
        return f"⚠️ Erro ao processar sua mensagem: {detail}"

    def _run_turn(self, msg: ChannelMessage, text: str) -> str:
        from .agent import run_one_turn

        ctx, lock = self._get_session(msg.session_key)
        with lock:
            ctx.add_user(text)
            response, _tool_log = run_one_turn(
                ctx, self._router, self._client, self._model_name
            )
            try:
                self._store.save(msg.session_key, ctx.messages)
            except Exception:
                logger.warning("Falha ao persistir sessão %s", msg.session_key)
        self.msgs_processed += 1
        return response.strip() or "🤔 O modelo não retornou resposta. Tente reformular."

    def _cmd_status(self, msg: ChannelMessage) -> str:
        ctx, _lock = self._get_session(msg.session_key)
        n_msgs = len(ctx.messages)
        pct = int(ctx.usage_pct * 100)
        return (
            f"📊 Bauer Agent\n"
            f"Modelo: {self._model_name} ({self._provider})\n"
            f"Contexto: {ctx.used_tokens}/{ctx.budget} tokens ({pct}%)\n"
            f"Sessão: {msg.session_key} — {n_msgs} mensagens\n"
            f"Processadas neste uptime: {self.msgs_processed}"
        )


class BaseBridge(ABC):
    """Contrato de um canal inbound do Bauer Gateway.

    Implementações: TelegramBridge (long-polling), DiscordBridge (Gateway
    WS). ``start()`` bloqueia até ``stop()`` — o gateway_runtime roda cada
    bridge numa thread própria.
    """

    name: str = "base"

    def __init__(self, backend: AgentBackend, rate_limiter: RateLimiter | None = None):
        self.backend = backend
        self.rate_limiter = rate_limiter or RateLimiter()
        self._stop_event = threading.Event()
        self.msgs_received = 0
        self.msgs_dropped = 0
        self.last_error: str = ""

    @abstractmethod
    def start(self) -> None:
        """Loop principal do canal (bloqueante até stop())."""

    def stop(self) -> None:
        self._stop_event.set()

    @property
    def stopped(self) -> bool:
        return self._stop_event.is_set()

    @abstractmethod
    def send_text(self, chat_id: str, text: str) -> None:
        """Envia texto ao chat (chunking por conta da implementação)."""

    @abstractmethod
    def _is_authorized(self, msg: ChannelMessage) -> bool:
        """Allowlist do canal — vazio nega tudo, allow_all libera."""

    def handle_message(self, msg: ChannelMessage) -> str | None:
        """Pipeline comum: auth → rate limit → backend. None = não responder."""
        self.msgs_received += 1
        if not self._is_authorized(msg):
            self.msgs_dropped += 1
            logger.info(
                "%s: mensagem de usuário não autorizado %s descartada",
                self.name, msg.user_id,
            )
            return None
        if not self.rate_limiter.allow(msg.user_id):
            return "⏳ Calma! Você atingiu o limite de mensagens por minuto."
        return self.backend.process(msg)

    def status(self) -> dict:
        return {
            "name": self.name,
            "running": not self.stopped,
            "msgs_received": self.msgs_received,
            "msgs_dropped": self.msgs_dropped,
            "last_error": self.last_error,
        }
