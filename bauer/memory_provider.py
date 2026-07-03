"""MemoryProvider — interface plugável para backends de memória do Bauer Agent.

Qualquer backend de memória deve implementar esta ABC. O `LocalMemoryProvider`
é a implementação padrão que envolve o `MemoryManager` existente (arquivos .md).

Ciclo de vida por sessão:
  initialize() → prefetch() → [loop: sync_turn() por turno] →
  on_session_end() → (antes de compressão) on_pre_compress()

Hooks extras:
  on_memory_write()  — chamado após o agent escrever na memória
  system_prompt_block() — bloco de contexto injetado no system prompt
  get_tool_schemas() — schemas de tools específicas de memória

Nudge:
  O agent deve chamar should_nudge() após cada turno. Se retornar True,
  a mensagem nudge_message() é adicionada ao contexto para lembrar o
  agent de registrar o que está fazendo.
"""

from __future__ import annotations

import json
import math
import re
import time
from abc import ABC, abstractmethod
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Base ABC
# ---------------------------------------------------------------------------

class MemoryProvider(ABC):
    """Interface que todos os backends de memória devem implementar."""

    # ------------------------------------------------------------------
    # Ciclo de vida
    # ------------------------------------------------------------------

    @abstractmethod
    def initialize(self, workspace: str | Path) -> None:
        """Inicializa o provider com o diretório de trabalho da sessão."""

    def prefetch(self) -> None:
        """Pré-carrega dados relevantes antes do loop principal. Opcional."""

    def queue_prefetch(self) -> None:
        """Dispara prefetch em background sem bloquear o loop principal.

        A implementação padrão chama prefetch() diretamente em uma thread daemon.
        Providers com acesso async podem sobrescrever para usar asyncio.
        """
        import threading
        t = threading.Thread(target=self._safe_prefetch, daemon=True)
        t.start()

    def _safe_prefetch(self) -> None:
        try:
            self.prefetch()
        except Exception:
            pass

    def on_turn_start(self, turn_index: int, messages: list[dict]) -> None:
        """Chamado antes de cada turno do agent (antes da chamada LLM).

        Permite que o provider atualize contexto fresco, injete snippets
        relevantes para a mensagem atual, ou acione prefetch adicional.
        """

    def sync_turn(self, turn_index: int, messages: list[dict]) -> None:
        """Sincroniza estado após cada turno do agent. Opcional."""

    def on_session_end(self, messages: list[dict]) -> None:
        """Cleanup/persistência ao fim da sessão. Opcional."""

    def on_pre_compress(self, messages: list[dict]) -> None:
        """Chamado antes de uma compressão de contexto. Opcional."""

    def on_memory_write(self, key: str, value: str) -> None:
        """Chamado quando o agent escreve explicitamente na memória. Opcional."""

    # ------------------------------------------------------------------
    # Contexto e tools
    # ------------------------------------------------------------------

    def system_prompt_block(self) -> str:
        """Bloco de texto para injetar no system prompt. Vazio = nada."""
        return ""

    def get_tool_schemas(self) -> list[dict]:
        """Schemas JSON de tools adicionais fornecidas por este provider."""
        return []

    def get_config_schema(self) -> dict:
        """Retorna JSON Schema das opções de configuração deste provider.

        Permite que UIs de configuração (CLI, Telegram /config) apresentem
        campos editáveis sem hardcode. Retorna dict vazio se sem config.
        """
        return {}

    # ------------------------------------------------------------------
    # Hooks de evento avançados
    # ------------------------------------------------------------------

    def on_delegation(self, sub_task: str, result: str) -> None:
        """Chamado após o agent delegar uma subtarefa a um sub-agente.

        Args:
            sub_task: descrição da tarefa delegada.
            result: resposta retornada pelo sub-agente.
        """

    def handle_tool_call(
        self, tool_name: str, tool_args: dict, tool_result: str
    ) -> None:
        """Chamado após cada execução de tool pelo agent loop.

        Permite que o provider observe (e opcionalmente indexe) o resultado
        de qualquer tool call — útil para extrair fatos ou rastrear ações.

        Args:
            tool_name: nome da tool executada.
            tool_args: argumentos passados à tool.
            tool_result: resultado retornado (string serializada).
        """

    # ------------------------------------------------------------------
    # Nudge
    # ------------------------------------------------------------------

    _NUDGE_INTERVAL: int = 8  # turnos sem escrita para disparar nudge

    def should_nudge(
        self, turn_index: int, last_write_turn: int | None
    ) -> bool:
        """Retorna True se o agent deve ser lembrado de registrar memória.

        Dispara quando `_NUDGE_INTERVAL` turnos se passaram sem nenhuma
        escrita de memória.
        """
        if last_write_turn is None:
            since = turn_index
        else:
            since = turn_index - last_write_turn
        return since >= self._NUDGE_INTERVAL

    def nudge_message(self) -> str:
        """Mensagem de nudge a ser injetada no contexto como assistant-hint."""
        return (
            "[memory-nudge] Você está trabalhando há muitos turnos sem "
            "registrar nada na memória. Considere usar as tools de memória "
            "para anotar decisões, lições ou contexto importante."
        )


