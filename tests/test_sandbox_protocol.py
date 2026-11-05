"""Tests for the controller/worker message format."""
from agent_smith.sandbox.protocol import decode, encode


def test_round_trip():
    message = {"type": "exec", "code": "print('hi')\n", "n": 3}
    assert decode(encode(message)) == message


def test_encode_gives_bytes():
    assert isinstance(encode({"a": 1}), bytes)
