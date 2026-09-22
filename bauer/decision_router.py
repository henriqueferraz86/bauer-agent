"""Decisão estruturada opcional com fallback seguro para o roteador local.

O cliente fala apenas com a API System One e transforma a resposta em um
``RouteDecision`` já validado. Jev não recebe ferramentas, não executa ações e
não tem acesso a credenciais além da chave usada no transporte.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Iterable

import httpx

from .model_router import ModelProfile, RouteDecision, classify_task

_TASK_TYPES = {"conversation", "tool_call", "coding", "architecture", "reasoning"}
_COMPLEXITIES = {"low", "medium", "high"}
_PROFILES = {"fast", "balanced", "coding", "heavy"}
_RUNTIMES = {"bauer_native", "agno"}


@dataclass(frozen=True)
class DecisionAlternative:
    """Uma alternativa comparável devolvida pelo decisor."""

    option: str
    probability: float
    rationale: str = ""


@dataclass(frozen=True)
class JevDecisionConfig:
    enabled: bool = False
    fallback_enabled: bool = True
    api_key: str = ""
    endpoint: str = "https://api.typesafe.ai/v1/systemone"
    model: str = "jev-latest"
    timeout_seconds: float = 2.0
    min_confidence: float = 0.65
    memory_enabled: bool = True

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
            memory_enabled=bool(getattr(raw, "memory_enabled", cls.memory_enabled)),
        )


class JevDecisionClient:
    """Cliente mínimo e síncrono da API System One.

    O transporte é deliberadamente pequeno para o extra Jev continuar
    opcional. `httpx` já é dependência base do Bauer.
    """

    def __init__(self, config: JevDecisionConfig):
        self.config = config

    def decide(
        self,
        message: str,
        *,
        candidates: dict[str, list[str]] | None = None,
        memory: list[dict[str, Any]] | None = None,
    ) -> RouteDecision:
        if not self.config.api_key:
            raise RuntimeError("TYPESAFE_API_KEY não configurada")
        state: str | dict[str, Any] = message
        if candidates or memory:
            state = {
                "message": message,
                "candidates": candidates or {},
                "similar_decisions": memory or [],
            }
        response = httpx.post(
            self.config.endpoint,
            headers={
                "Authorization": f"Bearer {self.config.api_key}",
                "Content-Type": "application/json",
            },
            json={
                "state": state,
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
                    "runtime": {
                        "type": "choice",
                        "instructions": "Qual runtime deve executar a decisão?",
                        "criteria": {runtime: runtime for runtime in sorted(_RUNTIMES)},
                    },
                    "team": {
                        "type": "choice",
                        "instructions": "Escolha um time do catálogo quando a tarefa exigir colaboração.",
                        "criteria": {item: item for item in (candidates or {}).get("teams", [])},
                    },
                    "agent": {
                        "type": "choice",
                        "instructions": "Escolha o agente mais adequado do catálogo.",
                        "criteria": {item: item for item in (candidates or {}).get("agents", [])},
                    },
                    "tools": {
                        "type": "list",
                        "instructions": "Liste somente tools do catálogo que serão necessárias.",
                        "criteria": {item: item for item in (candidates or {}).get("tools", [])},
                    },
                    "strategy": {
                        "type": "choice",
                        "instructions": "Escolha a estratégia de execução.",
                        "criteria": {
                            "direct": "resolver em uma etapa",
                            "plan_then_execute": "planejar e executar em etapas",
                            "team": "coordenar múltiplos agentes",
                        },
                    },
                    "plan": {
                        "type": "list",
                        "instructions": "Retorne passos curtos, ordenados e verificáveis.",
                    },
                    "alternatives": {
                        "type": "list",
                        "instructions": "Compare alternativas e informe probability entre 0 e 1 para cada uma.",
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
        probabilities, alternatives = _probabilities(answers, profile, confidence)
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
            probabilities=probabilities,
            alternatives=alternatives,
            runtime=_choice(answers, "runtime", _RUNTIMES, default="bauer_native"),
            team_id=_value(answers, "team"),
            agent_id=_value(answers, "agent"),
            tools=_list_value(answers, "tools"),
            strategy=_choice(
                answers,
                "strategy",
                {"direct", "plan_then_execute", "team"},
                default="team" if orchestrate else "direct",
            ),
            plan=_list_value(answers, "plan"),
        )


def decide_with_fallback(
    message: str,
    profiles: dict[str, ModelProfile] | None = None,
    config: Any | None = None,
    *,
    teams: Iterable[str] | None = None,
    agents: Iterable[str] | None = None,
    available_tools: Iterable[str] | None = None,
    decision_memory: Any | None = None,
    session_id: str | None = None,
) -> RouteDecision:
    """Decide via Jev quando habilitado; caso contrário usa fallback local.

    A função nunca deixa indisponibilidade de Jev derrubar um turno. Quando o
    fallback é explicitamente desligado, retorna um perfil `balanced` seguro,
    sem usar classificação externa adicional.
    """
    settings = JevDecisionConfig.from_config(config)
    candidates = _candidate_catalog(teams, agents, available_tools)
    memory_hits = _memory_context(decision_memory, message) if settings.memory_enabled else []
    if not settings.enabled:
        decision = _fallback_decision(message, candidates, memory_hits)
        return _record_decision(
            _with_profiles(decision, profiles), message, decision_memory, session_id,
        )
    try:
        decision = JevDecisionClient(settings).decide(
            message, candidates=candidates, memory=memory_hits,
        )
        if decision.confidence < settings.min_confidence:
            raise RuntimeError(
                f"confiança Jev abaixo do mínimo ({decision.confidence:.2f} < {settings.min_confidence:.2f})"
            )
        decision = _sanitize_decision(decision, candidates)
        return _record_decision(
            _with_profiles(decision, profiles), message, decision_memory, session_id,
        )
    except (httpx.HTTPError, RuntimeError, TypeError, ValueError, KeyError) as exc:
        if settings.fallback_enabled:
            fallback = _fallback_decision(message, candidates, memory_hits)
            fallback.source = "heuristic"
            fallback.error = _safe_error(exc)
            fallback.reason = f"fallback heurístico: {fallback.reason}"
            return _record_decision(
                _with_profiles(fallback, profiles), message, decision_memory, session_id,
            )
        return _record_decision(RouteDecision(
            task_type="reasoning",
            complexity="medium",
            profile="balanced",
            reason="Jev indisponível e fallback desligado; perfil seguro aplicado",
            confidence=0.0,
            source="safe-default",
            error=_safe_error(exc),
        ), message, decision_memory, session_id)


def _candidate_catalog(
    teams: Iterable[str] | None,
    agents: Iterable[str] | None,
    available_tools: Iterable[str] | None,
) -> dict[str, list[str]]:
    return {
        "teams": sorted({str(item).strip() for item in teams or [] if str(item).strip()}),
        "agents": sorted({str(item).strip() for item in agents or [] if str(item).strip()}),
        "tools": sorted({str(item).strip() for item in available_tools or [] if str(item).strip()}),
    }


def _fallback_decision(
    message: str,
    candidates: dict[str, list[str]],
    memory_hits: list[dict[str, Any]],
) -> RouteDecision:
    decision = classify_task(message)
    text = message.lower()
    decision.orchestrate = decision.complexity == "high" and any(
        marker in text
        for marker in (" e depois", " e também", " e tambem", "vários", "varios", "time", "equipe")
    )
    decision.probabilities = _heuristic_probabilities(decision.profile, decision.complexity)
    decision.alternatives = [
        DecisionAlternative(option=key, probability=value)
        for key, value in decision.probabilities.items()
    ]
    decision.team_id = _match_candidate(message, candidates["teams"])
    if decision.orchestrate and candidates["teams"] and not decision.team_id:
        decision.team_id = candidates["teams"][0]
    decision.agent_id = _match_candidate(message, candidates["agents"])
    if not decision.agent_id:
        decision.agent_id = _agent_for_task(decision.task_type, candidates["agents"], message)
    decision.runtime = "agno" if decision.team_id or decision.agent_id else "bauer_native"
    decision.tools = _recommended_tools(decision, candidates["tools"])
    decision.strategy = "team" if decision.orchestrate and decision.team_id else (
        "plan_then_execute" if decision.complexity == "high" else "direct"
    )
    decision.plan = _fallback_plan(decision)
    decision.memory_hits = memory_hits
    return decision


def _agent_for_task(task_type: str, candidates: list[str], message: str = "") -> str:
    """Escolhe o agente formal mais próximo quando Jev está desligado."""
    text = message.lower()
    specialist_hints = (
        (("security", "segurança", "seguranca", "vulnerabilidade", "secret", "segredo", "pentest"), "security"),
        (("research", "pesquisa", "pesquise", "pesquisar", "investigue", "investigar", "documentação oficial", "fontes"), "research"),
        (("documentação", "documentacao", "documente", "documentar", "readme", "guia técnico", "guia tecnico", "tutorial"), "docs"),
        (("dados", "dataset", "data pipeline", "etl", "sql", "csv", "database", "banco de dados", "análise de dados", "analise de dados"), "data"),
        (("arquitetura", "architecture", "dependências", "dependencias", "impacto técnico", "impacto tecnico"), "architect"),
    )
    for markers, hint in specialist_hints:
        if any(marker in text for marker in markers):
            specialist = next((item for item in candidates if hint in item.lower()), "")
            if specialist:
                return specialist
    if task_type == "architecture" and any(
        marker in text for marker in ("implemente", "implement", "código", "codigo", "teste", "testes")
    ):
        task_type = "coding"
    hints = {
        "coding": ("dev", "code", "engineering"),
        "architecture": ("product", "dev", "architecture"),
        "tool_call": ("devops", "ops", "dev"),
        "reasoning": ("product", "qa"),
        "conversation": ("product",),
    }
    for hint in hints.get(task_type, ()):
        for candidate in candidates:
            if hint in candidate.lower():
                return candidate
    return candidates[0] if candidates else ""


def _sanitize_decision(decision: RouteDecision, candidates: dict[str, list[str]]) -> RouteDecision:
    decision.probabilities, decision.alternatives = _normalise_probabilities(
        decision.probabilities, decision.profile, decision.confidence,
    )
    if decision.team_id not in candidates["teams"]:
        decision.team_id = ""
    if decision.agent_id not in candidates["agents"]:
        decision.agent_id = ""
    decision.tools = [tool for tool in decision.tools if tool in candidates["tools"]]
    if decision.runtime not in _RUNTIMES:
        decision.runtime = "bauer_native"
    if decision.strategy not in {"direct", "plan_then_execute", "team"}:
        decision.strategy = "team" if decision.orchestrate else "direct"
    decision.plan = [str(step).strip()[:400] for step in decision.plan if str(step).strip()][:12]
    return decision


def _heuristic_probabilities(profile: str, complexity: str) -> dict[str, float]:
    base = {name: 0.05 for name in sorted(_PROFILES)}
    base[profile] = 0.85 if complexity == "high" else 0.75
    total = sum(base.values())
    return {key: round(value / total, 6) for key, value in base.items()}


def _normalise_probabilities(
    probabilities: dict[str, float], profile: str, confidence: float,
) -> tuple[dict[str, float], list[DecisionAlternative]]:
    cleaned = {key: max(0.0, float(value)) for key, value in probabilities.items() if key in _PROFILES}
    if not cleaned:
        cleaned = _heuristic_probabilities(profile, "high" if confidence >= 0.8 else "medium")
    total = sum(cleaned.values()) or 1.0
    normalized = {key: round(value / total, 6) for key, value in cleaned.items()}
    return normalized, [DecisionAlternative(option=key, probability=value) for key, value in normalized.items()]


def _probabilities(
    answers: dict[str, Any], profile: str, confidence: float,
) -> tuple[dict[str, float], list[DecisionAlternative]]:
    answer = answers.get("probabilities")
    raw = answer.get("probabilities") if isinstance(answer, dict) else answer
    probabilities: dict[str, float] = {}
    if isinstance(raw, dict):
        for key, value in raw.items():
            try:
                probabilities[str(key)] = float(value)
            except (TypeError, ValueError):
                continue
    elif isinstance(raw, list):
        for item in raw:
            if isinstance(item, dict):
                key = item.get("option") or item.get("choice") or item.get("profile")
                value = item.get("probability", item.get("confidence", 0))
                if key and value is not None:
                    try:
                        probabilities[str(key)] = float(value)
                    except (TypeError, ValueError):
                        continue
    return _normalise_probabilities(probabilities, profile, confidence)


def _with_profiles(decision: RouteDecision, profiles: dict[str, ModelProfile] | None) -> RouteDecision:
    if profiles and decision.profile in profiles:
        selected = profiles[decision.profile]
        decision.provider, decision.model = selected.provider, selected.model
    return decision


def _choice(
    answers: dict[str, Any],
    name: str,
    allowed: set[str],
    *,
    default: str = "",
) -> str:
    answer = answers.get(name)
    value = answer.get("choice") if isinstance(answer, dict) else None
    value = str(value or default).strip().lower()
    if value not in allowed:
        if default:
            return default
        raise ValueError(f"resposta Jev inválida para {name}")
    return value


def _value(answers: dict[str, Any], name: str) -> str:
    answer = answers.get(name)
    if isinstance(answer, dict):
        value = answer.get("choice", answer.get("value", answer.get("text", "")))
    else:
        value = answer
    return str(value or "").strip()


def _list_value(answers: dict[str, Any], name: str) -> list[str]:
    answer = answers.get(name)
    if isinstance(answer, dict):
        value = answer.get("choices", answer.get("list", answer.get("value", [])))
    else:
        value = answer
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    if isinstance(value, (list, tuple, set, frozenset)):
        return [str(item).strip() for item in value if str(item).strip()]
    return []


def _match_candidate(message: str, candidates: list[str]) -> str:
    text = message.lower()
    for candidate in candidates:
        if candidate.lower() in text:
            return candidate
    return ""


def _recommended_tools(decision: RouteDecision, available: list[str]) -> list[str]:
    if not available:
        return []
    wanted: list[str] = []
    if decision.task_type == "tool_call":
        wanted.extend(["read_file", "list_dir", "web_search"])
    if decision.task_type in {"coding", "architecture"}:
        wanted.extend(["read_file", "search_text", "write_file"])
    if decision.orchestrate:
        wanted.append("delegate_task")
    return [tool for tool in wanted if tool in available]


def _fallback_plan(decision: RouteDecision) -> list[str]:
    if decision.strategy == "direct":
        return ["resolver a solicitação e verificar a resposta"]
    if decision.strategy == "team":
        return ["selecionar coordenador", "delegar aos agentes do time", "consolidar e validar o resultado"]
    return ["entender o objetivo", "executar as etapas necessárias", "validar o resultado"]


def _memory_context(memory: Any | None, message: str) -> list[dict[str, Any]]:
    if memory is None:
        return []
    try:
        return [
            {
                "id": record.id,
                "decision": record.decision[:500],
                "outcome": record.outcome,
                "score": record.score,
                "similarity": record.similarity,
            }
            for record in memory.search(message, top_k=3, outcome_filter="good", min_score=0.6)
        ]
    except Exception as exc:
        _ = exc
        return []


def _record_decision(
    decision: RouteDecision,
    message: str,
    memory: Any | None,
    session_id: str | None,
) -> RouteDecision:
    if memory is None:
        return decision
    try:
        payload = {
            "profile": decision.profile,
            "probabilities": decision.probabilities,
            "runtime": decision.runtime,
            "team_id": decision.team_id,
            "agent_id": decision.agent_id,
            "tools": decision.tools,
            "strategy": decision.strategy,
            "plan": decision.plan,
            "source": decision.source,
        }
        decision.memory_decision_id = memory.record(
            context=message,
            decision=json.dumps(payload, ensure_ascii=False),
            outcome="neutral",
            tags=["routing", decision.source, decision.profile],
            score=decision.confidence,
            session_id=session_id,
        )
    except Exception as exc:
        _ = exc
    return decision


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