# ---------------------------------------------------------------------------
# LocalMemoryProvider — wrapper sobre MemoryManager existente
# ---------------------------------------------------------------------------

@dataclass
class _NudgeState:
    last_write_turn: int | None = None
    nudge_sent_at: int | None = None


class LocalMemoryProvider(MemoryProvider):
    """Provider padrão — usa MemoryManager (arquivos .md) no workspace.

    Não tem dependências externas além do MemoryManager existente.
    Faz prefetch dos arquivos de memória mais relevantes e injeta
    um resumo no system prompt.
    """

    _PREFETCH_FILES = ["MEMORY.md", "USER_PREFERENCES.md", "RUNTIME_LESSONS.md"]
    _MAX_SYSTEM_BLOCK_CHARS = 2000

    def __init__(self) -> None:
        self._manager: Any = None  # MemoryManager, lazy-loaded
        self._workspace: Path | None = None
        self._prefetched: str = ""
        self._nudge_state = _NudgeState()
        self._initialized = False

    # ------------------------------------------------------------------
    # Ciclo de vida
    # ------------------------------------------------------------------

    def initialize(self, workspace: str | Path) -> None:
        self._workspace = Path(workspace)
        memory_dir = self._workspace / "memory"
        from .memory_manager import MemoryManager
        self._manager = MemoryManager(memory_dir=memory_dir)
        self._manager.init_files()
        self._initialized = True

    def prefetch(self) -> None:
        if not self._initialized or self._manager is None:
            return
        parts: list[str] = []
        for fname in self._PREFETCH_FILES:
            try:
                content = self._manager.read_file(fname)
                if content and not content.startswith("[arquivo"):
                    # Pega apenas as últimas 40 linhas para não explodir o contexto
                    lines = content.splitlines()[-40:]
                    parts.append(f"### {fname}\n" + "\n".join(lines))
            except Exception:
                pass
        self._prefetched = "\n\n".join(parts)

    def on_turn_start(self, turn_index: int, messages: list[dict]) -> None:
        if not self._initialized or self._manager is None:
            return
        if turn_index % 5 == 0 and turn_index > 0:
            # Atualiza prefetch a cada 5 turnos para pegar novas entradas de memória
            self.prefetch()

    def sync_turn(self, turn_index: int, messages: list[dict]) -> None:
        pass  # LocalMemoryProvider é append-only; sem sincronização extra.

    def on_session_end(self, messages: list[dict]) -> None:
        if not self._initialized or self._manager is None:
            return
        try:
            self._manager.add_note(
                "Sessão finalizada",
                f"Sessão encerrada com {len(messages)} mensagens.",
            )
        except Exception:
            pass

    def on_pre_compress(self, messages: list[dict]) -> None:
        if not self._initialized or self._manager is None:
            return
        try:
            self._manager.add_runtime_lesson(
                "Compressão de contexto",
                f"Contexto comprimido com {len(messages)} mensagens.",
            )
        except Exception:
            pass

    def on_memory_write(self, key: str, value: str) -> None:
        self._nudge_state.last_write_turn = _current_turn_marker()

    # ------------------------------------------------------------------
    # Contexto
    # ------------------------------------------------------------------

    def system_prompt_block(self) -> str:
        if not self._prefetched:
            return ""
        truncated = self._prefetched[: self._MAX_SYSTEM_BLOCK_CHARS]
        return f"## Memória do Projeto\n\n{truncated}"

    def get_tool_schemas(self) -> list[dict]:
        return []

    def get_config_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "workspace": {
                    "type": "string",
                    "description": "Diretório de trabalho onde os arquivos de memória são armazenados.",
                }
            },
        }

    def on_delegation(self, sub_task: str, result: str) -> None:
        if not self._initialized or self._manager is None:
            return
        try:
            summary = result[:300].replace("\n", " ")
            self._manager.add_note(
                "Delegação registrada",
                f"Subtarefa: {sub_task[:120]}\nResultado: {summary}",
            )
        except Exception:
            pass

    def handle_tool_call(
        self, tool_name: str, tool_args: dict, tool_result: str
    ) -> None:
        if tool_name in ("memory", "memory_search"):
            self._nudge_state.last_write_turn = _current_turn_marker()

    # ------------------------------------------------------------------
    # Nudge
    # ------------------------------------------------------------------

    def should_nudge(
        self, turn_index: int, last_write_turn: int | None = None
    ) -> bool:
        lw = last_write_turn if last_write_turn is not None else self._nudge_state.last_write_turn
        result = super().should_nudge(turn_index, lw)
        if not result:
            return False
        # Só manda nudge 1x por intervalo (evita spam)
        if self._nudge_state.nudge_sent_at is not None:
            since_last = turn_index - self._nudge_state.nudge_sent_at
            if since_last < self._NUDGE_INTERVAL:
                return False
        self._nudge_state.nudge_sent_at = turn_index
        return True


