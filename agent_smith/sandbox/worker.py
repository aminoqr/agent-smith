"""The sandbox worker: a separate process that runs code on request."""
import os
import resource
import signal

from agent_smith.models import SandboxConfig
from agent_smith.sandbox import audit
from agent_smith.sandbox.protocol import decode, encode
from agent_smith.sandbox.runtime import execute, new_namespace


def _limit_memory(megabytes: int) -> None:
    """Ask the operating system to stop this process from using more memory."""
    limit = megabytes * 1024 * 1024
    resource.setrlimit(resource.RLIMIT_AS, (limit, limit))


def _scrub_environment() -> None:
    """Remove every environment variable from this process.

    The worker needs none of them, and the agent's environment holds the API
    keys, which the model's code must never be able to read.
    """
    os.environ.clear()


def _control_message(exc: BaseException) -> dict:
    """Describe a SystemExit or KeyboardInterrupt so the controller can re-raise it."""
    code = getattr(exc, "code", None)
    if not (code is None or isinstance(code, (int, str))):
        code = str(code)
    return {"type": "control", "exception": type(exc).__name__, "code": code}


def worker_main(connection, config_json: str) -> None:
    """Serve exec requests on `connection` until told to shut down."""
    # Ctrl+C in the terminal reaches every process in the group. The controller
    # decides what to do about it (it kills this worker), so we ignore it here.
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    config = SandboxConfig.model_validate_json(config_json)
    _limit_memory(config.max_memory_mb)
    _scrub_environment()
    audit.install(config.allowed_directories)
    namespace = new_namespace(config)

    def send_output(text: str) -> None:
        connection.send_bytes(encode({"type": "stdout", "data": text}))

    while True:
        try:
            message = decode(connection.recv_bytes())
        except EOFError:
            break
        if message["type"] == "shutdown":
            break
        try:
            result = execute(
                message["code"],
                namespace,
                config.max_execution_time_seconds,
                on_output=send_output,
            )
        except (SystemExit, KeyboardInterrupt) as exc:
            connection.send_bytes(encode(_control_message(exc)))
            continue
        connection.send_bytes(
            encode({"type": "result", "error": result.error, "value": result.value})
        )
