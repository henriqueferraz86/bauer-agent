"""Auto-seleção de skill por turno (degrau 1 do "skills que disparam").

Casa a mensagem do usuário com as skills disponíveis (pacote + usuário) e
devolve a mais relevante para INJETAR seu conteúdo no contexto do turno — mas
só quando o match é confiante. Abaixo do threshold: devolve None (injeta nada).

Por que overlap coefficient + threshold 0.30 (medido empiricamente sobre as 47
skills do pacote): queries reais acertam o top-1 com score 0.33–0.83; ruído
puro dá 0.00 e casos ambíguos ~0.14. Um threshold de 0.30 dispara os acertos e
rejeita ruído + ambíguo — o design FALHA SEGURO: na dúvida, não injeta (melhor
que injetar a skill errada). Mesmo tokenizador/coeficiente do
`agent_registry.match_agents`, pela mesma razão (Jaccard penaliza doc rico).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

#: Threshold do overlap coefficient para injetar. Calibrado na medição: acertos
#: ficam em 0.33+, ambíguo/ruído em <=0.14. 0.30 separa com folga.
DEFAULT_THRESHOLD = 0.30

#: Teto do conteúdo injetado — não estourar o contexto (modelos fracos são
#: sensíveis a prompt longo). Skills maiores entram truncadas.
_CONTENT_CAP = 2000

_DOCS_CACHE: "list[dict] | None" = None

_STOPWORDS = {
    "uma", "uns", "umas", "para", "por", "com", "sem", "que", "qual", "quais",
    "este", "esta", "isto", "isso", "esse", "essa", "deste", "desta", "desse",
    "dessa", "faca", "faça", "fazer", "faz", "meu", "minha", "seu", "sua",
    "the", "and", "for", "with", "from", "this", "that",
}


@dataclass
class MatchedSkill:
    name: str
    score: float
    content: str
    source: str  # "builtin" | "user"


def _tokens(text: str) -> set[str]:
    return {
        token
        for token in re.findall(r"\b\w{3,}\b", text.lower(), flags=re.UNICODE)
        if token not in _STOPWORDS
    }


def _load_skill_docs() -> list[dict]:
    """(name, description, tags, content, source) de todas as skills.

    Pacote (SkillsHub) + usuário (~/.bauer/skills/*.yaml). Best-effort: um YAML
    quebrado é pulado, nunca derruba o turno.
    """
    try:
        import yaml
    except ImportError:
        return []

    docs: list[dict] = []
    try:
        from .skills_hub import SkillsHub
        for e in SkillsHub().list_skills():
            try:
                d = yaml.safe_load(e.path.read_text(encoding="utf-8")) or {}
            except Exception:
                continue
            docs.append({
                "name": d.get("name") or e.slug,
                "description": d.get("description", "") or "",
                "tags": d.get("tags", []) or [],
                "content": str(d.get("content") or d.get("invoke") or ""),
                "source": "builtin",
            })
    except Exception:
        pass

    try:
        from .paths import get_bauer_home
        user_dir = get_bauer_home() / "skills"
        if user_dir.is_dir():
            for p in sorted(user_dir.glob("*.yaml")):
                try:
                    d = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
                except Exception:
                    continue
                docs.append({
                    "name": d.get("name") or p.stem,
                    "description": d.get("description", "") or "",
                    "tags": d.get("tags", []) or [],
                    "content": str(d.get("content") or d.get("invoke") or ""),
                    "source": "user",
                })
    except Exception:
        pass
    return docs


def _get_docs(docs: "list[dict] | None") -> list[dict]:
    global _DOCS_CACHE
    if docs is not None:
        return docs
    if _DOCS_CACHE is None:
        _DOCS_CACHE = _load_skill_docs()
    return _DOCS_CACHE


def reset_cache() -> None:
    """Zera o cache de skills (usado por testes / após instalar skill nova)."""
    global _DOCS_CACHE
    _DOCS_CACHE = None


def match_skill(
    user_message: str,
    *,
    threshold: float = DEFAULT_THRESHOLD,
    docs: "list[dict] | None" = None,
) -> "MatchedSkill | None":
    """Skill mais relevante para a mensagem, ou None se abaixo do threshold.

    ``docs`` explícito é usado por testes; em runtime carrega (com cache) as
    skills reais.
    """
    qt = _tokens(user_message)
    if not qt:
        return None
    best: "dict | None" = None
    best_score = 0.0
    for d in _get_docs(docs):
        name_tags = f"{d['name']} {' '.join(d['tags'])}"
        description = d["description"]
        doc = f"{description} {name_tags} {d['content'][:200]}"
        dt = _tokens(doc)
        if not dt:
            continue
        inter = len(qt & dt)
        smaller = min(len(qt), len(dt))
        score = inter / smaller if smaller else 0.0
        if qt & _tokens(name_tags):
            score += 0.35
        elif qt & _tokens(description):
            score += 0.10
        score = min(score, 1.0)
        if score > best_score:
            best_score = score
            best = d
    if best is None or best_score < threshold:
        return None
    content = best["content"].strip()[:_CONTENT_CAP]
    if not content:
        return None
    return MatchedSkill(
        name=best["name"], score=round(best_score, 2),
        content=content, source=best["source"],
    )


def skill_injection_block(matched: "MatchedSkill") -> str:
    """Bloco de system efêmero com o conteúdo da skill selecionada."""
    return (
        "<skill-relevante>\n"
        f"[Skill '{matched.name}' selecionada automaticamente por relevância à "
        "sua mensagem — use como guia SE ajudar; ignore se não couber.]\n"
        f"{matched.content}\n"
        "</skill-relevante>"
    )