def _current_turn_marker() -> int:
    """Retorna um marcador de turno baseado em tempo (usado só para nudge)."""
    return int(time.monotonic() * 10)


# ---------------------------------------------------------------------------
# SimpleVectorProvider — TF-IDF local, zero-deps, JSON-persisted
# ---------------------------------------------------------------------------

class SimpleVectorProvider(MemoryProvider):
    """TF-IDF vector memory persisted to ~/.bauer/vector_memory.json.

    Indexes assistant turns; on sync_turn re-ranks by similarity to the
    last user message. Zero external dependencies.
    """

    _MAX_DOCS = 200
    _TOP_K = 5
    _MAX_TEXT_LEN = 800

    def __init__(self, persist_path: Path | None = None) -> None:
        self._persist_path = persist_path  # resolved in initialize
        self._docs: list[dict] = []        # [{id, text, tokens: {word: count}}]
        self._relevant: list[str] = []
        self._initialized = False

    def initialize(self, workspace: str | Path) -> None:
        if self._persist_path is None:
            self._persist_path = Path(workspace) / "vector_memory.json"
        self._load()
        self._initialized = True

    def _load(self) -> None:
        try:
            if self._persist_path and self._persist_path.exists():
                data = json.loads(self._persist_path.read_text(encoding="utf-8"))
                self._docs = data.get("docs", [])
        except Exception:
            self._docs = []

    def _save(self) -> None:
        if not self._persist_path:
            return
        try:
            self._persist_path.parent.mkdir(parents=True, exist_ok=True)
            trimmed = self._docs[-self._MAX_DOCS:]
            self._persist_path.write_text(
                json.dumps({"docs": trimmed}, ensure_ascii=False),
                encoding="utf-8",
            )
        except Exception:
            pass

    def _tokenize(self, text: str) -> Counter:
        words = text.lower().split()
        tokens: Counter = Counter()
        for w in words:
            w = w.strip(".,;:!?()[]{}\"'`")
            if len(w) > 2:
                tokens[w] += 1
        return tokens

    def _cosine(self, a: dict, b: dict) -> float:
        common = set(a) & set(b)
        if not common:
            return 0.0
        dot = sum(a[w] * b[w] for w in common)
        mag_a = math.sqrt(sum(v * v for v in a.values()))
        mag_b = math.sqrt(sum(v * v for v in b.values()))
        if mag_a == 0 or mag_b == 0:
            return 0.0
        return dot / (mag_a * mag_b)

    def prefetch(self) -> None:
        self._relevant = [d["text"] for d in self._docs[-self._TOP_K:]]

    def sync_turn(self, turn_index: int, messages: list[dict]) -> None:
        last_user = next(
            (m["content"] for m in reversed(messages) if m.get("role") == "user" and m.get("content")),
            None,
        )
        last_assistant = next(
            (m["content"] for m in reversed(messages) if m.get("role") == "assistant" and m.get("content")),
            None,
        )
        if last_assistant and len(last_assistant) >= 60:
            text = last_assistant[: self._MAX_TEXT_LEN]
            tokens = dict(self._tokenize(text))
            doc = {"id": f"t{turn_index}", "text": text, "tokens": tokens}
            self._docs = [d for d in self._docs if d.get("id") != doc["id"]]
            self._docs.append(doc)
            self._save()

        if last_user:
            query_tokens = dict(self._tokenize(last_user))
            scored = sorted(
                self._docs,
                key=lambda d: self._cosine(query_tokens, d.get("tokens", {})),
                reverse=True,
            )
            self._relevant = [d["text"] for d in scored[: self._TOP_K]]

    def system_prompt_block(self) -> str:
        if not self._relevant:
            return ""
        snippets = "\n---\n".join(s[:400] for s in self._relevant[:3])
        return f"## Memória Vetorial (contexto anterior relevante)\n\n{snippets}"


