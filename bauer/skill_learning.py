"""Skill learning — detecta pedidos repetidos e propõe skills automaticamente.

Fase 3.4 do plano de autonomia: se o usuário pede a mesma coisa ≥N vezes em
sessões diferentes, isso é um workflow — vira candidato a skill YAML
(`skill_system`). v1 é determinística (clustering por similaridade TF-IDF,
template gerado por heurística); um refinamento via LLM pode vir depois.

Fluxo::

    candidates = find_skill_candidates(min_occurrences=3)
    for c in candidates:
        draft = draft_skill_yaml(c)       # YAML pronto para revisão
        # usuário revisa e instala: SkillManager.install_from_yaml(draft)

Exposto no CLI como `bauer skills learn` (lista candidatos) e
`bauer skills learn --install <slug>` (instala o draft).
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field

# Pedidos mais curtos que isto não viram skill (ruído: "oi", "continua", "ok")
_MIN_ASK_CHARS = 25
# Similaridade mínima de cosseno para agrupar dois pedidos no mesmo cluster
_SIM_THRESHOLD = 0.55


@dataclass
class SkillCandidate:
    """Um cluster de pedidos repetidos que merece virar skill."""

    slug: str
    occurrences: int
    examples: list[str] = field(default_factory=list)
    sessions: list[str] = field(default_factory=list)

    @property
    def representative(self) -> str:
        """O exemplo mais longo costuma ser o mais completo."""
        return max(self.examples, key=len) if self.examples else ""


def find_skill_candidates(
    min_occurrences: int = 3,
    store=None,
    max_sessions: int = 200,
) -> list[SkillCandidate]:
    """Varre o histórico de sessões e agrupa pedidos de usuário similares.

    Retorna candidatos com >= min_occurrences, ordenados por frequência.
    Falha graciosa (lista vazia) se o session store não estiver disponível.
    """
    asks = _collect_user_asks(store, max_sessions)
    if len(asks) < min_occurrences:
        return []

    clusters = _cluster_by_similarity(asks)
    candidates: list[SkillCandidate] = []
    for cluster in clusters:
        if len(cluster) < min_occurrences:
            continue
        texts = [a[1] for a in cluster]
        sessions = sorted({a[0] for a in cluster})
        # Repetição na MESMA sessão é iteração de tarefa, não workflow:
        # exige presença em pelo menos 2 sessões diferentes.
        if len(sessions) < 2:
            continue
        slug = _slug_for(texts)
        candidates.append(SkillCandidate(
            slug=slug,
            occurrences=len(cluster),
            examples=texts[:5],
            sessions=sessions[:10],
        ))

    candidates.sort(key=lambda c: c.occurrences, reverse=True)
    return candidates


def draft_skill_yaml(candidate: SkillCandidate) -> str:
    """Gera o YAML da skill a partir do candidato (formato do skill_system).

    O template usa o pedido representativo com os literais variáveis
    (paths, nomes entre aspas) promovidos a parâmetros {target}.
    """
    rep = candidate.representative.strip().replace("\n", " ")
    invoke, has_target = _parametrize(rep)

    lines = [
        f"name: {candidate.slug}",
        "version: 1",
        f"description: Workflow aprendido — pedido repetido {candidate.occurrences}x "
        f"em {len(candidate.sessions)} sessões.",
        "invoke: |",
        f"  {invoke}",
    ]
    if has_target:
        lines += [
            "params:",
            "  target:",
            "    required: true",
            "    description: Alvo do workflow (arquivo, diretório ou nome).",
        ]
    return "\n".join(lines) + "\n"


# ─── Internals ─────────────────────────────────────────────────────────────────


def _collect_user_asks(store, max_sessions: int) -> list[tuple[str, str]]:
    """Retorna [(session_id, texto_do_pedido)] das mensagens de usuário.

    Ignora mensagens de sistema injetadas (resultados de tool, avisos) e
    pedidos curtos demais para serem workflows.
    """
    if store is None:
        try:
            from .sqlite_session_store import SqliteSessionStore
            store = SqliteSessionStore()
        except Exception:
            return []

    asks: list[tuple[str, str]] = []
    try:
        session_ids = store.list_sessions()[:max_sessions]
        for sid in session_ids:
            messages = store.load(sid) or []
            for m in messages:
                if m.get("role") != "user":
                    continue
                text = str(m.get("content", "")).strip()
                if len(text) < _MIN_ASK_CHARS:
                    continue
                if text.startswith(("[Resultado de", "[AVISO", "[SISTEMA", "[Resumo")):
                    continue  # mensagens injetadas pelo próprio agent
                asks.append((sid, text))
    except Exception:
        return []
    return asks


def _tokenize(text: str) -> Counter:
    words = re.findall(r"[a-záéíóúâêôãõç0-9_]{3,}", text.lower())
    return Counter(words)


def _cosine(a: Counter, b: Counter) -> float:
    if not a or not b:
        return 0.0
    common = set(a) & set(b)
    num = sum(a[w] * b[w] for w in common)
    den = (sum(v * v for v in a.values()) ** 0.5) * (sum(v * v for v in b.values()) ** 0.5)
    return num / den if den else 0.0


def _cluster_by_similarity(asks: list[tuple[str, str]]) -> list[list[tuple[str, str]]]:
    """Greedy single-pass clustering por cosseno de bag-of-words.

    Suficiente para o volume típico (<1k pedidos); sem dependências.
    """
    vectors = [(_tokenize(t), sid, t) for sid, t in asks]
    clusters: list[tuple[Counter, list[tuple[str, str]]]] = []
    for vec, sid, text in vectors:
        placed = False
        for centroid, members in clusters:
            if _cosine(vec, centroid) >= _SIM_THRESHOLD:
                members.append((sid, text))
                centroid.update(vec)  # centróide acumulativo
                placed = True
                break
        if not placed:
            clusters.append((Counter(vec), [(sid, text)]))
    return [members for _, members in clusters]


_STOPWORDS = frozenset({
    "para", "com", "que", "uma", "por", "favor", "fazer", "faz", "faça",
    "the", "and", "for", "this", "that", "você", "voce", "como", "qual",
})


def _slug_for(texts: list[str]) -> str:
    """Slug a partir das 3 palavras mais frequentes do cluster."""
    counter: Counter = Counter()
    for t in texts:
        counter.update(w for w in _tokenize(t) if w not in _STOPWORDS)
    top = [w for w, _ in counter.most_common(3)]
    slug = "_".join(top) or "workflow"
    return re.sub(r"[^a-z0-9_-]", "", slug)[:48] or "workflow"


def _parametrize(text: str) -> tuple[str, bool]:
    """Promove o literal mais óbvio (path ou 'nome entre aspas') a {target}."""
    # Paths: algo/com/barras.ext ou arquivo.ext
    m = re.search(r"[\w./\\-]+\.\w{1,5}\b", text)
    if m:
        return text.replace(m.group(0), "{target}", 1), True
    # Literais entre aspas
    m = re.search(r"['\"]([^'\"]{2,60})['\"]", text)
    if m:
        return text.replace(m.group(0), "'{target}'", 1), True
    return text, False
