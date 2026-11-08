"""Ask an MCP server what it offers, then try its run_tests tool.

Usage: uv run python scripts/mcp_probe.py "python mcp_tools_mbpp.py"

This is a plain MCP client: it knows the protocol, not our agent. Anything it
can do, the grader's own client can do to our server too.
"""
import asyncio
import shlex
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

GOOD = "def add(a, b): return a + b"
BAD = "def add(a, b): return a - b"
SLOW = "def add(a, b):\n    while True:\n        pass"
TESTS = ["assert add(1, 2) == 3", "assert add(0, 0) == 0"]


def text_of(result) -> str:
    """Pull the plain text out of a tool result's content list."""
    return "\n".join(item.text for item in result.content if item.type == "text")


async def probe(command: str) -> None:
    parts = shlex.split(command)
    parameters = StdioServerParameters(command=parts[0], args=parts[1:])
    async with stdio_client(parameters) as (incoming, outgoing):
        async with ClientSession(incoming, outgoing) as session:
            info = await session.initialize()
            print(f"connected to {info.serverInfo.name} (protocol {info.protocolVersion})")

            tools = await session.list_tools()
            print("\ntools:")
            for tool in tools.tools:
                required = tool.inputSchema.get("required", [])
                print(f"  {tool.name}({', '.join(required)}): {tool.description.splitlines()[0]}")
                print(f"    input schema: {tool.inputSchema.get('properties')}")

            resources = await session.list_resources()
            print("\nresources:", [str(item.uri) for item in resources.resources])
            prompts = await session.list_prompts()
            print("prompts:", [item.name for item in prompts.prompts])

            print("\nrun_tests with a correct solution:")
            print(" ", text_of(await session.call_tool("run_tests", {"code": GOOD, "test_list": TESTS})))
            print("run_tests with a wrong solution:")
            print(" ", text_of(await session.call_tool("run_tests", {"code": BAD, "test_list": TESTS})))
            print("run_tests with code that never finishes:")
            print(" ", text_of(await session.call_tool("run_tests", {"code": SLOW, "test_list": TESTS})))


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__.strip(), file=sys.stderr)
        return 2
    asyncio.run(probe(sys.argv[1]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
