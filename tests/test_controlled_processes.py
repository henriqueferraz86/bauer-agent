"""Owned subprocess cancellation, real descendants and legacy compatibility."""
from __future__ import annotations

import os
import shlex
import subprocess
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from bauer.execution_control import ExecutionControl
from bauer.process_control import controlled_popen, run_process, terminate_process_tree
from bauer.provider_retry import ProviderRetryInterrupted
from bauer.shell_runner import ShellRunner
from bauer.tool_router import ToolRouter
from bauer.tools.base import ToolError


def command(code):
    return f'{shlex.quote(sys.executable)} -c {shlex.quote(code)}'


def test_legacy_run_still_calls_subprocess_run(monkeypatch):
    sentinel = subprocess.CompletedProcess(['legacy'], 0, 'ok', '')
    legacy = Mock(return_value=sentinel)
    monkeypatch.setattr('bauer.process_control.subprocess.run', legacy)
    assert run_process(['legacy'], capture_output=True, timeout=3) is sentinel
    legacy.assert_called_once_with(['legacy'], capture_output=True, timeout=3)


def test_controlled_run_captures_text_and_return_code(tmp_path):
    control = ExecutionControl('run', 'session', tmp_path)
    with control.activate():
        result = run_process([sys.executable, '-c', 'import sys;print("ok");sys.exit(3)'],
                             capture_output=True, text=True, timeout=5, cwd=tmp_path)
    assert result.returncode == 3
    assert result.stdout.strip() == 'ok'
    assert not control._processes


def test_controlled_run_supports_input(tmp_path):
    with ExecutionControl('run', 'session', tmp_path).activate():
        result = run_process([sys.executable, '-c', 'import sys;print(sys.stdin.read())'],
                             input='hello', capture_output=True, text=True, timeout=5)
    assert result.stdout.strip() == 'hello'


def test_pre_cancel_never_launches_subprocess(tmp_path, monkeypatch):
    control = ExecutionControl('run', 'session', tmp_path)
    control.request_cancel()
    launch = Mock()
    monkeypatch.setattr('bauer.process_control.subprocess.Popen', launch)
    with control.activate(), pytest.raises(ProviderRetryInterrupted):
        run_process(['not-started'])
    launch.assert_not_called()


def test_shell_runner_preserves_typed_interruption(tmp_path):
    control = ExecutionControl('run', 'session', tmp_path)
    control.request_cancel()
    with control.activate(), pytest.raises(ProviderRetryInterrupted):
        ShellRunner(tmp_path).run(command('print("unreachable")'))


def test_controlled_timeout_reaps_direct_child(tmp_path):
    control = ExecutionControl('run', 'session', tmp_path)
    with control.activate(), pytest.raises(subprocess.TimeoutExpired):
        run_process([sys.executable, '-c', 'import time;time.sleep(30)'],
                    capture_output=True, timeout=.05)
    assert not control._processes


def test_cancel_during_process_interrupts_and_reaps(tmp_path):
    control = ExecutionControl('run', 'session', tmp_path)
    ready = tmp_path / 'ready'
    errors = []

    def worker():
        with control.activate():
            try:
                run_process([sys.executable, '-c',
                             f'from pathlib import Path;import time;Path({str(ready)!r}).touch();time.sleep(30)'],
                            capture_output=True, timeout=10)
            except ProviderRetryInterrupted as exc:
                errors.append(exc.reason)

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    deadline = time.monotonic() + 5
    while not ready.exists() and time.monotonic() < deadline:
        time.sleep(.01)
    try:
        assert ready.exists()
        control.request_cancel()
        thread.join(timeout=5)
        assert not thread.is_alive()
        assert errors == ['cancelled']
        assert not control._processes
    finally:
        control.request_cancel()


@pytest.mark.skipif(os.name == 'nt', reason='Unix process group semantics; Windows tested via mocked taskkill')
def test_cancel_kills_descendant_ignoring_term_and_preserves_other_run(tmp_path):
    control = ExecutionControl('first', 'session', tmp_path)
    other = ExecutionControl('second', 'other-session', tmp_path)
    ready = tmp_path / 'ready'
    effect = tmp_path / 'should-not-exist'
    descendant = ('import signal,time;from pathlib import Path;'
                  'signal.signal(signal.SIGTERM,signal.SIG_IGN);'
                  f'Path({str(ready)!r}).touch();time.sleep(1.3);'
                  f'Path({str(effect)!r}).touch();time.sleep(20)')
    parent = f'import subprocess,sys,time;subprocess.Popen([sys.executable,"-c",{descendant!r}]);time.sleep(30)'
    with control.activate():
        process = controlled_popen([sys.executable, '-c', parent])
    with other.activate():
        unrelated = controlled_popen([sys.executable, '-c', 'import time;time.sleep(30)'])
    try:
        deadline = time.monotonic() + 5
        while not ready.exists() and time.monotonic() < deadline:
            time.sleep(.01)
        assert ready.exists()
        control.request_cancel()
        control.close()
        assert process.poll() is not None
        assert unrelated.poll() is None
        time.sleep(1.4)
        assert not effect.exists()
    finally:
        terminate_process_tree(process)
        terminate_process_tree(unrelated)


