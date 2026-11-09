"""Contrato de escopo da consolidação de memória (plano 065, sprint 65).

Decisão vigente (plano 017 → 065): escopo ``workspace_only``. Toda leitura e
escrita da consolidação/feedback acontece dentro de UM workspace, identificado
por um id estável derivado do caminho resolvido. Escopos ``company`` e
``user`` são recusados explicitamente:

* ``company`` — rejeitado até existir um contrato de identidade (as sessões
  não carregam ``company_id``; promover para a empresa vazaria entre tenants);
* ``user`` — adiado (as sessões não têm ``user_id``).

O módulo também concentra a sanitização usada antes de qualquer texto de
sessão/feedback ser persistido ou enviado ao LLM auxiliar: redige com
:func:`bauer.privacy.redact_text` e marca como sensível o que o scanner de
segredos ainda detectar depois da redação (o chamador descarta).
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from pathlib import Path

#: Único escopo aceito pela consolidação nesta fase.
SCOPE_WORKSPACE = "workspace"

#: Escopos recusados, com o motivo exibido ao usuário.
REJECTED_SCOPES: dict[str, str] = {
    "company": "escopo 'company' rejeitado até existir contrato de identidade de empresa",
    "user": "escopo 'user' adiado: sessões não têm user_id",
}

#: Subdiretório (dentro de ``<workspace>/memory``) com o estado da consolidação.
STATE_DIRNAME = "consolidation"


class ScopeViolationError(ValueError):
    """Operação tentou atravessar ou trocar o escopo de workspace."""


@dataclass(frozen=True)
class WorkspaceScope:
    """Identidade de um workspace para memória consolidada."""

    workspace_id: str
    path: str
    kind: str = SCOPE_WORKSPACE

    @property
    def root(self) -> Path:
        return Path(self.path)

    @property
    def memory_dir(self) -> Path:
        return self.root / "memory"

    @property
    def state_dir(self) -> Path:
        return self.memory_dir / STATE_DIRNAME

    def ensure_state_dir(self) -> Path:
        """Cria ``memory/consolidation`` recusando symlink no caminho interno."""
        for directory in (self.memory_dir, self.state_dir):
            if directory.is_symlink():
                raise ScopeViolationError(f"diretório de memória é symlink: {directory}")
            directory.mkdir(parents=True, exist_ok=True)
        resolved = self.state_dir.resolve()
        if not resolved.is_relative_to(self.root.resolve()):
            raise ScopeViolationError(f"estado de memória fora do workspace: {resolved}")
        return self.state_dir

    def require_same(self, workspace_id: object) -> None:
        if workspace_id != self.workspace_id:
            raise ScopeViolationError(
                f"registro de outro escopo ({workspace_id!r}) recusado em {self.workspace_id}"
            )


def workspace_id_for(path: str | Path) -> str:
    resolved = Path(path).expanduser().resolve()
    digest = hashlib.sha256(os.path.normcase(str(resolved)).encode("utf-8")).hexdigest()
    return f"ws-{digest[:16]}"


def resolve_workspace_scope(workspace: object) -> WorkspaceScope | None:
    """Escopo do workspace, ou ``None`` quando o valor não é um caminho real.

    Mesmo guard de tipo de ``memory_context._safe_workspace``: só ``str`` e
    ``Path`` (um ``MagicMock`` satisfaz ``os.PathLike`` e viraria lixo em disco).
    """
    if isinstance(workspace, bytes):
        workspace = os.fsdecode(workspace)
    if not isinstance(workspace, (str, Path)) or not str(workspace).strip():
        return None
    resolved = Path(workspace).expanduser().resolve()
    return WorkspaceScope(workspace_id=workspace_id_for(resolved), path=str(resolved))


def validate_scope_kind(kind: str) -> str:
    """Aceita só ``workspace``; ``company``/``user`` falham com o motivo da decisão."""
    normalized = (kind or "").strip().lower()
    if normalized == SCOPE_WORKSPACE:
        return normalized
    reason = REJECTED_SCOPES.get(normalized, f"escopo desconhecido: {kind!r}")
    raise ScopeViolationError(reason)


@dataclass(frozen=True)
class SanitizedText:
    text: str
    redacted: bool
    sensitive: bool

    @property
    def decision(self) -> str:
        if self.sensitive:
            return "dropped"
        return "redacted" if self.redacted else "clean"


def sanitize_text(value: object, *, limit: int | None = None) -> SanitizedText:
    """Redige segredos e diz se o resultado ainda parece sensível.

    A redação acontece ANTES do corte por ``limit`` (cortar antes poderia
    partir um token no meio e escapar dos padrões). ``sensitive=True`` significa
    que o chamador deve descartar o texto (o resultado vem vazio).
    """
    from .privacy import redact_text
    from .secrets_scanner import has_secrets

    original = "" if value is None else str(value)
    redacted = redact_text(original)
    if has_secrets(redacted):
        return SanitizedText(text="", redacted=True, sensitive=True)
    changed = redacted != original
    if limit is not None and len(redacted) > limit:
        redacted = redacted[:limit]
    return SanitizedText(text=redacted, redacted=changed, sensitive=False)


def text_digest(value: str) -> str:
    """Digest estável de um texto JÁ sanitizado (nunca do texto cru)."""
    return hashlib.sha256(value.encode("utf-8", errors="replace")).hexdigest()
