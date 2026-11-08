"""Write the sandbox's instruction sheet from what it actually contains.

The manual is generated, never hand-written: when the sandbox is pointed at a
server nobody has seen before, the model still gets an accurate list of what
it can call. It also goes into the system prompt, where every token is counted
against the task budget, so it stays terse.
"""
from typing import List

from agent_smith.mcp_client.schema import ToolSpec, render_signature
from agent_smith.models import SandboxConfig

HELPERS = (
    "list_resources()",
    "read_resource(uri: str)",
    "list_prompts()",
    "get_prompt(name: str, arguments: dict = None)",
)


def build_manual(config: SandboxConfig, specs: List[ToolSpec] = (), mcp_connected: bool = False) -> str:
    """Describe the tools, the limits and the rules, in as few lines as possible."""
    lines = ["Python sandbox. Write code; the last expression is echoed back."]

    lines.append("")
    lines.append("Tools:")
    for spec in specs:
        lines.append(f"  {render_signature(spec)}")
        summary = _first_line(spec.description)
        if summary:
            lines.append(f"      {summary}")
    lines.append("  final_answer(answer: str)")
    lines.append("      Submit your answer. This ends the session immediately.")
    if mcp_connected:
        lines.append(f"Resources and prompts: {', '.join(HELPERS)}")

    lines.append("")
    lines.append(f"Allowed imports: {', '.join(config.authorized_imports)}")
    lines.append(
        f"Limits: {config.max_execution_time_seconds} s per execution, "
        f"{config.max_memory_mb} MB of memory, no network access, "
        f"files only under {', '.join(config.allowed_directories)}."
    )
    lines.append("Variables persist between executions unless the sandbox is restarted.")
    return "\n".join(lines)


def _first_line(text: str) -> str:
    """Tool descriptions can be whole docstrings; the manual wants one line."""
    for line in (text or "").strip().splitlines():
        if line.strip():
            return line.strip()
    return ""
