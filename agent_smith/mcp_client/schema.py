"""Turn a tool's JSON Schema into something Python can call.

An MCP server describes each tool with a JSON Schema. The model, however,
writes ordinary Python. This module is the translation layer: schema in,
real `inspect.Signature` and a readable signature line out.
"""
import inspect
import keyword
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List

JSON_TO_PYTHON = {
    "string": str,
    "integer": int,
    "number": float,
    "boolean": bool,
    "array": list,
    "object": dict,
}
JSON_TYPE_NAMES = {
    "string": "str",
    "integer": "int",
    "number": "float",
    "boolean": "bool",
    "array": "list",
    "object": "dict",
}


@dataclass
class ToolSpec:
    """One tool as the server described it, plus how we expose it in Python."""

    name: str
    description: str = ""
    schema: Dict[str, Any] = field(default_factory=dict)
    python_name: str = ""

    def __post_init__(self):
        self.python_name = self.python_name or identifier_for(self.name)

    def as_dict(self) -> dict:
        """JSON-safe form, for sending to the worker process."""
        return {
            "name": self.name,
            "description": self.description,
            "schema": self.schema,
            "python_name": self.python_name,
        }


def identifier_for(name: str) -> str:
    """Make a legal Python name out of a tool name like `get-weather`."""
    cleaned = re.sub(r"\W", "_", name).strip("_") or "tool"
    if cleaned[0].isdigit():
        cleaned = f"tool_{cleaned}"
    if keyword.iskeyword(cleaned):
        cleaned = f"{cleaned}_"
    return cleaned


def _sorted_properties(schema: Dict[str, Any]):
    """Required parameters first: Python forbids a default before a non-default."""
    properties = schema.get("properties") or {}
    required = schema.get("required") or []
    names = [name for name in properties if name in required]
    names += [name for name in properties if name not in required]
    return [(name, properties[name] or {}, name in required) for name in names]


def _json_type(definition: Dict[str, Any]):
    """The declared type, ignoring a `null` alternative from an optional field."""
    kind = definition.get("type")
    if isinstance(kind, list):
        kind = next((item for item in kind if item != "null"), None)
    if kind is None and "anyOf" in definition:
        for option in definition["anyOf"]:
            if option.get("type") not in (None, "null"):
                return option.get("type")
    return kind


def annotation_for(definition: Dict[str, Any]):
    """The Python type to annotate a parameter with."""
    kind = _json_type(definition)
    if kind == "array":
        items = _json_type(definition.get("items") or {})
        if items in JSON_TO_PYTHON:
            return List[JSON_TO_PYTHON[items]]
        return list
    return JSON_TO_PYTHON.get(kind, Any)


def type_name_for(definition: Dict[str, Any]) -> str:
    """The type as it should read in the manual: `str`, `list[str]`, ..."""
    kind = _json_type(definition)
    if kind == "array":
        items = _json_type(definition.get("items") or {})
        inner = JSON_TYPE_NAMES.get(items)
        return f"list[{inner}]" if inner else "list"
    return JSON_TYPE_NAMES.get(kind, "Any")


def signature_of(spec: ToolSpec) -> inspect.Signature:
    """A real signature, so wrong arguments fail like wrong arguments."""
    parameters = []
    for name, definition, is_required in _sorted_properties(spec.schema):
        default = inspect.Parameter.empty if is_required else definition.get("default", None)
        parameters.append(
            inspect.Parameter(
                identifier_for(name),
                inspect.Parameter.POSITIONAL_OR_KEYWORD,
                default=default,
                annotation=annotation_for(definition),
            )
        )
    return inspect.Signature(parameters)


def argument_names(spec: ToolSpec) -> Dict[str, str]:
    """Map the Python parameter name back to the name the server expects."""
    return {identifier_for(name): name for name, _, _ in _sorted_properties(spec.schema)}


def render_signature(spec: ToolSpec) -> str:
    """One readable line, e.g. `search(query: str, limit: int = 5)`."""
    rendered = []
    for name, definition, is_required in _sorted_properties(spec.schema):
        part = f"{identifier_for(name)}: {type_name_for(definition)}"
        if not is_required:
            part += f" = {definition.get('default', None)!r}"
        rendered.append(part)
    return f"{spec.python_name}({', '.join(rendered)})"


def spec_from_tool(tool) -> ToolSpec:
    """Build a ToolSpec from what `tools/list` returned."""
    return ToolSpec(
        name=tool.name,
        description=(tool.description or "").strip(),
        schema=tool.inputSchema or {},
    )
