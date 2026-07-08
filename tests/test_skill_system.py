"""Tests for bauer/skill_system.py — YAML skill install/list/remove."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from bauer.skill_system import (
    Skill,
    SkillParam,
    SkillManager,
    SkillError,
    SkillNotFound,
    SkillAlreadyExists,
    SkillValidationError,
    skill_from_yaml,
    skill_from_dict,
    get_default_manager,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


MINIMAL_YAML = """\
name: test_skill
description: A test skill
invoke: "Do something useful about {topic}."
params:
  topic:
    description: The topic to address.
    required: true
"""

FULL_YAML = """\
name: summarise_code
version: "2.0"
description: Summarise source code into compact docs.
author: bauer
tags: [code, documentation]
tools: [read_file, glob_files]
model: claude-3-5-haiku-20241022
params:
  path:
    description: File or directory to summarise.
    required: true
  format:
    description: Output format.
    required: false
    default: markdown
invoke: |
    Read {path} and produce a {format} summary covering:
    purpose, API, dependencies, and patterns.
"""


@pytest.fixture
def skill_dir(tmp_path):
    return tmp_path / "skills"


@pytest.fixture
def manager(skill_dir):
    return SkillManager(skills_dir=skill_dir)


# ---------------------------------------------------------------------------
# skill_from_yaml / skill_from_dict
# ---------------------------------------------------------------------------


def test_parse_minimal_yaml():
    skill = skill_from_yaml(MINIMAL_YAML)
    assert skill.name == "test_skill"
    assert skill.description == "A test skill"
    assert "topic" in skill.invoke
    assert "topic" in skill.params
    assert skill.params["topic"].required is True


def test_parse_full_yaml():
    skill = skill_from_yaml(FULL_YAML)
    assert skill.name == "summarise_code"
    assert skill.version == "2.0"
    assert skill.author == "bauer"
    assert "code" in skill.tags
    assert "read_file" in skill.tools
    assert skill.model == "claude-3-5-haiku-20241022"
    assert "path" in skill.params
    assert "format" in skill.params
    assert skill.params["format"].default == "markdown"
    assert skill.params["format"].required is False


def test_parse_missing_name_raises():
    yaml_text = "invoke: do something\n"
    with pytest.raises(SkillValidationError, match="name"):
        skill_from_yaml(yaml_text)


def test_parse_missing_invoke_raises():
    yaml_text = "name: myskill\ndescription: no invoke\n"
    with pytest.raises(SkillValidationError, match="invoke"):
        skill_from_yaml(yaml_text)


def test_parse_invalid_name_raises():
    yaml_text = "name: My Skill With Spaces\ninvoke: do something\n"
    with pytest.raises(SkillValidationError):
        skill_from_yaml(yaml_text)


def test_parse_from_dict():
    d = {
        "name": "dict_skill",
        "description": "from dict",
        "invoke": "Do {action}.",
        "tags": ["test"],
        "params": {
            "action": {"description": "The action", "required": True},
        }
    }
    skill = skill_from_dict(d)
    assert skill.name == "dict_skill"
    assert skill.params["action"].required is True


# ---------------------------------------------------------------------------
# Skill.render()
# ---------------------------------------------------------------------------


def test_render_with_required_param():
    skill = skill_from_yaml(MINIMAL_YAML)
    rendered = skill.render({"topic": "Python packaging"})
    assert "Python packaging" in rendered


def test_render_missing_required_param_raises():
    skill = skill_from_yaml(MINIMAL_YAML)
    with pytest.raises(SkillError, match="topic"):
        skill.render({})


def test_render_with_default_param():
    skill = skill_from_yaml(FULL_YAML)
    rendered = skill.render({"path": "bauer/agent.py"})
    # format defaults to markdown
    assert "markdown" in rendered
    assert "bauer/agent.py" in rendered


def test_render_with_override_default():
    skill = skill_from_yaml(FULL_YAML)
    rendered = skill.render({"path": "bauer/agent.py", "format": "rst"})
    assert "rst" in rendered


def test_render_unknown_placeholder_raises():
    skill = Skill(
        name="test",
        invoke="Do {known} and {unknown_placeholder}.",
        params={"known": SkillParam(required=True)},
    )
    with pytest.raises(SkillError, match="placeholder"):
        skill.render({"known": "val"})


# ---------------------------------------------------------------------------
# SkillManager — install
# ---------------------------------------------------------------------------


def test_install_from_yaml_str(manager):
    skill = manager.install_from_yaml(MINIMAL_YAML, source="test")
    assert skill.name == "test_skill"
    assert manager.exists("test_skill")


def test_install_from_file(manager, tmp_path):
    p = tmp_path / "test_skill.yaml"
    p.write_text(MINIMAL_YAML, encoding="utf-8")
    skill = manager.install_from_file(p)
    assert skill.name == "test_skill"


def test_install_from_file_not_found(manager, tmp_path):
    with pytest.raises(SkillError, match="not found"):
        manager.install_from_file(tmp_path / "nonexistent.yaml")


def test_install_duplicate_raises(manager):
    manager.install_from_yaml(MINIMAL_YAML)
    with pytest.raises(SkillAlreadyExists):
        manager.install_from_yaml(MINIMAL_YAML)


def test_install_force_overwrites(manager):
    manager.install_from_yaml(MINIMAL_YAML)
    # Change version and force-install
    updated = MINIMAL_YAML.replace(
        "description: A test skill",
        "description: Updated description",
    )
    skill = manager.install_from_yaml(updated, force=True)
    assert skill.description == "Updated description"
    # Should still only have one skill installed
    assert len(manager.list_skills()) == 1


def test_install_from_directory(manager, tmp_path):
    d = tmp_path / "skills_dir"
    d.mkdir()
    (d / "skill_a.yaml").write_text(MINIMAL_YAML, encoding="utf-8")
    (d / "skill_b.yaml").write_text(
        "name: skill_b\ninvoke: Do {x}.\nparams:\n  x:\n    required: false\n",
        encoding="utf-8",
    )
    skills = manager.install_from_directory(d)
    assert len(skills) == 2
    names = {s.name for s in skills}
    assert "test_skill" in names
    assert "skill_b" in names


# ---------------------------------------------------------------------------
# SkillManager — get / list / exists
# ---------------------------------------------------------------------------


def test_get_installed_skill(manager):
    manager.install_from_yaml(FULL_YAML)
    skill = manager.get("summarise_code")
    assert skill.name == "summarise_code"
    assert skill.version == "2.0"


def test_get_not_found_raises(manager):
    with pytest.raises(SkillNotFound):
        manager.get("nonexistent_skill")


def test_list_empty(manager):
    assert manager.list_skills() == []


def test_list_after_install(manager):
    manager.install_from_yaml(MINIMAL_YAML)
    manager.install_from_yaml(FULL_YAML)
    skills = manager.list_skills()
    assert len(skills) == 2


def test_list_filter_by_query(manager):
    manager.install_from_yaml(MINIMAL_YAML)
    manager.install_from_yaml(FULL_YAML)
    # "code" is in summarise_code description/name
    results = manager.list_skills(query="code")
    names = {s.name for s in results}
    assert "summarise_code" in names
    assert "test_skill" not in names


def test_list_filter_by_tags(manager):
    manager.install_from_yaml(FULL_YAML)
    results = manager.list_skills(tags=["code"])
    assert len(results) == 1
    assert results[0].name == "summarise_code"


def test_list_filter_by_nonexistent_tag(manager):
    manager.install_from_yaml(FULL_YAML)
    results = manager.list_skills(tags=["nonexistent_tag_xyz"])
    assert results == []


def test_exists_true(manager):
    manager.install_from_yaml(MINIMAL_YAML)
    assert manager.exists("test_skill") is True


def test_exists_false(manager):
    assert manager.exists("not_here") is False


# ---------------------------------------------------------------------------
# SkillManager — remove
# ---------------------------------------------------------------------------


def test_remove_installed_skill(manager):
    manager.install_from_yaml(MINIMAL_YAML)
    removed = manager.remove("test_skill")
    assert removed is True
    assert not manager.exists("test_skill")


def test_remove_nonexistent_returns_false(manager):
    removed = manager.remove("nonexistent")
    assert removed is False


# ---------------------------------------------------------------------------
# SkillManager — update
# ---------------------------------------------------------------------------


def test_update_from_yaml(manager):
    manager.install_from_yaml(MINIMAL_YAML)
    updated = MINIMAL_YAML.replace("A test skill", "Updated skill")
    skill = manager.update_from_yaml("test_skill", updated)
    assert skill.description == "Updated skill"


def test_update_not_found_raises(manager):
    with pytest.raises(SkillNotFound):
        manager.update_from_yaml("nonexistent", MINIMAL_YAML)


def test_update_rename_raises(manager):
    """Cannot rename a skill via update — name must match."""
    manager.install_from_yaml(MINIMAL_YAML)
    renamed = MINIMAL_YAML.replace("name: test_skill", "name: different_name")
    with pytest.raises(SkillError, match="rename"):
        manager.update_from_yaml("test_skill", renamed)


# ---------------------------------------------------------------------------
# Skill persistence (round-trip through JSON)
# ---------------------------------------------------------------------------


def test_roundtrip_persist_load(manager):
    manager.install_from_yaml(FULL_YAML)
    loaded = manager.get("summarise_code")
    assert loaded.name == "summarise_code"
    assert loaded.version == "2.0"
    assert loaded.tags == ["code", "documentation"]
    assert loaded.params["path"].required is True
    assert loaded.params["format"].default == "markdown"


def test_to_dict_and_back():
    skill = skill_from_yaml(FULL_YAML)
    d = skill.to_dict()
    assert d["name"] == "summarise_code"
    assert d["tags"] == ["code", "documentation"]
    assert d["params"]["path"]["required"] is True


# ---------------------------------------------------------------------------
# Skill.summary() and to_yaml_str()
# ---------------------------------------------------------------------------


def test_skill_summary():
    skill = skill_from_yaml(MINIMAL_YAML)
    summary = skill.summary()
    assert "test_skill" in summary
    assert "A test skill" in summary


def test_skill_to_yaml_str():
    skill = skill_from_yaml(FULL_YAML)
    yaml_str = skill.to_yaml_str()
    assert "summarise_code" in yaml_str
    assert "invoke:" in yaml_str


# ---------------------------------------------------------------------------
# get_default_manager
# ---------------------------------------------------------------------------


def test_get_default_manager_returns_manager():
    m = get_default_manager()
    assert isinstance(m, SkillManager)


def test_get_default_manager_singleton():
    m1 = get_default_manager()
    m2 = get_default_manager()
    assert m1 is m2