# ---------------------------------------------------------------------------
# HttpMemoryProvider — generic RAG HTTP endpoint (GET /search + POST /upsert)
# ---------------------------------------------------------------------------

class HttpMemoryProvider(MemoryProvider):
    """Connects to any HTTP RAG endpoint (OpenAI-style /search + /upsert).

    GET  {base_url}/search?q=<text>&limit=5&namespace=<ns>
      → {"results": [{"text": "...", "score": 0.9}, ...]}

    POST {base_url}/upsert
      → body: {"content": "...", "namespace": "<ns>"}
    """

    _TIMEOUT = 5.0

    def __init__(self, base_url: str, api_key: str = "", namespace: str = "bauer") -> None:
        self._base = base_url.rstrip("/")
        self._api_key = api_key
        self._namespace = namespace
        self._snippets: list[str] = []

    def initialize(self, workspace: str | Path) -> None:
        pass  # stateless; no workspace setup needed

    def _headers(self) -> dict:
        h: dict = {"Content-Type": "application/json"}
        if self._api_key:
            h["Authorization"] = f"Bearer {self._api_key}"
        return h

    def prefetch(self) -> None:
        try:
            import httpx
            resp = httpx.get(
                f"{self._base}/search",
                params={"q": "context", "limit": 5, "namespace": self._namespace},
                headers=self._headers(),
                timeout=self._TIMEOUT,
            )
            resp.raise_for_status()
            data = resp.json()
            self._snippets = [r.get("text", "") for r in data.get("results", []) if r.get("text")]
        except Exception:
            self._snippets = []

    def sync_turn(self, turn_index: int, messages: list[dict]) -> None:
        last_assistant = next(
            (m["content"] for m in reversed(messages) if m.get("role") == "assistant" and m.get("content")),
            None,
        )
        if not last_assistant or len(last_assistant) < 50:
            return
        try:
            import httpx
            httpx.post(
                f"{self._base}/upsert",
                json={"content": last_assistant[:1000], "namespace": self._namespace},
                headers=self._headers(),
                timeout=self._TIMEOUT,
            )
        except Exception:
            pass

    def system_prompt_block(self) -> str:
        if not self._snippets:
            return ""
        joined = "\n---\n".join(s[:400] for s in self._snippets[:3])
        return f"## Memória Remota (RAG)\n\n{joined}"


# ---------------------------------------------------------------------------
# Mem0Provider — mem0.ai cloud memory API
# ---------------------------------------------------------------------------

