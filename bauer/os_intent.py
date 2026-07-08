"""Intent router do Bauer OS — texto livre → (skill, inputs) via LLM auxiliar.

Fluxo do Command Palette (Ctrl+Space): quando o texto do usuário não casa com
nenhum atalho determinístico do ``/api/os/command``, este módulo pergunta a um
modelo barato (slot ``auxiliary.intent_router``; default = modelo principal)
qual skill do SkillRegistry atende a intenção e com quais inputs.

O chamador entrega o resultado ao ``SkillExecutor``, que já faz policy →
approval → execução → eventos. Este módulo NÃO executa nada — só interpreta.

Semântica best-effort (mesmo contrato do auxiliary_client): nenhuma função
daqui levanta exceção em operação normal; qualquer falha (sem provider, JSON
inválido, skill inexistente) vira ``None`` e o chamador cai no fallback
determinístico ("não reconheci esse comando").
"""

from __future__ import annotations

import json
import logging
import re
import sys
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

# Abaixo desta confiança o palpite do modelo é descartado — melhor dizer
# "não reconheci" do que abrir o app errado na frente do usuário.
MIN_CONFIDENCE = 0.5

_SYSTEM_PROMPT = """\
Você é o roteador de intenções do Bauer OS. O usuário digitou ou falou um
comando em linguagem natural. Sua única tarefa: escolher a skill do catálogo
que realiza a intenção e montar os inputs.

Regras:
- Responda APENAS um objeto JSON, sem markdown, sem texto extra.
- Formato: {"skill_id": "<id do catálogo>" | null, "inputs": {...},
  "confidence": 0.0-1.0, "reason": "<curto, em português>"}
- Use somente skills do catálogo e somente inputs declarados por elas.
- Omita inputs opcionais que o usuário não pediu explicitamente (ex.: não
  invente qual navegador usar — sem "browser" o sistema usa o padrão).
- Se a intenção envolve pesquisar algo na web, use a skill de navegador com
  url "https://www.google.com/search?q=<termos+codificados>".
- Se nenhuma skill do catálogo realiza a intenção (ex.: conversa, pergunta,
  pedido vago), devolva {"skill_id": null, "inputs": {}, "confidence": 0,
  "reason": "..."}. Não force uma skill errada.

Catálogo de skills disponíveis nesta máquina:
{catalog}
"""


@dataclass(slots=True)
class IntentDecision:
    """Resultado do roteamento: qual skill executar e com quais inputs."""

    skill_id: str
    inputs: dict[str, Any] = field(default_factory=dict)
    confidence: float = 0.0
    reason: str = ""


def current_platform() -> str:
    if sys.platform == "win32":
        return "windows"
    if sys.platform == "darwin":
        return "macos"
    return "linux"


def catalog_for_prompt(manifests: list[Any], *, platform_name: str | None = None) -> list[Any]:
    """Filtra manifests executáveis nesta plataforma (ou marcados como 'any')."""
    platform_name = platform_name or current_platform()
    out = []
    for manifest in manifests:
        platforms = [str(p).lower() for p in (getattr(manifest, "platforms", None) or [])]
        if not platforms or "any" in platforms or platform_name in platforms:
            out.append(manifest)
    return out


def _render_catalog(manifests: list[Any]) -> str:
    lines = []
    for manifest in manifests:
        inputs = getattr(manifest, "inputs", None) or {}
        input_desc = ", ".join(
            f"{name}: {spec.get('type', 'string') if isinstance(spec, dict) else 'string'}"
            for name, spec in inputs.items()
        ) or "nenhum"
        lines.append(
            f"- id: {manifest.id}\n"
            f"  descrição: {getattr(manifest, 'description', '')}\n"
            f"  inputs: {input_desc}"
        )
    return "\n".join(lines) if lines else "(vazio)"


def _extract_json(text: str) -> dict[str, Any] | None:
    """Extrai o primeiro objeto JSON do texto, tolerando cercas de markdown."""
    if not text:
        return None
    cleaned = re.sub(r"```(?:json)?", "", text).strip().strip("`")
    # Primeiro objeto { ... } balanceado — modelos às vezes prefixam prosa.
    start = cleaned.find("{")
    if start < 0:
        return None
    depth = 0
    for idx in range(start, len(cleaned)):
        if cleaned[idx] == "{":
            depth += 1
        elif cleaned[idx] == "}":
            depth -= 1
            if depth == 0:
                candidate = cleaned[start : idx + 1]
                try:
                    parsed = json.loads(candidate)
                except json.JSONDecodeError:
                    return None
                return parsed if isinstance(parsed, dict) else None
    return None


def route_intent(text: str, manifests: list[Any], *, cfg: Any = None) -> IntentDecision | None:
    """Mapeia texto livre para uma skill do catálogo. ``None`` = sem match.

    Nunca levanta: qualquer falha de provider/parse é logada em INFO e vira
    ``None`` para o chamador cair no fallback determinístico.
    """
    text = (text or "").strip()
    if not text:
        return None
    candidates = catalog_for_prompt(manifests)
    if not candidates:
        return None
    by_id = {manifest.id: manifest for manifest in candidates}

    try:
        from .auxiliary_client import get_text_auxiliary_client

        client, model = get_text_auxiliary_client("intent_router", cfg)
        if client is None or not model:
            return None
        prompt = _SYSTEM_PROMPT.replace("{catalog}", _render_catalog(candidates))
        raw = "".join(
            client.chat_stream(
                model,
                [
                    {"role": "system", "content": prompt},
                    {"role": "user", "content": text},
                ],
            )
        )
    except Exception as exc:  # noqa: BLE001 — best-effort por contrato.
        logger.info("os_intent: roteamento LLM falhou: %s", exc)
        return None

    parsed = _extract_json(raw)
    if not parsed:
        logger.info("os_intent: resposta sem JSON utilizável: %.200s", raw)
        return None
    skill_id = parsed.get("skill_id")
    if not skill_id or skill_id not in by_id:
        return None
    try:
        confidence = float(parsed.get("confidence") or 0.0)
    except (TypeError, ValueError):
        confidence = 0.0
    if confidence < MIN_CONFIDENCE:
        return None

    # Higiene: só repassa inputs que o manifest declara.
    declared = set((getattr(by_id[skill_id], "inputs", None) or {}).keys())
    raw_inputs = parsed.get("inputs") or {}
    inputs = (
        {k: v for k, v in raw_inputs.items() if k in declared}
        if isinstance(raw_inputs, dict)
        else {}
    )
    return IntentDecision(
        skill_id=str(skill_id),
        inputs=inputs,
        confidence=confidence,
        reason=str(parsed.get("reason") or ""),
    )
