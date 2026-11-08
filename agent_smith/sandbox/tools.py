"""Expose the connected MCP server inside the sandbox as plain Python.

The model never sees JSON-RPC. It sees functions with real signatures, so it
can loop over tool results, branch on them and combine them inside one code
block: the whole point of a code agent over a JSON tool-calling agent.
"""
import json
from typing import Callable, Dict, List

from agent_smith.mcp_client.schema import ToolSpec, argument_names, signature_of

# Names the sandbox owns; a server is not allowed to take any of them over.
HELPER_NAMES = ("list_resources", "read_resource", "list_prompts", "get_prompt")
PROTECTED_NAMES = frozenset({"final_answer", *HELPER_NAMES})
RENAME_PREFIX = "mcp_"


def resolve_names(specs: List[ToolSpec]) -> List[ToolSpec]:
    """Decide the final Python name of every tool, once, in the controller.

    A server could expose a tool called `final_answer` or `read_resource`;
    those names belong to the sandbox, so such a tool is renamed with an
    `mcp_` prefix. Both the worker and the generated manual must agree on the
    result, which is why this happens where the specs are fetched and not
    where the stubs are built.
    """
    taken = set(PROTECTED_NAMES)
    for spec in specs:
        name = spec.python_name
        if name in taken:
            name = RENAME_PREFIX + name
        while name in taken:
            name += "_"
        spec.python_name = name
        taken.add(name)
    return specs


def install(namespace: dict, specs: List[ToolSpec], request: Callable[[str, dict], str]) -> None:
    """Add one function per tool, plus the resource and prompt helpers."""
    for spec in specs:
        namespace[spec.python_name] = _stub(spec, request)
    namespace.update(_helpers(request))


def _stub(spec: ToolSpec, request: Callable[[str, dict], str]):
    """One function that calls one tool."""
    signature = signature_of(spec)
    server_names = argument_names(spec)

    def stub(*args, **kwargs):
        bound = signature.bind(*args, **kwargs)
        arguments = {server_names[name]: value for name, value in bound.arguments.items()}
        return request("call_tool", {"name": spec.name, "arguments": arguments})

    stub.__name__ = spec.python_name
    stub.__qualname__ = spec.python_name
    stub.__doc__ = spec.description
    stub.__signature__ = signature
    return stub


def _helpers(request: Callable[[str, dict], str]) -> Dict[str, Callable]:
    """The resource and prompt side of MCP, as four small functions."""

    def list_resources() -> list:
        """URIs of the resources this server offers."""
        return json.loads(request("list_resources", {}))

    def read_resource(uri: str) -> str:
        """The text of one resource."""
        return request("read_resource", {"uri": uri})

    def list_prompts() -> list:
        """Names of the prompts this server offers."""
        return json.loads(request("list_prompts", {}))

    def get_prompt(name: str, arguments: dict = None) -> str:
        """One prompt, filled in with `arguments`."""
        return request("get_prompt", {"name": name, "arguments": arguments or {}})

    return {
        "list_resources": list_resources,
        "read_resource": read_resource,
        "list_prompts": list_prompts,
        "get_prompt": get_prompt,
    }
