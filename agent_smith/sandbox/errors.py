"""Exceptions raised by the sandbox."""


class SandboxSecurityError(Exception):
    """Code tried to do something the sandbox policy forbids."""

class FinalAnswerSignal(BaseException):
    """Raised by final_answer() to end the session with an answer.

    It derives from BaseException so that `except Exception` in the model's
    code cannot swallow the answer.
    """

    def __init__(self, answer: str):
        super().__init__(answer)
        self.answer = answer


class SandboxTimeout(BaseException):
    """Raised inside the worker when code runs longer than its time limit.

    It derives from BaseException so that `except Exception` in the model's
    code cannot swallow it.
    """
