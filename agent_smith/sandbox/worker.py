"""The sandbox worker: a separate process that runs code on request."""
from agent_smith.models import SandboxConfig
from agent_smith.sandbox.protocol import decode, encode
from agent_smith.sandbox.runtime import execute, new_namespace


def worker_main(connection, config_json: str) -> None:
    """Serve exec requests on `connection` until told to shut down."""
    config = SandboxConfig.model_validate_json(config_json)
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
        result = execute(
            message["code"],
            namespace,
            config.max_execution_time_seconds,
            on_output=send_output,
        )
        connection.send_bytes(
            encode({"type": "result", "error": result.error, "value": result.value})
        )
