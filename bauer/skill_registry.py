"""Registro de skills sugeridas por frequência de tarefas.

Regra do projeto: nunca cria skill executável automaticamente.
Apenas detecta padrões repetidos e registra sugestão em SKILLS_LEARNED.md
para aprovação manual do usuário.
"""

from __future__ import annotations

import re
from collections import Counter
from pathlib import Path

_SKILL_THRESHOLD = 3  # quantas vezes uma tarefa precisa aparecer para virar sugestão

# Padrões de tarefas comuns — mapeados para nome de skill sugerida
_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"\b(diagno|doctor|ollama.*status|status.*ollama)\b", re.I), "diagnose_ollama"),
    (re.compile(r"\b(criar.*arquivo|write.*file|escrever.*arquivo)\b", re.I), "create_file"),
    (re.compile(r"\b(listar.*arquivo|list.*file|ls|dir)\b", re.I), "list_files"),
    (re.compile(r"\b(buscar|search|procurar|grep)\b", re.I), "search_text"),
    (re.compile(r"\b(criar.*site|create.*site|web.*site)\b", re.I), "create_website"),
    (re.compile(r"\b(testar.*modelo|test.*model|qual.*modelo)\b", re.I), "test_model"),
    (re.compile(r"\b(resumir|summarize|resumo)\b", re.I), "summarize"),
    (re.compile(r"\b(instalar|install|pip|npm)\b", re.I), "install_package"),
    (re.compile(r"\b(git|commit|push|pull|branch)\b", re.I), "git_operation"),
    (re.compile(r"\b(debug|erro|error|bug|fix|corrigir)\b", re.I), "debug_code"),
]


class SkillRegistry:
    """Detecta tarefas repetidas e sugere skills para aprovação manual."""

    def __init__(self, memory_dir: str | Path = "memory"):
        self.memory_dir = Path(memory_dir)
        self._session_counts: Counter[str] = Counter()

    def observe(self, user_message: str) -> str | None:
        """Analisa mensagem e retorna nome da skill sugerida se limiar atingido.

        Retorna None se nenhuma sugestão gerada.
        """
        for pattern, skill_name in _PATTERNS:
            if pattern.search(user_message):
                self._session_counts[skill_name] += 1
                total = self._session_counts[skill_name] + self._load_count(skill_name)
                if total >= _SKILL_THRESHOLD and not self._already_suggested(skill_name):
                    self._record_suggestion(skill_name, total)
                    return skill_name
        return None

    def _load_count(self, skill_name: str) -> int:
        """Conta quantas vezes a skill aparece em SKILLS_LEARNED.md."""
        p = self.memory_dir / "SKILLS_LEARNED.md"
        if not p.exists():
            return 0
        return p.read_text(encoding="utf-8").count(skill_name)

    def _already_suggested(self, skill_name: str) -> bool:
        p = self.memory_dir / "SKILLS_LEARNED.md"
        if not p.exists():
            return False
        return f"sugestão: {skill_name}" in p.read_text(encoding="utf-8")

    def _record_suggestion(self, skill_name: str, count: int) -> None:
        try:
            from .memory_manager import MemoryManager
            mm = MemoryManager(self.memory_dir)
            mm.append_entry(
                "SKILLS_LEARNED.md",
                f"sugestão: {skill_name}",
                fields={
                    "ocorrencias": str(count),
                    "status": "pendente_aprovacao",
                },
                body=(
                    f"Tarefa '{skill_name}' detectada {count}x. "
                    "Aprovar manualmente antes de virar skill disponivel."
                ),
            )
        except Exception:
            pass
