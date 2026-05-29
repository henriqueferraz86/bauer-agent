"""FeedbackStore — registra eventos de aprendizado (Fase 7).

Wrapper de conveniencia sobre MemoryManager para registrar falhas,
sucessos e preferencias de forma consistente.

Nao analisa nem recomenda — apenas persiste dados para o LearningEngine.
"""

from __future__ import annotations

from pathlib import Path

from .memory_manager import MemoryManager


class FeedbackStore:
    """Registra falhas, sucessos e preferencias nos arquivos de memoria.

    Cada registro e append-only e auditavel via bauer memory show.
    """

    def __init__(self, memory_dir: str | Path = "memory"):
        self.mm = MemoryManager(memory_dir)

    def record_model_failure(
        self,
        model: str,
        context_tokens: int,
        error: str,
        machine_id: str = "",
    ) -> None:
        """Registra falha de modelo em FAILED_ATTEMPTS.md."""
        title = f"{model} — contexto {context_tokens} falhou"
        fields: dict[str, str] = {"error": error}
        if machine_id:
            fields["machine_id"] = machine_id
        self.mm.append_entry("FAILED_ATTEMPTS.md", title, fields)

    def record_model_success(
        self,
        model: str,
        context_tokens: int,
        ram_used_mb: int,
        machine_id: str,
        lesson: str = "",
    ) -> None:
        """Registra sucesso de modelo em MODEL_EXPERIENCE.md."""
        self.mm.add_model_experience(
            model=model,
            context_tokens=context_tokens,
            result="ok",
            ram_used_mb=ram_used_mb,
            machine_id=machine_id,
            lesson=lesson,
        )

    def record_preference(self, key: str, value: str) -> None:
        """Registra preferencia do usuario em USER_PREFERENCES.md."""
        self.mm.add_preference(key, value)