def test_windows_controlled_launch_uses_new_group(tmp_path, monkeypatch):
    import bauer.process_control as module
    fake = Mock(pid=321)
    fake.poll.return_value = None
    launch = Mock(return_value=fake)
    monkeypatch.setattr(module, '_IS_WINDOWS', True)
    monkeypatch.setattr(module.subprocess, 'Popen', launch)
    control = ExecutionControl('run', 'session', tmp_path)
    with control.activate():
        assert controlled_popen(['python', 'worker.py'], shell=False) is fake
    assert launch.call_args.kwargs['creationflags'] & 0x200
    assert launch.call_args.kwargs['shell'] is False
    assert 'start_new_session' not in launch.call_args.kwargs


def test_windows_tree_termination_uses_taskkill_no_shell(monkeypatch):
    import bauer.process_control as module
    fake = Mock(pid=321)
    fake.poll.side_effect = [None, 0]
    kill = Mock()
    monkeypatch.setattr(module, '_IS_WINDOWS', True)
    monkeypatch.setattr(module.subprocess, 'run', kill)
    assert terminate_process_tree(fake)
    assert kill.call_args.args[0] == ['taskkill', '/PID', '321', '/T', '/F']
    assert kill.call_args.kwargs['shell'] is False


def make_router(tmp_path):
    return ToolRouter(workspace=tmp_path, shell_runner=ShellRunner(tmp_path, safe_mode=False))


@pytest.mark.parametrize('background_tool', ['process', 'run_command'])
def test_background_process_is_visible_only_to_owner_run(tmp_path, background_tool):
    router = make_router(tmp_path)
    first = ExecutionControl('first', 'same-session', tmp_path)
    second = ExecutionControl('second', 'same-session', tmp_path)
    cmd = command('import time;time.sleep(30)')
    with first.activate():
        if background_tool == 'process':
            router._process({'action': 'start', 'command': cmd})
        else:
            router._make_run_command(router._shell_runner)({'command': cmd, 'background': True})
        pid = next(iter(router._processes))
        assert pid in router._process({'action': 'list'})
    try:
        with second.activate():
            assert pid not in router._process({'action': 'list'})
            for action in ('kill', 'poll', 'log', 'write'):
                with pytest.raises(ToolError, match='não encontrado'):
                    router._process({'action': action, 'pid': pid, 'input': 'test'})
        with first.activate():
            assert router._processes[pid]['proc'].poll() is None
            text = router._process({'action': 'kill', 'pid': pid})
            assert 'encerrado' in text
            assert pid not in router._processes
        assert not first._processes
    finally:
        first.request_cancel()
        second.request_cancel()


def test_process_kill_does_not_claim_exit_while_process_alive(tmp_path, monkeypatch):
    router = make_router(tmp_path)
    control = ExecutionControl('first', 'session', tmp_path)
    proc = Mock(pid=123)
    proc.poll.return_value = None
    router._processes['123'] = {'proc': proc, 'label': 'alive', 'owner': ('session', 'first')}
    monkeypatch.setattr('bauer.tools.agent_misc.terminate_process_tree', lambda _: False)
    with control.activate():
        text = router._process({'action': 'kill', 'pid': '123'})
    assert 'ainda em execução' in text
    assert '123' in router._processes


def test_execute_code_does_not_wrap_cancellation_as_tool_error(tmp_path):
    router = make_router(tmp_path)
    control = ExecutionControl('run', 'session', tmp_path)
    control.request_cancel()
    with control.activate(), pytest.raises(ProviderRetryInterrupted):
        router._execute_code({'code': 'print("never")'})


def test_delegate_local_cancellation_never_falls_back_to_process(tmp_path, monkeypatch):
    router = make_router(tmp_path)
    control = ExecutionControl('run', 'session', tmp_path)
    def stream(*args):
        control.request_cancel()
        yield 'partial'
    router._llm_client = SimpleNamespace(chat_stream=stream)
    fallback = Mock()
    monkeypatch.setattr('bauer.tools.execution.run_process', fallback)
    with control.activate(), pytest.raises(ProviderRetryInterrupted):
        router._execute_delegated_task('task', None, '', '', 10)
    fallback.assert_not_called()
