"""Tests for the sandbox policy rules."""
import pytest

from agent_smith.models import SandboxConfig
from agent_smith.sandbox.errors import SandboxSecurityError
from agent_smith.sandbox.policy import (
    is_import_allowed,
    make_guarded_import,
    make_safe_builtins,
    is_path_allowed,
    make_guarded_open,
    validate_ast,
)



ALLOWLIST = SandboxConfig().authorized_imports


@pytest.mark.parametrize(
    "name, expected",
    [
        ("math", True),
        ("math.foo", True),
        ("collections.abc", True),
        ("re", True),
        ("os", False),
        ("mathx", False),
        ("re.foo", False),
        ("", False),
    ],
)
def test_is_import_allowed(name, expected):
    assert is_import_allowed(name, ALLOWLIST) == expected


def make_namespace():
    """A namespace whose only builtin is the guarded import."""
    return {"__builtins__": {"__import__": make_guarded_import(ALLOWLIST)}}


def test_guarded_import_allows_listed_module():
    namespace = make_namespace()
    exec("import math", namespace)
    assert namespace["math"].sqrt(9) == 3


def test_guarded_import_blocks_unlisted_module():
    with pytest.raises(SandboxSecurityError, match="'os'"):
        exec("import os", make_namespace())


def test_guarded_import_blocks_relative_import():
    with pytest.raises(SandboxSecurityError, match="relative"):
        exec("from . import x", make_namespace())


def test_guarded_import_checks_fromlist_submodules():
    namespace = {"__builtins__": {"__import__": make_guarded_import(["collections"])}}
    with pytest.raises(SandboxSecurityError, match="collections.abc"):
        exec("from collections import abc", namespace)
    exec("from collections import OrderedDict", namespace)
    assert "OrderedDict" in namespace

def run(code):
    """Run code with the safe builtins and return its namespace."""
    namespace = {"__builtins__": make_safe_builtins(SandboxConfig())}
    exec(code, namespace)
    return namespace


@pytest.mark.parametrize(
    "code",
    [
        "eval('1 + 1')",
        "exec('x = 1')",
        "compile('1', 'f', 'eval')",
        "input()",
        "breakpoint()",
        "globals()",
        "locals()",
        "vars()",
        "exit()",
        "quit()",
        "help()",
    ],
)
def test_dangerous_builtins_are_disabled(code):
    with pytest.raises(SandboxSecurityError, match="disabled in the sandbox"):
        run(code)


def test_normal_builtins_still_work():
    namespace = run("result = sorted([3, 1, 2]) + [len('abc')]")
    assert namespace["result"] == [1, 2, 3, 3]


def test_classes_still_work():
    namespace = run("class A:\n    value = 5\nresult = A().value")
    assert namespace["result"] == 5


def test_real_builtins_are_untouched():
    make_safe_builtins(SandboxConfig())
    assert eval("1 + 1") == 2


def test_safe_builtins_use_the_import_guard():
    with pytest.raises(SandboxSecurityError, match="'os'"):
        run("import os")

def test_path_inside_allowed_directory(tmp_path):
    assert is_path_allowed(str(tmp_path / "file.txt"), [str(tmp_path)])


def test_path_outside_allowed_directory(tmp_path):
    assert not is_path_allowed("/etc/passwd", [str(tmp_path)])


def test_dotdot_cannot_escape(tmp_path):
    sneaky = str(tmp_path / ".." / ".." / "etc" / "passwd")
    assert not is_path_allowed(sneaky, [str(tmp_path)])


def test_sibling_with_same_prefix_is_not_inside(tmp_path):
    allowed = tmp_path / "agent"
    sibling = tmp_path / "agent2"
    allowed.mkdir()
    sibling.mkdir()
    assert not is_path_allowed(str(sibling / "secret.txt"), [str(allowed)])


def test_symlink_cannot_escape(tmp_path):
    allowed = tmp_path / "agent"
    allowed.mkdir()
    link = allowed / "link"
    link.symlink_to("/etc")
    assert not is_path_allowed(str(link / "passwd"), [str(allowed)])


def test_guarded_open_reads_allowed_file(tmp_path):
    (tmp_path / "note.txt").write_text("hi")
    guarded = make_guarded_open([str(tmp_path)])
    with guarded(str(tmp_path / "note.txt")) as handle:
        assert handle.read() == "hi"


def test_guarded_open_blocks_other_files(tmp_path):
    guarded = make_guarded_open([str(tmp_path)])
    with pytest.raises(SandboxSecurityError, match="denied"):
        guarded("/etc/passwd")


def test_guarded_open_rejects_file_descriptors(tmp_path):
    guarded = make_guarded_open([str(tmp_path)])
    with pytest.raises(SandboxSecurityError, match="only file paths"):
        guarded(0)


@pytest.mark.parametrize(
    "code",
    [
        "().__class__.__bases__[0].__subclasses__()",
        "def f():\n    pass\nf.__globals__",
        "__builtins__",
        "x.__mro__",
        "def f():\n    pass\nf.__code__",
        "e.tb_frame",
    ],
)
def test_ast_blocks_escape_names(code):
    with pytest.raises(SandboxSecurityError, match="not allowed"):
        validate_ast(code)


def test_ast_reports_line_number():
    with pytest.raises(SandboxSecurityError, match="line 2"):
        validate_ast("x = 1\ny = x.__class__")


def test_ast_allows_normal_code():
    validate_ast("import math\nresult = [math.sqrt(n) for n in range(5)]\nprint(result)")


def test_ast_syntax_error_passes_through():
    with pytest.raises(SyntaxError):
        validate_ast("def broken(")
