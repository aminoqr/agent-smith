"""Run code inside a namespace, REPL style."""
import ast
import contextlib
import io
import signal
import traceback
from dataclasses import dataclass
from typing import Optional
from typing import Callable, Optional
from agent_smith.sandbox.audit import enforcing
from agent_smith.models import SandboxConfig
from agent_smith.sandbox.errors import FinalAnswerSignal, SandboxTimeout
from agent_smith.sandbox.policy import make_safe_builtins, validate_ast

SANDBOX_FILENAME = "<sandbox>"


@dataclass
class ExecResult:
    """What one execution produced."""

    stdout: str = ""
    error: Optional[str] = None
    value: Optional[str] = None
    final_answer: Optional[str] = None


def final_answer(answer: str):
    """Record the model's answer and end the session.

    Injected into the sandbox namespace, never offered as an MCP tool: the
    sandbox itself must own the end of the session.
    """
    if not isinstance(answer, str):
        raise TypeError(
            "final_answer() expects the answer as a string, got "
            f"{type(answer).__name__}"
        )
    raise FinalAnswerSignal(answer)


def new_namespace(config: SandboxConfig) -> dict:
    """A fresh namespace whose builtins are the restricted ones."""
    return {"__builtins__": make_safe_builtins(config), "final_answer": final_answer}


class _StreamingBuffer(io.StringIO):
    """A text buffer that also reports every write to `on_write`."""

    def __init__(self, on_write: Optional[Callable[[str], None]]):
        super().__init__()
        self._on_write = on_write

    def write(self, text: str) -> int:
        if text and self._on_write:
            self._on_write(text)
        return super().write(text)



def _raise_timeout(signum, frame):
    raise SandboxTimeout()


@contextlib.contextmanager
def _time_limit(seconds: Optional[float]):
    """Interrupt the code running inside the block after `seconds`."""
    if seconds is None:
        yield
        return
    previous = signal.signal(signal.SIGALRM, _raise_timeout)
    signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)


@contextlib.contextmanager
def timer_paused():
    """Stop the execution clock inside this block.

    MCP tools are a separate security domain and are not counted against the
    sandbox timeout, so the alarm is cancelled and then restored with the time
    that was left on it.
    """
    remaining = signal.setitimer(signal.ITIMER_REAL, 0)[0]
    try:
        yield
    finally:
        if remaining > 0:
            signal.setitimer(signal.ITIMER_REAL, remaining)


def execute(
    code: str,
    namespace: dict,
    timeout_seconds: Optional[float] = None,
    on_output: Optional[Callable[[str], None]] = None,
) -> ExecResult:

    """Run `code` in `namespace` and return its output, error and last value.

    Like a REPL: if the last statement is an expression, its repr is returned
    in `value`. If `timeout_seconds` is set, code still running after that
    long is interrupted; variables created so far are kept. KeyboardInterrupt
    and SystemExit are not caught.
    """
    stdout = _StreamingBuffer(on_output)
    result = ExecResult()
    try:
        validate_ast(code)
        tree = ast.parse(code, filename=SANDBOX_FILENAME)
        last = None
        if tree.body and isinstance(tree.body[-1], ast.Expr):
            last = ast.Expression(tree.body.pop().value)
        with contextlib.redirect_stdout(stdout), _time_limit(timeout_seconds), enforcing():
            exec(compile(tree, SANDBOX_FILENAME, "exec"), namespace)
            if last is not None:
                value = eval(compile(last, SANDBOX_FILENAME, "eval"), namespace)
                if value is not None:
                    result.value = repr(value)
    except FinalAnswerSignal as submitted:
        result.final_answer = submitted.answer
    except SandboxTimeout:
        result.error = (
            f"Timeout: execution took longer than {timeout_seconds} s. "
            "Output printed so far was kept; variables were kept."
        )
    except Exception as exc:
        result.error = _format_error(exc)
    result.stdout = stdout.getvalue()
    return result


def _format_error(exc: Exception) -> str:
    """One-line description of an error, with the line number in the model's code."""    
    if isinstance(exc, MemoryError):
        return "MemoryError: the sandbox memory limit was exceeded"
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


