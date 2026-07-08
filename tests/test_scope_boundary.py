"""Tests for bauer/scope_boundary.py — autonomous operation scope enforcement."""

from __future__ import annotations

from pathlib import Path

import pytest

from bauer.scope_boundary import ScopeBoundary, ScopeViolation, _is_under, _resolve


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _scope(
    write=None, denied=None, read=None,
    url_prefixes=None, denied_url_patterns=None, allow_all_urls=True,
    commands=None, max_depth=3,
) -> ScopeBoundary:
    return ScopeBoundary(
        allowed_write_paths=[Path(p) for p in (write or [])],
        denied_paths=[Path(p) for p in (denied or [])],
        allowed_read_paths=[Path(p) for p in (read or [])],
        allowed_url_prefixes=url_prefixes or [],
        denied_url_patterns=denied_url_patterns or [],
        allow_all_urls=allow_all_urls,
        allowed_commands=commands or [],
        max_task_depth=max_depth,
    )


# ---------------------------------------------------------------------------
# _is_under helper
# ---------------------------------------------------------------------------


def test_is_under_exact():
    assert _is_under(Path("/a/b"), Path("/a/b"))


def test_is_under_child():
    assert _is_under(Path("/a/b/c"), Path("/a/b"))


def test_is_under_not_sibling():
    assert not _is_under(Path("/a/c"), Path("/a/b"))


def test_is_under_root_prefix_not_substring():
    # /a/bb should NOT be considered under /a/b
    assert not _is_under(Path("/a/bb"), Path("/a/b"))


# ---------------------------------------------------------------------------
# ScopeViolation str representation
# ---------------------------------------------------------------------------


def test_violation_str_without_hint():
    v = ScopeViolation(kind="write", resource="/etc/passwd", reason="denied root")
    assert "SCOPE:WRITE" in str(v)
    assert "/etc/passwd" in str(v)


def test_violation_str_with_hint():
    v = ScopeViolation(kind="url", resource="http://x.com", reason="blocked", suggested_action="add prefix")
    assert "add prefix" in str(v)


# ---------------------------------------------------------------------------
# check_write — allowed write paths
# ---------------------------------------------------------------------------


def test_write_allowed_under_root(tmp_path):
    scope = _scope(write=[tmp_path])
    assert scope.check_write(tmp_path / "subdir" / "file.py") is None


def test_write_allowed_exact_root(tmp_path):
    scope = _scope(write=[tmp_path])
    assert scope.check_write(tmp_path) is None


def test_write_denied_outside_root(tmp_path):
    scope = _scope(write=[tmp_path / "project"])
    v = scope.check_write(tmp_path / "other" / "file.py")
    assert v is not None
    assert v.kind == "write"


def test_write_no_restriction_allows_everything(tmp_path):
    scope = _scope()  # no allowed_write_paths
    assert scope.check_write(Path("/etc/passwd")) is None


# ---------------------------------------------------------------------------
# check_write — denied paths take priority
# ---------------------------------------------------------------------------


def test_denied_path_blocks_even_if_under_write_root(tmp_path):
    # denied_paths overrides allowed_write_paths
    scope = _scope(write=[tmp_path], denied=[tmp_path / ".ssh"])
    v = scope.check_write(tmp_path / ".ssh" / "id_rsa")
    assert v is not None
    assert v.kind == "write"
    assert ".ssh" in v.reason


def test_denied_path_deep_child(tmp_path):
    scope = _scope(write=[tmp_path], denied=[tmp_path / "secrets"])
    v = scope.check_write(tmp_path / "secrets" / "very" / "deep" / "key.pem")
    assert v is not None


def test_write_outside_denied_still_allowed(tmp_path):
    scope = _scope(write=[tmp_path], denied=[tmp_path / ".ssh"])
    assert scope.check_write(tmp_path / "src" / "main.py") is None


# ---------------------------------------------------------------------------
# check_read
# ---------------------------------------------------------------------------


def test_read_denied_path_blocked(tmp_path):
    scope = _scope(denied=[tmp_path / ".aws"])
    v = scope.check_read(tmp_path / ".aws" / "credentials")
    assert v is not None
    assert v.kind == "read"


def test_read_not_in_denied_allowed(tmp_path):
    scope = _scope(denied=[tmp_path / ".ssh"])
    assert scope.check_read(tmp_path / "README.md") is None


# ---------------------------------------------------------------------------
# check_url
# ---------------------------------------------------------------------------


def test_url_allow_all_urls_passes_any():
    scope = _scope(allow_all_urls=True)
    assert scope.check_url("https://whatever.example.com/api") is None


def test_url_allowed_prefix_matches():
    scope = _scope(url_prefixes=["https://api.github.com/"], allow_all_urls=False)
    assert scope.check_url("https://api.github.com/repos/user/repo") is None


def test_url_no_matching_prefix_blocked():
    scope = _scope(url_prefixes=["https://api.github.com/"], allow_all_urls=False)
    v = scope.check_url("https://evil.com/payload")
    assert v is not None
    assert v.kind == "url"


def test_url_denied_pattern_overrides_allow_all():
    scope = _scope(
        allow_all_urls=True,
        denied_url_patterns=[r"169\.254\.169\.254"],
    )
    v = scope.check_url("http://169.254.169.254/latest/meta-data/")
    assert v is not None
    assert v.kind == "url"


def test_url_denied_pattern_case_insensitive():
    scope = _scope(denied_url_patterns=[r"metadata\.internal"], allow_all_urls=True)
    v = scope.check_url("http://METADATA.INTERNAL/token")
    assert v is not None


def test_url_multiple_prefixes_one_matches():
    scope = _scope(
        url_prefixes=["https://pypi.org/", "https://api.github.com/"],
        allow_all_urls=False,
    )
    assert scope.check_url("https://pypi.org/simple/requests/") is None


