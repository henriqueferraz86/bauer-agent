"""Testes de bauer/config_profiles.py — perfis, diff, validate, migrate."""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _base_config(tmp_path: Path, content: str = "agent:\n  workspace: /tmp\n") -> Path:
    p = tmp_path / "config.yaml"
    p.write_text(content)
    return p


# ---------------------------------------------------------------------------
# TestProfilePath
# ---------------------------------------------------------------------------

class TestProfilePath:
    def test_profile_path_format(self, tmp_path):
        from bauer.config_profiles import profile_path
        cfg = tmp_path / "config.yaml"
        p = profile_path("dev", cfg)
        assert p == tmp_path / "config.dev.yaml"

    def test_profile_path_default_dir(self, tmp_path):
        from bauer.config_profiles import profile_path
        with patch("bauer.config_profiles._config_dir", return_value=tmp_path):
            p = profile_path("prod")
        assert p.name == "config.prod.yaml"


# ---------------------------------------------------------------------------
# TestListProfiles
# ---------------------------------------------------------------------------

class TestListProfiles:
    def test_empty_dir_returns_empty(self, tmp_path):
        from bauer.config_profiles import list_profiles
        cfg = tmp_path / "config.yaml"
        result = list_profiles(cfg)
        assert result == []

    def test_finds_profiles(self, tmp_path):
        from bauer.config_profiles import list_profiles
        cfg = tmp_path / "config.yaml"
        cfg.write_text("")
        (tmp_path / "config.dev.yaml").write_text("")
        (tmp_path / "config.prod.yaml").write_text("")
        result = list_profiles(cfg)
        assert sorted(result) == ["dev", "prod"]

    def test_ignores_main_config(self, tmp_path):
        from bauer.config_profiles import list_profiles
        cfg = tmp_path / "config.yaml"
        cfg.write_text("")
        result = list_profiles(cfg)
        assert result == []

    def test_sorted_alphabetically(self, tmp_path):
        from bauer.config_profiles import list_profiles
        cfg = tmp_path / "config.yaml"
        for name in ["prod", "dev", "staging"]:
            (tmp_path / f"config.{name}.yaml").write_text("")
        result = list_profiles(cfg)
        assert result == sorted(result)


# ---------------------------------------------------------------------------
# TestCreateProfile
# ---------------------------------------------------------------------------

class TestCreateProfile:
    def test_creates_file(self, tmp_path):
        from bauer.config_profiles import create_profile
        cfg = _base_config(tmp_path)
        p = create_profile("dev", cfg)
        assert p.exists()

    def test_copies_source_content(self, tmp_path):
        from bauer.config_profiles import create_profile
        cfg = _base_config(tmp_path, content="key: value\n")
        p = create_profile("dev", cfg)
        assert "key: value" in p.read_text()

    def test_raises_on_existing_without_force(self, tmp_path):
        from bauer.config_profiles import create_profile
        cfg = _base_config(tmp_path)
        create_profile("dev", cfg)
        with pytest.raises(FileExistsError):
            create_profile("dev", cfg, overwrite=False)

    def test_overwrites_with_force(self, tmp_path):
        from bauer.config_profiles import create_profile
        cfg = _base_config(tmp_path, "key: v1\n")
        create_profile("dev", cfg)
        cfg.write_text("key: v2\n")
        create_profile("dev", cfg, overwrite=True)
        from bauer.config_profiles import profile_path
        p = profile_path("dev", cfg)
        assert "v2" in p.read_text()

    def test_creates_empty_when_no_source(self, tmp_path):
        from bauer.config_profiles import create_profile
        cfg = tmp_path / "config.yaml"  # não existe
        p = create_profile("test", cfg)
        assert p.exists()


# ---------------------------------------------------------------------------
# TestDeleteProfile
# ---------------------------------------------------------------------------

class TestDeleteProfile:
    def test_delete_existing(self, tmp_path):
        from bauer.config_profiles import create_profile, delete_profile
        cfg = _base_config(tmp_path)
        create_profile("dev", cfg)
        assert delete_profile("dev", cfg) is True
        from bauer.config_profiles import profile_path
        assert not profile_path("dev", cfg).exists()

    def test_delete_nonexistent_returns_false(self, tmp_path):
        from bauer.config_profiles import delete_profile
        cfg = tmp_path / "config.yaml"
        assert delete_profile("nonexistent", cfg) is False


# ---------------------------------------------------------------------------
# TestActiveProfile
# ---------------------------------------------------------------------------

class TestActiveProfile:
    def _fresh_dir(self, tmp_path):
        d = tmp_path / "bauer_state"
        d.mkdir()
        return d

    def test_get_active_none_when_not_set(self, tmp_path):
        from bauer.config_profiles import get_active_profile
        af = tmp_path / "active_profile"
        with patch("bauer.config_profiles._ACTIVE_PROFILE_FILE", af):
            assert get_active_profile() is None

    def test_set_and_get_active(self, tmp_path):
        from bauer.config_profiles import get_active_profile, set_active_profile
        af = tmp_path / "active_profile"
        with patch("bauer.config_profiles._ACTIVE_PROFILE_FILE", af):
            set_active_profile("dev")
            assert get_active_profile() == "dev"

    def test_set_none_clears(self, tmp_path):
        from bauer.config_profiles import get_active_profile, set_active_profile
        af = tmp_path / "active_profile"
        with patch("bauer.config_profiles._ACTIVE_PROFILE_FILE", af):
            set_active_profile("dev")
            set_active_profile(None)
            assert get_active_profile() is None

    def test_effective_config_uses_active(self, tmp_path):
        from bauer.config_profiles import create_profile, effective_config_path, set_active_profile
        cfg = _base_config(tmp_path)
        create_profile("dev", cfg)
        af = tmp_path / "active_profile"
        with patch("bauer.config_profiles._ACTIVE_PROFILE_FILE", af):
            set_active_profile("dev")
            effective = effective_config_path(cfg)
        from bauer.config_profiles import profile_path
        assert effective == profile_path("dev", cfg)

    def test_effective_config_fallback_to_default(self, tmp_path):
        from bauer.config_profiles import effective_config_path, set_active_profile
        cfg = _base_config(tmp_path)
        af = tmp_path / "active_profile"
        with patch("bauer.config_profiles._ACTIVE_PROFILE_FILE", af):
            set_active_profile(None)
            effective = effective_config_path(cfg)
        assert effective == cfg