class Mem0Provider(MemoryProvider):
    """Integrates with mem0.ai REST API.

    Requires MEM0_API_KEY environment variable (or pass api_key directly).
    GET  https://api.mem0.ai/v1/memories/?user_id=<uid>&limit=5
    POST https://api.mem0.ai/v1/memories/ {messages: [...], user_id: <uid>}
    """

    _BASE = "https://api.mem0.ai/v1"
    _TIMEOUT = 8.0

    def __init__(self, api_key: str = "", user_id: str = "bauer") -> None:
        import os
        self._api_key = api_key or os.environ.get("MEM0_API_KEY", "")
        self._user_id = user_id
        self._memories: list[str] = []

    def initialize(self, workspace: str | Path) -> None:
        pass

    def _headers(self) -> dict:
        return {
            "Authorization": f"Token {self._api_key}",
            "Content-Type": "application/json",
        }

    def prefetch(self) -> None:
        if not self._api_key:
            return
        try:
            import httpx
            resp = httpx.get(
                f"{self._BASE}/memories/",
                params={"user_id": self._user_id, "limit": 5},
                headers=self._headers(),
                timeout=self._TIMEOUT,
            )
            resp.raise_for_status()
            data = resp.json()
            results = data if isinstance(data, list) else data.get("results", [])
            self._memories = [
                r.get("memory", r.get("text", "")) for r in results if isinstance(r, dict)
            ]
            self._memories = [m for m in self._memories if m]
        except Exception:
            self._memories = []

    def sync_turn(self, turn_index: int, messages: list[dict]) -> None:
        if not self._api_key:
            return
        role_messages = [
            {"role": m["role"], "content": m["content"]}
            for m in messages[-4:]
            if m.get("role") in ("user", "assistant") and m.get("content")
        ]
        if not role_messages:
            return
        try:
            import httpx
            httpx.post(
                f"{self._BASE}/memories/",
                json={"messages": role_messages, "user_id": self._user_id},
                headers=self._headers(),
                timeout=self._TIMEOUT,
            )
        except Exception:
            pass

    def system_prompt_block(self) -> str:
        if not self._memories:
            return ""
        joined = "\n".join(f"- {m[:200]}" for m in self._memories[:5])
        return f"## Mem0 (memórias persistentes)\n\n{joined}"


# ---------------------------------------------------------------------------
# MultiMemoryProvider — aggregates multiple providers
# ---------------------------------------------------------------------------

class MultiMemoryProvider(MemoryProvider):
    """Aggregates multiple MemoryProvider instances into a single interface.

    All lifecycle hooks are delegated to every sub-provider in order.
    `system_prompt_block()` concatenates blocks (up to a total char limit).
    `get_tool_schemas()` merges schemas from all providers.
    `should_nudge()` fires if any provider fires.
    """

    _MAX_BLOCK_CHARS = 4000

    def __init__(self, providers: list[MemoryProvider]) -> None:
        self._providers = list(providers)

    # ------------------------------------------------------------------
    # Ciclo de vida
    # ------------------------------------------------------------------

    def initialize(self, workspace: str | Path) -> None:
        for p in self._providers:
            try:
                p.initialize(workspace)
            except Exception:
                pass

    def prefetch(self) -> None:
        for p in self._providers:
            try:
                p.prefetch()
            except Exception:
                pass

    def sync_turn(self, turn_index: int, messages: list[dict]) -> None:
        for p in self._providers:
            try:
                p.sync_turn(turn_index, messages)
            except Exception:
                pass

    def on_session_end(self, messages: list[dict]) -> None:
        for p in self._providers:
            try:
                p.on_session_end(messages)
            except Exception:
                pass

    def on_pre_compress(self, messages: list[dict]) -> None:
        for p in self._providers:
            try:
                p.on_pre_compress(messages)
            except Exception:
                pass

    def on_memory_write(self, key: str, value: str) -> None:
        for p in self._providers:
            try:
                p.on_memory_write(key, value)
            except Exception:
                pass

    def on_turn_start(self, turn_index: int, messages: list[dict]) -> None:
        for p in self._providers:
            try:
                p.on_turn_start(turn_index, messages)
            except Exception:
                pass

    def queue_prefetch(self) -> None:
        for p in self._providers:
            try:
                p.queue_prefetch()
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Contexto e tools
    # ------------------------------------------------------------------

    def system_prompt_block(self) -> str:
        parts: list[str] = []
        total = 0
        for p in self._providers:
            try:
                block = p.system_prompt_block()
                if block:
                    remaining = self._MAX_BLOCK_CHARS - total
                    if remaining <= 0:
                        break
                    truncated = block[:remaining]
                    parts.append(truncated)
                    total += len(truncated)
            except Exception:
                pass
        return "\n\n".join(parts)

    def get_tool_schemas(self) -> list[dict]:
        schemas: list[dict] = []
        seen_names: set[str] = set()
        for p in self._providers:
            try:
                for schema in p.get_tool_schemas():
                    name = schema.get("function", {}).get("name") or schema.get("name")
                    if name and name not in seen_names:
                        schemas.append(schema)
                        seen_names.add(name)
            except Exception:
                pass
        return schemas

    def get_config_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                p.__class__.__name__: p.get_config_schema()
                for p in self._providers
                if p.get_config_schema()
            },
        }

    def on_delegation(self, sub_task: str, result: str) -> None:
        for p in self._providers:
            try:
                p.on_delegation(sub_task, result)
            except Exception:
                pass

    def handle_tool_call(
        self, tool_name: str, tool_args: dict, tool_result: str
    ) -> None:
        for p in self._providers:
            try:
                p.handle_tool_call(tool_name, tool_args, tool_result)
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Nudge — fires if any provider fires
    # ------------------------------------------------------------------

    def should_nudge(self, turn_index: int, last_write_turn: int | None = None) -> bool:
        for p in self._providers:
            try:
                if p.should_nudge(turn_index, last_write_turn):  # type: ignore[call-arg]
                    return True
            except TypeError:
                try:
                    if p.should_nudge(turn_index):  # type: ignore[call-arg]
                        return True
                except Exception:
                    pass
            except Exception:
                pass
        return False

    def nudge_message(self) -> str:
        for p in self._providers:
            try:
                msg = p.nudge_message()
                if msg:
                    return msg
            except Exception:
                pass
        return super().nudge_message()


