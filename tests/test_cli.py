"""Tests for the `uv run sandbox` REPL, driven through its real command line."""
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
COMMAND = [sys.executable, "-m", "agent_smith.sandbox.cli"]


def run_cli(text, *args, command=None):
    """Feed `text` to the CLI on stdin and return the finished process."""
    return subprocess.run(
        (command or COMMAND) + list(args),
        input=text,
        capture_output=True,
        text=True,
        cwd=ROOT,
        timeout=60,
    )


def test_runs_code_and_prints_the_value():
    done = run_cli("x = 2\nx * 21\nexit\n")
    assert done.stdout == "42\n"
    assert done.returncode == 0


def test_end_of_input_ends_the_session():
    done = run_cli("print('hi')\n")
    assert done.stdout == "hi\n"
    assert done.returncode == 0


def test_a_block_ends_with_a_blank_line():
    done = run_cli("def f(x):\n    return x * 2\n\nf(21)\nexit\n")
    assert done.stdout == "42\n"


def test_config_file_is_used():
    done = run_cli("import os\n", "sandbox_template.json")
    assert "import of 'os' is not allowed" in done.stdout
    assert done.returncode == 0


def test_bad_config_is_one_line_on_stderr():
    done = run_cli("exit\n", "tests/fixtures/bad_sandbox.json")
    assert done.returncode == 2
    assert "max_memory_mb" in done.stderr
    assert "Traceback" not in done.stderr


def test_final_answer_ends_the_session():
    done = run_cli('final_answer("def f(): return 1")\nprint("never")\n')
    assert "Final answer:\ndef f(): return 1\n" in done.stdout
    assert "never" not in done.stdout
    assert done.returncode == 0


def test_final_answer_survives_except_exception():
    done = run_cli('try:\n    final_answer("kept")\nexcept Exception:\n    print("caught")\n\n')
    assert "caught" not in done.stdout
    assert "kept" in done.stdout


def test_an_error_does_not_end_the_session():
    done = run_cli("1 / 0\nprint('still here')\nexit\n")
    assert "ZeroDivisionError" in done.stdout
    assert "still here" in done.stdout


def test_mcp_flags_are_accepted_but_not_supported_yet():
    done = run_cli("exit\n", "--mcp-stdio", "python mcp_tools_mbpp.py")
    assert "not yet supported" in done.stderr
    assert done.returncode == 0


def test_system_exit_in_code_sets_the_exit_code():
    done = run_cli("raise SystemExit(3)\n")
    assert done.returncode == 3


@pytest.mark.skipif(shutil.which("sandbox") is None, reason="console script not installed")
def test_the_installed_console_script_works():
    done = run_cli("x = 2\nx * 21\nexit\n", command=["sandbox"])
    assert done.stdout == "42\n"
