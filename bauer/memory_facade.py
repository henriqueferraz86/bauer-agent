"""Fachada única para memória humana e memória auditável do runtime.

Os formatos antigos continuam existindo por compatibilidade. Esta camada
define o contrato para novas gravações: o registro runtime é criado primeiro e
uma projeção Markdown legível é escrita de forma best-effort.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, cast

from .core.runtime.memory import MemoryRecord, RuntimeMemoryManager
from .memory_manager import MemoryManager
from .memory_provider import MemoryProvider

_log = logging.getLogger("bauer.memory_facade")


class UnifiedMemory(MemoryProvider):
    """Coordena memória auditável e a projeção Markdown legível."""

    def __init__(
        self,
        *,
        memory_dir: str | Path = "memory",
        runtime_root: str | Path | None = None,
        runtime: RuntimeMemoryManager | None = None,
        markdown: MemoryManager | None = None,
    ) -> None:
        self.markdown = markdown or MemoryManager(memory_dir)
        self.runtime = runtime or RuntimeMemoryManager(
            root=runtime_root or Path(memory_dir) / "runtime"
        )
        self._workspace: Path | None = None
        self._prefetched = ""
        self._initialized = False

    def initialize(self, workspace: str | Path) -> None:
        """Inicializa os stores no workspace da sessão do agente."""
        self._workspace = Path(workspace)
        memory_dir = self._workspace / "memory"
        self.markdown = MemoryManager(memory_dir)
        self.runtime = RuntimeMemoryManager(root=memory_dir / "runtime")
        self.markdown.init_files()
        self._initialized = True

    def prefetch(self) -> None:
        """Carrega a parte humana da memória para o prompt do agente."""
        if not self._initialized:
            return
        parts: list[str] = []
        for filename in ("MEMORY.md", "USER_PREFERENCES.md", "RUNTIME_LESSONS.md"):
            try:
                content = self.markdown.read_file(filename)
                if content and not content.startswith("[arquivo"):
                    parts.append(f"### {filename}\n" + "\n".join(content.splitlines()[-40:]))
            except Exception as exc:  # noqa: BLE001 - memória é acessória
                _log.debug("unified memory prefetch failed: %s", exc)
        self._prefetched = "\n\n".join(parts)

    def on_turn_start(self, turn_index: int, messages: list[dict]) -> None:
        if not self._initialized:
            return
        query = next(
            (
                str(message.get("content", ""))
                for message in reversed(messages)
                if message.get("role") == "user" and message.get("content")
            ),
            "",
        )
        if query:
            hits = self.search(query, top_k=3)
            if hits:
                self._prefetched = "\n\n".join(
                    [
                        self._prefetched,
                        "### Memória unificada\n"
                        + "\n".join(f"- {hit['snippet'][:240]}" for hit in hits),
                    ]
                ).strip()
        elif turn_index % 5 == 0:
            self.prefetch()

    def sync_turn(self, turn_index: int, messages: list[dict]) -> None:
        return None

    def on_session_end(self, messages: list[dict]) -> None:
        if self._initialized:
            self.remember(
                scope="agent",
                content=f"Sessão encerrada com {len(messages)} mensagens.",
                source="session",
                title="Sessão finalizada",
            )

    def on_pre_compress(self, messages: list[dict]) -> None:
        if self._initialized:
            self.remember(
                scope="agent",
                content=f"Contexto comprimido com {len(messages)} mensagens.",
                source="context",
                title="Compressão de contexto",
                markdown_file="RUNTIME_LESSONS.md",
            )

    def on_memory_write(self, key: str, value: str) -> None:
        return None

    def system_prompt_block(self) -> str:
        return f"## Memória do Projeto\n\n{self._prefetched[:4000]}" if self._prefetched else ""

    def get_config_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "workspace": {
                    "type": "string",
                    "description": "Diretório de trabalho da memória unificada.",
                }
            },
        }

    def remember(
        self,
        *,
        scope: str,
        content: str,
        source: str,
        title: str | None = None,
        confidence: float = 1.0,
        valid_until: str | None = None,
        project_to_markdown: bool = True,
        markdown_file: str = "MEMORY.md",
    ) -> MemoryRecord:
        """Grava um fato e, por padrão, projeta-o em ``MEMORY.md``.

        A gravação auditável é a operação principal. Se o arquivo Markdown
        estiver indisponível, o registro runtime permanece válido e a falha é
        apenas registrada em DEBUG.
        """
        record = self.runtime.write(
            scope=scope,
            content=content,
            source=source,
            confidence=confidence,
            valid_until=valid_until,
        )
        if project_to_markdown:
            try:
                note_title = (title or content.strip().splitlines()[0][:120]).strip()
                note_title = note_title.replace("\n", " ") or "Memória runtime"
                self.markdown.append_entry(
                    markdown_file,
                    note_title,
                    fields={
                        "memory_id": record.id,
                        "scope": record.scope,
                        "source": record.source,
                        "confidence": f"{record.confidence:.2f}",
                    },
                    body=content.strip(),
                )
            except Exception as exc:  # noqa: BLE001 - projeção é acessória
                _log.debug("memory markdown projection failed: %s", exc)
        return record

    def search(
        self,
        query: str,
        *,
        top_k: int = 10,
        scope: str | None = None,
        include_expired: bool = False,
    ) -> list[dict[str, Any]]:
        """Busca nos dois formatos e remove duplicatas por conteúdo."""
        if top_k <= 0:
            return []

        results: list[dict[str, Any]] = []
        seen: set[str] = set()
        try:
            runtime_hits = cast(
                list[MemoryRecord],
                self.runtime.search(query, scope=scope, include_expired=include_expired),
            )
            for record in runtime_hits:
                key = record.content.strip().casefold()
                if key in seen:
                    continue
                seen.add(key)
                results.append(
                    {
                        "source": "runtime",
                        "id": record.id,
                        "scope": record.scope,
                        "title": record.source,
                        "score": 1.0,
                        "snippet": record.content[:240],
                        "content": record.content,
                    }
                )
        except Exception as exc:  # noqa: BLE001 - busca auxiliar é best-effort
            _log.debug("runtime memory search failed: %s", exc)

        try:
            markdown_hits = self.markdown.search(query, top_k=top_k)
        except Exception as exc:  # noqa: BLE001 - busca auxiliar é best-effort
            _log.debug("markdown memory search failed: %s", exc)
            markdown_hits = []
        for hit in markdown_hits:
            content = str(hit.get("snippet", "")).strip()
            key = content.casefold()
            if key and any(existing in key or key in existing for existing in seen):
                continue
            if key:
                seen.add(key)
            results.append(
                {
                    "source": "markdown",
                    "id": None,
                    "scope": None,
                    "title": hit.get("title", ""),
                    "score": hit.get("score", 0.0),
                    "snippet": content,
                    "content": content,
                    "file": hit.get("file", ""),
                }
            )
        results.sort(key=lambda item: float(item.get("score", 0.0)), reverse=True)
        return results[:top_k]