# ---------------------------------------------------------------------------
# HonchoProvider — honcho.dev session metamessages
# ---------------------------------------------------------------------------

class HonchoProvider(MemoryProvider):
    """Integrates with Honcho session memory API (honcho.dev).

    Requires HONCHO_API_KEY environment variable.
    Stores and retrieves metamessages of type "memory" scoped to a session.
    """

    _BASE = "https://api.honcho.dev/v1"
    _TIMEOUT = 8.0

    def __init__(
        self,
        api_key: str = "",
        user_id: str = "bauer",
        session_id: str = "default",
    ) -> None:
        import os
        self._api_key = api_key or os.environ.get("HONCHO_API_KEY", "")
        self._user_id = user_id
        self._session_id = session_id
        self._memories: list[str] = []

    def initialize(self, workspace: str | Path) -> None:
        pass

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

    def prefetch(self) -> None:
        if not self._api_key:
            return
        try:
            import httpx
            resp = httpx.get(
                f"{self._BASE}/apps/bauer/users/{self._user_id}/sessions/{self._session_id}/metamessages",
                params={"metamessage_type": "memory", "size": 5},
                headers=self._headers(),
                timeout=self._TIMEOUT,
            )
            resp.raise_for_status()
            data = resp.json()
            items = data if isinstance(data, list) else data.get("items", data.get("results", []))
            self._memories = [
                r.get("content", "") for r in items if isinstance(r, dict)
            ]
            self._memories = [m for m in self._memories if m]
        except Exception:
            self._memories = []

    def sync_turn(self, turn_index: int, messages: list[dict]) -> None:
        if not self._api_key:
            return
        last_assistant = next(
            (m.get("content", "") for m in reversed(messages) if m.get("role") == "assistant"),
            "",
        )
        if not last_assistant:
            return
        try:
            import httpx
            httpx.post(
                f"{self._BASE}/apps/bauer/users/{self._user_id}/sessions/{self._session_id}/metamessages",
                json={"metamessage_type": "memory", "content": last_assistant},
                headers=self._headers(),
                timeout=self._TIMEOUT,
            )
        except Exception:
            pass

    def system_prompt_block(self) -> str:
        if not self._memories:
            return ""
        joined = "\n".join(f"- {m[:200]}" for m in self._memories[:5])
        return f"## Honcho (memórias de sessão)\n\n{joined}"


# ---------------------------------------------------------------------------
# SupermemoryProvider — supermemory.ai semantic search
# ---------------------------------------------------------------------------

