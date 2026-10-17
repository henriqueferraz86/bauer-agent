"""Hermetic syntax and real subprocess tests, including Windows quoting."""

from pathlib import Path
import shlex
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from bauer.core.runtime.adapters.agno_adapter import AgnoRuntimeAdapter
from bauer.core.runtime.adapters.base import RuntimeAdapterError
from bauer.shell_runner import (
    BlockedCommandError,
    SafeModeError,
    ShellError,
    ShellRunner,
    validate_command_syntax,
)
from bauer.tools.base import ToolError
from bauer.tools.execution import ExecToolsMixin


@pytest.mark.parametrize("command", [
    "cd project && python app.py", "git status&&git diff",
    "git status || git diff", "git status;git diff", "git status | more",
    "python app.py &", "git status > result.txt", "git status 2>>result.txt",
    "python app.py < input.txt", "git status\ngit diff", "git status\r\ngit diff",
    "echo $(whoami)", "echo `whoami`", "(git status)",
    'echo "safe"&&git diff', 'echo "safe;value";git diff',
    'echo "escaped\\\"quote"|git diff',
])
def test_unquoted_shell_control_is_rejected_without_execution(command):
    with pytest.raises(ShellError, match="shell=False"):
        validate_command_syntax(command)


@pytest.mark.parametrize("command, expected", [
    ('python -c "print(1); print(2 > 1)"',
     ["python", "-c", "print(1); print(2 > 1)"]),
    ("echo '&& || ; > < & | ( ) ` $()'", ["echo", "&& || ; > < & | ( ) ` $()"]),
    ('echo "https://example.test/?a=1&b=2"', ["echo", "https://example.test/?a=1&b=2"]),
    (r'echo a\&b a\;b a\|b', ["echo", "a&b", "a;b", "a|b"]),
    (r'echo "escaped\"quote;literal"', ["echo", 'escaped"quote;literal']),
    (r'"C:\Program Files\Python\python.exe" -c "print(1)"',
     [r"C:\Program Files\Python\python.exe", "-c", "print(1)"]),
    ('"C:/Program Files/Python/python.exe" -c "print(1)"',
     ["C:/Program Files/Python/python.exe", "-c", "print(1)"]),
    ("python -c 'print(1)\nprint(2)'", ["python", "-c", "print(1)\nprint(2)"]),
    ('echo ""', ["echo", ""]),
])
def test_quoted_and_escaped_arguments_are_preserved(command, expected):
    assert validate_command_syntax(command) == expected


@pytest.mark.parametrize("command", [
    None, 42, [], "", "   ", '""', 'echo "oops', "echo \\", "echo \x00",
])
def test_malformed_input_raises_shell_error(command):
    with pytest.raises(ShellError):
        validate_command_syntax(command)


def test_invalid_syntax_does_not_prompt_allowlist_or_execute(tmp_path, monkeypatch):
    prompt = Mock()
    runner = ShellRunner(workspace=tmp_path, allowlist_callback=prompt)
    execute = Mock()
    monkeypatch.setattr(runner, "_execute", execute)
    with pytest.raises(ShellError, match="shell=False"):
        runner.run("unknown-program && git status", confirm=True)
    prompt.assert_not_called()
    execute.assert_not_called()


def test_syntax_validation_does_not_relax_existing_guards(tmp_path):
    runner = ShellRunner(workspace=tmp_path)
    with pytest.raises(BlockedCommandError):
        runner.validate("shutdown now", confirm=True)
    with pytest.raises(BlockedCommandError):
        runner.validate("unknown-program arg", confirm=True)
    with pytest.raises(SafeModeError):
        runner.validate("git push origin main")


def test_preflight_never_prompts_or_executes(tmp_path, monkeypatch):
    prompt = Mock(return_value="always")
    runner = ShellRunner(workspace=tmp_path, allowlist_callback=prompt)
    execute = Mock()
    monkeypatch.setattr(runner, "_execute", execute)
    with pytest.raises(BlockedCommandError, match="allowlist"):
        runner.preflight("unknown-program", confirm=True)
    prompt.assert_not_called()
    execute.assert_not_called()
    assert "unknown-program" not in runner._runtime_allowed


def test_preflight_checks_denylist_and_medium_risk_before_executable_lookup(tmp_path, monkeypatch):
    from bauer import shell_runner

    lookup = Mock()
    monkeypatch.setattr(shell_runner.shutil, "which", lookup)
    runner = ShellRunner(workspace=tmp_path)
    with pytest.raises(BlockedCommandError):
        runner.preflight("shutdown now", confirm=True)
    with pytest.raises(SafeModeError):
        runner.preflight("git push origin main")
    lookup.assert_not_called()


