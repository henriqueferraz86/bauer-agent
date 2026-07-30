"""G4 — LLM-based contextual tool approval.

Replaces the binary "requires_approval" flag with an LLM judgment that
examines the tool name, args, and recent conversation to decide whether
execution is safe given the apparent intent.

Fallback: if the auxiliary client is unavailable or the call times out,
the tool is approved (fail-open) to avoid blocking legitimate work.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass

logger = logging.getLogger(__name__)

try:
    from .auxiliary_client import call_aux_text
except Exception:  # pragma: no cover
    call_aux_text = None  # type: ignore[assignment]

_HIGH_RISK_TOOLS = frozenset({
    "delete_file",
    "run_command",
    "execute_code",
    "browser_cdp",
})

_SAFE_TOOLS = frozenset({
    "read_file",
    "list_dir",
    "glob_files",
    "regex_search",
    "memory",
    "session_search",
    "datetime_now",
    "json_query",
    "encode_decode",
    "calculate",
    "get_task_context",
})

_SYSTEM_PROMPT = """\
You are a security-aware tool approval system. You analyze LLM tool calls
and decide whether they are safe to execute given the conversation context.

Your output MUST be a single JSON object on one line:
{"approved": true/false, "confidence": 0.0-1.0, "reason": "...", "suggestion": "..."}

Rules:
- "approved": true  if the action matches the user's apparent goal and is not suspicious
- "approved": false if the action seems off-topic, destructive, or out-of-scope
- "confidence": your certainty (0.8+ = high, 0.5-0.8 = medium, <0.5 = low)
- "reason": one sentence explaining your decision
- "suggestion": if rejecting, a safer alternative action; otherwise ""

When in doubt, approve. Only reject when you see a clear mismatch or risk.
Safe reads (read_file, list_dir, search) should always be approved.
"""


@dataclass
class LLMApprovalResult:
    approved: bool
    reason: str
    confidence: float = 0.9
    suggestion: str = ""

    @classmethod
    def allow(cls, reason: str = "Approved by default") -> "LLMApprovalResult":
        return cls(approved=True, reason=reason, confidence=1.0)

    @classmethod
    def deny(cls, reason: str, suggestion: str = "") -> "LLMApprovalResult":
        return cls(approved=False, reason=reason, suggestion=suggestion, confidence=0.9)


def llm_evaluate_tool(
    tool_name: str,
    args: dict,
    recent_messages: list[dict],
    *,
    cfg=None,
    timeout_s: float = 8.0,
) -> LLMApprovalResult:
    """Evaluate whether executing `tool_name` is safe in the current context.

    Falls back to approved=True if the auxiliary client is unavailable or
    the call times out — fail-open so legitimate work is never blocked.
    """
    if tool_name in _SAFE_TOOLS:
        return LLMApprovalResult.allow("Safe read-only tool — auto-approved")

    if not juiz_independente(cfg):
        # Juiz que é o próprio réu não é juiz. Sem `auxiliary.approval_model`
        # declarado, `_resolve_slot` cai no modelo PRINCIPAL — e o G4 passa a
        # pedir ao modelo que ele mesmo audite a tool que acabou de escolher.
        #
        # Não é hipótese: observado em produção com modelo local, negando
        # `docker compose logs` com "não há consentimento claro para rodar" logo
        # depois de o usuário ter pedido exatamente isso. O erro anda nos dois
        # sentidos — nega trabalho legítimo e carimba o que ele mesmo propôs.
        #
        # Melhor NENHUM gate de LLM que um gate autojulgado: as camadas
        # determinísticas (allowlist, limiar de risco, aprovação humana)
        # continuam valendo, e elas não têm conflito de interesse.
        logger.info("llm_approval: sem juiz independente (auxiliary.approval_model "
                    "vazio) — G4 desligado para %s; camadas determinísticas seguem",
                    tool_name)
        return LLMApprovalResult.allow(
            "G4 desligado: sem juiz independente (configure auxiliary.approval_model)")

    try:
        context_lines = _format_context(recent_messages)
        user_msg = (
            f"Tool requested: {tool_name}\n"
            f"Arguments: {json.dumps(args, ensure_ascii=False)[:500]}\n\n"
            f"Recent conversation:\n{context_lines}\n\n"
            "Is this tool call safe and consistent with the user's intent? "
            "Respond with JSON only."
        )
        messages = [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": user_msg},
        ]

        raw = call_aux_text("approval_model", messages, cfg=cfg, fallback="")
        if not raw:
            logger.debug("llm_approval: no aux client — auto-approving %s", tool_name)
            return LLMApprovalResult.allow("No approval client configured")

        return _parse_response(raw)

    except Exception as exc:
        logger.info("llm_approval: error evaluating %s — approving: %s", tool_name, exc)
        return LLMApprovalResult.allow(f"Approval check failed ({exc!r}) — fail-open")


def juiz_independente(cfg=None) -> bool:
    """Existe um juiz do G4 DIFERENTE do modelo que está executando?

    ``auxiliary.approval_model`` vazio não significa "sem juiz": significa que o
    ``_resolve_slot`` cai no modelo principal, e o gate vira autoavaliação. Aqui
    o critério é explícito — provider **ou** modelo têm de diferir do principal.

    Provider diferente já basta: o mesmo nome de modelo servido por dois
    provedores são instâncias distintas, sem estado nem contexto compartilhado.
    """
    if cfg is None:
        from .auxiliary_client import _try_load_default_config
        cfg = _try_load_default_config()
    if cfg is None:
        return False

    aux = getattr(getattr(cfg, "auxiliary", None), "approval_model", None)
    aux_provider = (getattr(aux, "provider", "") or "").strip().lower()
    aux_model = (getattr(aux, "model", "") or "").strip().lower()
    if not aux_provider and not aux_model:
        return False

    main = getattr(cfg, "model", None)
    main_provider = (getattr(main, "provider", "") or "").strip().lower()
    main_model = (getattr(main, "name", "") or "").strip().lower()

    provider_efetivo = aux_provider or main_provider
    modelo_efetivo = aux_model or main_model
    return (provider_efetivo, modelo_efetivo) != (main_provider, main_model)


def _format_context(messages: list[dict], max_messages: int = 6) -> str:
    recent = messages[-max_messages:] if len(messages) > max_messages else messages
    lines = []
    for m in recent:
        role = m.get("role", "?")
        content = str(m.get("content", ""))[:300]
        lines.append(f"[{role}] {content}")
    return "\n".join(lines) if lines else "(no prior context)"


def _parse_response(raw: str) -> LLMApprovalResult:
    raw = raw.strip()
    m = re.search(r"\{.*\}", raw, re.DOTALL)
    if not m:
        logger.debug("llm_approval: could not find JSON in response — approving")
        return LLMApprovalResult.allow("Unparseable response — fail-open")
    try:
        data = json.loads(m.group())
        return LLMApprovalResult(
            approved=bool(data.get("approved", True)),
            confidence=float(data.get("confidence", 0.8)),
            reason=str(data.get("reason", "")),
            suggestion=str(data.get("suggestion", "")),
        )
    except (json.JSONDecodeError, ValueError):
        return LLMApprovalResult.allow("JSON parse error — fail-open")
