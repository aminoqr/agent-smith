"""Tests for the MBPP MCP server, called directly and over the protocol."""
import asyncio
import json
import sys
from pathlib import Path

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

import mcp_tools_mbpp
from agent_smith.models import SandboxConfig

ROOT = Path(__file__).parent.parent
GOOD = "def add(a, b): return a + b"
TESTS = ["assert add(1, 2) == 3", "assert add(0, 0) == 0"]


def test_correct_solution_passes_every_assertion():
    assert mcp_tools_mbpp.check_solution(GOOD, TESTS) == {
        "success": True,
        "output": "2/2 assertions passed",
    }


def test_wrong_solution_names_the_failing_assertion():
    result = mcp_tools_mbpp.check_solution("def add(a, b): return a - b", TESTS)
    assert result["success"] is False
    assert "1/2 assertions passed" in result["output"]
    assert "assert add(1, 2) == 3" in result["output"]


def test_code_that_cannot_run_is_reported_once():
    result = mcp_tools_mbpp.check_solution("def add(a, b) return a + b", TESTS)
    assert result["success"] is False
    assert "the code itself failed" in result["output"]
    assert "SyntaxError" in result["output"]


def test_without_assertions_nothing_is_claimed():
    result = mcp_tools_mbpp.check_solution(GOOD, [])
    assert result == {"success": False, "output": "no assertions were given to run"}


def test_candidate_code_runs_inside_the_sandbox():
    result = mcp_tools_mbpp.check_solution("import os\nos.listdir('/')", ["assert True"])
    assert result["success"] is False
    assert "not allowed" in result["output"]


def test_a_hanging_assertion_times_out_and_stops_the_run(monkeypatch):
    quick = SandboxConfig(max_execution_time_seconds=1)
    monkeypatch.setattr(mcp_tools_mbpp, "SANDBOX_CONFIG", quick)
    result = mcp_tools_mbpp.check_solution(
        "def add(a, b):\n    while True:\n        pass", TESTS
    )
    assert result["success"] is False
    assert "Timeout" in result["output"]
    assert "the remaining assertions were not run" in result["output"]


def test_the_tool_returns_json_text():
    payload = json.loads(mcp_tools_mbpp.run_tests(GOOD, TESTS))
    assert payload == {"success": True, "output": "2/2 assertions passed"}


async def _ask_the_server():
    """Start the server as its own process and use it like any MCP client would."""
    parameters = StdioServerParameters(
        command=sys.executable, args=["mcp_tools_mbpp.py"], cwd=str(ROOT)
    )
    async with stdio_client(parameters) as (incoming, outgoing):
        async with ClientSession(incoming, outgoing) as session:
            await session.initialize()
            tools = await session.list_tools()
            resources = await session.list_resources()
            prompts = await session.list_prompts()
            called = await session.call_tool(
                "run_tests", {"code": GOOD, "test_list": TESTS}
            )
            return tools, resources, prompts, called


@pytest.fixture(scope="module")
def served():
    return asyncio.run(_ask_the_server())


def test_server_advertises_run_tests(served):
    tools, _, _, _ = served
    tool = next(item for item in tools.tools if item.name == "run_tests")
    schema = tool.inputSchema
    assert schema["properties"]["code"]["type"] == "string"
    assert schema["properties"]["test_list"]["type"] == "array"
    assert sorted(schema["required"]) == ["code", "test_list"]


def test_server_exposes_a_resource_and_a_prompt(served):
    _, resources, prompts, _ = served
    assert "mbpp://guidelines" in [str(item.uri) for item in resources.resources]
    assert "solve_mbpp" in [item.name for item in prompts.prompts]


def test_tool_call_over_the_protocol_returns_the_json(served):
    _, _, _, called = served
    assert called.isError is False
    text = "\n".join(item.text for item in called.content if item.type == "text")
    assert json.loads(text) == {"success": True, "output": "2/2 assertions passed"}