def test_preflight_rejects_missing_executable_without_running_it(tmp_path, monkeypatch):
    from bauer import shell_runner

    monkeypatch.setattr(shell_runner.shutil, "which", lambda executable: None)
    runner = ShellRunner(workspace=tmp_path)
    with pytest.raises(ShellError, match="Comando nao encontrado"):
        runner.preflight("python --version")


def test_preflight_rejects_missing_workspace(tmp_path):
    runner = ShellRunner(workspace=tmp_path / "missing")
    with pytest.raises(ShellError, match="Workspace nao existe"):
        runner.preflight(shlex.join([sys.executable, "--version"]))


def test_preflight_accepts_real_python_without_launching_it(tmp_path, monkeypatch):
    from bauer import shell_runner

    subprocess_run = Mock(side_effect=AssertionError("preflight must not execute"))
    monkeypatch.setattr(shell_runner.subprocess, "run", subprocess_run)
    runner = ShellRunner(workspace=tmp_path)
    assert runner.preflight(shlex.join([sys.executable, "--version"])) == [
        sys.executable, "--version",
    ]
    subprocess_run.assert_not_called()


@pytest.mark.parametrize("executable", ["./python", "bin/python", "../bin/python"])
def test_preflight_resolves_explicit_relative_executable_in_workspace(tmp_path, monkeypatch, executable):
    from bauer import shell_runner

    lookup = Mock(return_value="found")
    monkeypatch.setattr(shell_runner.shutil, "which", lookup)
    runner = ShellRunner(workspace=tmp_path)
    assert runner.preflight(f"{executable} --version") == [executable, "--version"]
    lookup.assert_called_once_with(str(tmp_path / executable))


@pytest.mark.parametrize("background", [False, True])
def test_execution_mixin_rejects_before_rewrite_or_builtin_guidance(tmp_path, monkeypatch, background):
    from bauer.tools import execution

    find_python = Mock()
    monkeypatch.setattr(execution, "_find_bauer_python", find_python)
    tool = ExecToolsMixin()._make_run_command(ShellRunner(workspace=tmp_path))
    for command in ("bauer status && git status", "cd project && git status"):
        with pytest.raises(ToolError, match="shell=False"):
            tool({"command": command, "background": background})
    find_python.assert_not_called()


def test_actual_python_subprocess_preserves_quotes_operators_and_workspace(tmp_path):
    workspace = tmp_path / "workspace with spaces"
    workspace.mkdir()
    script = "import pathlib; print('&& | ; > < &'); print(pathlib.Path.cwd())"
    command = shlex.join([sys.executable, "-c", script])
    runner = ShellRunner(workspace=workspace)

    result = runner.run(command)

    assert result.returncode == 0, result.stderr
    assert result.command == [sys.executable, "-c", script]
    assert result.stdout.splitlines() == ["&& | ; > < &", str(workspace.resolve())]


@pytest.mark.parametrize("explicit", [False, True])
def test_agno_workspace_uses_config_with_explicit_adapter_override(tmp_path, monkeypatch, explicit):
    from bauer import tool_router

    config = SimpleNamespace(agent=SimpleNamespace(workspace=tmp_path / "configured"))
    override = {"workspace": str(tmp_path / "override")} if explicit else {}
    adapter = AgnoRuntimeAdapter(config=config, adapter_config=override)
    build_router = Mock()
    monkeypatch.setattr(tool_router, "ToolRouter", build_router)
    adapter._build_tool_router()

    expected = tmp_path / ("override" if explicit else "configured")
    assert adapter.workspace == expected
    assert build_router.call_args.kwargs["workspace"] == expected
    assert build_router.call_args.kwargs["shell_runner"].workspace == expected


def test_agno_workspace_rejects_malformed_config():
    with pytest.raises(RuntimeAdapterError, match="workspace"):
        AgnoRuntimeAdapter(adapter_config={"workspace": Mock()})


def test_actual_agno_function_description_exposes_execution_contract():
    from bauer.tool_router import ToolRouter

    function = AgnoRuntimeAdapter()._bauer_tool_callable("run_command", router=ToolRouter(shell_runner=ShellRunner()))
    description = function.__doc__
    for detail in (
        "shell=False", "workspace", "No cd &&", "Docker", "host port",
        "app identity/content", "unverified", "confirm does not bypass",
    ):
        assert detail in description
