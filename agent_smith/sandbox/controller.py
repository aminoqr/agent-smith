"""The sandbox controller: starts a worker process and sends it code to run."""
import multiprocessing
from dataclasses import dataclass
from typing import Optional
import time

from agent_smith.models import SandboxConfig
from agent_smith.sandbox.protocol import decode, encode
from agent_smith.sandbox.worker import worker_main
HARD_KILL_GRACE_SECONDS = 2
MAX_OUTPUT_CHARS = 10_000

@dataclass
class ExecutionResult:
    """What the worker sent back for one piece of code."""

    stdout: str = ""
    error: Optional[str] = None
    value: Optional[str] = None
    final_answer: Optional[str] = None


def _join_output(chunks: list[str]) -> str:
    """Glue chunks together, cutting the text at MAX_OUTPUT_CHARS with a note."""
    text = "".join(chunks)
    if len(text) <= MAX_OUTPUT_CHARS:
        return text
    return (
        text[:MAX_OUTPUT_CHARS]
        + f"\n[output truncated: kept {MAX_OUTPUT_CHARS} of {len(text)} characters]"
    )


def _control_exception(message: dict) -> BaseException:
    """Rebuild the SystemExit or KeyboardInterrupt the model's code raised."""
    if message["exception"] == "KeyboardInterrupt":
        return KeyboardInterrupt()
    return SystemExit(message["code"])


class Sandbox:
    """Runs code in a separate worker process that keeps its variables."""

    def __init__(self, config: SandboxConfig):
        self.config = config
        self._process = None
        self._connection = None

    def start(self) -> None:
        """Start the worker process."""
        context = multiprocessing.get_context("spawn")
        self._connection, worker_end = context.Pipe()
        self._process = context.Process(
            target=worker_main,
            args=(worker_end, self.config.model_dump_json()),
            daemon=True,
        )
        self._process.start()
        worker_end.close()

    def execute(self, code: str) -> ExecutionResult:
        """Run `code` in the worker and return what it produced."""
        limit = self.config.max_execution_time_seconds
        deadline = time.monotonic() + limit + HARD_KILL_GRACE_SECONDS
        chunks: list[str] = []
        control = None
        try:
            self._connection.send_bytes(encode({"type": "exec", "code": code}))
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0 or not self._connection.poll(remaining):
                    self._restart()
                    return ExecutionResult(
                        stdout=_join_output(chunks),
                        error=(
                            f"Timeout: execution took longer than {limit} s and could "
                            "not be interrupted. The sandbox was restarted, so all "
                            "variables were lost."
                        ),
                    )
                message = decode(self._connection.recv_bytes())
                if message["type"] == "stdout":
                    chunks.append(message["data"])
                elif message["type"] == "control":
                    control = _control_exception(message)
                    break
                else:
                    return ExecutionResult(
                        stdout=_join_output(chunks),
                        error=message["error"],
                        value=message["value"],
                        final_answer=message["final_answer"],
                    )
        except KeyboardInterrupt:
            # Ctrl+C reached us while we waited: stop the worker, then let it through.
            self._kill_worker()
            raise
        except (EOFError, OSError):
            self._restart()
            return ExecutionResult(
                stdout=_join_output(chunks),
                error=(
                    "The sandbox worker died unexpectedly (a crash or running out "
                    "of memory). The sandbox was restarted, so all variables were lost."
                ),
            )
        # Only reached when the model's code raised SystemExit or KeyboardInterrupt.
        raise control



    def _kill_worker(self) -> None:
        """Kill the worker process and wait until it is gone."""
        self._process.kill()
        self._process.join()

    def _restart(self) -> None:
        """Kill the worker and start a fresh one (its variables are lost)."""
        self._kill_worker()
        self._connection.close()
        self.start()


    def close(self) -> None:
        """Ask the worker to stop, and kill it if it does not."""
        if self._process is None:
            return
        try:
            self._connection.send_bytes(encode({"type": "shutdown"}))
        except OSError:
            pass
        self._process.join(timeout=2)
        if self._process.is_alive():
            self._process.kill()
            self._process.join()
        self._connection.close()
        self._process = None

    def __enter__(self) -> "Sandbox":
        self.start()
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()
