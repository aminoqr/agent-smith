"""Extract from four model styles, then really run the result in the sandbox.

Usage: uv run python scripts/extraction_probe.py

Proof that the conversion is not just string shaping: every format ends up
calling the same MCP tool inside the sandbox and getting the same answer.
"""
from agent_smith.agent import extract
from agent_smith.mcp_client.bridge import MCPBridge
from agent_smith.models import SandboxConfig
from agent_smith.sandbox.controller import Sandbox

SOLUTION = "def add(a, b): return a + b"
TESTS = ["assert add(1, 2) == 3"]

REPLIES = {
    "python": (
        "Thought: test it.\n```python\n"
        f"solution = {SOLUTION!r}\n"
        f"print(run_tests(solution, {TESTS!r}))\n"
        "```<end_code>"
    ),
    "xml": (
        '<invoke name="run_tests">'
        f"<parameter name=\"code\">{SOLUTION}</parameter>"
        f'<parameter name="test_list">["assert add(1, 2) == 3"]</parameter>'
        "</invoke>"
    ),
    "hermes": (
        '<tool_call>{"name": "run_tests", "arguments": '
        f'{{"code": "{SOLUTION}", "test_list": ["assert add(1, 2) == 3"]}}}}</tool_call>'
    ),
    "react": (
        "Thought: test it.\nAction: run_tests\n"
        f'Action Input: {{"code": "{SOLUTION}", "test_list": ["assert add(1, 2) == 3"]}}'
    ),
    "final answer, hermes style": (
        '<tool_call>{"name": "final_answer", "arguments": {"answer": "def add(a, b): return a + b"}}'
        "</tool_call>"
    ),
}


def main() -> None:
    bridge = MCPBridge().connect_stdio("python mcp_tools_mbpp.py")
    config = SandboxConfig()
    try:
        with Sandbox(config, bridge) as sandbox:
            tools = [spec.python_name for spec in sandbox.tool_specs]
            print(f"tools in the sandbox: {tools}\n")
            for label, reply in REPLIES.items():
                got = extract(reply, tools + ["final_answer"])
                print(f"--- {label}: fmt={got.fmt}")
                print(f"    code: {got.code!r}")
                if got.notes:
                    print(f"    notes: {got.notes}")
                outcome = sandbox.execute(got.code)
                print(f"    stdout: {outcome.stdout.strip()!r}")
                print(f"    error: {outcome.error}")
                print(f"    final_answer: {outcome.final_answer!r}\n")
    finally:
        bridge.close()

    print("--- why repr() and not an f-string ---")
    nasty = '"); import os; os.system("echo pwned")  #'
    print(f'    f-string version: result = run_tests(code="{nasty}")')
    print(f"    repr version:     result = run_tests(code={nasty!r})")


if __name__ == "__main__":
    main()
