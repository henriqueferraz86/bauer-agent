"""Testes para bauer/loop_skills.py — parsing YAML, validação, registry/matcher."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from bauer.loop_skills import (
    LoopSkill,
    LoopSkillError,
    LoopSkillNotFound,
    LoopSkillRegistry,
    LoopSkillValidationError,
    loop_skill_from_dict,
    loop_skill_from_yaml,
)


# ─── loop_skill_from_yaml / loop_skill_from_dict ────────────────────────────


def test_loop_skill_from_yaml_minimal():
    """Só os campos obrigatórios — defaults aplicados, aprovação nunca vira yolo sozinha."""
    text = """
name: minimal
trigger_pattern: 'hello'
task_template: 'do hello'
"""
    skill = loop_skill_from_yaml(text)
    assert skill.name == "minimal"
    assert skill.max_minutes == 30
    assert skill.max_tool_calls == 120
    assert skill.max_cost_usd == 2.0
    assert skill.approval_mode == "threshold"
    assert skill.approval_risk_threshold == 0.4
    assert skill.verify_command == ""
    assert skill.verify_auto is False
    assert skill.tags == []


def test_loop_skill_from_yaml_missing_name_raises():
    with pytest.raises(LoopSkillValidationError, match="name"):
        loop_skill_from_yaml("trigger_pattern: 'x'\ntask_template: 'y'\n")


def test_loop_skill_from_yaml_missing_trigger_pattern_raises():
    with pytest.raises(LoopSkillValidationError, match="trigger_pattern"):
        loop_skill_from_yaml("name: x\ntask_template: 'y'\n")


def test_loop_skill_from_yaml_missing_task_template_raises():
    with pytest.raises(LoopSkillValidationError, match="task_template"):
        loop_skill_from_yaml("name: x\ntrigger_pattern: 'y'\n")


def test_loop_skill_from_yaml_invalid_regex_raises():
    with pytest.raises(LoopSkillValidationError, match="regex"):
        loop_skill_from_yaml("name: x\ntrigger_pattern: '('\ntask_template: 'y'\n")


def test_loop_skill_from_yaml_invalid_approval_mode_raises():
    with pytest.raises(LoopSkillValidationError, match="approval_mode"):
        loop_skill_from_yaml(
            "name: x\ntrigger_pattern: 'y'\ntask_template: 'z'\napproval_mode: bogus\n"
        )


def test_loop_skill_from_yaml_not_a_mapping_raises():
    with pytest.raises(LoopSkillValidationError, match="mapa"):
        loop_skill_from_yaml("- just\n- a\n- list\n")


def test_loop_skill_from_yaml_bad_syntax_raises():
    with pytest.raises(LoopSkillValidationError, match="parse"):
        loop_skill_from_yaml("name: [unclosed\n")


def test_loop_skill_from_dict_invalid_number_raises():
    with pytest.raises(LoopSkillValidationError):
        loop_skill_from_dict(
            {"name": "x", "trigger_pattern": "y", "task_template": "z", "max_minutes": "not-a-number"}
        )


def test_loop_skill_from_yaml_full_fields():
    text = """
