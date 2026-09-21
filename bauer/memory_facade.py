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

_log = logging.getLogger("bauer.memory_facade")


class UnifiedMemory:
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
                self.markdown.add_note(
                    note_title,
                    "\n".join(
                        (
                            content.strip(),
                            "",
                            f"- memory_id: {record.id}",
                            f"- scope: {record.scope}",
                            f"- source: {record.source}",
                            f"- confidence: {record.confidence:.2f}",
                        )
                    ),
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
