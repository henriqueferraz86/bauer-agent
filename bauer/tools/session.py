"""Session-search tools: busca em sessoes via ..sqlite_session_store."""

from __future__ import annotations



from .base import ToolError


class SessionToolsMixin:

    def _session_search(self, args: dict) -> str:
        """Busca full-text/regex em memória persistente e logs de sessão.

        Fontes:
          memory   — .bauer_memory.json (chaves + valores)
          sessions — arquivos .jsonl / .json de sessão no workspace
          all      — ambas (padrão)
        """
        import re as _re

        action = str(args.get("action", "search")).lower().strip()
        source = str(args.get("source", "all")).lower().strip()

        if action == "recent":
            n = self._coerce_int(args.get("n", 10), default=10, minimum=1)
            return self._session_search_recent(n, source)

        if action != "search":
            raise ToolError("session_search: action deve ser 'search' ou 'recent'.")

        query = str(args.get("query", "")).strip()
        if not query:
            raise ToolError("session_search search requer 'query'.")

        results: list[str] = []

        # ── Busca em memory ───────────────────────────────────────────────
        if source in ("memory", "all"):
            mem = self._memory_load()
            try:
                pattern = _re.compile(query, _re.IGNORECASE)
            except _re.error:
                pattern = _re.compile(_re.escape(query), _re.IGNORECASE)

            mem_hits = []
            for key, entry in mem.items():
                val = entry["value"] if isinstance(entry, dict) else str(entry)
                ts = entry.get("updated_at", "") if isinstance(entry, dict) else ""
                if pattern.search(key) or pattern.search(val):
                    preview = val[:120].replace("\n", " ")
                    mem_hits.append(f"  [memory] {key}: {preview} ({ts[:10]})")

            if mem_hits:
                results.append(f"Memory ({len(mem_hits)} resultado(s)):")
                results.extend(mem_hits)

        # ── Busca em logs de sessão ───────────────────────────────────────
        if source in ("sessions", "all"):
            session_hits = self._search_session_files(query)
            if session_hits:
                results.append(f"\nSessoes ({len(session_hits)} resultado(s)):")
                results.extend(session_hits)

        if not results:
            return f"Nenhum resultado para '{query}' em '{source}'."

        header = f"session_search '{query}' em [{source}]:\n"
        return header + "\n".join(results)

    def _session_search_recent(self, n: int, source: str) -> str:
        """Retorna as N entradas mais recentes da memória/sessões."""
        results: list[str] = []
        n = max(1, min(n, 100))

        if source in ("memory", "all"):
            mem = self._memory_load()
            sorted_entries = sorted(
                mem.items(),
                key=lambda x: x[1].get("updated_at", "") if isinstance(x[1], dict) else "",
                reverse=True,
            )[:n]
            if sorted_entries:
                results.append(f"Memory (mais recentes {len(sorted_entries)}):")
                for key, entry in sorted_entries:
                    val = entry["value"] if isinstance(entry, dict) else str(entry)
                    ts = entry.get("updated_at", "")[:10] if isinstance(entry, dict) else ""
                    results.append(f"  [{ts}] {key}: {val[:80].replace(chr(10), ' ')}")

        return "\n".join(results) if results else "Nenhuma entrada recente encontrada."

    def _search_session_files(self, query: str, top_k: int = 20) -> list[str]:
        """Busca em sessões salvas — usa FTS5 (SqliteSessionStore) ou fallback JSONL.

        Tenta SqliteSessionStore primeiro (FTS5 semântico).
        Se o banco não existir, cai para busca linear em .jsonl.
        """
        # ── Caminho 1: SqliteSessionStore (FTS5) ──────────────────────────
        sessions_db_candidates = [
            self.workspace.parent / "memory" / "sessions" / "sessions.db",
            self.workspace / "memory" / "sessions" / "sessions.db",
        ]
        for db_path in sessions_db_candidates:
            if db_path.exists():
                try:
                    from ..sqlite_session_store import SqliteSessionStore as _SqliteStore
                    store = _SqliteStore(db_path.parent)
                    results = store.search_sessions(query, top_k=top_k)
                    if results:
                        return [
                            f"  [session:{r['session_id']}] [{r['role']}] {r['snippet']}"
                            for r in results
                        ]
                    return []  # banco existe mas sem resultados
                except Exception:
                    pass  # fallback para JSONL

        # ── Caminho 2: fallback linear em .jsonl ──────────────────────────
        import re as _re
        hits: list[str] = []
        try:
            pattern = _re.compile(query, _re.IGNORECASE)
        except _re.error:
            pattern = _re.compile(_re.escape(query), _re.IGNORECASE)

        search_dirs = [self.workspace, self.workspace.parent]
        for d in search_dirs:
            if not d.exists():
                continue
            for ext in ("*.jsonl", "*.json"):
                for fpath in list(d.glob(ext))[:20]:
                    if fpath.name.startswith(".bauer_"):
                        continue
                    try:
                        text = fpath.read_text(encoding="utf-8", errors="ignore")
                        for i, line in enumerate(text.splitlines()):
                            if pattern.search(line):
                                preview = line[:100].strip()
                                hits.append(f"  [{fpath.name}:{i+1}] {preview}")
                                if len(hits) >= top_k:
                                    return hits
                    except Exception:
                        continue
        return hits
