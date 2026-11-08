"""Tests for the sandbox controller and its worker process."""
import os
import signal
import threading
import time

import pytest

from agent_smith.models import SandboxConfig
from agent_smith.sandbox.controller import Sandbox


@pytest.fixture
def sandbox():
    with Sandbox(SandboxConfig()) as running:
        yield running


def test_runs_code_and_returns_stdout(sandbox):
    result = sandbox.execute("print('hi')")
    assert result.stdout == "hi\n"
    assert result.error is None


def test_variables_persist_between_calls(sandbox):
    sandbox.execute("x = 21")
    assert sandbox.execute("x * 2").value == "42"


def test_errors_come_back_as_text(sandbox):
    result = sandbox.execute("import os")
    assert "not allowed" in result.error


def test_close_stops_the_worker():
    sandbox = Sandbox(SandboxConfig())
    sandbox.start()
    process = sandbox._process
    sandbox.close()
    assert not process.is_alive()

@pytest.fixture
def quick_sandbox():
    with Sandbox(SandboxConfig(max_execution_time_seconds=1)) as running:
        yield running


def test_infinite_loop_is_killed(quick_sandbox):
    started = time.monotonic()
    result = quick_sandbox.execute("while True: pass")
    assert "Timeout" in result.error
    assert time.monotonic() - started < 5


def test_sandbox_still_works_after_timeout(quick_sandbox):
    quick_sandbox.execute("while True: pass")
    assert quick_sandbox.execute("1 + 1").value == "2"



def test_soft_timeout_keeps_variables(quick_sandbox):
    quick_sandbox.execute("x = 5")
    result = quick_sandbox.execute("while True: pass")
    assert "variables were kept" in result.error
    assert quick_sandbox.execute("x").value == "5"


def test_uninterruptible_code_is_hard_killed(quick_sandbox):
    quick_sandbox.execute("x = 5")
    result = quick_sandbox.execute("sum(range(10**12))")
    assert "restarted" in result.error
    assert "NameError" in quick_sandbox.execute("x").error


def test_output_before_a_hard_kill_is_kept(quick_sandbox):
    result = quick_sandbox.execute("print('before')\nsum(range(10**12))")
    assert result.stdout == "before\n"
    assert "restarted" in result.error


def test_output_before_a_soft_timeout_is_kept(quick_sandbox):
    result = quick_sandbox.execute("print('a')\nwhile True: pass")
    assert result.stdout == "a\n"
    assert "variables were kept" in result.error


def test_worker_that_dies_is_replaced(sandbox):
    sandbox._process.kill()
    sandbox._process.join()
    result = sandbox.execute("1 + 1")
    assert "died" in result.error
    assert sandbox.execute("1 + 1").value == "2"


def test_long_output_is_truncated_with_a_note(sandbox):
    result = sandbox.execute("print('x' * 50000)")
    assert len(result.stdout) < 10_200
    assert "output truncated" in result.stdout


def test_short_output_is_not_touched(sandbox):
    assert sandbox.execute("print('hello')").stdout == "hello\n"


def test_memory_bomb_is_stopped(sandbox):
    result = sandbox.execute("x = [0] * 10**10")
    assert "memory limit" in result.error
    assert sandbox.execute("1 + 1").value == "2"


def test_system_exit_in_code_reaches_the_caller(sandbox):
    with pytest.raises(SystemExit) as info:
        sandbox.execute("raise SystemExit(3)")
    assert info.value.code == 3
    assert sandbox.execute("1 + 1").value == "2"


def test_keyboard_interrupt_in_code_reaches_the_caller(sandbox):
    with pytest.raises(KeyboardInterrupt):
        sandbox.execute("raise KeyboardInterrupt")
    assert sandbox.execute("1 + 1").value == "2"


def test_ctrl_c_while_waiting_kills_the_worker():
    with Sandbox(SandboxConfig(max_execution_time_seconds=30)) as running:
        process = running._process
        threading.Timer(0.5, os.kill, args=(os.getpid(), signal.SIGINT)).start()
        with pytest.raises(KeyboardInterrupt):
            running.execute("while True: pass")
        assert not process.is_alive()
