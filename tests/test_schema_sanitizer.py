"""Tests for `bauer/schema_sanitizer.py`."""

from __future__ import annotations

import copy

import pytest

from bauer.schema_sanitizer import (
    SanitizerConfig,
    sanitize_parameters,
    sanitize_tool_schemas,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_tool(parameters: dict) -> dict:
    """Wrap *parameters* in a minimal OpenAI-compatible tool definition."""
    return {
        "type": "function",
        "function": {
            "name": "test_tool",
            "description": "A test tool.",
            "parameters": parameters,
        },
    }


def _params(tool: dict) -> dict:
    return tool["function"]["parameters"]


# ---------------------------------------------------------------------------
# sanitize_tool_schemas — structural
# ---------------------------------------------------------------------------


def test_returns_new_list_not_mutating_original():
    original = [_make_tool({"type": "object", "properties": {}})]
    result = sanitize_tool_schemas(original)
    assert result is not original
    assert result[0] is not original[0]


def test_non_function_entries_preserved():
    schema = {"type": "something_else", "extra": "data"}
    result = sanitize_tool_schemas([schema])
    assert result[0] == schema


def test_tool_without_parameters_preserved():
    tool = {"type": "function", "function": {"name": "no_params"}}
    result = sanitize_tool_schemas([tool])
    assert result[0] == tool


# ---------------------------------------------------------------------------
# Fix 2: Nullable union collapse
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("type_list,expected_type,nullable", [
    (["string", "null"],    "string",  True),
    (["null", "string"],    "string",  True),
    (["integer", "null"],   "integer", True),
    (["null", "boolean"],   "boolean", True),
    (["null", "null"],      "null",    False),   # all-null edge case
    (["string"],            "string",  False),   # single non-null → just flatten
])
def test_nullable_union_collapsed(type_list, expected_type, nullable):
    params = {
        "type": "object",
        "properties": {
            "x": {"type": type_list},
        },
    }
    result = sanitize_parameters(params)
    prop = result["properties"]["x"]
    assert prop["type"] == expected_type
    if nullable:
        assert prop.get("nullable") is True
    else:
        assert "nullable" not in prop


def test_nullable_union_multiple_non_null_left_alone():
    """[string, integer, null] has multiple real types — leave as-is."""
    params = {
        "type": "object",
        "properties": {
            "x": {"type": ["string", "integer", "null"]},
        },
    }
    result = sanitize_parameters(params)
    # The array should be unchanged.
    assert isinstance(result["properties"]["x"]["type"], list)


def test_non_array_type_untouched():
    params = {"type": "object", "properties": {"x": {"type": "string"}}}
    result = sanitize_parameters(params)
    assert result["properties"]["x"]["type"] == "string"
    assert "nullable" not in result["properties"]["x"]


# ---------------------------------------------------------------------------
# Fix 3: Combinator unwrapping
# ---------------------------------------------------------------------------


def test_anyof_single_branch_unwrapped():
    params = {
        "type": "object",
        "properties": {
            "x": {"anyOf": [{"type": "string", "description": "a str"}]},
        },
    }
    result = sanitize_parameters(params)
    prop = result["properties"]["x"]
    assert "anyOf" not in prop
    assert prop["type"] == "string"


def test_anyof_nullable_pattern_unwrapped():
    params = {
        "type": "object",
        "properties": {
            "x": {"anyOf": [{"type": "string"}, {"type": "null"}]},
        },
    }
    result = sanitize_parameters(params)
    prop = result["properties"]["x"]
    assert "anyOf" not in prop
    assert prop["type"] == "string"
    assert prop.get("nullable") is True


def test_oneof_nullable_pattern_unwrapped():
    params = {
        "type": "object",
        "properties": {
            "x": {"oneOf": [{"type": "null"}, {"type": "integer"}]},
        },
    }
    result = sanitize_parameters(params)
    prop = result["properties"]["x"]
    assert "oneOf" not in prop
    assert prop["type"] == "integer"
    assert prop.get("nullable") is True


def test_allof_single_branch_unwrapped():
    params = {
        "type": "object",
        "properties": {
            "x": {"allOf": [{"type": "string", "minLength": 1}]},
        },
    }
    result = sanitize_parameters(params)
    prop = result["properties"]["x"]
    assert "allOf" not in prop
    assert prop["type"] == "string"
    assert prop["minLength"] == 1


def test_anyof_multiple_real_branches_left_intact():
    """anyOf with 2+ real non-null branches should NOT be touched."""
    params = {
        "type": "object",
        "properties": {
            "x": {"anyOf": [{"type": "string"}, {"type": "integer"}]},
        },
    }
    result = sanitize_parameters(params)
    prop = result["properties"]["x"]
    assert "anyOf" in prop


# ---------------------------------------------------------------------------
# Fix 4 / 5: strip pattern / format
# ---------------------------------------------------------------------------


def test_pattern_not_stripped_by_default():
    params = {
        "type": "object",
        "properties": {"x": {"type": "string", "pattern": r"^\d+$"}},
    }
    result = sanitize_parameters(params)
    assert result["properties"]["x"]["pattern"] == r"^\d+$"


def test_pattern_stripped_when_configured():
    cfg = SanitizerConfig(strip_pattern=True)
    params = {
        "type": "object",
        "properties": {"x": {"type": "string", "pattern": r"^\d+$"}},
    }
    result = sanitize_parameters(params, config=cfg)
    assert "pattern" not in result["properties"]["x"]


def test_format_not_stripped_by_default():
    params = {
        "type": "object",
        "properties": {"x": {"type": "string", "format": "date-time"}},
    }
    result = sanitize_parameters(params)
    assert result["properties"]["x"]["format"] == "date-time"


def test_format_stripped_when_configured():
    cfg = SanitizerConfig(strip_format=True)
    params = {
        "type": "object",
        "properties": {"x": {"type": "string", "format": "date-time"}},
    }
    result = sanitize_parameters(params, config=cfg)
    assert "format" not in result["properties"]["x"]


def test_xai_config_strips_both():
    """Simulated xAI strict mode."""
    cfg = SanitizerConfig(strip_pattern=True, strip_format=True,
                          strip_combinators=True)
    params = {
        "type": "object",
        "properties": {
            "ts": {"type": "string", "format": "date-time"},
            "code": {"type": "string", "pattern": r"^\d{6}$"},
            "val": {"anyOf": [{"type": "integer"}, {"type": "null"}]},
        },
    }
    result = sanitize_parameters(params, config=cfg)
    assert "format" not in result["properties"]["ts"]
    assert "pattern" not in result["properties"]["code"]
    prop_val = result["properties"]["val"]
    assert "anyOf" not in prop_val
    assert prop_val["type"] == "integer"
    assert prop_val["nullable"] is True


# ---------------------------------------------------------------------------
# Fix 6: additionalProperties
# ---------------------------------------------------------------------------


def test_additional_properties_dict_replaced_in_strict_mode():
    cfg = SanitizerConfig(fix_additional_properties=True)
    params = {
        "type": "object",
        "additionalProperties": {"type": "string"},
    }
    result = sanitize_parameters(params, config=cfg)
    assert result["additionalProperties"] is False


def test_additional_properties_false_preserved():
    cfg = SanitizerConfig(fix_additional_properties=True)
    params = {
        "type": "object",
        "additionalProperties": False,
    }
    result = sanitize_parameters(params, config=cfg)
    assert result["additionalProperties"] is False


def test_additional_properties_not_touched_by_default():
    params = {
        "type": "object",
        "additionalProperties": {"type": "string"},
    }
    result = sanitize_parameters(params)
    # Not a bool — should remain unchanged.
    assert isinstance(result["additionalProperties"], dict)


# ---------------------------------------------------------------------------
# Bare-string property value
# ---------------------------------------------------------------------------


def test_bare_string_property_wrapped():
    """``"x": "string"`` in properties → ``"x": {"type": "string"}``."""
    params = {
        "type": "object",
        "properties": {
            "x": "string",
            "y": {"type": "integer"},
        },
    }
    result = sanitize_parameters(params)
    assert result["properties"]["x"] == {"type": "string"}
    assert result["properties"]["y"] == {"type": "integer"}


# ---------------------------------------------------------------------------
# Deep nesting
# ---------------------------------------------------------------------------


def test_nested_properties_sanitized():
    params = {
        "type": "object",
        "properties": {
            "outer": {
                "type": "object",
                "properties": {
                    "inner": {"type": ["integer", "null"]},
                },
            },
        },
    }
    result = sanitize_parameters(params)
    inner = result["properties"]["outer"]["properties"]["inner"]
    assert inner["type"] == "integer"
    assert inner["nullable"] is True


def test_array_items_sanitized():
    params = {
        "type": "object",
        "properties": {
            "tags": {
                "type": "array",
                "items": {"anyOf": [{"type": "string"}, {"type": "null"}]},
            },
        },
    }
    result = sanitize_parameters(params)
    items = result["properties"]["tags"]["items"]
    assert "anyOf" not in items
    assert items["type"] == "string"
    assert items.get("nullable") is True


def test_definitions_sanitized():
    params = {
        "type": "object",
        "definitions": {
            "MyType": {"type": ["string", "null"]},
        },
        "properties": {
            "x": {"$ref": "#/definitions/MyType"},
        },
    }
    result = sanitize_parameters(params)
    my_type = result["definitions"]["MyType"]
    assert my_type["type"] == "string"
    assert my_type["nullable"] is True


# ---------------------------------------------------------------------------
# sanitize_tool_schemas — multiple schemas, config propagated
# ---------------------------------------------------------------------------


def test_multiple_schemas_all_sanitized():
    schemas = [
        _make_tool({
            "type": "object",
            "properties": {"a": {"type": ["string", "null"]}},
        }),
        _make_tool({
            "type": "object",
            "properties": {"b": {"anyOf": [{"type": "integer"}, {"type": "null"}]}},
        }),
    ]
    result = sanitize_tool_schemas(schemas)
    assert _params(result[0])["properties"]["a"]["type"] == "string"
    assert _params(result[1])["properties"]["b"]["type"] == "integer"


def test_config_propagated_to_all_schemas():
    cfg = SanitizerConfig(strip_pattern=True)
    schemas = [
        _make_tool({
            "type": "object",
            "properties": {"x": {"type": "string", "pattern": r"\d+"}},
        }),
        _make_tool({
            "type": "object",
            "properties": {"y": {"type": "string", "pattern": r"[a-z]+"}},
        }),
    ]
    result = sanitize_tool_schemas(schemas, config=cfg)
    assert "pattern" not in _params(result[0])["properties"]["x"]
    assert "pattern" not in _params(result[1])["properties"]["y"]
