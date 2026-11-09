"""Tests for `python -m agent_mbpp`, the command the grader actually runs.

These drive the real CLI in a real subprocess against a local scripted
provider, so the whole path is exercised: config loading, the MCP server, the
sandbox worker, the loop, the output file, and SIGTERM handling. No API keys
and no network beyond localhost.
"""
import json
import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

from agent_smith.models import SolutionOutput

ROOT = Path(__file__).resolve().parent.parent
TASK = ROOT / "tests" / "fixtures" / "mbpp_sample.json"
PROVIDER_SCRIPT = ROOT / "tests" / "fixtures" / "scripted_provider.py"
KEY_VARIABLE = "SCRIPTED_API_KEY"


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


class ScriptedProvider:
    """The local provider plus the providers.json that points at it."""

    def __init__(self, tmp_path: Path, mode: str):
        self.port = free_port()
        self.url = f"http://127.0.0.1:{self.port}/v1"
        self.process = subprocess.Popen(
            [sys.executable, str(PROVIDER_SCRIPT), "--port", str(self.port), "--mode", mode],
            cwd=ROOT,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        self.catalogue = tmp_path / "providers.json"
        self.catalogue.write_text(
            json.dumps(
                {
                    "providers": [
                        {
                            "name": "scripted",
                            "base_url": self.url,
                            "key_env_vars": [KEY_VARIABLE],
                            "models": ["scripted-test"],
                        }
                    ]
                }
            )
        )
        self._wait_until_listening()

    def _wait_until_listening(self, timeout: float = 10.0) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with socket.socket() as probe:
                if probe.connect_ex(("127.0.0.1", self.port)) == 0:
                    return
            time.sleep(0.05)
        raise RuntimeError("the scripted provider never started listening")

    def stop(self) -> None:
        self.process.terminate()
        self.process.wait(timeout=10)


@pytest.fixture
def provider(request, tmp_path):
    mode = getattr(request, "param", "solve")
    running = ScriptedProvider(tmp_path, mode)
    try:
        yield running
    finally:
        running.stop()


def run_cli(provider, tmp_path, *extra, task=TASK, env_extra=None, timeout=120):
    output = tmp_path / "solution.json"
    env = {**os.environ, KEY_VARIABLE: "scripted-key-0001", **(env_extra or {})}
    done = subprocess.run(
        [
            sys.executable, "-m", "agent_mbpp",
            "--task-file", str(task),
            "--output", str(output),
            "--providers", str(provider.catalogue),
            "--env-file", str(tmp_path / "no-such.env"),
            *extra,
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        env=env,
        timeout=timeout,
    )
    return done, output


def load(output: Path) -> SolutionOutput:
    return SolutionOutput.model_validate_json(output.read_text())


# --- the happy path the grader runs ---------------------------------------


def test_the_cli_solves_a_task_and_writes_the_file(provider, tmp_path):
    done, output = run_cli(provider, tmp_path)

    assert done.returncode == 0, done.stderr
    solution = load(output)
    assert solution.success is True
    assert "def remove_Occ" in solution.solution
    assert solution.task_id == "11" and solution.benchmark == "mbpp"
    assert solution.iterations == 1
    assert "solved" in done.stdout


def test_the_file_satisfies_the_graders_consistency_rules(provider, tmp_path):
    _done, output = run_cli(provider, tmp_path)
    solution = load(output)

    assert solution.system_prompt
    assert all(step.llm_output for step in solution.steps)
    assert all(step.api_url and step.model_name for step in solution.steps)
    for step in solution.steps:
        if step.sandbox_output:
            assert step.sandbox_input
    timestamps = [step.timestamp for step in solution.steps]
    assert timestamps == sorted(timestamps)


def test_the_model_and_url_can_come_from_the_command_line(provider, tmp_path):
    done, output = run_cli(
        provider, tmp_path, "--model-name", "chosen-model", "--provider-url", provider.url
    )

    assert done.returncode == 0
    assert load(output).steps[0].model_name == "chosen-model"


def test_an_iteration_override_is_respected(provider, tmp_path):
    done, output = run_cli(provider, tmp_path, "--max-iterations", "1")

    assert done.returncode == 0
    assert load(output).iterations == 1


# --- failures still produce a valid file -----------------------------------


@pytest.mark.parametrize("provider", ["prose"], indirect=True)
def test_a_model_that_never_writes_code_fails_honestly(provider, tmp_path):
    done, output = run_cli(provider, tmp_path, "--max-iterations", "2")

    assert done.returncode == 0  # the pipeline must continue to validation
    solution = load(output)
    assert solution.success is False and solution.solution == ""
    assert solution.iterations == 2
    assert "final_answer() was never called" in solution.error
    assert all(step.sandbox_input == "" and step.sandbox_output == "" for step in solution.steps)


@pytest.mark.parametrize("provider", ["down"], indirect=True)
def test_a_dead_provider_is_reported_in_the_file(provider, tmp_path):
    done, output = run_cli(provider, tmp_path)

    assert done.returncode == 0
    solution = load(output)
    assert solution.success is False and solution.iterations == 0
    assert "could not be reached" in solution.error


def test_a_missing_api_key_still_writes_a_file(provider, tmp_path):
    done, output = run_cli(provider, tmp_path, env_extra={KEY_VARIABLE: ""})

    assert done.returncode == 0
    solution = load(output)
    assert solution.success is False
    assert "configuration error" in solution.error and KEY_VARIABLE in solution.error
    assert "error:" in done.stderr


def test_an_unreadable_task_file_is_the_one_case_that_exits_non_zero(provider, tmp_path):
    done, output = run_cli(provider, tmp_path, task=tmp_path / "nope.json")

    assert done.returncode == 2
    assert not output.exists()
    assert "error:" in done.stderr


def test_a_broken_agent_config_is_reported(provider, tmp_path):
    broken = tmp_path / "agent.json"
    broken.write_text('{"max_iteratons": 2}')
    done, output = run_cli(provider, tmp_path, "--config", str(broken))

    assert done.returncode == 0
    assert "configuration error" in load(output).error


# --- what the grader does when time runs out -------------------------------


@pytest.mark.parametrize("provider", ["slow"], indirect=True)
def test_sigterm_leaves_a_valid_file_and_no_leftover_processes(provider, tmp_path):
    """The grader signals the whole process group, then SIGKILLs it 10 s later."""
    output = tmp_path / "solution.json"
    env = {**os.environ, KEY_VARIABLE: "scripted-key-0001"}
    process = subprocess.Popen(
        [
            sys.executable, "-m", "agent_mbpp",
            "--task-file", str(TASK),
            "--output", str(output),
            "--providers", str(provider.catalogue),
            "--env-file", str(tmp_path / "no-such.env"),
        ],
        cwd=ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
        start_new_session=True,
    )
    children = _descendants(process.pid, wait_for=2, timeout=20)
    assert children, f"expected a worker and an MCP server; agent exited {process.poll()}"

    os.killpg(os.getpgid(process.pid), signal.SIGTERM)
    process.wait(timeout=15)

    assert output.exists()
    solution = load(output)
    assert solution.success is False
    assert "interrupted" in (solution.error or "")

    deadline = time.monotonic() + 10
    while time.monotonic() < deadline and _alive(children):
        time.sleep(0.1)
    assert not _alive(children), f"processes survived the signal: {_alive(children)}"


def _descendants(pid: int, wait_for: int = 1, timeout: float = 10.0):
    """PIDs below `pid`, once at least `wait_for` of them exist."""
    deadline = time.monotonic() + timeout
    found = []
    while time.monotonic() < deadline:
        found = _children_of(pid)
        if len(found) >= wait_for:
            return found
        time.sleep(0.1)
    return found


def _children_of(pid: int):
    listing = subprocess.run(
        ["ps", "-eo", "pid=,ppid="], capture_output=True, text=True, check=False
    ).stdout
    tree = {}
    for line in listing.splitlines():
        parts = line.split()
        if len(parts) == 2:
            tree.setdefault(int(parts[1]), []).append(int(parts[0]))
    found, queue = [], [pid]
    while queue:
        for child in tree.get(queue.pop(), []):
            found.append(child)
            queue.append(child)
    return found


def _alive(pids):
    return [pid for pid in pids if Path(f"/proc/{pid}").exists()]
