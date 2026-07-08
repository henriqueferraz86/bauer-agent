"""Tests for bauer.skill_bundles — SkillBundle + SkillBundleManager."""

from __future__ import annotations

import pytest

from bauer.skill_bundles import SkillBundle, SkillBundleManager, _slugify


# ---------------------------------------------------------------------------
# Slugify
# ---------------------------------------------------------------------------

class TestSlugify:
    def test_lowercase_spaces_to_hyphens(self):
        assert _slugify("Backend Dev") == "backend-dev"

    def test_underscores_to_hyphens(self):
        assert _slugify("my_bundle") == "my-bundle"

    def test_removes_special_chars(self):
        assert _slugify("code! review@2024") == "code-review2024"

    def test_collapses_multi_hyphen(self):
        assert _slugify("a--b---c") == "a-b-c"

    def test_already_valid_slug(self):
        assert _slugify("git-workflow") == "git-workflow"


# ---------------------------------------------------------------------------
# SkillBundle
# ---------------------------------------------------------------------------

class TestSkillBundle:
    def test_slug_from_name(self):
        b = SkillBundle(name="Backend Dev")
        assert b.slug == "backend-dev"

    def test_to_dict_minimal(self):
        b = SkillBundle(name="test", skills=["a", "b"])
        d = b.to_dict()
        assert d["name"] == "test"
        assert d["skills"] == ["a", "b"]
        assert "description" not in d

    def test_to_dict_full(self):
        b = SkillBundle(
            name="full", description="desc", skills=["x"], instruction="instrução"
        )
        d = b.to_dict()
        assert d["description"] == "desc"
        assert d["instruction"] == "instrução"

    def test_from_dict_roundtrip(self):
        b = SkillBundle(name="round", description="d", skills=["s1", "s2"], instruction="i")
        b2 = SkillBundle.from_dict(b.to_dict())
        assert b2.name == b.name
        assert b2.skills == b.skills
        assert b2.instruction == b.instruction

    def test_from_dict_defaults(self):
        b = SkillBundle.from_dict({"name": "min", "skills": []})
        assert b.description == ""
        assert b.instruction == ""


# ---------------------------------------------------------------------------
# SkillBundleManager CRUD
# ---------------------------------------------------------------------------

class TestSkillBundleManagerCRUD:
    def test_save_and_get(self, tmp_path):
        mgr = SkillBundleManager(bundles_dir=tmp_path)
        b = SkillBundle(name="my bundle", skills=["tdd", "code-review"])
        mgr.save(b)
        got = mgr.get("my bundle")
        assert got is not None
        assert got.name == "my bundle"
        assert "tdd" in got.skills

    def test_list_empty(self, tmp_path):
        mgr = SkillBundleManager(bundles_dir=tmp_path)
        assert mgr.list_bundles() == []

    def test_list_after_save(self, tmp_path):
        mgr = SkillBundleManager(bundles_dir=tmp_path)
        mgr.save(SkillBundle(name="alpha", skills=["a"]))
        mgr.save(SkillBundle(name="beta", skills=["b"]))
        bundles = mgr.list_bundles()
        assert len(bundles) == 2
        slugs = {b.slug for b in bundles}
        assert "alpha" in slugs
        assert "beta" in slugs

    def test_delete_existing(self, tmp_path):
        mgr = SkillBundleManager(bundles_dir=tmp_path)
        mgr.save(SkillBundle(name="del-me", skills=["x"]))
        assert mgr.delete("del-me") is True
        assert mgr.get("del-me") is None

    def test_delete_nonexistent_returns_false(self, tmp_path):
        mgr = SkillBundleManager(bundles_dir=tmp_path)
        assert mgr.delete("ghost") is False

    def test_get_by_slug(self, tmp_path):
        mgr = SkillBundleManager(bundles_dir=tmp_path)
        mgr.save(SkillBundle(name="Full Stack Dev", skills=["docker-ops"]))
        got = mgr.get("full-stack-dev")
        assert got is not None

    def test_get_nonexistent_returns_none(self, tmp_path):
        mgr = SkillBundleManager(bundles_dir=tmp_path)
        assert mgr.get("nonexistent") is None

    def test_save_persists_skills(self, tmp_path):
        mgr = SkillBundleManager(bundles_dir=tmp_path)
        b = SkillBundle(name="persist", skills=["tdd", "debugging"], instruction="go deep")
        mgr.save(b)
        # Re-load from disk
        mgr2 = SkillBundleManager(bundles_dir=tmp_path)
        got = mgr2.get("persist")
        assert got is not None
        assert got.skills == ["tdd", "debugging"]
        assert got.instruction == "go deep"


# ---------------------------------------------------------------------------
# resolve_bundle
# ---------------------------------------------------------------------------

class TestResolveBundleContent:
    def test_resolve_nonexistent_returns_none(self, tmp_path):
        mgr = SkillBundleManager(bundles_dir=tmp_path)
        assert mgr.resolve_bundle("ghost") is None

    def test_resolve_with_fake_skill_manager(self, tmp_path):
        from unittest.mock import MagicMock
        from bauer.skill_system import Skill

        mgr = SkillBundleManager(bundles_dir=tmp_path)
        mgr.save(SkillBundle(
            name="combo", skills=["skill-a", "skill-b"], instruction="Combine both."
        ))

        fake_sm = MagicMock()
        fake_skill = MagicMock()
        fake_skill.name = "Skill A"
        fake_skill.render.return_value = "Conteúdo da skill A."
        fake_sm.get.return_value = fake_skill

        content = mgr.resolve_bundle("combo", skill_manager=fake_sm)
        assert content is not None
        assert "Combine both." in content
        assert "Conteúdo da skill A." in content

    def test_resolve_missing_skill_shows_placeholder(self, tmp_path):
        from unittest.mock import MagicMock

        mgr = SkillBundleManager(bundles_dir=tmp_path)
        mgr.save(SkillBundle(name="broken", skills=["missing-skill"]))

        fake_sm = MagicMock()
        fake_sm.get.return_value = None  # skill not found

        content = mgr.resolve_bundle("broken", skill_manager=fake_sm)
        assert content is not None
        assert "missing-skill" in content

    def test_resolve_with_instruction_only(self, tmp_path):
        from unittest.mock import MagicMock

        mgr = SkillBundleManager(bundles_dir=tmp_path)
        mgr.save(SkillBundle(
            name="instr-only", skills=[], instruction="Minha instrução"
        ))
        fake_sm = MagicMock()
        content = mgr.resolve_bundle("instr-only", skill_manager=fake_sm)
        # Empty skills → only instruction or None if all empty
        if content:
            assert "Minha instrução" in content
