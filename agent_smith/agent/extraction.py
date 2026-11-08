"""Turning whatever the model wrote into Python the sandbox can run.

Models answer in whichever shape they were trained on: a fenced Python block,
an Anthropic-style `<invoke>`, a Hermes `<tool_call>`, or ReAct's
`Action:` / `Action Input:`. Everything here is normalised to Python, so the
sandbox never learns that other formats exist.

`extract()` is a pure function: text in, code plus diagnostics out. No I/O, no
state. That is what makes it the most heavily tested part of the project, and
it is also why every "malformed but interpreted anyway" decision has to come
back as a note. The subject is explicit that the model must never be left
guessing what we did with its answer.
"""
import ast
import json
import re
import xml.etree.ElementTree as ElementTree
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

#: Fence languages we are willing to run. A ```bash block is not Python.
PYTHON_LANGUAGES = frozenset({"", "python", "py", "python3", "ipython"})

#: Sentinels we ask the model to end on. Providers strip a `stop` string from
#: the text, but not every provider honours `stop`, so we remove them too.
END_MARKERS = ("<end_code>", "<end_action>")

_FENCE_LINE = re.compile(r"^[ \t]*```[ \t]*([A-Za-z0-9_+.#-]*)[ \t]*$")
_OBSERVATION_LINE = re.compile(r"^[ \t]*(?:Observation|OBSERVATION|observation)[ \t]*:")
_INVOKE = re.compile(r"<invoke\b[^>]*>.*?</invoke>", re.DOTALL)
_TOOL_CALL_OPEN = re.compile(r"<tool_call>", re.IGNORECASE)
_ACTION = re.compile(r"^[ \t]*Action[ \t]*:[ \t]*(.+?)[ \t]*$", re.MULTILINE)
_ACTION_INPUT = re.compile(r"^[ \t]*Action[ _]?Input[ \t]*:[ \t]*(.*)$", re.MULTILINE)


