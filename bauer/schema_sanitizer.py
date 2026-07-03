"""Tool-schema sanitizer — fix common JSON Schema bugs before sending to LLMs.

Different LLM providers have different levels of JSON Schema strictness. This
module deep-walks tool schemas and applies a series of normalisation passes so
they work across the widest range of providers without having to maintain
separate schema trees per provider.

Fixes applied
-------------
1. **Bare-string type** — ``"type": "object"`` is valid JSON Schema but some
   providers (notably xAI / grok) require the full form ``{"type": "object"}``.
   We never encounter bare-string *schemas* in practice; this pass handles the
   edge case where a property value is a bare string instead of a dict.

2. **Nullable union** — ``"type": ["string", "null"]`` and
   ``"type": ["null", "string"]`` are collapsed to
   ``"type": "string", "nullable": true`` (OpenAI style). Some providers
   (Ollama, llama.cpp) crash on the array form.

3. **Top-level combinators** — ``anyOf``/``oneOf``/``allOf`` at the *property*
   level confuse strict providers (e.g. xAI). When a schema is a combinator
   wrapping a single branch we unwrap it; when it wraps ``[{…}, {"type": "null"}]``
   we collapse it to the concrete branch + ``"nullable": true``.

4. **``pattern`` / ``format`` stripping** — llama.cpp ignores these fields but
   occasionally crashes when they're present in function schemas. Configurable:
   ``strip_pattern`` and ``strip_format`` flags.

5. **``additionalProperties`` bool** — Some providers expect
   ``"additionalProperties": false`` not ``"additionalProperties": true``. We
   leave the value unchanged but ensure the field is a bool, not a schema dict,
   when in strict mode.

Usage::

    from bauer.schema_sanitizer import sanitize_tool_schemas, SanitizerConfig

    # Default: fix everything
    clean = sanitize_tool_schemas(raw_schemas)

    # xAI strict mode (strip pattern/format, unwrap combinators)
    xai_cfg = SanitizerConfig(strip_pattern=True, strip_format=True,
                               strip_combinators=True)
    clean = sanitize_tool_schemas(raw_schemas, config=xai_cfg)

    # llama.cpp mode (strip pattern/format only)
    llama_cfg = SanitizerConfig(strip_pattern=True, strip_format=True)
    clean = sanitize_tool_schemas(raw_schemas, config=llama_cfg)

Inspired by Hermes Agent's ``tools/schema_sanitizer.py``.
"""

from __future__ import annotations

import copy
import logging
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

_NULL_TYPES: frozenset[str] = frozenset({"null", "None", "NoneType"})
_COMBINATOR_KEYS: frozenset[str] = frozenset({"anyOf", "oneOf", "allOf"})


@dataclass
class SanitizerConfig:
    """Knobs controlling which fixes are applied.

    All fixes are enabled by default. Disable specific ones if your target
    provider handles the raw form correctly and you want a lighter touch.
    """
    fix_nullable_union: bool = True
    """Collapse ``type: [X, "null"]`` → ``type: X, nullable: true``."""

    strip_combinators: bool = True
    """Unwrap single-branch anyOf/oneOf/allOf; collapse nullable patterns."""

    strip_pattern: bool = False
    """Remove ``pattern`` fields from all schemas. llama.cpp crashes on them."""

    strip_format: bool = False
    """Remove ``format`` fields from all schemas. llama.cpp ignores + crashes."""

    fix_additional_properties: bool = False
    """Replace ``"additionalProperties": {…}`` with ``false`` in strict mode."""


_DEFAULT_CONFIG = SanitizerConfig()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def sanitize_tool_schemas(
    schemas: list[dict[str, Any]],
    *,
    config: SanitizerConfig | None = None,
) -> list[dict[str, Any]]:
    """Return a deep-copy of *schemas* with all requested fixes applied.

    Each entry in *schemas* is expected to be an OpenAI-compatible tool
    definition::

        {
            "type": "function",
            "function": {
                "name": "...",
                "description": "...",
                "parameters": { ... }   ← JSON Schema object
            }
        }

    The sanitiser operates on the ``parameters`` sub-tree. Top-level tool
    metadata is preserved unchanged.
    """
    cfg = config or _DEFAULT_CONFIG
    result: list[dict[str, Any]] = []
    for schema in schemas:
        cleaned = copy.deepcopy(schema)
        if "function" in cleaned and "parameters" in cleaned["function"]:
            cleaned["function"]["parameters"] = _sanitize_schema(
                cleaned["function"]["parameters"], cfg
            )
        result.append(cleaned)
    return result


def sanitize_parameters(
    parameters: dict[str, Any],
    *,
    config: SanitizerConfig | None = None,
) -> dict[str, Any]:
    """Sanitise a single ``parameters`` JSON-Schema dict.

    Convenience wrapper for callers that already have an unwrapped schema.
    Returns a deep-copy — never mutates the input.
    """
    cfg = config or _DEFAULT_CONFIG
    return _sanitize_schema(copy.deepcopy(parameters), cfg)


# ---------------------------------------------------------------------------
# Internal deep-walker
# ---------------------------------------------------------------------------


