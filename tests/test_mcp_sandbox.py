"""Tests for MCP tools used from inside the sandbox.

Most of them point the sandbox at `tests/fixtures/dummy_server.py`, a server
it has never seen, because that is what the grader does: dynamic discovery is
the feature, not a convenience.
"""
import json
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

from agent_smith.mcp_client.bridge import MCPBridge, MCPError
from agent_smith.mcp_client.schema import ToolSpec, render_signature, signature_of
from agent_smith.models import SandboxConfig
from agent_smith.sandbox.controller import Sandbox, truncate
from agent_smith.sandbox.manual import build_manual
from agent_smith.sandbox.tools import resolve_names

ROOT = Path(__file__).parent.parent
DUMMY = f"{sys.executable} {ROOT / 'tests' / 'fixtures' / 'dummy_server.py'}"
MBPP = f"{sys.executable} {ROOT / 'mcp_tools_mbpp.py'}"


@pytest.fixture(scope="module")
def dummy_bridge():
    with MCPBridge().connect_stdio(DUMMY) as bridge:
        yield bridge


@pytest.fixture(scope="module")
def dummy_sandbox(dummy_bridge):
    with Sandbox(SandboxConfig(max_execution_time_seconds=2), dummy_bridge) as sandbox:
        yield sandbox


# ----- the schema translation (no server needed) -----------------------


def test_odd_tool_names_become_identifiers():
    spec = ToolSpec(name="get-weather", schema={})
    assert spec.python_name == "get_weather"


def test_signature_keeps_types_and_defaults():
    spec = ToolSpec(
        name="search",
        schema={
            "properties": {
                "query": {"type": "string"},
                "limit": {"type": "integer", "default": 5},
            },
            "required": ["query"],
        },
    )
    assert render_signature(spec) == "search(query: str, limit: int = 5)"
    assert str(signature_of(spec)) == "(query: str, limit: int = 5)"


def test_required_parameters_come_first():
    spec = ToolSpec(
        name="t",
        schema={
            "properties": {"a": {"type": "string", "default": "x"}, "b": {"type": "string"}},
            "required": ["b"],
        },
    )
    assert render_signature(spec) == "t(b: str, a: str = 'x')"


def test_a_server_cannot_steal_the_sandbox_names():
    specs = resolve_names([ToolSpec(name="final_answer"), ToolSpec(name="read_resource")])
    assert [spec.python_name for spec in specs] == ["mcp_final_answer", "mcp_read_resource"]


# ----- the bridge -----------------------------------------------------


def test_bridge_lists_what_the_server_offers(dummy_bridge):
    names = {spec.name for spec in dummy_bridge.list_tools()}
    assert {"get-weather", "search", "explode"} <= names


def test_bridge_refuses_calls_after_close():
    bridge = MCPBridge().connect_stdio(DUMMY)
    bridge.close()
    with pytest.raises(MCPError):
        bridge.invoke("list_resources", {})


def test_connecting_to_nothing_fails_clearly():
    with pytest.raises(MCPError, match="could not connect"):
        MCPBridge().connect_stdio(f"{sys.executable} no_such_server.py")


# ----- tools inside the sandbox ---------------------------------------


def test_a_tool_is_an_ordinary_python_function(dummy_sandbox):
    assert dummy_sandbox.execute("get_weather('Paris')").value == "'Paris: sunny for 3 days'"


def test_defaults_and_keywords_work(dummy_sandbox):
    assert dummy_sandbox.execute("search('cats', limit=2)").value == (
        "'cats result 0\\ncats result 1'"
    )


def test_wrong_arguments_fail_like_python(dummy_sandbox):
    assert "missing a required argument" in dummy_sandbox.execute("get_weather()").error


def test_tool_results_can_be_used_in_a_loop(dummy_sandbox):
    code = "names = [get_weather(city) for city in ('Lyon', 'Nice')]\nlen(names)"
    assert dummy_sandbox.execute(code).value == "2"


