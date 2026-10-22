"""Subprocess groups owned by a cooperative execution, preserving legacy calls."""
from __future__ import annotations

import os
import signal
import subprocess
import threading
from contextlib import nullcontext
import time
from typing import Any

from .execution_control import current_control
from .logging_config import log_suppressed
from .provider_retry import ProviderRetryInterrupted

_IS_WINDOWS = os.name == "nt"
_POLL_SECONDS = 0.1
_TERMINATE_GRACE_SECONDS = 0.3


def terminate_process_tree(proc: subprocess.Popen) -> bool:
    """Stop an owned process group; success means the direct child exited.

    Owned Unix children start their own session. On Windows taskkill traverses
    descendants; failure to terminate is represented by a still-running poll.
    Never use this helper for a process not created through ``controlled_popen``.
    """
    lock = getattr(proc, "_bauer_termination_lock", None)
    with lock if isinstance(lock, type(threading.Lock())) else nullcontext():
        if getattr(proc, "_bauer_tree_terminated", False) is True:
            return proc.poll() is not None
        exited = _terminate_process_tree(proc)
        if exited:
            setattr(proc, "_bauer_tree_terminated", True)
        return exited


def _terminate_process_tree(proc: subprocess.Popen) -> bool:
    if _IS_WINDOWS:
        if proc.poll() is None:
            try:
                subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                               timeout=5, check=False, shell=False)
            except (OSError, subprocess.TimeoutExpired) as exc:
                log_suppressed("process_control.taskkill_fallback", exc)
                try:
                    proc.kill()
                except OSError as exc:
                    log_suppressed("process_control.taskkill_fallback_kill", exc)
    else:
        try:
            os.killpg(proc.pid, signal.SIGTERM)
        except ProcessLookupError as exc:
            log_suppressed("process_control.sigterm_group_gone", exc)
        except OSError as exc:
            log_suppressed("process_control.sigterm_group", exc)
            try:
                proc.terminate()
            except OSError as exc:
                log_suppressed("process_control.sigterm_fallback", exc)
        # A parent can exit before a descendant that ignores TERM. Escalate for
        # the entire owned group even when the parent was reaped during grace.
        deadline = time.monotonic() + _TERMINATE_GRACE_SECONDS
        try:
            proc.wait(timeout=_TERMINATE_GRACE_SECONDS)
        except subprocess.TimeoutExpired as exc:
            log_suppressed("process_control.terminate_grace_wait", exc)
        remaining = deadline - time.monotonic()
        if remaining > 0:
            time.sleep(remaining)
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError as exc:
            log_suppressed("process_control.sigkill_group_gone", exc)
        except OSError as exc:
            log_suppressed("process_control.sigkill_group", exc)
            try:
                proc.kill()
            except OSError as exc:
                log_suppressed("process_control.sigkill_fallback", exc)
    try:
        proc.wait(timeout=2)
    except (OSError, subprocess.TimeoutExpired) as exc:
        log_suppressed("process_control.final_reap_wait", exc)
    return proc.poll() is not None


def controlled_popen(args, **kwargs) -> subprocess.Popen:
    """Register a subprocess and its tree before returning it to the caller."""
    control = current_control()
    if control is None:
        return subprocess.Popen(args, **kwargs)
    control.checkpoint()
    if _IS_WINDOWS:
        kwargs["creationflags"] = kwargs.get("creationflags", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)
    else:
        kwargs["start_new_session"] = True
    proc = subprocess.Popen(args, **kwargs)
    try:
        setattr(proc, "_bauer_termination_lock", threading.Lock())

        def terminate() -> None:
            terminate_process_tree(proc)
        control.register_process(proc, terminate)
        control.checkpoint()
    except BaseException:
        terminate_process_tree(proc)
        control.unregister_process(proc)
        raise
    return proc


def release_process(proc: subprocess.Popen, control: Any = None) -> None:
    """Unregister only after exit, so cancellation still owns a live child."""
    control = control if control is not None else current_control()
    if control is not None and proc.poll() is not None:
        control.unregister_process(proc)


def run_process(args, **kwargs) -> subprocess.CompletedProcess:
    """subprocess.run with cooperative cancellation under an active control.

    Without a control this is the exact legacy ``subprocess.run`` path, including
    its mocks and timeout semantics. Controlled calls use repeated communicate
    waits so cancellation is observed without force-stopping Python threads.
    """
    control = current_control()
    if control is None:
        return subprocess.run(args, **kwargs)
    timeout = kwargs.pop("timeout", None)
    check = kwargs.pop("check", False)
    input_data = kwargs.pop("input", None)
    if kwargs.pop("capture_output", False):
        if kwargs.get("stdout") is not None or kwargs.get("stderr") is not None:
            raise ValueError("stdout and stderr arguments may not be used with capture_output")
        kwargs["stdout"] = subprocess.PIPE
        kwargs["stderr"] = subprocess.PIPE
    if input_data is not None:
        if kwargs.get("stdin") is not None:
            raise ValueError("stdin and input arguments may not both be used")
        kwargs["stdin"] = subprocess.PIPE
    proc = controlled_popen(args, **kwargs)
    deadline = time.monotonic() + timeout if timeout is not None else None
    try:
        while True:
            control.checkpoint()
            wait = _POLL_SECONDS
            if deadline is not None:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise subprocess.TimeoutExpired(args, timeout)
                wait = min(wait, remaining)
            try:
                stdout, stderr = proc.communicate(input=input_data, timeout=wait)
                break
            except subprocess.TimeoutExpired:
                input_data = None
        control.checkpoint()
        result = subprocess.CompletedProcess(args, proc.returncode, stdout, stderr)
        if check:
            result.check_returncode()
        return result
    except ProviderRetryInterrupted:
        terminate_process_tree(proc)
        raise
    except BaseException:
        terminate_process_tree(proc)
        raise
    finally:
        release_process(proc, control)


def process_owner() -> tuple[str, str | None] | None:
    control = current_control()
    return (control.session_id, control.run_id) if control is not None else None


def owns_process(info: dict) -> bool:
    return info.get("owner") == process_owner()