def _sanitize_schema(
    node: Any,
    cfg: SanitizerConfig,
) -> Any:
    """Recursively sanitise *node* in-place (expects a deep-copy)."""
    if not isinstance(node, dict):
        return node

    # --- Fix 1: bare-string "type" value → leave as-is (already a string) --
    # JSON Schema allows "type": "string" — that's fine. What we're guarding
    # against is a *property value* that is a bare string instead of a schema
    # dict (e.g. ``"foo": "string"`` instead of ``"foo": {"type": "string"}``).
    # This is caught when processing properties below.

    # --- Fix 2: nullable union -------------------------------------------
    if cfg.fix_nullable_union:
        node = _fix_nullable_union(node)

    # --- Fix 3: combinators ----------------------------------------------
    if cfg.strip_combinators:
        node = _fix_combinators(node)

    # --- Fix 4 / 5: strip pattern / format --------------------------------
    if cfg.strip_pattern:
        node.pop("pattern", None)
    if cfg.strip_format:
        node.pop("format", None)

    # --- Fix 6: additionalProperties -------------------------------------
    if cfg.fix_additional_properties and isinstance(
        node.get("additionalProperties"), dict
    ):
        node["additionalProperties"] = False

    # --- Recurse into nested schemas -------------------------------------
    # properties
    if "properties" in node and isinstance(node["properties"], dict):
        fixed_props: dict[str, Any] = {}
        for prop_name, prop_schema in node["properties"].items():
            if isinstance(prop_schema, str):
                # bare-string property value → wrap
                fixed_props[prop_name] = {"type": prop_schema}
            else:
                fixed_props[prop_name] = _sanitize_schema(prop_schema, cfg)
        node["properties"] = fixed_props

    # items (arrays)
    if "items" in node:
        node["items"] = _sanitize_schema(node["items"], cfg)

    # anyOf / oneOf / allOf branches (already partially handled above, but
    # recurse into surviving branches)
    for key in _COMBINATOR_KEYS:
        if key in node and isinstance(node[key], list):
            node[key] = [_sanitize_schema(branch, cfg) for branch in node[key]]

    # definitions / $defs
    for defs_key in ("definitions", "$defs"):
        if defs_key in node and isinstance(node[defs_key], dict):
            node[defs_key] = {
                k: _sanitize_schema(v, cfg)
                for k, v in node[defs_key].items()
            }

    return node


# ---------------------------------------------------------------------------
# Fix helpers
# ---------------------------------------------------------------------------


def _fix_nullable_union(node: dict[str, Any]) -> dict[str, Any]:
    """Collapse ``"type": ["X", "null"]`` → ``"type": "X", "nullable": true``.

    Handles both orderings: ``["string", "null"]`` and ``["null", "string"]``.
    Also handles 2-element lists where one element is a null-ish type.
    No-op if the type is already a string or if there are 3+ non-null types.
    """
    type_val = node.get("type")
    if not isinstance(type_val, list):
        return node

    non_null = [t for t in type_val if t not in _NULL_TYPES]
    has_null = len(non_null) < len(type_val)

    if not has_null:
        # No null in the list — flatten to single type if only one element.
        if len(non_null) == 1:
            node["type"] = non_null[0]
        return node

    if len(non_null) == 1:
        node["type"] = non_null[0]
        node["nullable"] = True
    elif len(non_null) == 0:
        # Entire type list is null variants — unlikely but handle gracefully.
        node["type"] = "null"
    # else: multiple non-null types — leave as-is, provider must handle it.
    return node


def _fix_combinators(node: dict[str, Any]) -> dict[str, Any]:
    """Unwrap anyOf/oneOf when safe to do so.

    Cases handled:
    - ``anyOf: [{…}]`` (single branch) → inline the branch
    - ``anyOf: [{…}, {"type": "null"}]`` → inline the non-null branch +
      ``"nullable": true``
    - Same patterns for ``oneOf``
    - ``allOf: [{…}]`` → inline (single branch only)

    If neither condition matches we leave the combinator intact so the schema
    remains semantically correct.
    """
    for key in ("anyOf", "oneOf"):
        if key not in node:
            continue
        branches = node[key]
        if not isinstance(branches, list) or not branches:
            continue

        null_branches = [b for b in branches if _is_null_schema(b)]
        real_branches = [b for b in branches if not _is_null_schema(b)]

        if len(real_branches) == 1:
            # Safe to unwrap.
            real = real_branches[0]
            del node[key]
            node.update(real)
            if null_branches:
                node["nullable"] = True
            return node  # re-entry not needed; caller will recurse

        elif len(branches) == 1:
            # Single branch (already handled above via real_branches logic,
            # but guard again for safety).
            real = branches[0]
            del node[key]
            node.update(real)
            return node

    # allOf: only unwrap if single branch
    if "allOf" in node:
        branches = node["allOf"]
        if isinstance(branches, list) and len(branches) == 1:
            real = branches[0]
            del node["allOf"]
            node.update(real)

    return node


def _is_null_schema(schema: Any) -> bool:
    """Return True if *schema* represents a null-only type."""
    if not isinstance(schema, dict):
        return False
    t = schema.get("type")
    if isinstance(t, str) and t in _NULL_TYPES:
        return True
    if isinstance(t, list) and all(x in _NULL_TYPES for x in t):
        return True
    # ``{}`` (empty schema) is NOT considered null-only.
    return False
