"""MCP server exposing the MBPP tool set.

It runs as its own process and talks the Model Context Protocol, so the agent
only needs to know the protocol, not this file. The one mandatory tool is
`run_tests(code, test_list)`, which executes candidate code inside a sandbox
worker: a tool that ran model-written code unrestricted would be a hole
straight through the sandbox.

In stdio mode stdout belongs to the protocol, so nothing here may print.
"""
import argparse
import json
from pathlib import Path
from typing import List

from mcp.server.fastmcp import FastMCP

from agent_smith.config import load_json_model
from agent_smith.models import SandboxConfig
from agent_smith.sandbox.controller import ExecutionResult, Sandbox

DEFAULT_CONFIG_PATH = Path(__file__).parent / "sandbox_template.json"
DEFAULT_TEST_TIMEOUT_SECONDS = 5

GUIDELINES = """\
How to solve an MBPP task with this server:
- Write one self-contained function with the exact name and signature asked for.
- Put any imports your solution needs inside your own code: the tests do not add them.
- Check your work with run_tests(code, test_list) before submitting.
- Submit the function's source code as a string with final_answer(code).
- Some tests are hidden, so solve the general problem instead of fitting the
  assertions you can see.
"""


def _build_config(path, timeout_seconds: int) -> SandboxConfig:
    """Sandbox settings for one test run: the project defaults, but quicker."""
    path = Path(path)
    base = load_json_model(path, SandboxConfig) if path.exists() else SandboxConfig()
    return base.model_copy(update={"max_execution_time_seconds": timeout_seconds})


SANDBOX_CONFIG = _build_config(DEFAULT_CONFIG_PATH, DEFAULT_TEST_TIMEOUT_SECONDS)

mcp = FastMCP(
    "agent-smith-mbpp",
    instructions="Tools for solving MBPP tasks: run candidate code against assertions.",
)


def check_solution(code: str, test_list: List[str]) -> dict:
    """Run `code`, then each assertion, in a fresh sandbox worker.

    Returns {"success": bool, "output": str}. Plain function on purpose: the
    tests call it directly, without the protocol in the way.
    """
    if not test_list:
        return {"success": False, "output": "no assertions were given to run"}
    passed = 0
    failures = []
    stopped = None
    with Sandbox(SANDBOX_CONFIG) as sandbox:
        setup = _execute(sandbox, code)
        if setup.error is not None:
            return {"success": False, "output": f"the code itself failed: {setup.error}"}
        for assertion in test_list:
            result = _execute(sandbox, assertion)
            if result.error is None:
                passed += 1
                continue
            failures.append(f"{assertion} -> {_reason(result.error)}")
            if result.error.startswith("Timeout:"):
                # One hang per assertion would eat the whole task's time budget.
                stopped = "stopped after the timeout; the remaining assertions were not run"
                break
    summary = f"{passed}/{len(test_list)} assertions passed"
    if not failures:
        return {"success": True, "output": summary}
    return {"success": False, "output": "\n".join([summary, *failures, *filter(None, [stopped])])}


def _reason(error: str) -> str:
    """Make a bare AssertionError say something a model can act on."""
    if error.split("[line", 1)[0].strip() == "AssertionError:":
        return "the assertion is false (wrong result)"
    return error


def _execute(sandbox: Sandbox, code: str) -> ExecutionResult:
    """Run one piece of code, turning a control exception into an ordinary error.

    `raise SystemExit` in the candidate code must not take this server down.
    """
    try:
        return sandbox.execute(code)
    except (SystemExit, KeyboardInterrupt) as exc:
        return ExecutionResult(error=f"the code raised {type(exc).__name__}")


@mcp.tool()
def run_tests(code: str, test_list: List[str]) -> str:
    """Run `code`, then each assertion of `test_list`, inside the sandbox.

    Returns JSON: {"success": bool, "output": "2/2 assertions passed"}.
    """
    return json.dumps(check_solution(code, test_list))


@mcp.resource("mbpp://guidelines")
def guidelines() -> str:
    """House rules for solving an MBPP task."""
    return GUIDELINES


@mcp.prompt()
def solve_mbpp(task_definition: str, function_definition: str) -> str:
    """A ready-made instruction for one MBPP task."""
    return (
        f"Solve this task: {task_definition}\n"
        f"Use exactly this signature: {function_definition}\n"
        "Check your solution with run_tests before you submit it with final_answer."
    )


def main() -> None:
    """Start the server on the transport asked for."""
    parser = argparse.ArgumentParser(description="MBPP tools over MCP.")
    parser.add_argument("--transport", choices=["stdio", "http"], default="stdio")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--sandbox-config", default=str(DEFAULT_CONFIG_PATH))
    parser.add_argument("--test-timeout", type=int, default=DEFAULT_TEST_TIMEOUT_SECONDS)
    args = parser.parse_args()

    global SANDBOX_CONFIG
    SANDBOX_CONFIG = _build_config(args.sandbox_config, args.test_timeout)
    if args.transport == "http":
        mcp.settings.host = args.host
        mcp.settings.port = args.port
        mcp.run(transport="streamable-http")
    else:
        mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
