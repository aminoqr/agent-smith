"""Exceptions raised by the sandbox."""


class SandboxSecurityError(Exception):
    """Code tried to do something the sandbox policy forbids."""

class SandboxTimeout(BaseException):
    """Raised inside the worker when code runs longer than its time limit.

    It derives from BaseException so that `except Exception` in the model's
    code cannot swallow it.
    """
