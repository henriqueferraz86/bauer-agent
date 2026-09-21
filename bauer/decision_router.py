"""Decisão estruturada opcional com fallback seguro para o roteador local.

O cliente fala apenas com a API System One e transforma a resposta em um
``RouteDecision`` já validado. Jev não recebe ferramentas, não executa ações e
não tem acesso a credenciais além da chave usada no transporte.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx

from .model_router import ModelProfile, RouteDecision, classify_task

_TASK_TYPES = {"conversation", "tool_call", "coding", "architecture", "reasoning"}
_COMPLEXITIES = {"low", "medium", "high"}
_PROFILES = {"fast", "balanced", "coding", "heavy"}


@dataclass(frozen=True)
class JevDecisionConfig:
    enabled: bool = False
    fallback_enabled: bool = True
    api_key: str = ""
    endpoint: str = "https://api.typesafe.ai/v1/systemone"
    model: str = "jev-latest"
    timeout_seconds: float = 2.0
    min_confidence: float = 0.65

    @classmethod
    def from_config(cls, config: Any | None) -> "JevDecisionConfig":
        raw = getattr(config, "decision", None)
        if raw is None:
            return cls()
        return cls(
            enabled=bool(getattr(raw, "jev_enabled", False)),
            fallback_enabled=bool(getattr(raw, "fallback_enabled", True)),
            api_key=str(getattr(raw, "api_key", "") or "").strip(),
            endpoint=str(getattr(raw, "endpoint", cls.endpoint) or cls.endpoint).strip(),
            model=str(getattr(raw, "model", cls.model) or cls.model).strip(),
            timeout_seconds=float(getattr(raw, "timeout_seconds", cls.timeout_seconds)),
            min_confidence=float(getattr(raw, "min_confidence", cls.min_confidence)),
        )


class JevDecisionClient:
    """Cliente mínimo e síncrono da API System One.

    O transporte é deliberadamente pequeno para o extra Jev continuar
    opcional. `httpx` já é dependência base do Bauer.
    """

    def __init__(self, config: JevDecisionConfig):
        self.config = config

    def decide(self, message: str) -> RouteDecision:
        if not self.config.api_key:
            raise RuntimeError("TYPESAFE_API_KEY não configurada")
        response = httpx.post(
            self.config.endpoint,
            headers={
                "Authorization": f"Bearer {self.config.api_key}",
                "Content-Type": "application/json",
            },
            json={
                "state": message,
                "model": self.config.model,
                "questions": {
                    "task_type": {
                        "type": "choice",
                        "instructions": "Qual é o tipo principal desta tarefa?",
                        "criteria": {
                            "conversation": "saudação ou conversa simples",
                            "tool_call": "ler, escrever, listar, executar ou pesquisar",
                            "coding": "programação, código, debugging ou testes",
                            "architecture": "arquitetura, redesign ou migração ampla",
                            "reasoning": "análise, explicação ou raciocínio geral",
                        },
                    },
                    "complexity": {
                        "type": "choice",
                        "instructions": "Qual é a complexidade da tarefa?",
                        "criteria": {
                            "low": "curta e direta",
                            "medium": "exige algum raciocínio ou contexto",
                            "high": "ampla, técnica ou com múltiplas etapas",
                        },
                    },
                    "profile": {
                        "type": "choice",
                        "instructions": "Qual tier de modelo deve atender a tarefa?",
                        "criteria": {
                            "fast": "resposta simples e rápida",
                            "balanced": "tarefa geral com equilíbrio",
                            "coding": "código, debugging ou implementação",
                            "heavy": "arquitetura, análise profunda ou alta complexidade",
                        },
                    },
                    "orchestrate": {
                        "type": "noul",
                        "instructions": "A tarefa exige múltiplos objetivos distintos e coordenação de agentes?",
                    },
                },
            },
            timeout=self.config.timeout_seconds,
        )
        response.raise_for_status()
        payload = response.json()
        return self._parse(payload)

    @staticmethod
    def _parse(payload: Any) -> RouteDecision:
        if not isinstance(payload, dict) or not isinstance(payload.get("answers"), dict):
            raise ValueError("resposta Jev sem answers")
        answers = payload["answers"]
        task_type = _choice(answers, "task_type", _TASK_TYPES)
        complexity = _choice(answers, "complexity", _COMPLEXITIES)
        profile = _choice(answers, "profile", _PROFILES)
        confidence = _confidence(answers, "profile")
        orchestrate = _noul(answers, "orchestrate") >= 0.5
        if confidence <= 0:
            raise ValueError("resposta Jev sem confiança válida")
        reason = f"Jev classificou tarefa '{task_type}' de complexidade '{complexity}'"
        return RouteDecision(
            task_type=task_type,
            complexity=complexity,
            profile=profile,
            reason=reason,
            matched=[],
            confidence=confidence,
            source="jev",
            orchestrate=orchestrate,
        )


def decide_with_fallback(
    message: str,
    profiles: dict[str, ModelProfile] | None = None,
    config: Any | None = None,
) -> RouteDecision:
    """Decide via Jev quando habilitado; caso contrário usa fallback local.

    A função nunca deixa indisponibilidade de Jev derrubar um turno. Quando o
    fallback é explicitamente desligado, retorna um perfil `balanced` seguro,
    sem usar classificação externa adicional.
    """
    settings = JevDecisionConfig.from_config(config)
    if not settings.enabled:
        return _with_profiles(classify_task(message), profiles)
    try:
        decision = JevDecisionClient(settings).decide(message)
        if decision.confidence < settings.min_confidence:
            raise RuntimeError(
                f"confiança Jev abaixo do mínimo ({decision.confidence:.2f} < {settings.min_confidence:.2f})"
            )
        return _with_profiles(decision, profiles)
    except (httpx.HTTPError, RuntimeError, TypeError, ValueError, KeyError) as exc:
        if settings.fallback_enabled:
            fallback = classify_task(message)
            fallback.source = "heuristic"
            fallback.error = _safe_error(exc)
            fallback.reason = f"fallback heurístico: {fallback.reason}"
            return _with_profiles(fallback, profiles)
        return RouteDecision(
            task_type="reasoning",
            complexity="medium",
            profile="balanced",
            reason="Jev indisponível e fallback desligado; perfil seguro aplicado",
            confidence=0.0,
            source="safe-default",
            error=_safe_error(exc),
        )


def _with_profiles(decision: RouteDecision, profiles: dict[str, ModelProfile] | None) -> RouteDecision:
    if profiles and decision.profile in profiles:
        selected = profiles[decision.profile]
        decision.provider, decision.model = selected.provider, selected.model
    return decision


def _choice(answers: dict[str, Any], name: str, allowed: set[str]) -> str:
    answer = answers.get(name)
    value = answer.get("choice") if isinstance(answer, dict) else None
    value = str(value or "").strip().lower()
    if value not in allowed:
        raise ValueError(f"resposta Jev inválida para {name}")
    return value


def _confidence(answers: dict[str, Any], name: str) -> float:
    answer = answers.get(name)
    raw = answer.get("confidence") if isinstance(answer, dict) else None
    if not isinstance(raw, (int, float, str)):
        raise ValueError("confiança Jev ausente")
    value = float(raw)
    if not 0.0 <= value <= 1.0:
        raise ValueError("confiança Jev fora do intervalo")
    return value


def _noul(answers: dict[str, Any], name: str) -> float:
    answer = answers.get(name)
    raw = answer.get("noul", 0.0) if isinstance(answer, dict) else 0.0
    value = float(raw)
    return value if 0.0 <= value <= 1.0 else 0.0


def _safe_error(exc: Exception) -> str:
    text = str(exc).replace("\n", " ").strip()
    return text[:240] or exc.__class__.__name__