name: full
description: Descricao completa
trigger_pattern: 'conserta os testes'
task_template: 'Corrija os testes falhando'
max_minutes: 15
max_tool_calls: 50
max_cost_usd: 0.5
approval_mode: deny_all
approval_risk_threshold: 0.2
verify_command: 'pytest -q'
verify_auto: false
tags: [testing, maintenance]
"""
    skill = loop_skill_from_yaml(text, source="/tmp/full.yaml")
    assert skill.description == "Descricao completa"
    assert skill.max_minutes == 15
    assert skill.max_tool_calls == 50
    assert skill.max_cost_usd == 0.5
    assert skill.approval_mode == "deny_all"
    assert skill.approval_risk_threshold == 0.2
    assert skill.verify_command == "pytest -q"
    assert skill.tags == ["testing", "maintenance"]
    assert skill.source == "/tmp/full.yaml"


# ─── LoopSkill.render_task ───────────────────────────────────────────────────


def test_loop_skill_render_task_positional_groups():
    skill = LoopSkill(
        name="x", trigger_pattern=r"corrige (\w+) em (\S+)",
        task_template="Corrija {0} em {1}.",
    )
    m = re.search(skill.trigger_pattern, "por favor corrige bugs em app.py")
    assert skill.render_task(m) == "Corrija bugs em app.py."


def test_loop_skill_render_task_named_groups():
    skill = LoopSkill(
        name="x", trigger_pattern=r"corrige (?P<what>\w+) em (?P<target>\S+)",
        task_template="Corrija {what} em {target}.",
    )
    m = re.search(skill.trigger_pattern, "corrige bugs em app.py")
    assert skill.render_task(m) == "Corrija bugs em app.py."


def test_loop_skill_render_task_missing_placeholder_raises():
    skill = LoopSkill(name="x", trigger_pattern=r"(\w+)", task_template="{0} e {1}")
    m = re.search(skill.trigger_pattern, "hello")
    with pytest.raises(LoopSkillError, match="grupo inexistente"):
        skill.render_task(m)


def test_loop_skill_render_task_no_placeholders_is_static():
    skill = LoopSkill(name="x", trigger_pattern="hello", task_template="tarefa fixa, sem grupos")
    m = re.search(skill.trigger_pattern, "hello world")
    assert skill.render_task(m) == "tarefa fixa, sem grupos"


# ─── LoopSkillRegistry ────────────────────────────────────────────────────


@pytest.fixture
def skills_dir(tmp_path: Path) -> Path:
    d = tmp_path / "loop_skills"
    d.mkdir()
    return d


def _write_skill(d: Path, filename: str, name: str, trigger: str, task: str = "faz algo") -> None:
    (d / filename).write_text(
        f"name: {name}\ntrigger_pattern: '{trigger}'\ntask_template: '{task}'\n",
        encoding="utf-8",
    )


def test_loop_skill_registry_list_empty_dir(skills_dir: Path):
    registry = LoopSkillRegistry(skills_dir)
    assert registry.list() == []


def test_loop_skill_registry_list_skips_invalid_yaml(skills_dir: Path):
    _write_skill(skills_dir, "good.yaml", "good-skill", "hello")
    (skills_dir / "broken.yaml").write_text("name: [unclosed\n", encoding="utf-8")
    registry = LoopSkillRegistry(skills_dir)
    names = [s.name for s in registry.list()]
    assert names == ["good-skill"]


def test_loop_skill_registry_match_first_wins(skills_dir: Path):
    # Ambos os arquivos casam a mesma entrada — ordem alfabética de arquivo decide.
    _write_skill(skills_dir, "a_first.yaml", "skill-a", "conserta")
    _write_skill(skills_dir, "b_second.yaml", "skill-b", "conserta")
    registry = LoopSkillRegistry(skills_dir)
    matched = registry.match("preciso conserta algo aqui")
    assert matched is not None
    skill, m = matched
    assert skill.name == "skill-a"


def test_loop_skill_registry_match_no_match_returns_none(skills_dir: Path):
    _write_skill(skills_dir, "a.yaml", "skill-a", "hello")
    registry = LoopSkillRegistry(skills_dir)
    assert registry.match("nada a ver com o padrao") is None


def test_loop_skill_registry_get_not_found_raises(skills_dir: Path):
    registry = LoopSkillRegistry(skills_dir)
    with pytest.raises(LoopSkillNotFound):
        registry.get("does-not-exist")


def test_loop_skill_registry_get_found(skills_dir: Path):
    _write_skill(skills_dir, "a.yaml", "skill-a", "hello")
    registry = LoopSkillRegistry(skills_dir)
    assert registry.get("skill-a").name == "skill-a"


def test_loop_skill_registry_reloads_from_disk(skills_dir: Path):
    """Editar o YAML manualmente tem efeito imediato — sem cache stale."""
    _write_skill(skills_dir, "a.yaml", "skill-a", "hello")
    registry = LoopSkillRegistry(skills_dir)
    assert len(registry.list()) == 1
    _write_skill(skills_dir, "b.yaml", "skill-b", "world")
    assert len(registry.list()) == 2


def test_loop_skills_dir_uses_bauer_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("BAUER_HOME", str(tmp_path / "bauer-home"))
    from bauer.paths import loop_skills_dir

    d = loop_skills_dir()
    assert d == tmp_path / "bauer-home" / "loop_skills"
    assert d.is_dir()


def test_loop_skill_registry_default_dir_is_opt_in_empty(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Sem nenhum YAML instalado, o registry é um no-op completo."""
    monkeypatch.setenv("BAUER_HOME", str(tmp_path / "bauer-home"))
    registry = LoopSkillRegistry()
    assert registry.list() == []
    assert registry.match("qualquer coisa") is None
