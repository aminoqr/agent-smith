"""Tests for the sandbox controller and its worker process."""
import pytest
import time

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
