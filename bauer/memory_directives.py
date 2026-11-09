"""Diretivas derivadas de feedback, com aprovação humana (plano 065, sprint 68).

Regra de promoção (decisão registrada no plano 065):

* agrupa por ``fingerprint`` estável **dentro do mesmo workspace**;
* exige ≥3 sinais do mesmo tipo (👎 → ``avoid``, 👍 → ``prefer``) em
  ≥2 sessões distintas;
* só conta sinais dos últimos 30 dias (decay);
* gera um **rascunho** — nunca ativa sozinho;
* cada diretiva é aprovada individualmente (``bauer memory directives
  approve <id>``), com expiração obrigatória, revogação e proveniência
  (ids do feedback que a sustenta).

Só diretivas ``approved`` e não vencidas entram no prompt
(``LocalMemoryProvider.system_prompt_block``), calculadas a cada chamada —
revogar ou vencer tira a diretiva do prompt na próxima montagem.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone

from . import confined_files
from .memory_feedback import FeedbackRecord, FeedbackStore
from .memory_scope import ScopeViolationError, WorkspaceScope, resolve_workspace_scope, sanitize_text

PROMOTION_MIN_SIGNALS = 3
PROMOTION_MIN_SESSIONS = 2
DECAY_DAYS = 30
DEFAULT_TTL_DAYS = 90
MAX_TTL_DAYS = 365
MAX_DIRECTIVE_CHARS = 200
MAX_PROMPT_DIRECTIVES = 10
MAX_PROMPT_CHARS = 1200
FINGERPRINT_VERSION = "v1"

_STOPWORDS = frozenset(
    "a o as os um uma uns umas de do da dos das em no na nos nas por para pra com sem "
    "que e é ou se mas mais muito muita pouco isso isto esse essa este esta aquilo como "
    "the and or to of in on for with is are be it this that you your me my nao não sim "
    "eu voce você ele ela eles elas meu minha seu sua foi ser ter tem fazer faz".split()
)
_TOKEN_RE = re.compile(r"[a-zA-ZÀ-ú0-9_]{3,}")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _keywords(text: str, *, limit: int) -> list[str]:
    tokens = [t for t in _TOKEN_RE.findall((text or "").casefold()) if t not in _STOPWORDS]
    counts = Counter(tokens)
    ranked = sorted(counts, key=lambda t: (-counts[t], t))
    return sorted(ranked[:limit])


def fingerprint(record: FeedbackRecord) -> str:
    """Fingerprint estável do padrão avaliado.

    Com motivo explícito (``/thumbsdown <motivo>``), o padrão É o motivo
    (palavras-chave normalizadas, ordem irrelevante). Sem motivo, usa as
    tools do turno + 3 palavras-chave do pedido do usuário.
    """
    if record.reason.strip():
        basis = "reason:" + " ".join(_keywords(record.reason, limit=6))
    else:
        tools = ",".join(sorted(set(record.tool_names)))
        basis = f"turn:{tools}|" + " ".join(_keywords(record.user_excerpt, limit=3))
    raw = f"{FINGERPRINT_VERSION}|{basis}"
    return "fp-" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _label(record: FeedbackRecord) -> str:
    if record.reason.strip():
        return record.reason.strip()
    words = " ".join(_keywords(record.user_excerpt, limit=3)) or "pedidos semelhantes"
    tools = ", ".join(sorted(set(record.tool_names)))
    return f"pedidos sobre {words}" + (f" (tools: {tools})" if tools else "")


def draft_text(kind: str, record: FeedbackRecord) -> str:
    label = _label(record)
    if record.reason.strip():
        text = f"{'Evite' if kind == 'avoid' else 'Prefira'}: {label}"
    elif kind == "avoid":
        text = f"Evite repetir o padrão de resposta que o usuário reprovou em {label}."
    else:
        text = f"Mantenha o padrão de resposta que o usuário aprovou em {label}."
    return sanitize_text(text, limit=MAX_DIRECTIVE_CHARS).text


@dataclass
class Directive:
    directive_id: str
    workspace_id: str
    fingerprint: str
    kind: str  # avoid | prefer
    text: str
    status: str = "draft"  # draft | approved | rejected | revoked
    supporting_feedback_ids: list[str] = field(default_factory=list)
    supporting_sessions: list[str] = field(default_factory=list)
    created_at: str = ""
    updated_at: str = ""
    approved_at: str = ""
    expires_at: str = ""
    closed_at: str = ""  # rejeição ou revogação

    def is_active(self, now: datetime) -> bool:
        if self.status != "approved" or not self.expires_at:
            return False
        try:
            return datetime.fromisoformat(self.expires_at) > now
        except ValueError:
            return False


class DirectiveError(ValueError):
    """Operação inválida sobre uma diretiva."""


class DirectiveStore:
    """``<workspace>/memory/consolidation/directives.json`` (só deste workspace)."""

    FILENAME = "directives.json"

    def __init__(self, scope: WorkspaceScope) -> None:
        self.scope = scope
        self.path = scope.state_dir / self.FILENAME

    @classmethod
    def for_workspace(cls, workspace: object) -> "DirectiveStore | None":
        scope = resolve_workspace_scope(workspace)
        return cls(scope) if scope is not None else None

    # -- persistência --------------------------------------------------------

    def load(self) -> list[Directive]:
        if not self.scope.state_dir.is_dir():
            return []
        try:
            raw = confined_files.read_text(self.path, self.scope.state_dir)
        except (confined_files.ConfinementError, OSError) as exc:
            raise ScopeViolationError(f"store de diretivas inválido: {exc}") from exc
        if not raw:
            return []
        try:
            data = json.loads(raw)
        except ValueError:
            return []
        items = data.get("directives", []) if isinstance(data, dict) else []
        result: list[Directive] = []
        fields = set(Directive.__dataclass_fields__)
        for item in items:
            if not isinstance(item, dict) or item.get("workspace_id") != self.scope.workspace_id:
                continue  # linha de outro workspace nunca é lida
            try:
                result.append(Directive(**{k: v for k, v in item.items() if k in fields}))
            except TypeError:
                continue
        return result

    def save(self, directives: list[Directive]) -> None:
        self.scope.ensure_state_dir()
        payload = {"schema_version": 1, "directives": [asdict(d) for d in directives]}
        confined_files.write_text_atomic(
            self.path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            self.scope.state_dir,
        )

    def get(self, directive_id: str) -> Directive | None:
        return next((d for d in self.load() if d.directive_id == directive_id), None)

    # -- regra de promoção --------------------------------------------------

    def propose(self, *, now: datetime | None = None) -> list[Directive]:
        """Cria/atualiza RASCUNHOS a partir do feedback. Nunca aprova."""
        now = now or _now()
        feedback = FeedbackStore(self.scope).list(
            since=(now - timedelta(days=DECAY_DAYS)).isoformat(), limit=100_000,
        )
        groups: dict[tuple[str, str], list[FeedbackRecord]] = {}
        for rec in feedback:
            if rec.workspace_id != self.scope.workspace_id:
                continue
            kind = "avoid" if rec.rating == "negative" else "prefer"
            groups.setdefault((kind, fingerprint(rec)), []).append(rec)

        directives = self.load()
        by_key = {(d.kind, d.fingerprint): d for d in directives}
        changed: list[Directive] = []
        for (kind, fp), records in sorted(groups.items()):
            existing = by_key.get((kind, fp))
            if existing is not None and existing.status in {"rejected", "revoked"}:
                # Só re-propõe com sinais NOVOS depois da recusa/revogação.
                records = [r for r in records if r.created_at > existing.closed_at]
            sessions = sorted({r.session_id for r in records if r.session_id})
            if len(records) < PROMOTION_MIN_SIGNALS or len(sessions) < PROMOTION_MIN_SESSIONS:
                continue
            ids = [r.feedback_id for r in records]
            stamp = now.isoformat()
            if existing is not None and existing.status in {"draft", "approved"}:
                if existing.supporting_feedback_ids != ids:
                    existing.supporting_feedback_ids = ids
                    existing.supporting_sessions = sessions
                    existing.updated_at = stamp
                    changed.append(existing)
                continue
            text = draft_text(kind, records[-1])
            if not text:
                continue
            generation = 1 + sum(1 for d in directives if d.kind == kind and d.fingerprint == fp)
            draft = Directive(
                directive_id=f"dir-{fp[3:11]}-{kind}-{generation}",
                workspace_id=self.scope.workspace_id, fingerprint=fp, kind=kind,
                text=text, supporting_feedback_ids=ids, supporting_sessions=sessions,
                created_at=stamp, updated_at=stamp,
            )
            # A recusada/revogada fica no histórico (auditoria); a nova é outra.
            directives.append(draft)
            by_key[(kind, fp)] = draft
            changed.append(draft)
        if changed:
            self.save(directives)
        return changed

    # -- ciclo de vida (uma diretiva por vez) ---------------------------------

    def _mutate(self, directive_id: str, fn) -> Directive:
        directives = self.load()
        target = next((d for d in directives if d.directive_id == directive_id), None)
        if target is None:
            raise DirectiveError(f"diretiva não encontrada: {directive_id}")
        fn(target)
        self.save(directives)
        return target

    def approve(self, directive_id: str, *, ttl_days: int = DEFAULT_TTL_DAYS,
                text: str | None = None, now: datetime | None = None) -> Directive:
        now = now or _now()
        if not 1 <= int(ttl_days) <= MAX_TTL_DAYS:
            raise DirectiveError(f"ttl_days deve estar entre 1 e {MAX_TTL_DAYS}")
        new_text: str | None = None
        if text is not None:
            clean = sanitize_text(" ".join(text.split()), limit=MAX_DIRECTIVE_CHARS)
            if clean.sensitive or clean.redacted or not clean.text:
                raise DirectiveError("texto da diretiva vazio ou com dado sensível")
            new_text = clean.text

        def _apply(d: Directive) -> None:
            if d.status != "draft":
                raise DirectiveError(f"só rascunhos podem ser aprovados (status={d.status})")
            if new_text:
                d.text = new_text
            d.status = "approved"
            d.approved_at = now.isoformat()
            d.expires_at = (now + timedelta(days=int(ttl_days))).isoformat()
            d.updated_at = now.isoformat()

        return self._mutate(directive_id, _apply)

    def reject(self, directive_id: str, *, now: datetime | None = None) -> Directive:
        now = now or _now()

        def _apply(d: Directive) -> None:
            if d.status != "draft":
                raise DirectiveError(f"só rascunhos podem ser recusados (status={d.status})")
            d.status = "rejected"
            d.closed_at = d.updated_at = now.isoformat()

        return self._mutate(directive_id, _apply)

    def revoke(self, directive_id: str, *, now: datetime | None = None) -> Directive:
        now = now or _now()

        def _apply(d: Directive) -> None:
            if d.status != "approved":
                raise DirectiveError(f"só diretivas aprovadas podem ser revogadas (status={d.status})")
            d.status = "revoked"
            d.closed_at = d.updated_at = now.isoformat()

        return self._mutate(directive_id, _apply)

    def active(self, *, now: datetime | None = None) -> list[Directive]:
        now = now or _now()
        return [d for d in self.load() if d.is_active(now)]


def directives_prompt_block(workspace: object, *, now: datetime | None = None) -> str:
    """Bloco do prompt com as diretivas APROVADAS e vigentes ("" se nenhuma)."""
    store = DirectiveStore.for_workspace(workspace)
    if store is None:
        return ""
    active = store.active(now=now)[:MAX_PROMPT_DIRECTIVES]
    if not active:
        return ""
    lines = ["### Diretivas aprovadas pelo usuário (revogáveis)"]
    for d in active:
        lines.append(f"- {d.text} [{d.directive_id}, até {d.expires_at[:10]}]")
    return "\n".join(lines)[:MAX_PROMPT_CHARS]

