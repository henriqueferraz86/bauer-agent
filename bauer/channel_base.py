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
from contextlib import contextmanager
from contextvars import ContextVar, copy_context
from abc import ABC, abstractmethod
from collections import OrderedDict, deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .channel_projects import (
    ChannelProjectContext, ChannelProjectError, ChannelProjectStore,
    context_key, operator_key, private_telegram,
)
from .channel_execution import (
    ChannelExecution, ChannelExecutionRegistry, SessionWorker, governance_stop_reason,
)

_project_scope: ContextVar[tuple[int, ChannelProjectContext | None] | None] = ContextVar(
    "bauer_channel_project", default=None,
)

logger = logging.getLogger("bauer.channels")

# Sessões com ContextManager vivo em memória (as demais ficam só no SQLite)
_MAX_ACTIVE_SESSIONS = 50

# Mensagens aguardando na fila de uma sessão ocupada (busy queue)
_MAX_PENDING_PER_SESSION = 5

# Tempo de espera do transporte. Ao expirar, solicita interrupção; o worker
# mantém contexto, lock e custódia do Kernel até a saída real.
_TURN_TIMEOUT_SECONDS = int(os.environ.get("BAUER_GATEWAY_TURN_TIMEOUT", "120"))


class TurnTimeout(Exception):
    """Turno excedeu _TURN_TIMEOUT_SECONDS — vira resposta amigável ao usuário."""

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

    channel: str            # "telegram" | "discord" | "slack" | ...
    user_id: str
    chat_id: str
    text: str
    user_name: str = ""
    raw: dict = field(default_factory=dict)
    project_snapshot: Any = field(default=None, repr=False)
    project_snapshot_captured: bool = False

    @property
    def session_key(self) -> str:
        prefix = {"telegram": "tg", "discord": "dc", "slack": "sl"}.get(self.channel, self.channel)
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
        self._fallback_clients: list = []  # (client, model) p/ fallback em 429/5xx
        self._system_prompt: str = ""
        self._init_error: str = ""
        self._config_mtime: float = 0.0  # hot-reload: detecta `bauer model` etc.
        # Kernel do canal (lazy, invalidado no hot-reload junto com o client).
        self._kernel: Any = None
        self._kernel_resolvido = False
        self._kernel_workspace: Path | None = None
        self._kernel_lock = threading.Lock()
        self._workspace_kernels: OrderedDict[Path, Any] = OrderedDict()
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
        # Fila de mensagens que chegaram com a sessão ocupada (busy queue):
        # a thread ativa drena ao terminar o turno corrente.
        self._pending: dict[str, deque] = {}
        self._pending_lock = threading.RLock()
        self._executions = ChannelExecutionRegistry()
        # Rastreia quando cada sessão adquiriu o lock (monotonic seconds).
        # Permite detectar turno travado e resetar automaticamente.
        self._turn_started_at: dict[str, float] = {}
        self._turn_ts_lock = threading.Lock()
        self.msgs_processed = 0
        self.errors = 0
        self._project_store = ChannelProjectStore(self.sessions_dir / "channel_projects.sqlite3")
        self._project_routers: Any = None
        self._project_router_builder: Any = None
        self._project_model_loaded: set[str] = set()

    def _channel_project_router(self, workspace: Path) -> Any:
        if self._project_router_builder is not None:
            return self._project_router_builder(workspace)
        from .commands._runtime import _build_router
        from .config_loader import load_config
        return _build_router(load_config(self.config_path), workspace,
                             llm_client=self._client, session_id="gateway")

    def list_channel_projects(self, msg: ChannelMessage) -> list[dict[str, str]]:
        if not private_telegram(msg):
            raise ChannelProjectError("Selecione o projeto em uma conversa privada com o bot.")
        if not self.is_ready:
            self.initialize()
        from . import projects_registry as registry
        entries = []
        for row in registry.load_registry().get("projects", []):
            if not isinstance(row, dict):
                continue
            try:
                path = Path(row["path"]).expanduser().resolve()
                pid = str(row["id"])
                if (path.is_dir() and not registry.is_sensitive_dir(path)
                        and pid == registry.project_id(path)):
                    entries.append({"id": pid, "name": str(row.get("name") or path.name)})
            except (KeyError, TypeError, ValueError, OSError):
                continue
        if not entries:
            path = Path(self._router.workspace).resolve()
            if path.is_dir() and not registry.is_sensitive_dir(path):
                entries.append({"id": "__default__", "name": f"Workspace configurado ({path.name})"})
        return entries

    def _resolve_project(self, msg: ChannelMessage, pid: str, pinned: str | None = None
                         ) -> ChannelProjectContext:
        from . import projects_registry as registry
        if pid == "__default__":
            path = Path(self._router.workspace).resolve()
            name = f"Workspace configurado ({path.name})"
            router = self._router
        else:
            row = registry.get_project(pid)
            if not row or not isinstance(row.get("path"), str):
                raise ChannelProjectError("Projeto removido ou indisponível. Use /projetos.")
            path = Path(row["path"]).expanduser().resolve()
            name = str(row.get("name") or path.name)
            if pid != registry.project_id(path):
                raise ChannelProjectError("O caminho do projeto mudou. Selecione novamente em /projetos.")
            router = None
        if (not path.is_dir() or registry.is_sensitive_dir(path)
                or (pinned is not None and str(path) != pinned)):
            raise ChannelProjectError("Projeto indisponível ou com caminho alterado. Use /projetos.")
        if router is None:
            from .project_routers import ProjectRouterCache
            if self._project_routers is None:
                self._project_routers = ProjectRouterCache(self._router, self._channel_project_router)
            router = self._project_routers.get(pid)
            if router is self._router or Path(router.workspace).resolve() != path:
                raise ChannelProjectError("Não consegui preparar o projeto. Nenhuma ação foi iniciada.")
        from .agent import _build_system_prompt
        router._channel_session_store = self._store
        prompt = _build_system_prompt(router, client=self._client)
        prompt += f"\n\nPROJETO DESTA CONVERSA: {name}\nWORKSPACE EFETIVO: {path}\n"
        return ChannelProjectContext(pid, name, path, context_key(msg, pid), router, prompt)

    def select_channel_project(self, msg: ChannelMessage, pid: str) -> ChannelProjectContext:
        offered = self.list_channel_projects(msg)
        if pid not in {entry["id"] for entry in offered}:
            raise ChannelProjectError("Projeto indisponível. Abra /projetos novamente.")
        context = self._resolve_project(msg, pid)
        self._project_store.bind(operator_key(msg), pid, context.workspace)
        return context

    def project_context(self, msg: ChannelMessage) -> ChannelProjectContext | None:
        if msg.project_snapshot_captured:
            return msg.project_snapshot
        if not private_telegram(msg):
            return None
        binding = self._project_store.binding(operator_key(msg))
        if binding is None:
            return None
        if not self.is_ready:
            self.initialize()
        return self._resolve_project(msg, binding[0], binding[1])

    def capture_project_context(self, msg: ChannelMessage) -> ChannelProjectContext | None:
        if not msg.project_snapshot_captured:
            msg.project_snapshot = self.project_context(msg)
            msg.project_snapshot_captured = True
        return msg.project_snapshot

    def validate_project_snapshot(self, context: ChannelProjectContext | None) -> None:
        """Revalidate a queued request without following a changed selection."""
        if context is None:
            return
        from . import projects_registry as registry
        if context.project_id == "__default__":
            path = Path(self._router.workspace).resolve()
        else:
            row = registry.get_project(context.project_id)
            if not row or not isinstance(row.get("path"), str):
                raise ChannelProjectError("Projeto removido ou indisponível. Use /projetos.")
            path = Path(row["path"]).expanduser().resolve()
            if registry.project_id(path) != context.project_id:
                raise ChannelProjectError("O caminho do projeto mudou. Use /projetos.")
        if (path != context.workspace or not path.is_dir() or registry.is_sensitive_dir(path)
                or Path(context.router.workspace).resolve() != context.workspace):
            raise ChannelProjectError("Projeto indisponível ou com caminho alterado. Use /projetos.")

    @contextmanager
    def project_scope(self, msg: ChannelMessage):
        context = self.capture_project_context(msg)
        self.validate_project_snapshot(context)
        token = _project_scope.set((id(self), context))
        try:
            yield context
        finally:
            _project_scope.reset(token)

    def _active_project(self) -> ChannelProjectContext | None:
        active = _project_scope.get()
        return active[1] if active is not None and active[0] == id(self) else None

    def session_key(self, msg: ChannelMessage) -> str:
        context = self.project_context(msg)
        return context.session_key if context is not None else msg.session_key

    def _effective_router(self) -> Any:
        context = self._active_project()
        return context.router if context is not None else self._router

    def _restore_project_model(self, key: str) -> None:
        context = self._active_project()
        if context is None or key in self._project_model_loaded:
            return
        saved = self._project_store.model(key)
        if saved is not None:
            provider, model = saved
            if provider == self._provider:
                self._model_overrides[key] = model
                client = self._client
            else:
                client = self._build_client_for_provider(provider, model)
                self._session_overrides[key] = (client, model, provider)
            with self._sessions_lock:
                entry = self._sessions.get(key)
            if entry is not None:
                entry[0].set_llm(client, model)
        self._project_model_loaded.add(key)

    def _persist_project_model(self, key: str) -> None:
        context = self._active_project()
        if context is None:
            return
        if key in self._session_overrides:
            _, model, provider = self._session_overrides[key]
        else:
            model = self._model_overrides.get(key, "")
            provider = self._provider
        self._project_store.save_model(key, provider, model)

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
                # _build_router (não ToolRouter direto) — é o único lugar que le
                # tools.web_enabled/shell_enabled do config.yaml; construir o
                # ToolRouter aqui na mao deixava essas flags sempre False,
                # ignorando o config.yaml (bug: web_search/run_command nunca
                # apareciam no gateway mesmo com web_enabled=true configurado).
                from .commands._runtime import _build_router, build_fallback_clients
                self._router = _build_router(cfg, workspace, llm_client=self._client, session_id="gateway")
                # Fallback de provider (429/5xx) — paridade com o CLI. Falha ao
                # montar um fallback é tolerável (best-effort, nunca bloqueia o boot).
                try:
                    self._fallback_clients = build_fallback_clients(cfg)
                except Exception:  # noqa: BLE001
                    self._fallback_clients = []
                self._store = SqliteSessionStore(
                    self.sessions_dir,
                    semantic_indexing_enabled=cfg.memory.semantic_indexing_enabled,
                    semantic_indexing_debounce_s=cfg.memory.semantic_indexing_debounce_s,
                    semantic_indexing_batch_size=cfg.memory.semantic_indexing_batch_size,
                )
                self._system_prompt = _build_system_prompt(self._router, client=self._client)
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
                # o Kernel foi montado a partir do config ANTIGO (policy root,
                # evaluator, flag) — invalida junto com o client
                self._kernel_resolvido = False
                self._kernel = None
                with self._kernel_lock:
                    self._workspace_kernels.clear()
                self._project_routers = None
                # A persisted model belongs to its provider, even when the
                # global config switches providers. Restore those pairs lazily.
                for project_key in tuple(self._project_model_loaded):
                    self._model_overrides.pop(project_key, None)
                    self._session_overrides.pop(project_key, None)
                self._project_model_loaded.clear()
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

        with self._sessions_lock:
            if key in self._sessions:
                self._sessions.move_to_end(key)
                return self._sessions[key]
            from .core.context import ContextBuilder

            project = self._active_project()
            prompt = project.system_prompt if project is not None else self._system_prompt
            if key in self._session_overrides:
                session_client, session_model, _ = self._session_overrides[key]
            else:
                session_client = self._client
                session_model = self._model_overrides.get(key, self._model_name)

            ctx, _ = (
                ContextBuilder(applied_context=self._applied_context,
                               provider=self._provider)
                .instrucao("seguranca", prompt)
                .montar(llm_client=session_client, llm_model=session_model))
            saved = self._store.load(key)
            if saved:
                ctx.messages = saved
            entry = (ctx, threading.Lock())
            self._sessions[key] = entry
            # Evict LRU: o histórico já está persistido a cada turno —
            # derrubar do cache só libera RAM.
            for candidate in list(self._sessions):
                if len(self._sessions) <= _MAX_ACTIVE_SESSIONS:
                    break
                if not self._sessions[candidate][1].locked():
                    self._sessions.pop(candidate, None)
            return entry

    def _clear_session(self, key: str) -> bool:
        with self._sessions_lock:
            current = self._sessions.get(key)
            if current is not None and not current[1].acquire(blocking=False):
                return False
        try:
            self._model_overrides.pop(key, None)
            self._session_overrides.pop(key, None)
            self._persist_project_model(key)
            with self._pending_lock:
                self._pending.pop(key, None)
            try:
                self._store.delete(key)
            except Exception:
                logger.warning("Falha ao apagar sessão %s do store", key)
            with self._sessions_lock:
                self._sessions.pop(key, None)
        finally:
            if current is not None:
                current[1].release()
        return True

    def _evict_session(self, key: str) -> None:
        """Evict an idle context without deleting history or a live worker."""
        with self._sessions_lock:
            current = self._sessions.get(key)
            if current is None or not current[1].locked():
                self._sessions.pop(key, None)

    # ── Processamento ──────────────────────────────────────────────────────

    def process(self, msg: ChannelMessage, on_delta: Any = None,
                send_fn: Any = None) -> str:
        """Processa uma mensagem inbound e retorna o texto de resposta.

        Nunca propaga exceção — qualquer falha vira mensagem amigável
        (o bridge só precisa entregar a string ao usuário).

        on_delta: sink de streaming opcional (protocolo bauer.delta_stream) —
        o canal mostra a resposta crescendo / progresso de tools ao vivo.
        send_fn(text): entrega respostas de mensagens drenadas da fila busy.
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

        try:
            with self.project_scope(msg):
                self._restore_project_model(self.session_key(msg))
                return self._process_ready(msg, on_delta, send_fn)
        except ChannelProjectError as exc:
            return f"⚠️ {exc}"
        except Exception as exc:  # noqa: BLE001 — model restore/persistence failures
            logger.error("Falha ao preparar contexto do canal: %s", type(exc).__name__)
            return "⚠️ Não consegui preparar esta conversa. Nenhuma ação foi iniciada."

    def _process_ready(self, msg: ChannelMessage, on_delta: Any = None,
                       send_fn: Any = None) -> str:

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
        if command in ("/tasks", "/tarefas"):
            return self._cmd_tasks()
        if command in ("/clear", "/new"):
            if not self._clear_session(self.session_key(msg)):
                return "⏳ Esta conversa ainda está trabalhando. Use /cancelar e aguarde a interrupção antes de /new."
            return "🧹 Conversa nova — histórico apagado."

        try:
            return self._run_turn(msg, text, on_delta=on_delta, send_fn=send_fn)
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
        if isinstance(exc, TurnTimeout):
            return (
                "⏱ O tempo de espera terminou; interrupção solicitada. "
                "A conversa continua ocupada até o trabalho parar de fato. "
                "Efeitos já realizados e custo consumido serão preservados."
            )
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

    def _run_turn(self, msg: ChannelMessage, text: str, on_delta: Any = None,
                  send_fn: Any = None) -> str:
        key = self.session_key(msg)
        if key in self._session_overrides:
            client, model, _provider = self._session_overrides[key]
        else:
            client = self._client
            model = self._model_overrides.get(key, self._model_name)

        ctx, lock = self._get_session(key)
        if not lock.acquire(blocking=False):
            with self._pending_lock:
                active = self._live_session_execution(key)
                if active is not None and active.batch.stop_queue.is_set():
                    return "⏳ Interrupção solicitada; aguarde o trabalho parar. Esta mensagem não foi executada nem enfileirada."
                queue = self._pending.setdefault(key, deque())
                if len(queue) >= _MAX_PENDING_PER_SESSION:
                    return "🚦 Fila cheia — aguarde a resposta atual antes de mandar mais."
                queue.append((msg, text, client, model, on_delta, send_fn))
            return "⏳ Ainda estou na sua mensagem anterior — esta entrou na fila."

        batch = SessionWorker()
        execution = self._new_channel_execution(msg, key, batch)
        batch.current = execution
        self._executions.add(execution)
        with self._turn_ts_lock:
            self._turn_started_at[key] = time.monotonic()

        def work() -> None:
            current = execution
            current_text, current_client, current_model = text, client, model
            current_delta, current_send = on_delta, send_fn
            first = True
            try:
                while True:
                    try:
                        response = self._execute_turn(
                            ctx, key, current_client, current_model, current_text,
                            current_delta, execution=current,
                        )
                    except BaseException:
                        current.done.set()
                        raise
                    if first:
                        batch.response = response
                    elif current_send is not None:
                        try:
                            current_send(response)
                        except Exception:
                            logger.debug("Queued channel reply delivery failed", exc_info=True)
                    else:
                        batch.response += f"\n\n{response}"
                    first = False
                    if current.control.cancel_event.is_set():
                        self._stop_worker_queue(current, current.requested_reason or "cancelled")
                    with self._pending_lock:
                        current.done.set()
                        queue = self._pending.get(key)
                        if batch.stop_queue.is_set() or not queue:
                            break
                        queued_msg, current_text, current_client, current_model, current_delta, queued_send = queue.popleft()
                        current_send = queued_send or send_fn
                        current = self._new_channel_execution(queued_msg, key, batch)
                        batch.current = current
                        self._executions.add(current)
            except BaseException as exc:
                batch.error = exc
                self._stop_worker_queue(current, "error")
            finally:
                current.done.set()
                with self._turn_ts_lock:
                    self._turn_started_at.pop(key, None)
                lock.release()
                batch.done.set()
                if batch.timed_out and send_fn is not None:
                    terminal = (batch.response if batch.error is None else
                                self._friendly_provider_error(batch.error))
                    try:
                        send_fn(terminal + self._pending_discard_notice(batch))
                    except Exception:
                        logger.debug("Late channel result delivery failed", exc_info=True)

        inherited = copy_context()
        worker = threading.Thread(target=lambda: inherited.run(work),
                                  name=f"bauer-channel:{key}", daemon=True)
        try:
            worker.start()
        except BaseException:
            execution.done.set()
            lock.release()
            raise
        if not batch.done.wait(timeout=_TURN_TIMEOUT_SECONDS):
            batch.timed_out = True
            with self._pending_lock:
                if batch.done.is_set():
                    if batch.error is not None:
                        raise batch.error
                    return batch.response + self._pending_discard_notice(batch)
                current = batch.current or execution
                if current.phase == "evaluating":
                    self._stop_worker_queue(current, "validation_timeout", signal=False)
                    return ("⏱ A execução já encerrou; a validação está em andamento. "
                            "A conversa continua ocupada até a validação terminar."
                            + self._pending_discard_notice(batch))
                self._stop_worker_queue(current, "timeout")
            return self._friendly_provider_error(TurnTimeout()) + self._pending_discard_notice(batch)
        if batch.error is not None:
            raise batch.error
        return batch.response + self._pending_discard_notice(batch)

    def _new_channel_execution(self, msg: ChannelMessage, key: str,
                               batch: SessionWorker) -> ChannelExecution:
        project = self._active_project()
        return ChannelExecution(
            owner=operator_key(msg), channel=msg.channel, chat_id=msg.chat_id,
            project_id=project.project_id if project is not None else None,
            session_key=key, workspace=Path(self._effective_router().workspace).resolve(),
            batch=batch,
        )

    def _live_session_execution(self, key: str) -> ChannelExecution | None:
        return self._executions.active_for_session(key)

    def _stop_worker_queue(self, execution: ChannelExecution, reason: str,
                           *, signal: bool = True) -> int:
        with self._pending_lock:
            execution.requested_reason = execution.requested_reason or reason
            execution.batch.stop_queue.set()
            pending = self._pending.pop(execution.session_key, ())
            count = len(pending)
            execution.batch.discarded_pending += count
        if reason != "error" and signal:
            execution.control.request_cancel()
        return count

    @staticmethod
    def _pending_discard_notice(batch: SessionWorker) -> str:
        if not batch.discarded_pending:
            return ""
        return (f"\n{batch.discarded_pending} mensagem(ns) pendente(s) não foram executadas; "
                "reenvie as que desejar.")

    def cancel_channel_execution(self, msg: ChannelMessage, run_id: str | None = None) -> str:
        """Request stop for this private operator's current project only."""
        if not private_telegram(msg):
            return "Use /cancelar em uma conversa privada com o bot."
        # Reading the saved binding does not require the project directory to
        # still exist. Its owner can stop a worker after project removal.
        try:
            binding = self._project_store.binding(operator_key(msg))
        except Exception:
            logger.debug("Cancellation project binding unavailable", exc_info=True)
            return "Não consegui validar o projeto desta conversa; nenhum cancelamento foi aplicado."
        project_id = binding[0] if binding is not None else None
        with self._pending_lock:
            candidates = self._executions.owned(
                owner=operator_key(msg), channel=msg.channel, chat_id=msg.chat_id,
                project_id=project_id, run_id=run_id,
            )
            if not candidates:
                return "Nenhuma execução ativa correspondente nesta conversa e projeto."
            if len(candidates) > 1:
                choices = "\n".join(f"/cancelar {job.id}" for job in candidates)
                return f"Escolha a execução que deseja interromper:\n{choices}"
            execution = candidates[0]
            if execution.phase == "evaluating":
                return "A execução já encerrou; a validação está em andamento. Aguarde o resultado."
            self._stop_worker_queue(execution, "user")
        return (f"Cancelamento solicitado para {execution.id}. "
                "A conversa permanece ocupada até o trabalho parar de fato. "
                "Efeitos e custo já consumidos serão preservados."
                + self._pending_discard_notice(execution.batch))

    def _resolver_kernel(self) -> Any:
        """Kernel do canal (lazy). None = ``kernel.enabled`` desligado.

        Canais (Telegram/Slack/Discord) recebem input de TERCEIROS — é o caminho
        onde a policy mais importa, e era um dos que executavam turno sem Run,
        sem policy_check e sem gates.
        """
        workspace = Path(self._effective_router().workspace).resolve()
        with self._kernel_lock:
            if workspace in self._workspace_kernels:
                self._workspace_kernels.move_to_end(workspace)
                return self._workspace_kernels[workspace]
            from .config_loader import ConfigError, load_config
            try:
                cfg = load_config(self.config_path)
            except (ConfigError, OSError) as exc:
                if self._config_mtime > 0:
                    from .core.kernel import KernelWiringError
                    raise KernelWiringError("A configuração do gateway está indisponível; execução bloqueada.") from exc
                # Config missing preserves the injected/legacy backend contract.
                return None
            from .channel_governance import build_channel_kernel
            kernel = build_channel_kernel(cfg, workspace=workspace,
                                          sessions_dir=self.sessions_dir)
            self._workspace_kernels[workspace] = kernel
            while len(self._workspace_kernels) > 8:
                self._workspace_kernels.popitem(last=False)
            self._kernel = kernel
            self._kernel_workspace = workspace
            self._kernel_resolvido = True
            return kernel

    def _execute_turn(self, ctx: Any, key: str, client: Any, model: str,
                      text: str, on_delta: Any = None, *,
                      execution: ChannelExecution | None = None) -> str:
        """Run synchronously inside the session's custody-owning worker."""
        from .core.kernel import run_governed
        from .channel_governance import TurnMetrics, configure_router
        from .provider_retry import ProviderRetryInterrupted

        configure_router(self._effective_router(), sessions_dir=self.sessions_dir)
        kernel = self._resolver_kernel()
        canal = execution.channel if execution is not None else (
            getattr(self, "name", None) or type(self).__name__.lower()
        )
        falha: list[BaseException] = []

        def _executar(_payload):
            metrics = TurnMetrics()
            if execution is not None:
                execution.phase = "running"
            control = execution.control if execution is not None else None
            before_tools = control.tool_calls if control is not None else 0
            if control is not None:
                control.run_id = _payload.get("run_id")
                profile = kernel.budget.get_profile() if kernel is not None and kernel.budget is not None else None
                if profile is not None:
                    control.max_tool_calls = profile.max_tool_calls_per_run or None
                control.check_callback = lambda: governance_stop_reason(
                    kernel, metrics, execution,
                    max_runtime_s=profile.max_runtime_s_per_run if profile is not None else 0,
                )

            def result(**kwargs):
                data = metrics.result(**kwargs)
                if control is not None:
                    data["tool_calls_count"] = max(
                        data["tool_calls_count"], control.tool_calls - before_tools,
                    )
                return data

            try:
                with metrics.context(key, _payload.get("run_id")):
                    if control is None:
                        output = self._turno_cru(ctx, key, client, model, text, on_delta,
                                                 metrics=metrics)
                    else:
                        with control.activate():
                            try:
                                control.checkpoint()
                                output = self._turno_cru(ctx, key, client, model, text, on_delta,
                                                         metrics=metrics)
                            finally:
                                control.close()
                            control.checkpoint()
                return result(output=output)
            except ProviderRetryInterrupted as exc:
                reason = {
                    "cancelled": "Execução cancelada. Efeitos já realizados foram preservados.",
                    "kill_switch": "Execução interrompida pelo kill-switch.",
                    "budget_exhausted": "Limite de execução atingido; trabalho interrompido.",
                }[exc.reason]
                return result(output=reason, status="cancelled", error=reason)
            except BaseException as exc:  # noqa: BLE001
                falha.append(exc)
                return result(status="failed", error=str(exc))
            finally:
                if execution is not None:
                    execution.phase = "evaluating"

        project = self._active_project()
        gov = run_governed(
            kernel, _executar,
            agent_id=f"channel.{canal}", task=text, session_id=key,
            runtime_adapter="bauer_native",
            input={"endpoint": f"canal:{canal}", "session": key,
                   "project_id": project.project_id if project is not None else None,
                   "workspace": str(self._effective_router().workspace),
                   "operator": execution.owner if execution is not None else None},
        )
        if falha:
            raise falha[0]
        if gov.blocked_before_start:
            return f"⛔ {gov.error or gov.policy_reason or 'bloqueado pela política'}"
        texto = str(gov.output if gov.output is not None
                    else (gov.result or {}).get("output") or "")
        if not gov.ok and gov.governed:
            # gate reprovou: o texto ainda vale mais que um erro genérico para
            # quem está do outro lado do chat
            return texto or f"⚠ {gov.error or 'não consegui concluir'}"
        return texto

    def _turno_cru(self, ctx: Any, key: str, client: Any, model: str,
                   text: str, on_delta: Any = None, *, metrics: Any = None) -> str:
        """One turn in the existing worker, retaining partial history and cost."""
        from .agent import run_one_turn_with_fallback
        from .execution_control import current_control

        token = None
        if on_delta is not None:
            from .delta_stream import set_sink
            token = set_sink(on_delta)
        recorded_tools = False
        try:
            ctx.add_user(text)
            from .chat_team_routing import resolve_team_intent
            from .core.runtime.team_registry import TeamRegistry
            team_choice = resolve_team_intent(text, TeamRegistry())
            if team_choice is None:
                response, tool_log = run_one_turn_with_fallback(
                    ctx, self._effective_router(), client, model, self._fallback_clients,
                )
            else:
                response = self._run_chat_team(team_choice.team_id, text, key)
                ctx.add_assistant(response)
                tool_log = []
            if metrics is not None:
                metrics.record_tools(tool_log)
                recorded_tools = True
            control = current_control()
            if control is not None:
                control.checkpoint()
        except BaseException as exc:
            if metrics is not None and not recorded_tools:
                metrics.record_tools(getattr(exc, "partial_tool_log", []))
            raise
        finally:
            try:
                self._store.save(key, ctx.messages)
            except Exception:
                logger.warning("Falha ao persistir sessão %s", key)
            if token is not None:
                from .delta_stream import reset_sink
                reset_sink(token)
        self.msgs_processed += 1
        return (response or "").strip() or "🤔 O modelo não retornou resposta. Tente reformular."

    def _run_chat_team(self, team_id: str, task: str, session_id: str) -> str:
        """Run a formal team inside the channel's already-governed Kernel run."""
        from .chat_team_routing import project_scoped_team_task
        from .config_loader import load_config
        from .core.runtime import AgnoTeamOrchestrator
        from .core.runtime.adapters.agno_adapter import AgnoRuntimeAdapter
        from .core.runtime.agent_registry import RuntimeAgentRegistry
        from .core.runtime.team_registry import TeamRegistry
        from .runtime_ids import get_runtime_ids

        kernel = self._resolver_kernel()
        if kernel is None:
            raise RuntimeError("Não consegui iniciar o time: o Kernel governado não está disponível.")
        cfg = load_config(self.config_path)
        adapter_cfg = AgnoRuntimeAdapter._adapter_config_from(cfg)
        adapter_cfg.setdefault("tool_context", "chat")
        adapter_cfg["workspace"] = str(self._effective_router().workspace)
        run_store = getattr(getattr(kernel, "runs", None), "store", None)
        runtime_root = getattr(run_store, "root", self.sessions_dir.parent / "runtime")
        adapter = AgnoRuntimeAdapter(
            config=cfg, adapter_config=adapter_cfg,
            runtime_event_bus=kernel.bus, runtime_policy_root=runtime_root,
        )
        orchestrator = AgnoTeamOrchestrator(
            kernel=kernel, config=cfg, root=runtime_root,
            team_registry=TeamRegistry(), agent_registry=RuntimeAgentRegistry(),
            adapter=adapter, event_bus=kernel.bus,
        )
        team = orchestrator._require_team(team_id)
        orchestrator._authorize(team, orchestrator._supervisor(team), 0.0)
        runtime = get_runtime_ids()
        run_id = runtime[1] if runtime else None
        project = self._active_project()
        project_name = project.name if project is not None else self._effective_router().workspace.name
        scoped_task = project_scoped_team_task(
            task, project_name, str(self._effective_router().workspace),
        )
        result = orchestrator._execute(
            team, {"run_id": run_id, "task": scoped_task, "session_id": session_id},
            user_id=f"telegram:{session_id}",
        )
        if result.get("status") == "failed" or result.get("event") == "run.failed":
            detail = result.get("error") or result.get("message") or "falha sem detalhes"
            raise RuntimeError(f"O time {team_id} não concluiu. {detail}")
        output = str(result.get("output") or "").strip()
        if not output:
            raise RuntimeError(f"O time {team_id} terminou sem produzir uma resposta final.")
        return output

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
        """Todos os providers suportados (para /model mostrar lista completa)."""
        if self._providers_fetcher is not None:
            try:
                return list(self._providers_fetcher())
            except Exception:  # noqa: BLE001
                pass
        try:
            from .provider_profile import list_providers as _lp
            result = [p.name for p in _lp()]
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
        key = self.session_key(msg)
        self._restore_project_model(key)
        # Determina o par (provider, model) ativo nesta sessão
        if key in self._session_overrides:
            _, active_model, active_provider = self._session_overrides[key]
        else:
            active_model = self._model_overrides.get(key, self._model_name)
            active_provider = self._provider

        if arg.lower() in ("reset", "padrao", "padrão", "default"):
            self._model_overrides.pop(key, None)
            self._session_overrides.pop(key, None)
            self._persist_project_model(key)
            with self._sessions_lock:
                entry = self._sessions.get(key)
            if entry is not None:
                entry[0].set_llm(self._client, self._model_name)
            return f"↩️ Voltou ao padrão global: *{self._provider}* / *{self._model_name}*"

        providers = self._configured_providers()

        # ── Sem argumento: lista providers ───────────────────────────────────
        if not arg:
            try:
                from .provider_profile import get_profile as _gp
            except Exception:  # noqa: BLE001
                _gp = lambda _: None  # noqa: E731
            lines = [f"🧠 Ativo: *{active_model}* ({active_provider})"]
            if key in self._session_overrides or key in self._model_overrides:
                lines.append(f"(global: {self._model_name} via {self._provider})")
            lines.append("\nProviders disponíveis:")
            for i, p in enumerate(providers, 1):
                marker = " ←" if p == active_provider else ""
                profile = _gp(p)
                free_tag = " 🆓" if profile and profile.is_free else ""
                lines.append(f"{i}. {p}{free_tag}{marker}")
            lines.append("\n🆓 = gratuito ou sem cobrança por uso")
            lines.append("/model <número ou nome> — ver modelos do provider")
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
            try:
                from .provider_profile import get_profile as _gp2
                _prof = _gp2(chosen_provider)
            except Exception:  # noqa: BLE001
                _prof = None
            any_free = _prof and any(_prof.is_model_free(m) for m in models[:20])
            lines = [f"📦 *{chosen_provider}* — modelos disponíveis:"]
            for i, m in enumerate(models[:20], 1):
                marker = " ←" if m == active_model and chosen_provider == active_provider else ""
                free_tag = " 🆓" if _prof and _prof.is_model_free(m) else ""
                lines.append(f"{i}. {m}{free_tag}{marker}")
            if any_free:
                lines.append("\n🆓 = gratuito")
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

        self._persist_project_model(key)
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
        self._persist_project_model(key)
        with self._sessions_lock:
            entry = self._sessions.get(key)
        if entry is not None:
            entry[0].set_llm(self._client, chosen)
        return f"✅ Esta conversa agora usa *{self._provider}* / *{chosen}*.{warning}"

    def _cmd_tasks(self) -> str:
        """Lista read-only do kanban do workspace (TASKS.md)."""
        try:
            from .workspace_manager_factory import get_workspace_manager
            wm = get_workspace_manager(self._effective_router().workspace)
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
        key = self.session_key(msg)
        ctx, _lock = self._get_session(key)
        n_msgs = len(ctx.messages)
        pct = int(ctx.usage_pct * 100)
        if key in self._session_overrides:
            _, active, active_prov = self._session_overrides[key]
        else:
            active = self._model_overrides.get(key, self._model_name)
            active_prov = self._provider
        model_line = f"Modelo: {active} ({active_prov})"
        if key in self._session_overrides or key in self._model_overrides:
            model_line += f" — desta conversa (global: {self._model_name} via {self._provider})"
        project = self._active_project()
        project_line = f"Projeto: {project.name}\n" if project is not None else ""
        return (
            f"📊 Bauer Agent\n"
            f"{project_line}"
            f"{model_line}\n"
            f"Contexto: {ctx.used_tokens}/{ctx.budget} tokens ({pct}%)\n"
            f"Sessão: {key} — {n_msgs} mensagens\n"
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

    def reset(self) -> None:
        """Limpa o stop_event para permitir uma nova chamada a start() após restart."""
        self._stop_event.clear()
        self.last_error = ""

    @property
    def stopped(self) -> bool:
        return self._stop_event.is_set()

    @abstractmethod
    def send_text(self, chat_id: str, text: str) -> None:
        """Envia texto ao chat (chunking por conta da implementação)."""

    @abstractmethod
    def _is_authorized(self, msg: ChannelMessage) -> bool:
        """Allowlist do canal — vazio nega tudo, allow_all libera."""

    def handle_message(self, msg: ChannelMessage, on_delta: Any = None) -> str | None:
        """Pipeline comum: auth → rate limit → backend. None = não responder.

        on_delta: sink de streaming opcional (ver bauer.delta_stream) — o
        canal mostra a resposta/progresso ao vivo enquanto o agente trabalha.
        """
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
        send_fn = lambda text: self.send_text(msg.chat_id, text)  # noqa: E731
        return self.backend.process(msg, on_delta=on_delta, send_fn=send_fn)

    def status(self) -> dict:
        return {
            "name": self.name,
            "running": not self.stopped,
            "msgs_received": self.msgs_received,
            "msgs_dropped": self.msgs_dropped,
            "last_error": self.last_error,
        }
