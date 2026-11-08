"""`uv run sandbox`: a prompt for running code in the sandbox by hand."""
import argparse
import codeop
import sys
from typing import List, Optional

from agent_smith.config import ConfigError, load_json_model
from agent_smith.models import SandboxConfig
from agent_smith.sandbox.controller import ExecutionResult, Sandbox

PROMPT = ">>> "
CONTINUATION = "... "


def _parse_args(argv: Optional[List[str]]) -> argparse.Namespace:
    """Describe the command line the subject asks for."""
    parser = argparse.ArgumentParser(
        prog="sandbox",
        description="Run Python code inside the Agent Smith sandbox.",
    )
    parser.add_argument(
        "config",
        nargs="?",
        help="JSON file with the sandbox configuration (defaults are used without it)",
    )
    servers = parser.add_mutually_exclusive_group()
    servers.add_argument("--mcp-stdio", metavar="CMD", help="MCP server to launch (M5.5)")
    servers.add_argument("--mcp-server", metavar="URL", help="MCP server to connect to (M5.5)")
    return parser.parse_args(argv)


def _load_config(path: Optional[str]) -> SandboxConfig:
    """Read the config file, or use the defaults when no file was given."""
    if path is None:
        return SandboxConfig()
    return load_json_model(path, SandboxConfig)


def _report(result: ExecutionResult) -> None:
    """Show what one execution produced: output first, then the value or the error."""
    if result.stdout:
        print(result.stdout, end="" if result.stdout.endswith("\n") else "\n")
    if result.error is not None:
        print(result.error)
    elif result.value is not None:
        print(result.value)


def _is_complete(source: str) -> bool:
    """True unless Python needs more lines before it can run `source`."""
    try:
        return codeop.compile_command(source, "<sandbox>", "exec") is not None
    except SyntaxError:
        # Broken code counts as complete: the sandbox reports the error, not us.
        return True


def _read_entry(interactive: bool) -> Optional[str]:
    """Collect one complete entry, asking for more lines while it is unfinished.

    Returns None at end of input. A blank line ends a block, the way the real
    `>>>` prompt does.
    """
    lines: List[str] = []
    while True:
        prompt = (CONTINUATION if lines else PROMPT) if interactive else ""
        try:
            line = input(prompt)
        except EOFError:
            if interactive:
                print()
            return "\n".join(lines) if lines else None
        if lines:
            if not line.strip():
                return "\n".join(lines)
            lines.append(line)
            continue
        if not line.strip():
            return ""
        lines.append(line)
        if _is_complete(line):
            return line


def main(argv: Optional[List[str]] = None) -> int:
    """Run the prompt until `exit`, end of input, or a final answer."""
    args = _parse_args(argv)
    if args.mcp_stdio or args.mcp_server:
        print("MCP servers are not yet supported (coming in milestone 5.5)", file=sys.stderr)
    try:
        config = _load_config(args.config)
    except ConfigError as error:
        print(error, file=sys.stderr)
        return 2
    interactive = sys.stdin.isatty()
    try:
        with Sandbox(config) as sandbox:
            while True:
                entry = _read_entry(interactive)
                if entry is None or entry.strip() in ("exit", "quit"):
                    return 0
                if not entry.strip():
                    continue
                result = sandbox.execute(entry)
                _report(result)
                if result.final_answer is not None:
                    print("Final answer:")
                    print(result.final_answer)
                    return 0
    except KeyboardInterrupt:
        print("Interrupted", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
