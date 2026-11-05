"""Messages exchanged between the sandbox controller and its worker process."""
import json


def encode(message: dict) -> bytes:
    """Turn a message into bytes for the pipe (JSON, never pickle)."""
    return json.dumps(message).encode("utf-8")


def decode(data: bytes) -> dict:
    """Turn bytes from the pipe back into a message."""
    return json.loads(data.decode("utf-8"))