@dataclass
class ExtractionResult:
    """What we got out of one model reply.

    `code` is None when there was nothing runnable. `fmt` names the format we
    recognised, which the loop logs: it is the cheapest way to notice that a
    new model answers in a shape we handle badly.
    """

    code: Optional[str]
    fmt: str = "none"
    notes: List[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.code is not None

    def feedback(self) -> str:
        """The sentence to send back to the model, or "" when all was well.

        Two of the subject's mandatory feedback cases live here: no valid code
        block, and a malformed block that we interpreted anyway (with how).
        """
        if self.code is None:
            reason = " ".join(self.notes) or "I could not find any code in your reply."
            return (
                f"No valid code block was found in your reply. {reason} "
                "Answer with a Thought, then one ```python block ending in <end_code>."
            )
        if self.notes:
            return "Your code block was not well formed, so I interpreted it: " + " ".join(self.notes)
        return ""


def extract(text: str, known_tools: Sequence[str] = ()) -> ExtractionResult:
    """Pull runnable Python out of one model reply.

    The parsers run in a fixed order: Python fences, then XML, then Hermes,
    then ReAct. Python wins because it is what we ask for in the system prompt
    and it is the only format that can express several statements at once; a
    reply containing both a fence and a `<tool_call>` is a model hedging, and
    the fence is the richer of the two.

    `known_tools` is optional. When given, a call to something else is still
    converted (the sandbox will raise `NameError` and the model will see it),
    but we say so in the notes.
    """
    notes: List[str] = []
    cleaned = _strip_end_markers(text or "")
    cleaned, dropped = _cut_hallucinated_observation(cleaned)
    if dropped:
        notes.append(
            "you wrote an 'Observation:' yourself, so I dropped everything after it and ran "
            "only the code above it; observations come from the sandbox, not from you."
        )

    for parser in (_from_python_fence, _from_bare_python, _from_xml, _from_hermes, _from_react):
        result = parser(cleaned, notes, known_tools)
        if result is not None:
            result.notes = notes
            _syntax_check(result)
            return result

    notes.append(_why_nothing(cleaned))
    return ExtractionResult(code=None, fmt="none", notes=notes)


# --- (a) Python code blocks ------------------------------------------------


def _from_python_fence(
    text: str, notes: List[str], _known: Sequence[str]
) -> Optional[ExtractionResult]:
    blocks, unclosed, other_languages = _fenced_blocks(text)
    if not blocks:
        if other_languages:
            notes.append(
                "the only code block was tagged "
                f"{', '.join(sorted(other_languages))}, which I cannot run as Python."
            )
        return None

    if unclosed:
        notes.append(
            "the last code block was never closed with ```, so I treated everything after the "
            "opening fence as code (this usually means the answer was cut off)."
        )
    if other_languages:
        notes.append(f"I ignored the {', '.join(sorted(other_languages))} block(s).")
    if len(blocks) > 1:
        notes.append(f"your reply had {len(blocks)} code blocks, so I ran them in order as one program.")

    code = "\n".join(block.strip("\n") for block in blocks).strip()
    if not code:
        notes.append("the code block was empty.")
        return None
    return ExtractionResult(code=code, fmt="python")


def _fenced_blocks(text: str) -> Tuple[List[str], bool, set]:
    """Walk the lines, tracking fences. Simpler and safer than one big regex.

    A closing fence is recognised only on a line of its own, so backticks in
    the middle of a line (inside a string, say) cannot end a block early.
    """
    blocks: List[str] = []
    other_languages: set = set()
    current: Optional[List[str]] = None
    language = ""
    unclosed = False

    for line in text.splitlines():
        fence = _FENCE_LINE.match(line)
        if current is None:
            if fence:
                language = fence.group(1).lower()
                current = []
            continue
        if fence and not fence.group(1):
            if language in PYTHON_LANGUAGES:
                blocks.append("\n".join(current))
            else:
                other_languages.add(language)
            current = None
            continue
        current.append(line)

    if current is not None:
        if language in PYTHON_LANGUAGES:
            blocks.append("\n".join(current))
            unclosed = True
        else:
            other_languages.add(language)
    return [block for block in blocks if block.strip()], unclosed, other_languages


def _from_bare_python(
    text: str, notes: List[str], _known: Sequence[str]
) -> Optional[ExtractionResult]:
    """A reply that forgot the fence but is obviously a program.

    Small free-tier models do this often. The guard is deliberately strict:
    the whole reply must parse as Python *and* contain a real statement, so
    prose like "Hello" (a valid Python expression!) is not executed.
    """
    body = text.strip()
    if not body or "```" in body:
        return None
    try:
        tree = ast.parse(body)
    except SyntaxError:
        return None
    interesting = (
        ast.FunctionDef,
        ast.AsyncFunctionDef,
        ast.ClassDef,
        ast.Assign,
        ast.AnnAssign,
        ast.AugAssign,
        ast.For,
        ast.While,
        ast.If,
        ast.With,
        ast.Try,
        ast.Import,
        ast.ImportFrom,
        ast.Return,
    )
    statements = [node for node in tree.body if isinstance(node, interesting)]
    calls = [
        node for node in tree.body
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call)
    ]
    if not statements and not calls:
        return None
    notes.append(
        "your reply had no ```python fence, but the whole text parsed as Python, so I ran it; "
        "please use a fenced block next time."
    )
    return ExtractionResult(code=body, fmt="bare-python")


# --- (b) XML tool calls ----------------------------------------------------