def test_a_failing_tool_raises_a_catchable_error(dummy_sandbox):
    result = dummy_sandbox.execute(
        "try:\n    explode()\nexcept Exception as error:\n    print(type(error).__name__)"
    )
    assert result.stdout.strip() == "ToolError"
    assert result.error is None


def test_the_sandbox_keeps_its_own_final_answer(dummy_sandbox):
    assert dummy_sandbox.execute("final_answer('mine')").final_answer == "mine"
    assert "swallowed" in dummy_sandbox.execute("mcp_final_answer('theirs')").value


def test_resources_and_prompts_are_reachable(dummy_sandbox):
    assert dummy_sandbox.execute("list_resources()").value == "['dummy://notes']"
    assert dummy_sandbox.execute("read_resource('dummy://notes')").value == "'dummy notes'"
    assert dummy_sandbox.execute("list_prompts()").value == "['dummy_prompt']"
    assert "cats" in dummy_sandbox.execute(
        "get_prompt('dummy_prompt', {'topic': 'cats'})"
    ).value


def test_tool_time_is_not_sandbox_time(dummy_sandbox):
    """A 3 s tool call under a 2 s limit must survive: the server is its own domain."""
    started = time.monotonic()
    result = dummy_sandbox.execute("sleep_for(3)\n'done'")
    assert result.value == "'done'"
    assert result.error is None
    assert time.monotonic() - started >= 3


def test_the_timeout_still_applies_to_python_code(dummy_sandbox):
    assert "Timeout" in dummy_sandbox.execute("while True: pass").error


def test_a_flood_of_tool_output_is_truncated(dummy_sandbox):
    value = dummy_sandbox.execute("flood(100000)").value
    assert "output truncated" in value
    assert len(value) < 10_300


def test_truncate_keeps_the_start_and_the_end():
    text = "A" * 100 + "B" * 100
    cut = truncate(text, 60)
    assert cut.startswith("A")
    assert cut.endswith("B")
    assert "kept 60 of 200 characters" in cut


# ----- the generated manual -------------------------------------------


def test_the_manual_describes_the_server_it_is_connected_to(dummy_sandbox):
    manual = build_manual(dummy_sandbox.config, dummy_sandbox.tool_specs, mcp_connected=True)
    assert "get_weather(city: str, days: int = 3)" in manual
    assert "final_answer(answer: str)" in manual
    assert "run_tests" not in manual
    assert "read_resource(uri: str)" in manual


def test_the_manual_works_without_any_server():
    manual = build_manual(SandboxConfig())
    assert "final_answer(answer: str)" in manual
    assert "list_resources()" not in manual
    assert "no network access" in manual


# ----- without a server, and over HTTP --------------------------------


def test_without_a_server_there_are_no_tools():
    with Sandbox(SandboxConfig()) as sandbox:
        assert "NameError" in sandbox.execute("list_resources()").error


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def test_the_same_tools_work_over_http():
    port = _free_port()
    server = subprocess.Popen(
        [sys.executable, str(ROOT / "mcp_tools_mbpp.py"), "--transport", "http", "--port", str(port)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        cwd=str(ROOT),
    )
    try:
        bridge = MCPBridge()
        for _ in range(40):
            try:
                bridge.connect_http(f"http://127.0.0.1:{port}/mcp")
                break
            except MCPError:
                time.sleep(0.5)
        else:
            pytest.fail("the HTTP server never became reachable")
        try:
            with Sandbox(SandboxConfig(), bridge) as sandbox:
                result = sandbox.execute(
                    'run_tests("def add(a, b): return a + b", ["assert add(1, 2) == 3"])'
                )
            assert json.loads(result.value.strip("'")) == {
                "success": True,
                "output": "1/1 assertions passed",
            }
        finally:
            bridge.close()
    finally:
        server.terminate()
        server.wait(timeout=10)