def test_url_denied_before_allowed_prefix():
    scope = _scope(
        url_prefixes=["https://api.github.com/"],
        denied_url_patterns=[r"github"],
        allow_all_urls=False,
    )
    v = scope.check_url("https://api.github.com/repos")
    assert v is not None  # denied pattern takes priority


# ---------------------------------------------------------------------------
# check_command
# ---------------------------------------------------------------------------


def test_command_no_restriction_allows_any():
    scope = _scope(commands=[])
    assert scope.check_command("curl -s https://example.com") is None


def test_command_allowed_stem_passes():
    scope = _scope(commands=["git", "python", "pytest"])
    assert scope.check_command("git status") is None
    assert scope.check_command("python --version") is None
    assert scope.check_command("pytest tests/ -v") is None


def test_command_disallowed_stem_blocked():
    scope = _scope(commands=["git", "python"])
    v = scope.check_command("curl https://example.com")
    assert v is not None
    assert v.kind == "command"
    assert "curl" in v.reason


def test_command_exe_extension_stripped():
    scope = _scope(commands=["python"])
    # python.exe should match python stem
    assert scope.check_command("python.exe --version") is None


def test_command_case_insensitive():
    scope = _scope(commands=["git"])
    assert scope.check_command("Git status") is None


def test_command_absolute_path_uses_stem():
    scope = _scope(commands=["git"])
    assert scope.check_command("/usr/bin/git status") is None


def test_command_empty_string_passes():
    scope = _scope(commands=["git"])
    assert scope.check_command("") is None


# ---------------------------------------------------------------------------
# check_task_depth
# ---------------------------------------------------------------------------


def test_depth_at_limit_passes():
    scope = _scope(max_depth=3)
    assert scope.check_task_depth(3) is None


def test_depth_exceeds_limit_blocked():
    scope = _scope(max_depth=3)
    v = scope.check_task_depth(4)
    assert v is not None
    assert v.kind == "depth"
    assert "4" in v.reason


def test_depth_zero_passes():
    scope = _scope(max_depth=3)
    assert scope.check_task_depth(0) is None


# ---------------------------------------------------------------------------
# Convenience methods
# ---------------------------------------------------------------------------


def test_is_write_allowed(tmp_path):
    scope = _scope(write=[tmp_path])
    assert scope.is_write_allowed(tmp_path / "file.py") is True
    assert scope.is_write_allowed(Path("/etc/passwd")) is False


def test_is_url_allowed():
    scope = _scope(url_prefixes=["https://api.github.com/"], allow_all_urls=False)
    assert scope.is_url_allowed("https://api.github.com/users") is True
    assert scope.is_url_allowed("https://evil.com/") is False


def test_is_command_allowed():
    scope = _scope(commands=["git"])
    assert scope.is_command_allowed("git status") is True
    assert scope.is_command_allowed("bash -c ls") is False


# ---------------------------------------------------------------------------
# Factory: ScopeBoundary.default
# ---------------------------------------------------------------------------


def test_default_allows_write_inside_project(tmp_path):
    scope = ScopeBoundary.default(tmp_path)
    assert scope.check_write(tmp_path / "src" / "main.py") is None


def test_default_blocks_write_to_etc(tmp_path):
    scope = ScopeBoundary.default(tmp_path)
    v = scope.check_write(Path("/etc/passwd"))
    assert v is not None


def test_default_blocks_write_to_ssh(tmp_path):
    scope = ScopeBoundary.default(tmp_path)
    ssh = Path.home() / ".ssh" / "id_rsa"
    v = scope.check_write(ssh)
    assert v is not None


def test_default_allows_all_urls(tmp_path):
    scope = ScopeBoundary.default(tmp_path)
    assert scope.allow_all_urls is True


def test_default_allows_git_command(tmp_path):
    scope = ScopeBoundary.default(tmp_path)
    assert scope.is_command_allowed("git status")


# ---------------------------------------------------------------------------
# Factory: ScopeBoundary.open
# ---------------------------------------------------------------------------


def test_open_no_restrictions():
    scope = ScopeBoundary.open()
    assert scope.check_write(Path("/etc/passwd")) is None
    assert scope.check_url("http://169.254.169.254/") is None
    assert scope.check_command("bash -c whoami") is None


# ---------------------------------------------------------------------------
# Factory: from_config
# ---------------------------------------------------------------------------


def test_from_config_parses_dict(tmp_path):
    config = {
        "scope": {
            "allowed_write_paths": [str(tmp_path)],
            "denied_paths": [str(tmp_path / ".ssh")],
            "allowed_url_prefixes": ["https://pypi.org/"],
            "allow_all_urls": False,
            "allowed_commands": ["git", "python"],
            "max_task_depth": 2,
        }
    }
    scope = ScopeBoundary.from_config(config)
    assert scope.is_write_allowed(tmp_path / "file.py")
    assert not scope.is_write_allowed(tmp_path / ".ssh" / "key")
    assert not scope.is_url_allowed("https://evil.com/")
    assert scope.is_url_allowed("https://pypi.org/simple/")
    assert scope.max_task_depth == 2


def test_from_config_without_scope_key(tmp_path):
    config = {
        "allowed_write_paths": [str(tmp_path)],
        "allow_all_urls": True,
    }
    scope = ScopeBoundary.from_config(config)
    assert scope.allow_all_urls is True


# ---------------------------------------------------------------------------
# summary()
# ---------------------------------------------------------------------------


def test_summary_returns_string(tmp_path):
    scope = ScopeBoundary.default(tmp_path)
    s = scope.summary()
    assert isinstance(s, str)
    assert "ScopeBoundary" in s
