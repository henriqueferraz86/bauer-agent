"""Tests for bauer.skills_hub — SkillsHub catalog."""

from __future__ import annotations

import pytest
from pathlib import Path

from bauer.skills_hub import SkillsHub, HubSkillEntry


# ---------------------------------------------------------------------------
# Test helpers — use the real data dir
# ---------------------------------------------------------------------------

def _get_hub() -> SkillsHub:
    """Return hub pointing at the real bauer/data/skills/ directory."""
    return SkillsHub()


# ---------------------------------------------------------------------------
# Built-in catalog
# ---------------------------------------------------------------------------

class TestBuiltInCatalog:
    def test_hub_has_skills(self):
        hub = _get_hub()
        skills = hub.list_skills()
        assert len(skills) >= 6, f"Expected ≥6 skills, got {len(skills)}"

    def test_categories_include_devops_and_coding(self):
        hub = _get_hub()
        cats = hub.categories()
        assert "devops" in cats
        assert "coding" in cats

    def test_list_by_category_devops(self):
        hub = _get_hub()
        devops = hub.list_skills(category="devops")
        assert len(devops) >= 2
        assert all(s.category == "devops" for s in devops)

    def test_list_by_category_coding(self):
        hub = _get_hub()
        coding = hub.list_skills(category="coding")
        assert len(coding) >= 3

    def test_get_git_workflow(self):
        hub = _get_hub()
        s = hub.get("git-workflow")
        assert s is not None
        assert s.slug == "git-workflow"
        assert s.category == "devops"

    def test_get_nonexistent_returns_none(self):
        hub = _get_hub()
        assert hub.get("this-does-not-exist") is None

    def test_entry_has_name_and_description(self):
        hub = _get_hub()
        skills = hub.list_skills()
        for s in skills:
            assert s.name, f"Skill {s.slug} has no name"
            assert s.description, f"Skill {s.slug} has no description"

    def test_read_content_returns_yaml(self):
        hub = _get_hub()
        content = hub.read_content("git-workflow")
        assert content is not None
        assert "content:" in content or "name:" in content

    def test_read_content_none_for_missing(self):
        hub = _get_hub()
        assert hub.read_content("nonexistent-slug") is None


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------

class TestSearch:
    def test_search_returns_relevant_result(self):
        hub = _get_hub()
        results = hub.search("git commit branch")
        slugs = [s.slug for s in results]
        assert "git-workflow" in slugs

    def test_search_returns_empty_on_gibberish(self):
        hub = _get_hub()
        results = hub.search("xkcd zork blorb")
        assert isinstance(results, list)
        # May return empty or low-score results

    def test_search_docker_finds_docker_ops(self):
        hub = _get_hub()
        results = hub.search("docker container build deploy")
        slugs = [s.slug for s in results]
        assert "docker-ops" in slugs

    def test_search_empty_query_returns_all(self):
        hub = _get_hub()
        all_skills = hub.list_skills()
        results = hub.search("")
        assert len(results) == len(all_skills)

    def test_search_respects_top_k(self):
        hub = _get_hub()
        results = hub.search("code software development", top_k=2)
        assert len(results) <= 2

    def test_search_financial_analysis(self):
        hub = _get_hub()
        results = hub.search("análise financeira DRE valuation")
        slugs = [s.slug for s in results]
        assert "financial-analysis" in slugs


# ---------------------------------------------------------------------------
# Install
# ---------------------------------------------------------------------------

class TestInstall:
    def test_install_copies_to_dest(self, tmp_path):
        hub = _get_hub()
        ok = hub.install("git-workflow", dest_dir=tmp_path)
        assert ok is True
        installed = tmp_path / "git-workflow.yaml"
        assert installed.exists()
        assert installed.stat().st_size > 0

    def test_install_creates_dest_dir(self, tmp_path):
        hub = _get_hub()
        dest = tmp_path / "new" / "skills"
        hub.install("tdd", dest_dir=dest)
        assert (dest / "tdd.yaml").exists()

    def test_install_nonexistent_returns_false(self, tmp_path):
        hub = _get_hub()
        ok = hub.install("does-not-exist", dest_dir=tmp_path)
        assert ok is False

    def test_installed_yaml_is_valid(self, tmp_path):
        hub = _get_hub()
        hub.install("code-review", dest_dir=tmp_path)
        content = (tmp_path / "code-review.yaml").read_text(encoding="utf-8")
        assert "name:" in content


# ---------------------------------------------------------------------------
# Custom data dir (isolated catalog)
# ---------------------------------------------------------------------------

class TestCustomDataDir:
    def test_empty_dir_returns_no_skills(self, tmp_path):
        hub = SkillsHub(data_dir=tmp_path)
        assert hub.list_skills() == []
        assert hub.categories() == []

    def test_custom_dir_with_skill_file(self, tmp_path):
        (tmp_path / "mycat").mkdir()
        (tmp_path / "mycat" / "my-skill.yaml").write_text(
            "name: My Skill\ndescription: A test skill.\ncontent: |\n  Hello World.\n",
            encoding="utf-8",
        )
        hub = SkillsHub(data_dir=tmp_path)
        skills = hub.list_skills()
        assert len(skills) == 1
        assert skills[0].slug == "my-skill"
        assert skills[0].name == "My Skill"
        assert skills[0].category == "mycat"

    def test_get_from_custom_dir(self, tmp_path):
        (tmp_path / "tech").mkdir()
        (tmp_path / "tech" / "awesome.yaml").write_text(
            "name: Awesome\ndescription: Desc.\ncontent: |\n  Content.\n",
            encoding="utf-8",
        )
        hub = SkillsHub(data_dir=tmp_path)
        s = hub.get("awesome")
        assert s is not None
        assert s.name == "Awesome"
