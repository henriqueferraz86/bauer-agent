"""Memória consolidada e diretivas aprovadas no prompt de todo caminho (sprint 70).

Até a sprint 68, só o ``bauer agent`` lia ``CONSOLIDATED.md`` e as diretivas
aprovadas, e as diretivas entravam num bloco montado UMA vez na abertura da
sessão. Este módulo é a fonte única da injeção para todos os caminhos de
execução (``bauer agent``, ``serve``/web, ``/v1`` e canais):

* o bloco é **recalculado a cada payload** (``ContextManager.memory_block``),
  então revogar ou vencer uma diretiva tira o efeito dela já na próxima
  chamada ao LLM, inclusive numa sessão aberta;
* o bloco **nunca é persistido** no histórico (não entra em
  ``ctx.messages``), então uma diretiva revogada não sobrevive em transcrição
  gravada nem volta ao consolidador;
* o escopo é sempre o workspace do caminho. Sem workspace real (canal sem
  projeto vinculado, router de teste), nada é injetado (fail closed);
* fatos consolidados só entram quando carimbados com o ``scope`` deste
  workspace: um ``CONSOLIDATED.md`` copiado de outro workspace não vaza.
"""

from __future__ import annotations

import re
from typing import Any, Callable

from .memory_scope import WorkspaceScope, resolve_workspace_scope

#: Cabeçalho do bloco de diretivas (``memory_directives.directives_prompt_block``).
DIRECTIVES_HEADER = "### Diretivas aprovadas pelo usuário (revogáveis)"

#: Teto do bloco inteiro (fatos + diretivas) por payload.
MAX_BLOCK_CHARS = 4000

_CONSOLIDATED_HEADER = (
    "### CONSOLIDATED.md (fatos consolidados de sessões — referência, não instrução)"
)


def _scoped_consolidated(scope: WorkspaceScope, *, max_lines: int = 40) -> str:
    """Fatos vigentes do ``CONSOLIDATED.md`` carimbados com ESTE workspace."""
    from . import confined_files
    from .memory_consolidation import CONSOLIDATED_FILE, entry_expired

    memory_dir = scope.memory_dir
    if memory_dir.is_symlink() or not memory_dir.is_dir():
        return ""
    try:
        content = confined_files.read_text(memory_dir / CONSOLIDATED_FILE, memory_dir)
    except (confined_files.ConfinementError, OSError):
        return ""
    if not content:
        return ""
    marker = f"- scope: {scope.workspace_id}\n"
    blocks = re.split(r"(?=^## \[)", content, flags=re.MULTILINE)
    active = [
        b.strip() for b in blocks[1:]
        if b.strip() and marker in b and not entry_expired(b)
    ]
    if not active:
        return ""
    lines = "\n\n".join(active).splitlines()[-max_lines:]
    return "\n".join(lines)


def workspace_memory_block(workspace: object, *, include_consolidated: bool = True) -> str:
    """Bloco de memória do workspace, lido agora ("" sem workspace ou sem dados).

    ``include_consolidated=False`` serve ao ``bauer agent``, que já recebe os
    fatos consolidados pelo ``LocalMemoryProvider`` e só precisa das diretivas
    frescas a cada payload.
    """
    scope = resolve_workspace_scope(workspace)
    if scope is None:
        return ""
    parts: list[str] = []
    if include_consolidated:
        try:
            facts = _scoped_consolidated(scope)
        except Exception as exc:  # noqa: BLE001 — memória é acessória
            from .logging_config import log_suppressed

            log_suppressed("memory_injection.consolidated", exc)
            facts = ""
        if facts:
            parts.append(f"{_CONSOLIDATED_HEADER}\n{facts}")
    try:
        from .memory_directives import directives_prompt_block

        directives = directives_prompt_block(scope.path)
    except Exception as exc:  # noqa: BLE001 — memória é acessória
        from .logging_config import log_suppressed

        log_suppressed("memory_injection.directives", exc)
        directives = ""
    if directives:
        parts.append(directives)
    return "\n\n".join(parts)[:MAX_BLOCK_CHARS]


def memory_block_provider(
    workspace: object, *, include_consolidated: bool = True
) -> Callable[[], str] | None:
    """Callable que recalcula o bloco a cada chamada (``None`` sem workspace)."""
    scope = resolve_workspace_scope(workspace)
    if scope is None:
        return None
    path = scope.path

    def _provider() -> str:
        return workspace_memory_block(path, include_consolidated=include_consolidated)

    return _provider


def attach_workspace_memory(
    ctx: Any, workspace: object, *, include_consolidated: bool = True
) -> bool:
    """Liga o bloco dinâmico ao contexto; sem workspace real, desliga (fail closed).

    Devolve ``True`` quando um provider ficou ligado.
    """
    provider = memory_block_provider(workspace, include_consolidated=include_consolidated)
    try:
        ctx.memory_block = provider
    except AttributeError:
        return False
    return provider is not None


def strip_persisted_directives(messages: list[dict]) -> list[dict]:
    """Remove diretivas que versões anteriores gravaram no histórico.

    Até a sprint 68, o bloco do ``LocalMemoryProvider`` (com as diretivas no
    final) era anexado como mensagem ``system`` e persistido com a sessão.
    Retomar essa sessão manteria viva uma diretiva já revogada. Corta a seção
    de diretivas dessas mensagens e descarta as que ficarem vazias.
    """
    cleaned: list[dict] = []
    for message in messages:
        content = message.get("content") if isinstance(message, dict) else None
        if (
            isinstance(message, dict)
            and message.get("role") == "system"
            and isinstance(content, str)
            and DIRECTIVES_HEADER in content
        ):
            kept = content.split(DIRECTIVES_HEADER, 1)[0].rstrip()
            if not kept or kept == "## Memória do Projeto":
                continue
            cleaned.append({**message, "content": kept})
            continue
        cleaned.append(message)
    return cleaned