class SupermemoryProvider(MemoryProvider):
    """Integrates with Supermemory.ai REST API.

    Requires SUPERMEMORY_API_KEY environment variable.
    POST /search for retrieval; POST /memories to store.
    """

    _BASE = "https://api.supermemory.ai/v1"
    _TIMEOUT = 8.0

    def __init__(self, api_key: str = "") -> None:
        import os
        self._api_key = api_key or os.environ.get("SUPERMEMORY_API_KEY", "")
        self._memories: list[str] = []

    def initialize(self, workspace: str | Path) -> None:
        pass

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

    def prefetch(self) -> None:
        if not self._api_key:
            return
        try:
            import httpx
            resp = httpx.post(
                f"{self._BASE}/search",
                json={"q": "contexto bauer", "limit": 5},
                headers=self._headers(),
                timeout=self._TIMEOUT,
            )
            resp.raise_for_status()
            data = resp.json()
            results = data.get("results", [])
            self._memories = [
                r.get("content", "") for r in results if isinstance(r, dict)
            ]
            self._memories = [m for m in self._memories if m]
        except Exception:
            self._memories = []

    def sync_turn(self, turn_index: int, messages: list[dict]) -> None:
        if not self._api_key:
            return
        last_two = [
            m.get("content", "")
            for m in messages[-2:]
            if m.get("role") in ("user", "assistant") and m.get("content")
        ]
        if not last_two:
            return
        content = "\n".join(last_two)
        try:
            import httpx
            httpx.post(
                f"{self._BASE}/memories",
                json={"content": content, "tags": ["bauer"]},
                headers=self._headers(),
                timeout=self._TIMEOUT,
            )
        except Exception:
            pass

    def system_prompt_block(self) -> str:
        if not self._memories:
            return ""
        joined = "\n".join(f"- {m[:200]}" for m in self._memories[:5])
        return f"## Supermemory\n\n{joined}"


# ---------------------------------------------------------------------------
# HindsightProvider — local knowledge-graph (triplas sujeito-predicado-objeto)
# ---------------------------------------------------------------------------

class HindsightProvider(MemoryProvider):
    """Local knowledge-graph provider persisted in ~/.bauer/hindsight.json.

    Extracts simple (subject, predicate, object) triples from user messages
    using regex patterns for Portuguese copula/possession verbs.
    No external API required.
    """

    _MAX_FACTS = 200
    _TIMEOUT = 8.0  # unused (local), kept for interface consistency
    _VERBS = re.compile(r"\b(é|são|tem|usa)\b", re.IGNORECASE)

    def __init__(self, base_dir: Path | None = None) -> None:
        self._base = base_dir or (Path.home() / ".bauer")
        self._store = self._base / "hindsight.json"
        self._facts: list[dict] = []

    def initialize(self, workspace: str | Path) -> None:
        pass

    def _load(self) -> list[dict]:
        try:
            if self._store.exists():
                return json.loads(self._store.read_text(encoding="utf-8")).get("facts", [])
        except Exception:
            pass
        return []

    def _save(self, facts: list[dict]) -> None:
        try:
            self._base.mkdir(parents=True, exist_ok=True)
            self._store.write_text(
                json.dumps({"facts": facts}, ensure_ascii=False), encoding="utf-8"
            )
        except Exception:
            pass

    def prefetch(self) -> None:
        self._facts = self._load()

    def sync_turn(self, turn_index: int, messages: list[dict]) -> None:
        user_msgs = [
            m.get("content", "")
            for m in messages[-4:]
            if m.get("role") == "user" and m.get("content")
        ]
        new_facts = list(self._load())
        changed = False
        for text in user_msgs:
            for line in text.splitlines():
                m = self._VERBS.search(line)
                if m:
                    verb = m.group(0)
                    subject = line[: m.start()].strip()[:30]
                    obj = line[m.end() :].strip()[:60]
                    if subject and obj:
                        new_facts.append({
                            "subject": subject,
                            "predicate": verb,
                            "object": obj,
                            "ts": time.time(),
                        })
                        changed = True
        if changed:
            # circular buffer
            new_facts = new_facts[-self._MAX_FACTS :]
            self._facts = new_facts
            self._save(new_facts)

    def system_prompt_block(self) -> str:
        if not self._facts:
            return ""
        top5 = sorted(self._facts, key=lambda f: f.get("ts", 0), reverse=True)[:5]
        lines = "\n".join(
            f"- {f['subject']} {f['predicate']} {f['object']}" for f in top5
        )
        return f"## Hindsight (fatos)\n\n{lines}"

    def get_config_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "base_dir": {
                    "type": "string",
                    "description": "Diretório base onde hindsight.json é armazenado (padrão: ~/.bauer).",
                },
                "max_facts": {
                    "type": "integer",
                    "description": "Limite máximo de fatos no buffer circular.",
                    "default": 200,
                },
            },
        }

    def on_delegation(self, sub_task: str, result: str) -> None:
        facts = self._load()
        facts.append({
            "subject": "delegação",
            "predicate": "resultou",
            "object": result[:60],
            "ts": time.time(),
        })
        facts = facts[-self._MAX_FACTS:]
        self._facts = facts
        self._save(facts)

    def handle_tool_call(
        self, tool_name: str, tool_args: dict, tool_result: str
    ) -> None:
        if tool_name not in ("write_file", "patch", "memory"):
            return
        snippet = tool_result[:80].replace("\n", " ")
        if not snippet:
            return
        facts = self._load()
        facts.append({
            "subject": tool_name,
            "predicate": "retornou",
            "object": snippet,
            "ts": time.time(),
        })
        facts = facts[-self._MAX_FACTS:]
        self._facts = facts
        self._save(facts)


