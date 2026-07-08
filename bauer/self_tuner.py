"""Auto-tuner de startup do Bauer Agent (Fase 6).

Le o historico de aprendizado + RAM disponivel e retorna o melhor
modelo e contexto para esta maquina, com motivo auditavel.

Nao altera config.yaml. Nao executa nada. Apenas recomenda.
Toda decisao e registrada em RUNTIME_LESSONS.md.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class TuneResult:
    model: str
    context_tokens: int
    reason: str
    adjustments: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


class SelfTuner:
    """Combina historico de aprendizado + RAM para auto-tunar o runtime.

    Responsabilidades:
    - Consulta LearningEngine para falhas e recomendacoes historicas.
    - Verifica RAM disponivel via ModelRegistry.
    - Seleciona o melhor modelo e contexto para esta maquina.
    - Registra decisoes em RUNTIME_LESSONS.md.
    """

    def __init__(
        self,
        memory_dir: str | Path = "memory",
        safety_margin_mb: int = 1024,
    ):
        self.memory_dir = Path(memory_dir)
        self.safety_margin_mb = safety_margin_mb

    def tune(
        self,
        desired_model: str,
        desired_context: int,
        minimum_context: int,
        installed_models: list[str],
        registry,
        ram_available_mb: int,
        machine_id: str = "",
        honor_user_preference: bool = False,
    ) -> TuneResult:
        """Calcula modelo e contexto ideais para esta maquina.

        Args:
            desired_model: Modelo do config.yaml.
            desired_context: Contexto solicitado no config.yaml.
            minimum_context: Contexto minimo aceitavel.
            installed_models: Modelos instalados no Ollama.
            registry: ModelRegistry carregado.
            ram_available_mb: RAM livre antes de iniciar o modelo.
            machine_id: ID da maquina (para filtrar historico).
            honor_user_preference: Se True, nunca troca o modelo (só ajusta contexto).

        Returns:
            TuneResult com modelo, contexto, motivo e lista de ajustes.
        """
        from .learning_engine import LearningEngine
        from .model_registry import contexto_seguro

        adjustments: list[str] = []
        warnings: list[str] = []
        engine = LearningEngine(self.memory_dir)

        # --- Passo 1: verificar historico de falhas para o modelo desejado ---
        bad_history = self._get_bad_history(engine, desired_model, machine_id)
        if bad_history >= 2:
            _warn = f"'{desired_model}' falhou {bad_history}x nesta maquina — historico negativo."
            if honor_user_preference:
                _warn += " (mantido por preferência explícita do usuário)"
            warnings.append(_warn)

        # --- Passo 2: verificar RAM ---
        info = registry.get(desired_model)
        safe_ctx = contexto_seguro(info, ram_available_mb, self.safety_margin_mb) if info else desired_context
        # honor_user_preference bloqueia troca de modelo — só ajusta contexto
        ram_ok = (safe_ctx > 0 and bad_history < 2) or honor_user_preference

        if not ram_ok:
            # Tenta encontrar melhor alternativa instalada
            best_model, best_ctx = self._find_best_alternative(
                desired_model, installed_models, registry, ram_available_mb, machine_id, engine
            )
            if best_model and best_ctx:
                adjustments.append(
                    f"Modelo trocado de '{desired_model}' para '{best_model}' "
                    f"({'RAM insuficiente' if bad_history < 2 else 'historico negativo'})"
                )
                model = best_model
                context = min(best_ctx, desired_context)
            else:
                # Sem alternativa — usa desejado mesmo com aviso
                warnings.append(
                    "Nenhuma alternativa encontrada. Usando modelo configurado mesmo com risco."
                )
                model = desired_model
                context = max(safe_ctx, minimum_context) if safe_ctx > 0 else minimum_context
        else:
            model = desired_model
            # Ajusta contexto se necessario
            if safe_ctx < desired_context:
                adjustments.append(
                    f"Contexto reduzido de {desired_context} para {safe_ctx} "
                    f"(limite seguro de RAM: {ram_available_mb} MB disponiveis)"
                )
                context = max(safe_ctx, minimum_context)
            else:
                context = desired_context

        # Garante minimo
        if context < minimum_context:
            context = minimum_context
            adjustments.append(f"Contexto forcado ao minimo: {minimum_context}")

        # --- Passo 3: verificar contexto estavel no historico ---
        stable_ctx = self._best_stable_context(engine, model, machine_id)
        if stable_ctx and stable_ctx < context:
            adjustments.append(
                f"Contexto ajustado para {stable_ctx} (valor estavel no historico desta maquina)"
            )
            context = stable_ctx

        reason = self._build_reason(desired_model, model, desired_context, context, adjustments, warnings)

        # Registra apenas se houve ajustes
        if adjustments:
            self._log_adjustments(model, context, reason, adjustments)

        return TuneResult(
            model=model,
            context_tokens=context,
            reason=reason,
            adjustments=adjustments,
            warnings=warnings,
        )

    # --- internos -------------------------------------------------------------

    def _get_bad_history(self, engine, model_name: str, machine_id: str) -> int:
        try:
            exps = engine.load_experience()
            bad = {"oom", "slow", "error", "out of memory"}
            return sum(
                1 for e in exps
                if (not e.machine_id or e.machine_id == machine_id)
                and model_name.lower() in e.title.lower()
                and any(b in e.result.lower() for b in bad)
            )
        except Exception:
            return 0

    def _find_best_alternative(
        self,
        exclude_model: str,
        installed: list[str],
        registry,
        ram_mb: int,
        machine_id: str,
        engine,
    ) -> tuple[str, int] | tuple[None, None]:
        from .model_registry import contexto_seguro

        candidates: list[tuple[str, int, int]] = []
        for name in installed:
            if name == exclude_model:
                continue
            info = registry.get(name)
            if info is None:
                continue
            ctx = contexto_seguro(info, ram_mb, self.safety_margin_mb)
            if ctx > 0:
                bad = self._get_bad_history(engine, name, machine_id)
                if bad < 2:
                    candidates.append((name, info.ram_base_mb, ctx))

        if not candidates:
            return None, None
        # Prefere maior modelo que cabe
        best = max(candidates, key=lambda x: x[1])
        return best[0], best[2]

    def _best_stable_context(self, engine, model_name: str, machine_id: str) -> int | None:
        """Retorna o maior contexto com resultado 'ok' no historico, ou None."""
        try:
            exps = engine.load_experience()
            ok_ctxs = [
                e.context_tokens for e in exps
                if (not e.machine_id or e.machine_id == machine_id)
                and model_name.lower() in e.title.lower()
                and e.result == "ok"
                and e.context_tokens > 0
            ]
            return max(ok_ctxs) if ok_ctxs else None
        except Exception:
            return None

    def _build_reason(
        self,
        orig_model: str,
        final_model: str,
        orig_ctx: int,
        final_ctx: int,
        adjustments: list[str],
        warnings: list[str],
    ) -> str:
        if not adjustments and not warnings:
            return f"Configuracao '{final_model}' com {final_ctx} tokens — sem ajustes necessarios."
        parts = [f"Modelo: {orig_model}"]
        if final_model != orig_model:
            parts.append(f"-> {final_model}")
        if final_ctx != orig_ctx:
            parts.append(f"Contexto: {orig_ctx} -> {final_ctx}")
        if adjustments:
            parts.extend(adjustments)
        return " | ".join(parts)

    def _log_adjustments(self, model: str, context: int, reason: str, adjustments: list[str]) -> None:
        try:
            from .memory_manager import MemoryManager
            mm = MemoryManager(self.memory_dir)
            mm.add_runtime_lesson(
                decision=f"Auto-tuner: modelo='{model}', contexto={context}",
                reason=reason,
                undo="Edite config.yaml ou rode 'bauer learning reset' para resetar historico.",
            )
        except Exception:
            pass
