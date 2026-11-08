"""Security tests: things the sandbox must refuse, whatever route the code takes."""
import pytest

from agent_smith.models import SandboxConfig
from agent_smith.sandbox.controller import Sandbox


@pytest.fixture
def sandbox():
    with Sandbox(SandboxConfig()) as running:
        yield running


@pytest.mark.parametrize(
    "code",
    [
        "import random\nrandom._os.system('echo pwned')",
        "import random\nrandom._os.listdir('/')",
        "import random\nrandom._os.listdir()",
        "import random\nrandom._os.remove('/tmp/x')",
        "import random\nrandom._os.kill(random._os.getppid(), 0)",
        "import json\njson.codecs.open('/etc/hostname').read()",
        "open('/etc/passwd')",
    ],
)
def test_escape_attempts_are_refused(sandbox, code):
    result = sandbox.execute(code)
    assert result.error is not None
    assert "denied" in result.error or "not allowed" in result.error


def test_network_is_blocked_even_if_socket_is_allowed():
    with Sandbox(SandboxConfig(authorized_imports=["socket"])) as sandbox:
        result = sandbox.execute("import socket\nsocket.socket()")
    assert "not allowed" in result.error


def test_processes_are_blocked_even_if_subprocess_is_allowed():
    with Sandbox(SandboxConfig(authorized_imports=["subprocess"])) as sandbox:
        result = sandbox.execute("import subprocess\nsubprocess.run(['echo', 'hi'])")
    assert "not allowed" in result.error


def test_files_inside_an_allowed_directory_still_work(tmp_path):
    target = str(tmp_path / "a.txt")
    code = f"f = open({target!r}, 'w')\nf.write('hi')\nf.close()\nopen({target!r}).read()"
    with Sandbox(SandboxConfig(allowed_directories=[str(tmp_path)])) as sandbox:
        assert sandbox.execute(code).value == "'hi'"


def test_normal_imports_still_work(sandbox):
    assert sandbox.execute("import json, re, heapq\njson.dumps([1])").value == "'[1]'"


def test_environment_variables_are_not_visible(monkeypatch):
    monkeypatch.setenv("FAKE_API_KEY", "sk-this-is-a-secret")
    with Sandbox(SandboxConfig()) as running:
        leaked = running.execute("import random\nrandom._os.environ.get('FAKE_API_KEY')")
        count = running.execute("import random\nlen(random._os.environ)")
    assert leaked.error is None and leaked.value is None
    assert count.value == "0"


def test_proc_environ_cannot_be_read(sandbox):
    result = sandbox.execute("open('/proc/self/environ')")
    assert "denied" in result.error


@pytest.mark.parametrize(
    "code",
    [
        "print.__self__",
        "getattr(print, '__se' + 'lf__')",
        "hasattr(print, '__se' + 'lf__')",
        "__import__('time')",
        "().__getattribute__('__cl' + 'ass__')",
    ],
)
def test_routes_to_the_real_builtins_are_refused(sandbox, code):
    assert "not allowed" in sandbox.execute(code).error


def test_object_graph_escape_still_cannot_run_programs(sandbox):
    code = (
        "import operator\n"
        "get = operator.attrgetter\n"
        "subclasses = get('__class__.__base__.__subclasses__')(())()\n"
        "wrap = [c for c in subclasses if c.__name__ == '_wrap_close'][0]\n"
        "get('__init__.__globals__')(wrap)['system']('echo pwned')"
    )
    assert "not allowed" in sandbox.execute(code).error


@pytest.mark.parametrize(
    "code",
    [
        "import datetime\ndatetime.datetime.strptime('2020-01-02', '%Y-%m-%d').year",
        "import datetime\ndatetime.datetime(2020, 1, 2).strftime('%Y')",
        "import datetime\ndatetime.date.today().year > 2000",
    ],
)
def test_datetime_works_despite_hidden_imports(sandbox, code):
    assert sandbox.execute(code).error is None


def test_models_own_import_of_time_is_still_refused(sandbox):
    assert "not allowed" in sandbox.execute("import time").error
    assert "not allowed" in sandbox.execute("from time import sleep").error