def _from_xml(text: str, notes: List[str], known: Sequence[str]) -> Optional[ExtractionResult]:
    match = _INVOKE.search(text)
    if match is None:
        return None
    try:
        element = ElementTree.fromstring(match.group(0))
    except ElementTree.ParseError as exc:
        notes.append(f"I found an <invoke> block but could not parse it as XML: {exc}.")
        return None

    name = (element.get("name") or "").strip()
    if not name:
        notes.append("the <invoke> block has no name attribute, so I do not know which tool to call.")
        return None

    arguments: Dict[str, Any] = {}
    for parameter in element.findall("parameter"):
        key = (parameter.get("name") or "").strip()
        if not key:
            continue
        arguments[key] = _coerce("".join(parameter.itertext()))
    notes.append("you answered with an XML tool call, which I converted to a Python call.")
    return _as_call(name, arguments, notes, known, "xml")


# --- (c) JSON / Hermes tool calls ------------------------------------------


def _from_hermes(text: str, notes: List[str], known: Sequence[str]) -> Optional[ExtractionResult]:
    match = _TOOL_CALL_OPEN.search(text)
    if match is None:
        return None
    rest = text[match.end():]
    closing = rest.lower().find("</tool_call>")
    payload = rest if closing == -1 else rest[:closing]
    if closing == -1:
        notes.append("your <tool_call> was never closed, so I read everything after it as the call.")

    parsed = _loose_json(payload)
    if not isinstance(parsed, dict):
        notes.append("I found a <tool_call> but its body is not a JSON object, so I ran nothing.")
        return None

    name = str(parsed.get("name") or parsed.get("tool") or "").strip()
    if not name:
        notes.append("the <tool_call> JSON has no \"name\" field.")
        return None

    arguments = parsed.get("arguments", parsed.get("parameters", {}))
    if isinstance(arguments, str):
        # Several models double-encode: "arguments": "{\"code\": \"...\"}"
        decoded = _loose_json(arguments)
        if isinstance(decoded, (dict, list)):
            notes.append("the arguments were a JSON string inside the JSON, which I decoded.")
            arguments = decoded
    notes.append("you answered with a JSON tool call, which I converted to a Python call.")
    return _as_call(name, arguments, notes, known, "hermes")


# --- (d) ReAct -------------------------------------------------------------


def _from_react(text: str, notes: List[str], known: Sequence[str]) -> Optional[ExtractionResult]:
    action = _ACTION.search(text)
    if action is None:
        return None
    name = action.group(1).strip().strip("`\"'")
    if not name:
        return None

    arguments: Any = {}
    tail = _ACTION_INPUT.search(text, action.end())
    if tail is not None:
        raw = text[tail.start(1):]
        raw = _until_blank_line_or_observation(raw)
        parsed = _loose_json(raw)
        if isinstance(parsed, (dict, list)):
            arguments = parsed
        elif raw.strip():
            # `Action Input: some/path.py` - one unnamed argument.
            arguments = [_coerce(raw.strip())]
            notes.append("your Action Input was not JSON, so I passed it as a single argument.")
    notes.append("you answered in ReAct format, which I converted to a Python call.")
    return _as_call(name, arguments, notes, known, "react")


def _until_blank_line_or_observation(raw: str) -> str:
    """ReAct has no end marker, so an argument ends at a blank line."""
    lines: List[str] = []
    for line in raw.splitlines():
        if not lines and not line.strip():
            continue
        if lines and (not line.strip() or _OBSERVATION_LINE.match(line) or _ACTION.match(line)):
            break
        lines.append(line)
    return "\n".join(lines)


# --- shared: building the Python call --------------------------------------


