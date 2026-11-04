"""Tests for running code in the sandbox namespace."""
import pytest

from agent_smith.models import SandboxConfig
from agent_smith.sandbox.runtime import execute, new_namespace


@pytest.fixture
def namespace():
    return new_namespace(SandboxConfig())


def test_captures_stdout(namespace):
    result = execute("print('hi')", namespace)
    assert result.stdout == "hi\n"
    assert result.error is None
    assert result.value is None


def test_variables_persist_between_calls(namespace):
    execute("x = 21", namespace)
    assert execute("x * 2", namespace).value == "42"


def test_last_expression_is_echoed(namespace):
    assert execute("1 + 1\n2 + 2", namespace).value == "4"


def test_classes_work(namespace):
    assert execute("class A:\n    value = 5\nA().value", namespace).value == "5"


def test_blocked_import_is_reported(namespace):
    result = execute("x = 1\nimport os", namespace)
    assert result.error == "SandboxSecurityError: import of 'os' is not allowed [line 2]"


def test_blocked_builtin_is_reported(namespace):
    result = execute("eval('1 + 1')", namespace)
    assert "disabled in the sandbox" in result.error


def test_blocked_path_is_reported(namespace):
    result = execute("open('/etc/passwd')", namespace)
    assert "denied" in result.error


def test_runtime_error_keeps_partial_stdout(namespace):
    result = execute("print('before')\n1 / 0", namespace)
    assert result.stdout == "before\n"
    assert result.error == "ZeroDivisionError: division by zero [line 2]"


def test_syntax_error_is_reported(namespace):
    result = execute("def broken(", namespace)
    assert result.error.startswith("SyntaxError")


def test_system_exit_is_not_swallowed(namespace):
    with pytest.raises(SystemExit):
        execute("raise SystemExit(3)", namespace)
