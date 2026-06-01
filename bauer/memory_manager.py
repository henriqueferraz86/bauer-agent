"""Gerenciador de memória Markdown do Bauer Agent (Fase 3).

Decisão 4: Markdown para coisas que o humano lê e edita.
Decisão 5: MODEL_EXPERIENCE.md inclui machine_id para tornar aprendizado portável.

Regras:
- Sempre APPEND, nunca sobrescreve arquivo inteiro.
- Toda entrada tem timestamp UTC.
- Arquivos são simples o suficiente para editar no Bloco de Notas.
- Nada de vector DB, RAG ou skill automática nesta fase.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

# Nomes canônicos dos arquivos de memória.
MEMORY_FILES = {
    "MEMORY": "MEMORY.md",
    "DECISIONS": "DECISIONS.md",
    "FAILED_ATTEMPTS": "FAILED_ATTEMPTS.md",
    "MODEL_EXPERIENCE": "MODEL_EXPERIENCE.md",
    "USER_PREFERENCES": "USER_PREFERENCES.md",
    "RUNTIME_LESSONS": "RUNTIME_LESSONS.md",
    "SKILLS_LEARNED": "SKILLS_LEARNED.md",
}

_HEADERS: dict[str, str] = {
    "MEMORY.md": (
        "# MEMORY.md — Notas gerais de sessao\n\n"
        "Resumos de sessao, observacoes e contexto geral do projeto.\n"
        "Adicione via: bauer memory add-note\n\n---\n"
    ),
    "DECISIONS.md": (
        "# DECISIONS.md — Decisoes tecnicas\n\n"
        "Registro auditavel de decisoes tomadas durante o desenvolvimento.\n"
        "Adicione via: bauer memory add-decision\n\n---\n"
    ),
    "FAILED_ATTEMPTS.md": (
        "# FAILED_ATTEMPTS.md — Tentativas falhas e correcoes\n\n"
        "O que falhou, por que falhou e o que corrigiu.\n"
        "Adicione via: bauer memory add-failure\n\n---\n"
    ),
    "MODEL_EXPERIENCE.md": (
        "# MODEL_EXPERIENCE.md — Experiencia com modelos por maquina\n\n"
        "Historico de desempenho de modelos com contexto e RAM.\n"
        "Cada entrada inclui machine_id (Decisao 5) para filtrar por ambiente.\n"
        "Adicione via: bauer memory add-model-exp\n\n---\n"
    ),
    "USER_PREFERENCES.md": (
        "# USER_PREFERENCES.md — Preferencias tecnicas do usuario\n\n"
        "Profile preferido, modelo favorito, opcoes recorrentes.\n"
        "Adicione via: bauer memory add-pref\n\n---\n"
    ),
    "RUNTIME_LESSONS.md": (
        "# RUNTIME_LESSONS.md — Decisoes automaticas do Bauer\n\n"
        "Toda decisao automatica tomada pelo runtime fica registrada aqui.\n"
        "Auditavel e reversivel. Adicione via: bauer memory add-lesson\n\n---\n"
    ),
    "SKILLS_LEARNED.md": (
        "# SKILLS_LEARNED.md — Skills sugeridas por frequencia\n\n"
        "Tarefas repetidas que podem virar skill. Sugestoes apenas — nada e executado automaticamente.\n"
        "Aprovacao manual necessaria antes de virar skill disponivel.\n\n---\n"
    ),
}


class MemoryManager:
    def __init__(self, memory_dir: str | Path = "memory"):
        self.memory_dir = Path(memory_dir)

    # --- inicializacao ----------------------------------------------------------

    def init_files(self) -> list[Path]:
        """Cria diretório memory/ e todos os arquivos .md se não existirem.

        Nunca sobrescreve arquivo que já existe.
        """
        self.memory_dir.mkdir(parents=True, exist_ok=True)
        created: list[Path] = []
        for filename, header in _HEADERS.items():
            p = self.memory_dir / filename
            if not p.exists():
                p.write_text(header, encoding="utf-8")
                created.append(p)
        return created

    # --- leitura ----------------------------------------------------------------

    def read_file(self, filename: str) -> str:
        """Lê o conteúdo de um arquivo de memória."""
        p = self._resolve(filename)
        if not p.exists():
            return f"[arquivo {p.name} nao encontrado — rode: bauer memory init]"
        return p.read_text(encoding="utf-8")

    def list_files(self) -> list[tuple[str, int, int]]:
        """Retorna [(nome, linhas, entradas)] para todos os arquivos de memória."""
        result = []
        for name in MEMORY_FILES.values():
            p = self.memory_dir / name
            if p.exists():
                lines = p.read_text(encoding="utf-8").splitlines()
                entries = sum(1 for l in lines if l.startswith("## ["))
            else:
                lines = []
                entries = 0
            result.append((name, len(lines), entries))
        return result

    # --- escrita de entradas ----------------------------------------------------

    def append_entry(
        self,
        filename: str,
        title: str,
        fields: dict[str, str] | None = None,
        body: str = "",
    ) -> Path:
        """Anexa uma entrada formatada a um arquivo de memória.

        Formato:
            ## [2026-05-27 16:30 UTC] Titulo

            - campo: valor

            Corpo opcional.
        """
        p = self._resolve(filename)
        if not p.exists():
            self.init_files()

        ts = _now_utc()
        lines: list[str] = [f"\n## [{ts}] {title}\n"]
        if fields:
            for key, val in fields.items():
                lines.append(f"- {key}: {val}")
            lines.append("")
        if body:
            lines.append(body.strip())
            lines.append("")

        with p.open("a", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
        return p

    def add_decision(self, title: str, body: str, context: str = "") -> Path:
        fields: dict[str, str] = {}
        if context:
            fields["context"] = context
        return self.append_entry("DECISIONS.md", title, fields or None, body)

    def add_failure(self, title: str, error: str, fix: str = "") -> Path:
        fields: dict[str, str] = {"error": error}
        if fix:
            fields["fix"] = fix
        return self.append_entry("FAILED_ATTEMPTS.md", title, fields)

    def add_model_experience(
        self,
        model: str,
        context_tokens: int,
        result: str,
        ram_used_mb: int,
        machine_id: str,
        lesson: str = "",
    ) -> Path:
        """Registra experiência de modelo com machine_id (Decisão 5)."""
        title = f"{model} — contexto {context_tokens}"
        fields: dict[str, str] = {
            "machine_id": machine_id,
            "context_tokens": str(context_tokens),
            "result": result,
            "ram_used_mb": str(ram_used_mb),
        }
        if lesson:
            fields["lesson"] = lesson
        return self.append_entry("MODEL_EXPERIENCE.md", title, fields)

    def add_runtime_lesson(
        self,
        decision: str,
        reason: str,
        how_to_undo: str = "",
    ) -> Path:
        fields: dict[str, str] = {"reason": reason}
        if how_to_undo:
            fields["how_to_undo"] = how_to_undo
        return self.append_entry("RUNTIME_LESSONS.md", decision, fields)

    def add_note(self, title: str, body: str) -> Path:
        return self.append_entry("MEMORY.md", title, body=body)

    def add_preference(self, key: str, value: str) -> Path:
        return self.append_entry("USER_PREFERENCES.md", key, {"value": value})

    # --- busca TF-IDF -----------------------------------------------------------

    def search(
        self,
        query: str,
        top_k: int = 5,
        files: list[str] | None = None,
    ) -> list[dict]:
        """Busca semântica leve via TF-IDF nos arquivos de memória.

        Divide cada arquivo em blocos de seção (## [timestamp] Título).
        Pontua cada bloco pelo score TF-IDF em relação à query.
        Retorna os top_k blocos mais relevantes.

        Args:
            query: Texto de busca.
            top_k: Número de resultados a retornar.
            files: Lista de arquivos a pesquisar (padrão: todos de MEMORY_FILES).

        Returns:
            Lista de dicts com: file, title, score, snippet.
        """
        import math
        import re

        # Tokeniza texto em palavras minúsculas
        def _tokenize(text: str) -> list[str]:
            return re.findall(r"\b[a-zA-ZÀ-ú0-9_]{2,}\b", text.lower())

        def _term_freq(tokens: list[str]) -> dict[str, float]:
            if not tokens:
                return {}
            freq: dict[str, int] = {}
            for t in tokens:
                freq[t] = freq.get(t, 0) + 1
            n = len(tokens)
            return {t: c / n for t, c in freq.items()}

        # Coleta todos os blocos de todos os arquivos
        target_files = files or list(MEMORY_FILES.values())
        blocks: list[dict] = []  # {file, title, text}

        for fname in target_files:
            p = self.memory_dir / fname
            if not p.exists():
                continue
            content = p.read_text(encoding="utf-8")
            # Divide em seções: cada "## " é um novo bloco
            parts = re.split(r"(?=^## )", content, flags=re.MULTILINE)
            for part in parts:
                part = part.strip()
                if not part:
                    continue
                # Extrai título da primeira linha se for cabeçalho de seção
                lines = part.splitlines()
                title = lines[0].lstrip("#").strip() if lines else fname
                blocks.append({"file": fname, "title": title, "text": part})

        if not blocks:
            return []

        # Calcula IDF sobre todos os blocos
        query_tokens = _tokenize(query)
        if not query_tokens:
            return []

        n_docs = len(blocks)
        doc_freqs: dict[str, int] = {}
        block_tfs: list[dict[str, float]] = []

        for b in blocks:
            tokens = _tokenize(b["text"])
            tf = _term_freq(tokens)
            block_tfs.append(tf)
            for term in set(tf):
                doc_freqs[term] = doc_freqs.get(term, 0) + 1

        idf: dict[str, float] = {
            term: math.log((n_docs + 1) / (freq + 1)) + 1.0
            for term, freq in doc_freqs.items()
        }

        # Pontua cada bloco com TF-IDF
        results: list[tuple[float, dict]] = []
        for i, (block, tf) in enumerate(zip(blocks, block_tfs)):
            score = sum(tf.get(t, 0.0) * idf.get(t, 0.0) for t in query_tokens)
            if score > 0:
                # Snippet: primeiras 200 chars de conteúdo (sem a linha de título)
                text_body = "\n".join(block["text"].splitlines()[1:]).strip()
                snippet = text_body[:200].replace("\n", " ")
                results.append((score, {
                    "file": block["file"],
                    "title": block["title"],
                    "score": round(score, 4),
                    "snippet": snippet,
                }))

        results.sort(key=lambda x: -x[0])
        return [r[1] for r in results[:top_k]]

    # --- TTL cleanup ------------------------------------------------------------

    def cleanup_old_entries(
        self,
        max_age_days: int = 90,
        files: list[str] | None = None,
        dry_run: bool = False,
    ) -> dict[str, int]:
        """Remove entradas de memória mais antigas que `max_age_days` dias.

        Cada entrada começa com a linha "## [YYYY-MM-DD HH:MM UTC] título".
        Entradas sem timestamp são preservadas (ex: cabeçalho do arquivo).

        Args:
            max_age_days: Entradas com mais de N dias são removidas.
            files: Lista de arquivos a limpar (padrão: todos de MEMORY_FILES).
            dry_run: Se True, conta entradas sem modificar os arquivos.

        Returns:
            Dict {nome_arquivo: n_entradas_removidas}.
        """
        import re
        from datetime import timedelta

        cutoff = datetime.now(timezone.utc) - timedelta(days=max_age_days)
        _TS_RE = re.compile(r"^## \[(\d{4}-\d{2}-\d{2})")

        target_files = files or list(MEMORY_FILES.values())
        removed: dict[str, int] = {}

        for fname in target_files:
            p = self.memory_dir / fname
            if not p.exists():
                continue
            content = p.read_text(encoding="utf-8")
            # Divide em blocos — cabeçalho do arquivo + seções de entrada
            raw_blocks = re.split(r"(?=^## )", content, flags=re.MULTILINE)
            kept: list[str] = []
            n_removed = 0

            for block in raw_blocks:
                m = _TS_RE.match(block.lstrip())
                if m is None:
                    # Cabeçalho do arquivo ou bloco sem timestamp → preserva sempre
                    kept.append(block)
                    continue
                try:
                    entry_date = datetime.strptime(m.group(1), "%Y-%m-%d").replace(
                        tzinfo=timezone.utc
                    )
                except ValueError:
                    kept.append(block)
                    continue
                if entry_date < cutoff:
                    n_removed += 1
                else:
                    kept.append(block)

            removed[fname] = n_removed
            if n_removed > 0 and not dry_run:
                p.write_text("".join(kept), encoding="utf-8")

        return removed

    # --- internos ---------------------------------------------------------------

    def _resolve(self, filename: str) -> Path:
        """Aceita nome canônico ('DECISIONS') ou nome de arquivo ('DECISIONS.md')."""
        if not filename.endswith(".md"):
            filename = MEMORY_FILES.get(filename.upper(), filename + ".md")
        return self.memory_dir / filename


def _now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
