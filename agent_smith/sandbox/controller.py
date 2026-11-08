"""The sandbox controller: starts a worker process and sends it code to run."""
import json
import multiprocessing
from dataclasses import dataclass
from typing import List, Optional
import time

from agent_smith.mcp_client.schema import ToolSpec
from agent_smith.models import SandboxConfig
from agent_smith.sandbox.protocol import decode, encode
from agent_smith.sandbox.tools import resolve_names
from agent_smith.sandbox.worker import worker_main

HARD_KILL_GRACE_SECONDS = 2
MAX_OUTPUT_CHARS = 10_000
MAX_TOOL_OUTPUT_CHARS = 10_000

@dataclass
class ExecutionResult:
    """What the worker sent back for one piece of code."""

    stdout: str = ""
    error: Optional[str] = None
    value: Optional[str] = None
    final_answer: Optional[str] = None


def truncate(text: str, limit: int) -> str:
    """Keep the start and the end of a long text, and say what was dropped.

    The end matters: an error message or a test summary is usually there.
    """
    if len(text) <= limit:
        return text
    head = limit * 2 // 3
    tail = limit - head
    note = f"\n[output truncated: kept {limit} of {len(text)} characters]\n"
    return text[:head] + note + text[-tail:]


def _join_output(chunks: list[str]) -> str:
    """Glue the streamed chunks together, capped with a visible note."""
    return truncate("".join(chunks), MAX_OUTPUT_CHARS)


def _control_exception(message: dict) -> BaseException:
    """Rebuild the SystemExit or KeyboardInterrupt the model's code raised."""
    if message["exception"] == "KeyboardInterrupt":
        return KeyboardInterrupt()
    return SystemExit(message["code"])


class Sandbox:
    """Runs code in a separate worker process that keeps its variables."""

    def __init__(self, config: SandboxConfig, bridge=None):
        self.config = config
        self._bridge = bridge
        self._tool_specs: Optional[List[ToolSpec]] = None
        self._process = None
        self._connection = None

    @property
    def tool_specs(self) -> List[ToolSpec]:
        """What the connected server offers, as the worker sees it."""
        return list(self._tool_specs or [])

    def start(self) -> None:
        """Start the worker process, telling it about the MCP tools."""
        if self._bridge is not None and self._tool_specs is None:
            self._tool_specs = resolve_names(self._bridge.list_tools())
        context = multiprocessing.get_context("spawn")
        self._connection, worker_end = context.Pipe()
        self._process = context.Process(
            target=worker_main,
            args=(worker_end, self.config.model_dump_json(), self._mcp_json()),
            daemon=True,
        )
        self._process.start()
        worker_end.close()

    def _mcp_json(self) -> str:
        """The tool list the worker needs to build its stubs."""
        if self._bridge is None:
            return ""
        return json.dumps(
            {"connected": True, "tools": [spec.as_dict() for spec in self._tool_specs or []]}
        )

    def _serve_mcp(self, message: dict) -> float:
        """Relay one request to the MCP server. Returns how long it took.

        The worker is blocked waiting for this answer, and the time spent here
        is given back to the execution deadline: tools are outside the
        sandbox's time limit (the server is its own security domain).
        """
        started = time.monotonic()
        try:
            if self._bridge is None:
                raise RuntimeError("no MCP server is connected to this sandbox")
            reply = {"ok": True, "text": truncate(self._bridge.invoke(
                message["method"], message["payload"]
            ), MAX_TOOL_OUTPUT_CHARS)}
        except Exception as exc:
            # The message is shown to the model, so keep it one readable line.
            reply = {"ok": False, "text": str(exc) or type(exc).__name__}
        reply["type"] = "mcp_result"
        self._connection.send_bytes(encode(reply))
        return time.monotonic() - started

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
                elif message["type"] == "mcp":
                    deadline += self._serve_mcp(message)
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