# ---------------------------------------------------------------------------
# RetainDBProvider — retaindb.com persistent memory
# ---------------------------------------------------------------------------

class RetainDBProvider(MemoryProvider):
    """Integrates with RetainDB REST API.

    Requires RETAINDB_API_KEY environment variable.
    GET /memories?limit=5 for retrieval; POST /memories to store.
    """

    _BASE = "https://api.retaindb.com/v1"
    _TIMEOUT = 8.0

    def __init__(self, api_key: str = "") -> None:
        import os
        self._api_key = api_key or os.environ.get("RETAINDB_API_KEY", "")
        self._memories: list[str] = []

    def initialize(self, workspace: str | Path) -> None:
        pass

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

    def prefetch(self) -> None:
        if not self._api_key:
            return
        try:
            import httpx
            resp = httpx.get(
                f"{self._BASE}/memories",
                params={"limit": 5},
                headers=self._headers(),
                timeout=self._TIMEOUT,
            )
            resp.raise_for_status()
            data = resp.json()
            items = data if isinstance(data, list) else data.get("results", [])
            self._memories = [
                r.get("text", "") for r in items if isinstance(r, dict)
            ]
            self._memories = [m for m in self._memories if m]
        except Exception:
            self._memories = []

    def sync_turn(self, turn_index: int, messages: list[dict]) -> None:
        if not self._api_key:
            return
        last_content = next(
            (m.get("content", "") for m in reversed(messages) if m.get("content")),
            "",
        )
        if not last_content:
            return
        try:
            import httpx
            httpx.post(
                f"{self._BASE}/memories",
                json={"text": last_content, "metadata": {"source": "bauer"}},
                headers=self._headers(),
                timeout=self._TIMEOUT,
            )
        except Exception:
            pass

    def system_prompt_block(self) -> str:
        if not self._memories:
            return ""
        joined = "\n".join(f"- {m[:200]}" for m in self._memories[:5])
        return f"## RetainDB\n\n{joined}"


# ---------------------------------------------------------------------------
# Registry simples
# ---------------------------------------------------------------------------

_DEFAULT_PROVIDER: MemoryProvider | None = None


def get_memory_provider() -> MemoryProvider:
    """Retorna o provider de memória ativo.

    Por padrão cria MultiMemoryProvider([LocalMemoryProvider, SimpleVectorProvider]),
    ativando a busca vetorial local sem configuração extra.
    """
    global _DEFAULT_PROVIDER
    if _DEFAULT_PROVIDER is None:
        _DEFAULT_PROVIDER = MultiMemoryProvider([LocalMemoryProvider(), SimpleVectorProvider()])
    return _DEFAULT_PROVIDER


def set_memory_provider(provider: MemoryProvider) -> None:
    """Configura o provider de memória (injeção de dependência / testes)."""
    global _DEFAULT_PROVIDER
    _DEFAULT_PROVIDER = provider


def reset_memory_provider() -> None:
    """Reseta o provider para None (usado em testes)."""
    global _DEFAULT_PROVIDER
    _DEFAULT_PROVIDER = None