def _as_call(
    name: str,
    arguments: Any,
    notes: List[str],
    known: Sequence[str],
    fmt: str,
) -> ExtractionResult:
    """Render `name(**arguments)` as Python.

    Every value goes through `repr()`, so a string argument becomes a Python
    literal and cannot inject code. An f-string with hand-written quotes
    breaks on the first argument that contains the quote character (or a
    newline, or a backslash); `repr()` is the escaping Python already owns.
    """
    if not name.isidentifier():
        notes.append(f"'{name}' is not a usable Python name, so I ran nothing.")
        return ExtractionResult(code=None, fmt="none")

    if isinstance(arguments, dict):
        if all(str(key).isidentifier() for key in arguments):
            rendered = ", ".join(f"{key}={value!r}" for key, value in arguments.items())
        else:
            # A key that is not an identifier can still be passed, just not by name.
            notes.append("some argument names are not Python identifiers, so I expanded a dict.")
            rendered = f"**{arguments!r}"
    elif isinstance(arguments, (list, tuple)):
        rendered = ", ".join(repr(value) for value in arguments)
    else:
        rendered = repr(arguments)

    if known and name not in known:
        notes.append(
            f"'{name}' is not one of the tools I gave you ({', '.join(known)}), "
            "so the sandbox will report a NameError."
        )

    if name == "final_answer":
        return ExtractionResult(code=f"final_answer({rendered})", fmt=fmt)
    return ExtractionResult(code=f"result = {name}({rendered})\nprint(result)", fmt=fmt)


def _coerce(raw: str) -> Any:
    """`"3"` is a number, `"[1, 2]"` is a list, anything else stays a string.

    XML parameters and ReAct inputs are untyped text, but `run_tests` wants a
    real list for `test_list`, so a JSON-looking value is decoded.
    """
    text = raw.strip()
    if not text:
        return ""
    try:
        value = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return raw
    return value if isinstance(value, (dict, list, int, float, bool)) or value is None else raw


def _loose_json(raw: str) -> Any:
    """`json.loads`, then a second attempt on the outermost {...} or [...].

    Models wrap JSON in prose, add a trailing comma, or quote with single
    quotes. We recover from the first case and from Python-style literals via
    `ast.literal_eval`; we do not try to repair arbitrary broken JSON, because
    guessing what the model meant is how an agent runs the wrong code.
    """
    text = (raw or "").strip().strip("`")
    for candidate in _json_candidates(text):
        try:
            return json.loads(candidate)
        except (json.JSONDecodeError, ValueError):
            pass
        try:
            return ast.literal_eval(candidate)
        except (ValueError, SyntaxError):
            pass
    return None


def _json_candidates(text: str) -> List[str]:
    candidates = [text]
    for opening, closing in (("{", "}"), ("[", "]")):
        start = text.find(opening)
        end = text.rfind(closing)
        if start != -1 and end > start:
            candidates.append(text[start:end + 1])
    return candidates


def _strip_end_markers(text: str) -> str:
    for marker in END_MARKERS:
        text = text.replace(marker, "")
    return text


def _cut_hallucinated_observation(text: str) -> Tuple[str, bool]:
    """Drop a model-written `Observation:` and everything after it.

    Without `stop` sequences (or with a provider that ignores them) models
    happily invent the tool output and then reason about it. Keeping that text
    would mean running code the model wrote *after* believing a fake result.
    An `Observation:` inside a code block is left alone: there it is data.
    """
    lines = text.splitlines()
    inside = False
    for index, line in enumerate(lines):
        if _FENCE_LINE.match(line):
            inside = not inside
            continue
        if not inside and _OBSERVATION_LINE.match(line):
            return "\n".join(lines[:index]), True
    return text, False


def _syntax_check(result: ExtractionResult) -> None:
    """Report a syntax error as a note, but still send the code down.

    The sandbox reports the real error with its own line numbers, and seeing
    the same failure from the thing that ran it is less confusing for the
    model than a pre-emptive refusal from us.
    """
    if result.code is None:
        return
    try:
        ast.parse(result.code)
    except SyntaxError as exc:
        result.notes.append(
            f"your code does not parse (line {exc.lineno}: {exc.msg}), I sent it anyway."
        )


def _why_nothing(text: str) -> str:
    if "```" in text:
        return "There is a ``` fence but no runnable Python inside it."
    if "<tool_call" in text or "<invoke" in text:
        return "There is a tool call that I could not read."
    if not text.strip():
        return "Your reply was empty."
    return "Your reply was prose only."
