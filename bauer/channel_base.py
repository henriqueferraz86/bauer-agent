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
    "/model — lista modelos e troca o desta conversa\n"
    "/tasks — tarefas do kanban do workspace\n"
    "/new — começa uma conversa nova (apaga o histórico)\n"
    "/clear — o mesmo que /new\n"
    "/help — esta mensagem"
)

# TTL do cache da lista de modelos do provider (chamada HTTP de 8s — não
# vale a pena repetir a cada /model)
_MODELS_CACHE_TTL_S = 300.0


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
        # /model por conversa: session_key -> nome do modelo (override).
        # Em memória de propósito — restart do gateway volta ao global.
        self._model_overrides: dict[str, str] = {}
        # /model provider+model override: session_key -> (client, model, provider)
        # Permite trocar o PROVIDER inteiro por conversa (não só o modelo).
        self._session_overrides: dict[str, tuple[Any, str, str]] = {}
        # Injetável: callable() -> list[str]. Default: provider_profile.
        self._models_fetcher: Any = None
        # Injetável: callable() -> list[str] de nomes de providers.
        self._providers_fetcher: Any = None
        # Cache de modelos: provider_name -> (fetched_at, list[str])
        self._models_cache: tuple[float, list[str]] = (0.0, [])
        self._models_cache_by_provider: dict[str, tuple[float, list[str]]] = {}
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
        # _config_mtime == 0 significa que o client NÃO veio do config.yaml
        # (injetado via testes/embedding) — não há o que "re"-carregar; sem
        # este guard, um backend fake era substituído por um provider real
        # quando havia um config.yaml no CWD (aconteceu no CI).
        if not self.is_ready or self._config_mtime == 0.0:
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
        self._model_overrides.pop(key, None)
        self._session_overrides.pop(key, None)
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
            arg = text.split(maxsplit=1)[1].strip() if " " in text else ""
            return self._cmd_model(msg, arg)
        if command == "/tasks":
            return self._cmd_tasks()
        if command in ("/clear", "/new"):
            self._clear_session(msg.session_key)
            return "🧹 Conversa nova — histórico apagado."

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

        # Provider+model override de sessão (/model <provider> <modelo>) tem
        # prioridade; senão, usa model-only override ou o global.
        if msg.session_key in self._session_overrides:
            client, model, _provider = self._session_overrides[msg.session_key]
        else:
            client = self._client
            model = self._model_overrides.get(msg.session_key, self._model_name)

        ctx, lock = self._get_session(msg.session_key)
        with lock:
            ctx.add_user(text)
            response, _tool_log = run_one_turn(
                ctx, self._router, client, model
            )
            try:
                self._store.save(msg.session_key, ctx.messages)
            except Exception:
                logger.warning("Falha ao persistir sessão %s", msg.session_key)
        self.msgs_processed += 1
        return response.strip() or "🤔 O modelo não retornou resposta. Tente reformular."

    # ── /model — listar providers + modelos e trocar por conversa ─────────────

    def _available_models(self) -> list[str]:
        """Modelos do provider ativo, com cache TTL."""
        return self._models_for_provider(self._provider)

    def _models_for_provider(self, provider: str) -> list[str]:
        """Modelos de um provider específico, com cache por provider."""
        now = time.monotonic()
        cached_at, cached_models = self._models_cache_by_provider.get(provider, (0.0, []))
        if cached_models and now - cached_at < _MODELS_CACHE_TTL_S:
            return cached_models

        # Fallback para o provider ativo: aceita _models_fetcher injetado (testes)
        if provider == self._provider and self._models_fetcher is not None:
            try:
                models = list(self._models_fetcher())
            except Exception as exc:  # noqa: BLE001
                logger.warning("fetch_models falhou: %s", exc)
                models = []
        else:
            try:
                from .provider_profile import get_profile
                profile = get_profile(provider)
                models = profile.fetch_models() if profile else []
            except Exception as exc:  # noqa: BLE001
                logger.warning("fetch_models(%s) falhou: %s", provider, exc)
                models = []

        if models:
            self._models_cache_by_provider[provider] = (now, models)
        # Compatibilidade com o cache legado do provider ativo
        if provider == self._provider and models:
            self._models_cache = (now, models)
        return models

    def _configured_providers(self) -> list[str]:
        """Lista de providers com credenciais configuradas (env vars presentes)."""
        if self._providers_fetcher is not None:
            try:
                return list(self._providers_fetcher())
            except Exception:  # noqa: BLE001
                pass
        try:
            from .provider_profile import configured_providers as _cp
            result = [p.name for p in _cp()]
            return result if result else [self._provider]
        except Exception:  # noqa: BLE001
            return [self._provider]

    def _build_client_for_provider(self, provider: str, model: str) -> Any:
        """Constrói um cliente LLM para um provider diferente do ativo.

        Carrega o config.yaml atual, modifica provider+model em memória
        (Pydantic v2 é mutável por padrão) e delega ao mesmo _build_client_fn.
        """
        from .config_loader import load_config
        cfg = load_config(self.config_path)
        cfg.model.provider = provider
        cfg.model.name = model
        return self._build_client_fn()(cfg)

    def _cmd_model(self, msg: ChannelMessage, arg: str) -> str:
        """Dois níveis:
        /model               → lista providers configurados
        /model <p>           → lista modelos do provider p
        /model <p> <m>       → troca para provider p, modelo m
        /model reset         → volta ao global
        """
        key = msg.session_key
        # Determina o par (provider, model) ativo nesta sessão
        if key in self._session_overrides:
            _, active_model, active_provider = self._session_overrides[key]
        else:
            active_model = self._model_overrides.get(key, self._model_name)
            active_provider = self._provider

        if arg.lower() in ("reset", "padrao", "padrão", "default"):
            self._model_overrides.pop(key, None)
            self._session_overrides.pop(key, None)
            with self._sessions_lock:
                entry = self._sessions.get(key)
            if entry is not None:
                entry[0].set_llm(self._client, self._model_name)
            return f"↩️ Voltou ao padrão global: *{self._provider}* / *{self._model_name}*"

        providers = self._configured_providers()

        # ── Sem argumento: lista providers ───────────────────────────────────
        if not arg:
            lines = [f"🧠 Ativo: *{active_model}* ({active_provider})"]
            if key in self._session_overrides or key in self._model_overrides:
                lines.append(f"(global: {self._model_name} via {self._provider})")
            lines.append("\nProviders configurados:")
            for i, p in enumerate(providers, 1):
                marker = " ←" if p == active_provider else ""
                lines.append(f"{i}. {p}{marker}")
            lines.append("\n/model <número ou nome> — ver modelos do provider")
            lines.append("/model <provider> <modelo> — trocar direto")
            lines.append("/model reset — voltar ao padrão")
            return "\n".join(lines)

        # ── Parseia args: pode ser "2", "ollama", "2 3", "ollama qwen2.5:3b" ─
        parts = arg.split(None, 1)
        provider_arg = parts[0]
        model_arg = parts[1].strip() if len(parts) > 1 else ""

        # Resolve provider_arg como nome ou índice; None = não é um provider
        chosen_provider = self._resolve_provider(provider_arg, providers)

        # ── Arg não é provider conhecido: fallback para troca de modelo no
        #    provider atual (backward compat: /model alfa / /model 3) ─────────
        if chosen_provider is None:
            return self._switch_model_same_provider(
                key, provider_arg, active_model, active_provider
            )

        # ── Só provider informado: lista modelos desse provider ───────────────
        if not model_arg:
            models = self._models_for_provider(chosen_provider)
            if not models:
                return (
                    f"📦 *{chosen_provider}* — não consegui listar modelos.\n"
                    f"Troque direto: /model {chosen_provider} <nome-do-modelo>"
                )
            lines = [f"📦 *{chosen_provider}* — modelos disponíveis:"]
            for i, m in enumerate(models[:20], 1):
                marker = " ←" if m == active_model and chosen_provider == active_provider else ""
                lines.append(f"{i}. {m}{marker}")
            lines.append(f"\nTrocar: /model {chosen_provider} <número ou nome>")
            return "\n".join(lines)

        # ── Provider + modelo: troca ──────────────────────────────────────────
        models = self._models_for_provider(chosen_provider)
        chosen_model = model_arg
        if model_arg.isdigit():
            idx = int(model_arg)
            if not models:
                return f"Lista de modelos de {chosen_provider} indisponível — use o nome direto."
            if not 1 <= idx <= min(len(models), 20):
                return f"Número fora da lista (1–{min(len(models), 20)})."
            chosen_model = models[idx - 1]

        warning = ""
        if models and chosen_model not in models:
            warning = "\n⚠️ Modelo não está na lista — usando assim mesmo."

        if chosen_provider == self._provider:
            # Mesmo provider: só override de modelo (sem rebuild de client)
            self._session_overrides.pop(key, None)
            self._model_overrides[key] = chosen_model
            with self._sessions_lock:
                entry = self._sessions.get(key)
            if entry is not None:
                entry[0].set_llm(self._client, chosen_model)
        else:
            # Provider diferente: precisa de novo client
            try:
                new_client = self._build_client_for_provider(chosen_provider, chosen_model)
            except Exception as exc:  # noqa: BLE001
                return f"⚠️ Não consegui conectar ao provider {chosen_provider}: {exc}"
            self._model_overrides.pop(key, None)
            self._session_overrides[key] = (new_client, chosen_model, chosen_provider)
            with self._sessions_lock:
                entry = self._sessions.get(key)
            if entry is not None:
                entry[0].set_llm(new_client, chosen_model)

        return f"✅ Esta conversa agora usa *{chosen_provider}* / *{chosen_model}*.{warning}"

    @staticmethod
    def _resolve_provider(arg: str, providers: list[str]) -> str | None:
        """Resolve arg (nome ou índice 1-N) para nome de provider.

        Retorna None quando não bate com nenhum provider — o caller pode
        usar isso como fallback para troca de modelo no provider atual.
        """
        if arg.isdigit():
            idx = int(arg)
            if 1 <= idx <= len(providers):
                return providers[idx - 1]
            return None  # número fora do range de providers
        arg_low = arg.lower()
        for p in providers:
            if p.lower() == arg_low:
                return p
        return None  # nome não reconhecido como provider

    def _switch_model_same_provider(
        self, key: str, model_arg: str, active_model: str, active_provider: str
    ) -> str:
        """Troca modelo no provider atual (backward compat: /model <nome|número>)."""
        models = self._models_for_provider(self._provider)

        chosen = model_arg
        if model_arg.isdigit():
            idx = int(model_arg)
            if not models:
                return "Lista de modelos indisponível — use /model <nome>."
            if not 1 <= idx <= min(len(models), 20):
                return f"Número fora da lista (1–{min(len(models), 20)}). Veja /model."
            chosen = models[idx - 1]

        warning = ""
        if models and chosen not in models:
            warning = "\n⚠️ Não está na lista do provider — usando assim mesmo."

        self._session_overrides.pop(key, None)
        self._model_overrides[key] = chosen
        with self._sessions_lock:
            entry = self._sessions.get(key)
        if entry is not None:
            entry[0].set_llm(self._client, chosen)
        return f"✅ Esta conversa agora usa *{self._provider}* / *{chosen}*.{warning}"

    def _cmd_tasks(self) -> str:
        """Lista read-only do kanban do workspace (TASKS.md)."""
        try:
            from .workspace_manager import WorkspaceManager
            wm = WorkspaceManager(self._router.workspace)
            tasks = wm.list_tasks()
        except Exception as exc:  # noqa: BLE001
            return f"⚠️ Não consegui ler as tasks: {exc}"
        if not tasks:
            return "📋 Nenhuma tarefa no kanban. Peça: \"crie uma task para...\""
        by_status: dict[str, int] = {}
        lines = ["📋 *Tasks do workspace:*"]
        for t in tasks[:15]:
            status = getattr(t, "status", "?")
            by_status[status] = by_status.get(status, 0) + 1
            lines.append(f"• [{status}] {getattr(t, 'id', '')} {getattr(t, 'title', '')}")
        if len(tasks) > 15:
            lines.append(f"… e mais {len(tasks) - 15}.")
        resumo = " | ".join(f"{k}: {v}" for k, v in sorted(by_status.items()))
        lines.append(f"\n{resumo}")
        return "\n".join(lines)

    def _cmd_status(self, msg: ChannelMessage) -> str:
        ctx, _lock = self._get_session(msg.session_key)
        n_msgs = len(ctx.messages)
        pct = int(ctx.usage_pct * 100)
        if msg.session_key in self._session_overrides:
            _, active, active_prov = self._session_overrides[msg.session_key]
        else:
            active = self._model_overrides.get(msg.session_key, self._model_name)
            active_prov = self._provider
        model_line = f"Modelo: {active} ({active_prov})"
        if msg.session_key in self._session_overrides or msg.session_key in self._model_overrides:
            model_line += f" — desta conversa (global: {self._model_name} via {self._provider})"
        return (
            f"📊 Bauer Agent\n"
            f"{model_line}\n"
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
