"""G16b: testes do catálogo de skills built-in (SkillsHub + data/skills)."""
from __future__ import annotations

from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

from bauer.skills_hub import SkillsHub, get_default_hub

_SKILLS_DIR = Path(__file__).resolve().parent.parent / "bauer" / "data" / "skills"

# Slugs adicionados no G16b — devem existir no catálogo.
NEW_SLUGS = [
    "write-blog-post", "proofread", "translate", "summarize-doc",
    "explain-code", "generate-tests", "code-review-security", "optimize-performance",
    "eda-report", "feature-engineering", "model-evaluation",
    "incident-report", "postmortem", "runbook-generator",
    "literature-review", "fact-check", "compare-options",
    "weekly-summary", "action-items", "meeting-notes",
]


@pytest.fixture(scope="module")
def hub():
    return get_default_hub()


# ---------------------------------------------------------------------------
# Integridade dos arquivos YAML
# ---------------------------------------------------------------------------

def _all_yaml_files():
    return sorted(_SKILLS_DIR.glob("**/*.yaml"))


def test_skills_dir_exists():
    assert _SKILLS_DIR.is_dir()


def test_every_yaml_is_parseable():
    for p in _all_yaml_files():
        data = yaml.safe_load(p.read_text(encoding="utf-8"))
        assert isinstance(data, dict), f"{p} nao parseia como dict"


def test_every_skill_has_required_fields():
    for p in _all_yaml_files():
        data = yaml.safe_load(p.read_text(encoding="utf-8"))
        for field in ("name", "description", "content"):
            assert data.get(field), f"{p.relative_to(_SKILLS_DIR)} sem campo '{field}'"


def test_descriptions_are_meaningful():
    for p in _all_yaml_files():
        data = yaml.safe_load(p.read_text(encoding="utf-8"))
        assert len(data["description"]) >= 15, f"{p.name}: description curta demais"


# ---------------------------------------------------------------------------
# SkillsHub
# ---------------------------------------------------------------------------

def test_hub_lists_at_least_32_skills(hub):
    assert len(hub.list_skills()) >= 32


def test_no_duplicate_slugs(hub):
    slugs = [s.slug for s in hub.list_skills()]
    dupes = [s for s in set(slugs) if slugs.count(s) > 1]
    assert not dupes, f"slugs duplicados: {dupes}"


@pytest.mark.parametrize("slug", NEW_SLUGS)
def test_new_skill_present(hub, slug):
    assert hub.get(slug) is not None, f"skill '{slug}' nao encontrada no hub"


def test_new_categories_exist(hub):
    cats = hub.categories()
    assert "writing" in cats
    assert "sre" in cats


@pytest.mark.parametrize("category", ["writing", "sre", "coding", "data-science", "research", "productivity"])
def test_category_has_skills(hub, category):
    assert hub.list_skills(category=category), f"categoria '{category}' vazia"


def test_search_returns_results(hub):
    results = hub.search("postmortem incidente")
    assert results
    assert any(s.slug in ("postmortem", "incident-report", "runbook-generator") for s in results)


def test_search_empty_query_returns_all(hub):
    assert len(hub.search("")) == len(hub.list_skills())


def test_get_unknown_slug_returns_none(hub):
    assert hub.get("slug-que-nao-existe-xyz") is None


def test_read_content_returns_yaml(hub):
    content = hub.read_content("postmortem")
    assert content and "name:" in content


def test_install_copies_skill(hub, tmp_path):
    ok = hub.install("eda-report", dest_dir=tmp_path)
    assert ok is True
    assert (tmp_path / "eda-report.yaml").is_file()


def test_entry_category_matches_dir(hub):
    for s in hub.list_skills():
        assert s.category == s.path.parent.name