# ---------------------------------------------------------------------------
# TestConfigDiff
# ---------------------------------------------------------------------------

class TestConfigDiff:
    def test_identical_files_empty_diff(self, tmp_path):
        from bauer.config_profiles import config_diff
        a = tmp_path / "a.yaml"
        b = tmp_path / "b.yaml"
        content = "key: value\n"
        a.write_text(content)
        b.write_text(content)
        diff = config_diff(a, b)
        assert diff == []

    def test_diff_shows_added_lines(self, tmp_path):
        from bauer.config_profiles import config_diff
        a = tmp_path / "a.yaml"
        b = tmp_path / "b.yaml"
        a.write_text("key: v1\n")
        b.write_text("key: v1\nnew_key: v2\n")
        diff = config_diff(a, b)
        assert any("+new_key" in line for line in diff)

    def test_diff_shows_removed_lines(self, tmp_path):
        from bauer.config_profiles import config_diff
        a = tmp_path / "a.yaml"
        b = tmp_path / "b.yaml"
        a.write_text("key: v1\nold_key: v2\n")
        b.write_text("key: v1\n")
        diff = config_diff(a, b)
        assert any("-old_key" in line for line in diff)

    def test_missing_file_treated_as_empty(self, tmp_path):
        from bauer.config_profiles import config_diff
        a = tmp_path / "nonexistent.yaml"
        b = tmp_path / "b.yaml"
        b.write_text("key: v\n")
        diff = config_diff(a, b)
        assert any("+key" in line for line in diff)

    def test_diff_has_filenames(self, tmp_path):
        from bauer.config_profiles import config_diff
        a = tmp_path / "a.yaml"
        b = tmp_path / "b.yaml"
        a.write_text("k: 1\n")
        b.write_text("k: 2\n")
        diff = config_diff(a, b)
        assert any("a.yaml" in line for line in diff)


# ---------------------------------------------------------------------------
# TestValidateConfig
# ---------------------------------------------------------------------------

class TestValidateConfig:
    def test_missing_file_returns_error(self, tmp_path):
        from bauer.config_profiles import validate_config
        errors = validate_config(tmp_path / "nonexistent.yaml")
        assert len(errors) == 1
        assert "não encontrado" in errors[0]

    def test_empty_file_returns_error(self, tmp_path):
        from bauer.config_profiles import validate_config
        cfg = tmp_path / "config.yaml"
        cfg.write_text("")
        errors = validate_config(cfg)
        assert len(errors) > 0

    def test_invalid_yaml_returns_error(self, tmp_path):
        from bauer.config_profiles import validate_config
        cfg = tmp_path / "config.yaml"
        cfg.write_text("{invalid yaml :")
        errors = validate_config(cfg)
        assert len(errors) > 0
        assert any("YAML" in e or "yaml" in e.lower() for e in errors)

    def test_non_dict_root_returns_error(self, tmp_path):
        from bauer.config_profiles import validate_config
        cfg = tmp_path / "config.yaml"
        cfg.write_text("- item1\n- item2\n")
        errors = validate_config(cfg)
        assert len(errors) > 0


# ---------------------------------------------------------------------------
# TestListMigrations
# ---------------------------------------------------------------------------

class TestListMigrations:
    def test_returns_list(self):
        from bauer.config_profiles import list_migrations
        result = list_migrations()
        assert isinstance(result, list)

    def test_each_has_key_and_description(self):
        from bauer.config_profiles import list_migrations
        for m in list_migrations():
            assert "key" in m
            assert "description" in m


# ---------------------------------------------------------------------------
# TestRunMigration
# ---------------------------------------------------------------------------

class TestRunMigration:
    def test_unknown_migration_returns_error(self, tmp_path):
        from bauer.config_profiles import run_migration
        cfg = _base_config(tmp_path)
        result = run_migration("99.0→99.1", config_path=cfg, dry_run=True)
        assert any("não encontrada" in r or "not found" in r.lower() or "disponíveis" in r for r in result)

    def test_missing_file_returns_error(self, tmp_path):
        from bauer.config_profiles import run_migration
        result = run_migration("0.1→0.2", config_path=tmp_path / "x.yaml")
        assert any("não encontrado" in r or "not found" in r.lower() for r in result)

    def test_dry_run_does_not_modify_file(self, tmp_path):
        from bauer.config_profiles import run_migration
        cfg = _base_config(tmp_path, "agent:\n  workspace: /tmp\n")
        original = cfg.read_text()
        run_migration("0.1→0.2", config_path=cfg, dry_run=True)
        assert cfg.read_text() == original

    def test_no_changes_needed_message(self, tmp_path):
        from bauer.config_profiles import run_migration
        cfg = _base_config(tmp_path)
        result = run_migration("0.1→0.2", config_path=cfg, dry_run=True)
        assert isinstance(result, list)
        assert len(result) > 0
