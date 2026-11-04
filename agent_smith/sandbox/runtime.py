"""Run code inside a namespace, REPL style."""
import ast
import contextlib
import io
import traceback
from dataclasses import dataclass
from typing import Optional

from agent_smith.models import SandboxConfig
from agent_smith.sandbox.policy import make_safe_builtins, validate_ast

SANDBOX_FILENAME = "<sandbox>"


@dataclass
class ExecResult:
    """What one execution produced."""

    stdout: str = ""
    error: Optional[str] = None
    value: Optional[str] = None


def new_namespace(config: SandboxConfig) -> dict:
    """A fresh namespace whose builtins are the restricted ones."""
    return {"__builtins__": make_safe_builtins(config)}


def execute(code: str, namespace: dict) -> ExecResult:
    """Run `code` in `namespace` and return its output, error and last value.

    Like a REPL: if the last statement is an expression, its repr is returned
    in `value`. KeyboardInterrupt and SystemExit are not caught.
    """
    stdout = io.StringIO()
    result = ExecResult()
    try:
        validate_ast(code)
        tree = ast.parse(code, filename=SANDBOX_FILENAME)
        last = None
        if tree.body and isinstance(tree.body[-1], ast.Expr):
            last = ast.Expression(tree.body.pop().value)
        with contextlib.redirect_stdout(stdout):
            exec(compile(tree, SANDBOX_FILENAME, "exec"), namespace)
            if last is not None:
                value = eval(compile(last, SANDBOX_FILENAME, "eval"), namespace)
                if value is not None:
                    result.value = repr(value)
    except Exception as exc:
        result.error = _format_error(exc)
    result.stdout = stdout.getvalue()
    return result


def _format_error(exc: Exception) -> str:
    """One-line description of an error, with the line number in the model's code."""
    if isinstance(exc, SyntaxError):
        line = exc.lineno
    else:
        frames = [
            frame
            for frame in traceback.extract_tb(exc.__traceback__)
            if frame.filename == SANDBOX_FILENAME
        ]
        line = frames[-1].lineno if frames else None
    where = f" [line {line}]" if line else ""
    return f"{type(exc).__name__}: {exc}{where}"
