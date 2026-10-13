"""K06: o gate de escopo deve julgar arquivos reais, com nomes preservados."""

import os
import subprocess
from pathlib import Path

import pytest

from bauer.core.kernel.gates.scope import ScopeGate, arquivos_alterados
from bauer.core.task import TaskContract


UNIX_FILENAME = pytest.mark.skipif(os.name == "nt", reason="caracteres inválidos em nomes Windows")


def _git(repo: Path, *args: str):
    return subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, check=True)


@pytest.fixture
def repo(tmp_path: Path):
    path = tmp_path / "repo"
    path.mkdir()
    _git(path, "init", "-q")
    return path


def test_untracked_directory_checks_each_file(repo):
    (repo / "src").mkdir()
    (repo / "src" / "allowed.py").write_text("ok = True\n")
    (repo / "src" / "private.py").write_text("out_of_task = True\n")
    contract = TaskContract.from_mapping({
        "scope": {"allowed": ["src/"], "forbidden": ["src/private.py"]},
    })

    assert set(arquivos_alterados(repo)) == {"src/allowed.py", "src/private.py"}
    result = ScopeGate(repo, contract).check(request=None, result={})
    assert not result.passed
    assert "src/private.py" in result.reason


@pytest.mark.parametrize("name", [
    "space name.py", "ação.py",
    pytest.param('quote"name.py', marks=UNIX_FILENAME),
    pytest.param("a -> b.py", marks=UNIX_FILENAME),
    pytest.param(" leading trailing .py ", marks=UNIX_FILENAME),
    pytest.param("carriage\rreturn.py", marks=UNIX_FILENAME),
])
def test_untracked_paths_keep_exact_characters(repo, name):
    (repo / name).write_text("ok = True\n")
    assert arquivos_alterados(repo) == [name]
    contract = TaskContract.from_mapping({"scope": {"allowed": [name]}})
    assert ScopeGate(repo, contract).check(request=None, result={}).passed


@pytest.mark.parametrize("destination", [
    "new name.py", "ação.py",
    pytest.param('new"name.py', marks=UNIX_FILENAME),
    pytest.param("ação -> final.py", marks=UNIX_FILENAME),
])
def test_rename_uses_destination_without_parsing_filename(repo, destination):
    (repo / "old.py").write_text("ok = True\n")
    _git(repo, "add", "old.py")
    _git(repo, "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-qm", "fixture")
    _git(repo, "mv", "old.py", destination)
    assert arquivos_alterados(repo) == [destination]


def test_runtime_noise_does_not_hide_other_untracked_files(repo):
    (repo / "memory" / "runtime").mkdir(parents=True)
    (repo / "memory" / "runtime" / "events.jsonl").write_text("{}\n")
    (repo / "src").mkdir()
    (repo / "src" / "new.py").write_text("ok = True\n")
    assert arquivos_alterados(repo) == ["src/new.py"]


@UNIX_FILENAME
def test_non_utf8_path_is_preserved(repo):
    name = os.fsdecode(b"invalid-\xff.py")
    (repo / name).write_text("ok = True\n")
    assert arquivos_alterados(repo) == [name]
    contract = TaskContract.from_mapping({"scope": {"allowed": [name]}})
    assert ScopeGate(repo, contract).check(request=None, result={}).passed


@UNIX_FILENAME
def test_whitespace_directory_prefix_matches_exactly(repo):
    (repo / " dir ").mkdir()
    (repo / " dir " / "file.py").write_text("ok = True\n")
    contract = TaskContract.from_mapping({"scope": {"allowed": [" dir /"]}})
    assert arquivos_alterados(repo) == [" dir /file.py"]
    assert ScopeGate(repo, contract).check(request=None, result={}).passed
