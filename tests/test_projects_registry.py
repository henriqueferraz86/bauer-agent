"""Tests for projects_registry — registro de múltiplos workspaces (Desktop A1)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from bauer import projects_registry as pr


@pytest.fixture
def reg_path(tmp_path: Path) -> Path:
    return tmp_path / "projects.json"


@pytest.fixture
def proj_dir(tmp_path: Path) -> Path:
    d = tmp_path / "MyProject"
    d.mkdir()
    (d / "config.yaml").write_text("model:\n  provider: opencode\n  name: deepseek\n")
    return d


# ---------------------------------------------------------------------------
# project_id
# ---------------------------------------------------------------------------

class TestProjectId:
    def test_stable_for_same_path(self, proj_dir):
        assert pr.project_id(proj_dir) == pr.project_id(proj_dir)

    def test_differs_for_different_paths(self, tmp_path):
        a = tmp_path / "a"; a.mkdir()
        b = tmp_path / "b"; b.mkdir()
        assert pr.project_id(a) != pr.project_id(b)

    def test_length_12(self, proj_dir):
        assert len(pr.project_id(proj_dir)) == 12


# ---------------------------------------------------------------------------
# load/save
# ---------------------------------------------------------------------------

class TestLoadRegistry:
    def test_missing_returns_empty(self, reg_path):
        reg = pr.load_registry(reg_path)
        assert reg == {"active": None, "projects": []}

    def test_corrupted_returns_empty(self, reg_path):
        reg_path.write_text("{not valid json")
        reg = pr.load_registry(reg_path)
        assert reg["projects"] == []

    def test_roundtrip(self, reg_path):
        pr.save_registry({"active": "x", "projects": [{"id": "x"}]}, reg_path)
        reg = pr.load_registry(reg_path)
        assert reg["active"] == "x"
        assert reg["projects"][0]["id"] == "x"

    def test_non_dict_returns_empty(self, reg_path):
        reg_path.write_text("[1, 2, 3]")
        assert pr.load_registry(reg_path)["projects"] == []


# ---------------------------------------------------------------------------
# add_project
# ---------------------------------------------------------------------------

class TestAddProject:
    def test_adds_and_persists(self, reg_path, proj_dir):
        entry = pr.add_project(proj_dir, registry_path=reg_path)
        assert entry["name"] == "MyProject"
        assert entry["id"] == pr.project_id(proj_dir)
        reg = pr.load_registry(reg_path)
        assert len(reg["projects"]) == 1

    def test_first_becomes_active(self, reg_path, proj_dir):
        pr.add_project(proj_dir, registry_path=reg_path)
        assert pr.get_active(reg_path) == pr.project_id(proj_dir)

    def test_idempotent_same_path(self, reg_path, proj_dir):
        pr.add_project(proj_dir, registry_path=reg_path)
        pr.add_project(proj_dir, name="Renamed", registry_path=reg_path)
        reg = pr.load_registry(reg_path)
        assert len(reg["projects"]) == 1
        assert reg["projects"][0]["name"] == "Renamed"

    def test_custom_name(self, reg_path, proj_dir):
        entry = pr.add_project(proj_dir, name="Custom", registry_path=reg_path)
        assert entry["name"] == "Custom"

    def test_missing_dir_raises(self, reg_path, tmp_path):
        with pytest.raises(NotADirectoryError):
            pr.add_project(tmp_path / "nope", registry_path=reg_path)

    def test_require_config_raises_without(self, reg_path, tmp_path):
        d = tmp_path / "NoConfig"; d.mkdir()
        with pytest.raises(FileNotFoundError):
            pr.add_project(d, registry_path=reg_path, require_config=True)

    def test_second_does_not_steal_active(self, reg_path, proj_dir, tmp_path):
        pr.add_project(proj_dir, registry_path=reg_path)
        d2 = tmp_path / "P2"; d2.mkdir()
        pr.add_project(d2, registry_path=reg_path)
        assert pr.get_active(reg_path) == pr.project_id(proj_dir)


# ---------------------------------------------------------------------------
# remove / active
# ---------------------------------------------------------------------------

class TestRemoveAndActive:
    def test_remove_returns_true(self, reg_path, proj_dir):
        pid = pr.add_project(proj_dir, registry_path=reg_path)["id"]
        assert pr.remove_project(pid, registry_path=reg_path) is True
        assert pr.get_project(pid, registry_path=reg_path) is None

    def test_remove_unknown_false(self, reg_path):
        assert pr.remove_project("zzz", registry_path=reg_path) is False

    def test_remove_active_reassigns(self, reg_path, proj_dir, tmp_path):
        pid1 = pr.add_project(proj_dir, registry_path=reg_path)["id"]
        d2 = tmp_path / "P2"; d2.mkdir()
        pid2 = pr.add_project(d2, registry_path=reg_path)["id"]
        pr.remove_project(pid1, registry_path=reg_path)
        assert pr.get_active(reg_path) == pid2

    def test_remove_last_active_none(self, reg_path, proj_dir):
        pid = pr.add_project(proj_dir, registry_path=reg_path)["id"]
        pr.remove_project(pid, registry_path=reg_path)
        assert pr.get_active(reg_path) is None

    def test_set_active_unknown_false(self, reg_path, proj_dir):
        pr.add_project(proj_dir, registry_path=reg_path)
        assert pr.set_active("zzz", registry_path=reg_path) is False

    def test_set_active_switches(self, reg_path, proj_dir, tmp_path):
        pr.add_project(proj_dir, registry_path=reg_path)
        d2 = tmp_path / "P2"; d2.mkdir()
        pid2 = pr.add_project(d2, registry_path=reg_path)["id"]
        assert pr.set_active(pid2, registry_path=reg_path) is True
        assert pr.get_active(reg_path) == pid2


# ---------------------------------------------------------------------------
# list_projects
# ---------------------------------------------------------------------------

class TestListProjects:
    def test_empty(self, reg_path):
        assert pr.list_projects(registry_path=reg_path) == []

    def test_marks_active(self, reg_path, proj_dir):
        pr.add_project(proj_dir, registry_path=reg_path)
        items = pr.list_projects(registry_path=reg_path)
        assert items[0]["active"] is True

    def test_no_enrich_skips_config(self, reg_path, proj_dir):
        pr.add_project(proj_dir, registry_path=reg_path)
        items = pr.list_projects(registry_path=reg_path, enrich=False)
        assert "model" not in items[0]

    def test_enrich_reads_model(self, reg_path, proj_dir):
        pr.add_project(proj_dir, registry_path=reg_path)
        items = pr.list_projects(registry_path=reg_path, enrich=True)
        # config.yaml tem provider opencode / name deepseek
        assert items[0]["provider"] == "opencode"
        assert items[0]["model"] == "deepseek"

    def test_enrich_defensive_on_bad_config(self, reg_path, tmp_path):
        d = tmp_path / "Bad"; d.mkdir()
        (d / "config.yaml").write_text("::: not yaml :::")
        pr.add_project(d, registry_path=reg_path)
        items = pr.list_projects(registry_path=reg_path, enrich=True)
        assert items[0]["model"] is None  # falha de parse não quebra


# ---------------------------------------------------------------------------
# project_stats
# ---------------------------------------------------------------------------

class TestProjectStats:
    def test_unknown_returns_zeros(self, reg_path):
        s = pr.project_stats("zzz", registry_path=reg_path)
        assert s == {"sessions": 0, "cost_usd": 0.0, "total_tokens": 0}

    def test_known_returns_shape(self, reg_path, proj_dir):
        pid = pr.add_project(proj_dir, registry_path=reg_path)["id"]
        s = pr.project_stats(pid, registry_path=reg_path)
        assert set(s) == {"sessions", "cost_usd", "total_tokens"}


# ---------------------------------------------------------------------------
# find_project_for_cwd (detecção do projeto pela pasta atual)
# ---------------------------------------------------------------------------

class TestFindProjectForCwd:
    def test_none_when_empty_registry(self, reg_path, proj_dir):
        assert pr.find_project_for_cwd(proj_dir, registry_path=reg_path) is None

    def test_matches_exact_dir(self, reg_path, proj_dir):
        pid = pr.add_project(proj_dir, registry_path=reg_path)["id"]
        assert pr.find_project_for_cwd(proj_dir, registry_path=reg_path) == pid

    def test_matches_from_subdir(self, reg_path, proj_dir):
        """Rodar de uma subpasta do projeto ainda resolve o projeto (sobe a árvore)."""
        pid = pr.add_project(proj_dir, registry_path=reg_path)["id"]
        sub = proj_dir / "src" / "components"
        sub.mkdir(parents=True)
        assert pr.find_project_for_cwd(sub, registry_path=reg_path) == pid

    def test_none_for_unrelated_dir(self, reg_path, proj_dir, tmp_path):
        pr.add_project(proj_dir, registry_path=reg_path)
        other = tmp_path / "outra"
        other.mkdir()
        assert pr.find_project_for_cwd(other, registry_path=reg_path) is None


# ---------------------------------------------------------------------------
# is_sensitive_dir (guard da adoção automática)
# ---------------------------------------------------------------------------

class TestIsSensitiveDir:
    def test_project_dir_is_not_sensitive(self, proj_dir):
        assert pr.is_sensitive_dir(proj_dir) is False

    def test_home_is_sensitive(self):
        assert pr.is_sensitive_dir(Path.home()) is True

    def test_drive_or_fs_root_is_sensitive(self):
        root = Path(Path.cwd().anchor or "/")
        assert pr.is_sensitive_dir(root) is True

    def test_bauer_home_is_sensitive(self, monkeypatch, tmp_path):
        home = tmp_path / ".bauer"
        home.mkdir()
        monkeypatch.setenv("BAUER_HOME", str(home))
        assert pr.is_sensitive_dir(home) is True
