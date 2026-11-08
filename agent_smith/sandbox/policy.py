"""Rules that decide what sandboxed code may do."""
import builtins
import sys
import os
import ast
from fnmatch import fnmatchcase
from typing import List

from agent_smith.sandbox.errors import SandboxSecurityError
from agent_smith.models import SandboxConfig


def is_import_allowed(name: str, allowlist: List[str]) -> bool:
    """Return True if module `name` matches an entry of the allowlist.

    An entry is a glob pattern: "re" allows exactly "re", and
    "collections.*" allows "collections.abc" but not "collections" itself.
    """
    return any(fnmatchcase(name, pattern) for pattern in allowlist)


# Modules that Python's own C code imports behind the scenes on behalf of an
# allowed module (datetime.strptime needs _strptime, datetime.strftime needs time).
# Such an import arrives with fromlist == [] (an empty list); a `import x`
# statement passes None and `from x import y` passes a tuple, so the model's own
# `import time` is still refused.
HIDDEN_IMPORTS = frozenset({"_strptime", "time"})


def _is_hidden_import(name: str, fromlist) -> bool:
    return name in HIDDEN_IMPORTS and isinstance(fromlist, list) and not fromlist


def make_guarded_import(allowlist: List[str]):
    """Build a replacement for __import__ that enforces the allowlist."""
    real_import = builtins.__import__

    def guarded_import(name, globals=None, locals=None, fromlist=(), level=0):
        if level > 0:
            raise SandboxSecurityError("relative imports are not allowed in the sandbox")
        if not _is_hidden_import(name, fromlist) and not is_import_allowed(name, allowlist):
            raise SandboxSecurityError(f"import of '{name}' is not allowed")
        module = real_import(name, globals, locals, fromlist, level)
        for item in fromlist or ():
            submodule = f"{name}.{item}"
            if submodule in sys.modules and not is_import_allowed(submodule, allowlist):
                raise SandboxSecurityError(f"import of '{submodule}' is not allowed")
        return module

    return guarded_import

DISABLED_BUILTINS = (
    "eval", "exec", "compile", "input", "breakpoint",
    "globals", "locals", "vars", "exit", "quit", "help",
)


def _disabled(name: str):
    """Build a stand-in for a forbidden builtin that always raises."""
    def stub(*args, **kwargs):
        raise SandboxSecurityError(f"builtin '{name}' is disabled in the sandbox")
    return stub


def make_safe_builtins(config: SandboxConfig) -> dict:
    """Return a copy of the builtins with dangerous ones disabled."""
    safe = dict(vars(builtins))
    for name in DISABLED_BUILTINS:
        safe[name] = _disabled(name)
    safe["__import__"] = make_guarded_import(config.authorized_imports)
    safe["open"] = make_guarded_open(config.allowed_directories)
    for name in ATTRIBUTE_BUILTINS:
        safe[name] = _guard_attribute_builtin(getattr(builtins, name), name)

    return safe

def is_path_allowed(path: str, allowed_directories: List[str]) -> bool:
    """Return True if `path` resolves to a location inside an allowed directory."""
    real = os.path.realpath(path)
    for directory in allowed_directories:
        allowed = os.path.realpath(directory)
        if os.path.commonpath([real, allowed]) == allowed:
            return True
    return False


def make_guarded_open(allowed_directories: List[str]):
    """Build a replacement for open() that only touches allowed directories."""
    real_open = builtins.open

    def guarded_open(file, *args, **kwargs):
        if not isinstance(file, (str, bytes, os.PathLike)):
            raise SandboxSecurityError("open() accepts only file paths in the sandbox")
        path = os.fsdecode(file)
        if not is_path_allowed(path, allowed_directories):
            allowed = ", ".join(allowed_directories)
            raise SandboxSecurityError(f"access to '{path}' denied (allowed: {allowed})")
        return real_open(file, *args, **kwargs)

    return guarded_open


FORBIDDEN_NAMES = frozenset({
    "__subclasses__", "__bases__", "__base__", "__mro__", "__class__",
    "__globals__", "__builtins__", "__code__", "__closure__",
    "f_globals", "f_builtins", "f_back", "f_locals", "tb_frame", "gi_frame",
    "cr_frame", "ag_frame", "__self__", "__import__", "__getattribute__",
    "__loader__", "__spec__",
})

# getattr("__cl" + "ass__") builds the name at run time, so the AST check cannot
# see it. These wrappers check the final name instead.
ATTRIBUTE_BUILTINS = ("getattr", "hasattr", "setattr", "delattr")


def _guard_attribute_builtin(real_function, name: str):
    """Wrap getattr/hasattr/setattr/delattr so they refuse forbidden names."""
    def guarded(obj, attribute, *args):
        if isinstance(attribute, str) and attribute in FORBIDDEN_NAMES:
            raise SandboxSecurityError(f"access to '{attribute}' is not allowed")
        return real_function(obj, attribute, *args)
    guarded.__name__ = name
    return guarded


def validate_ast(code: str) -> None:
    """Raise SandboxSecurityError if code uses a name that can escape the sandbox.

    A SyntaxError from ast.parse is not caught here: the caller reports it.
    """
    for node in ast.walk(ast.parse(code)):
        if isinstance(node, ast.Attribute):
            name = node.attr
        elif isinstance(node, ast.Name):
            name = node.id
        else:
            continue
        if name in FORBIDDEN_NAMES:
            raise SandboxSecurityError(
                f"access to '{name}' is not allowed (line {node.lineno})"
            )
