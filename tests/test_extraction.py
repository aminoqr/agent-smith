"""Tests for the code extraction layer.

Extraction is a pure function, so the tests are mostly a table of realistic
model replies. The replies below are the shapes we have actually seen from
free-tier models: fenced Python, Anthropic XML, Hermes JSON, ReAct, and all the
ways those get mangled when the answer is cut off.
"""
import ast

import pytest

from agent_smith.agent import extract
from agent_smith.agent.extraction import ExtractionResult

TOOLS = ("run_tests", "final_answer")


# --- (a) Python fences -----------------------------------------------------


def test_the_normal_answer():
    reply = """Thought: I will write the function and test it.
```python
def add(a, b):
    return a + b
print(run_tests("def add(a, b): return a + b", ["assert add(1, 2) == 3"]))
```<end_code>"""
    got = extract(reply, TOOLS)

    assert got.fmt == "python"
    assert got.code.startswith("def add(a, b):")
    assert "<end_code>" not in got.code
    assert got.notes == []
    assert got.feedback() == ""


@pytest.mark.parametrize("tag", ["python", "py", "python3", "PYTHON", ""])
def test_the_fence_tag_can_be_spelled_several_ways(tag):
    got = extract(f"```{tag}\nx = 1\n```")

    assert got.code == "x = 1"
    assert got.fmt == "python"


def test_several_blocks_are_run_in_order_and_said_so():
    reply = "First:\n```python\na = 1\n```\nThen:\n```python\nprint(a + 1)\n```"
    got = extract(reply)

    assert got.code == "a = 1\nprint(a + 1)"
    assert any("2 code blocks" in note for note in got.notes)
    assert "interpreted" in got.feedback()


def test_an_unclosed_fence_is_interpreted_and_explained():
    reply = "Thought: here goes\n```python\ndef f(x):\n    return x * 2\nprint(f(3))"
    got = extract(reply)

    assert got.code.endswith("print(f(3))")
    assert any("never closed" in note for note in got.notes)
    assert "malformed" not in got.feedback()  # we explain, we do not jargon
    assert "I interpreted it" in got.feedback()


def test_a_fake_observation_and_everything_after_it_is_dropped():
    reply = """```python
print(run_tests(code, tests))
```
Observation: 1/1 assertions passed
Great, now I am done.
```python
final_answer(code)
```"""
    got = extract(reply)

    assert got.code == "print(run_tests(code, tests))"
    assert "final_answer" not in got.code
    assert any("Observation" in note for note in got.notes)


def test_an_observation_inside_the_code_is_data_not_a_hallucination():
    got = extract('```python\nprint("Observation: all good")\n```')

    assert got.code == 'print("Observation: all good")'
    assert got.notes == []


def test_a_non_python_fence_is_reported_not_run():
    got = extract("Run this:\n```bash\nrm -rf /\n```")

    assert got.code is None
    assert any("bash" in note for note in got.notes)
    assert "No valid code block" in got.feedback()


def test_a_python_block_wins_over_a_bash_block():
    got = extract("```bash\nls\n```\n```python\nx = 1\n```")

    assert got.code == "x = 1"
    assert any("ignored" in note and "bash" in note for note in got.notes)


def test_backticks_inside_a_string_do_not_end_the_block():
    got = extract('```python\nprint("```python is a fence")\nx = 2\n```')

    assert got.code == 'print("```python is a fence")\nx = 2'


def test_an_empty_fence_is_not_code():
    got = extract("```python\n\n```")

    assert got.code is None
    assert "No valid code block" in got.feedback()


def test_prose_only_gets_a_reason_and_an_instruction():
    got = extract("Sure! The answer is to use a loop and then sum the values.")

    assert got.code is None and got.fmt == "none"
    assert "prose only" in got.feedback()
    assert "```python" in got.feedback()


def test_an_empty_reply_is_handled():
    got = extract("")

    assert got.code is None
    assert "empty" in got.feedback()


def test_a_missing_fence_is_accepted_when_the_whole_reply_is_a_program():
    got = extract("def add(a, b):\n    return a + b\nprint(add(1, 2))")

    assert got.fmt == "bare-python"
    assert any("no ```python fence" in note for note in got.notes)


def test_a_single_prose_word_is_not_mistaken_for_code():
    """'Hello' parses as a Python expression, so the guard has to be stricter."""
    assert extract("Hello").code is None
    assert extract("Done").code is None


def test_a_syntax_error_is_flagged_but_still_sent_to_the_sandbox():
    got = extract("```python\ndef broken(:\n    pass\n```")

    assert got.code is not None
    assert any("does not parse" in note and "line 1" in note for note in got.notes)


# --- (b) XML tool calls ----------------------------------------------------


def test_an_xml_tool_call_becomes_a_python_call():
    reply = """I will check the solution.
<invoke name="run_tests">
<parameter name="code">def add(a, b): return a + b</parameter>
<parameter name="test_list">["assert add(1, 2) == 3"]</parameter>
</invoke>"""
    got = extract(reply, TOOLS)

    assert got.fmt == "xml"
    assert got.code == (
        "result = run_tests(code='def add(a, b): return a + b', "
        "test_list=['assert add(1, 2) == 3'])\nprint(result)"
    )


def test_xml_parameters_keep_their_json_types():
    reply = '<invoke name="t"><parameter name="n">3</parameter>' \
            '<parameter name="flag">true</parameter>' \
            '<parameter name="who">bob</parameter></invoke>'
    got = extract(reply)

    assert "n=3" in got.code and "flag=True" in got.code and "who='bob'" in got.code


def test_an_invoke_without_a_name_is_refused_politely():
    got = extract("<invoke><parameter name=\"x\">1</parameter></invoke>")

    assert got.code is None
    assert any("no name attribute" in note for note in got.notes)


# --- (c) Hermes / JSON tool calls -----------------------------------------


def test_the_hermes_case_from_the_plan_is_injection_safe():
    reply = '<tool_call>{"name": "run_tests", "arguments": {"code": "x = \'\\"\'", "test_list": []}}</tool_call>'
    got = extract(reply, TOOLS)

    assert got.fmt == "hermes"
    assert got.code == "result = run_tests(code='x = \\'\"\\'', test_list=[])\nprint(result)"
    # The real guarantee: whatever the string contains, it stays one argument.
    call = ast.parse(got.code).body[0].value
    assert call.keywords[0].arg == "code"
    assert call.keywords[0].value.value == "x = '\"'"


def test_a_shell_command_in_an_argument_cannot_escape_the_call():
    nasty = '"); import os; os.system("rm -rf /")\n#'
    reply = '<tool_call>{"name": "run_tests", "arguments": {"code": %s}}</tool_call>' % (
        __import__("json").dumps(nasty)
    )
    got = extract(reply, TOOLS)

    tree = ast.parse(got.code)
    call = tree.body[0].value
    assert len(tree.body) == 2  # the assignment and the print, nothing more
    assert call.keywords[0].value.value == nasty


def test_double_encoded_arguments_are_decoded():
    reply = '<tool_call>{"name": "run_tests", "arguments": "{\\"code\\": \\"x = 1\\"}"}</tool_call>'
    got = extract(reply, TOOLS)

    assert got.code.startswith("result = run_tests(code='x = 1')")
    assert any("JSON string inside the JSON" in note for note in got.notes)


def test_an_unclosed_tool_call_is_still_read():
    reply = '<tool_call>{"name": "run_tests", "arguments": {"code": "x = 1", "test_list": []}}'
    got = extract(reply, TOOLS)

    assert got.code is not None
    assert any("never closed" in note for note in got.notes)


def test_python_quoted_json_is_tolerated():
    reply = "<tool_call>{'name': 'run_tests', 'arguments': {'code': 'x = 1'}}</tool_call>"
    got = extract(reply, TOOLS)

    assert got.code.startswith("result = run_tests(code='x = 1')")


def test_final_answer_is_called_directly_without_printing():
    reply = '<tool_call>{"name": "final_answer", "arguments": {"answer": "def f(): pass"}}</tool_call>'
    got = extract(reply, TOOLS)

    assert got.code == "final_answer(answer='def f(): pass')"
    assert "print" not in got.code


def test_a_tool_we_never_offered_is_converted_but_called_out():
    reply = '<tool_call>{"name": "read_file", "arguments": {"path": "/etc/passwd"}}</tool_call>'
    got = extract(reply, TOOLS)

    assert got.code.startswith("result = read_file(path='/etc/passwd')")
    assert any("NameError" in note for note in got.notes)


def test_a_tool_call_that_is_not_json_runs_nothing():
    got = extract("<tool_call>run the tests please</tool_call>")

    assert got.code is None
    assert "could not read" in got.feedback()


# --- (d) ReAct -------------------------------------------------------------


def test_a_react_block_becomes_a_python_call():
    reply = """Thought: I should test my function.
Action: run_tests
Action Input: {"code": "def add(a, b): return a + b", "test_list": ["assert add(1, 2) == 3"]}"""
    got = extract(reply, TOOLS)

    assert got.fmt == "react"
    assert got.code == (
        "result = run_tests(code='def add(a, b): return a + b', "
        "test_list=['assert add(1, 2) == 3'])\nprint(result)"
    )


def test_react_stops_at_a_blank_line_and_ignores_a_fake_observation():
    reply = """Action: run_tests
Action Input: {"code": "x = 1"}

Observation: it worked
Thought: done"""
    got = extract(reply, TOOLS)

    assert got.code.startswith("result = run_tests(code='x = 1')")


def test_a_plain_react_input_is_passed_as_one_argument():
    got = extract("Action: read_file\nAction Input: /testbed/src/module.py")

    assert got.code == "result = read_file('/testbed/src/module.py')\nprint(result)"
    assert any("not JSON" in note for note in got.notes)


# --- order and shape -------------------------------------------------------


def test_a_python_fence_beats_a_tool_call_in_the_same_reply():
    reply = '```python\nx = 1\n```\n<tool_call>{"name": "run_tests", "arguments": {}}</tool_call>'
    got = extract(reply, TOOLS)

    assert (got.fmt, got.code) == ("python", "x = 1")


def test_xml_beats_hermes_which_beats_react():
    both = (
        '<invoke name="a"><parameter name="x">1</parameter></invoke>\n'
        '<tool_call>{"name": "b", "arguments": {}}</tool_call>\n'
        "Action: c\nAction Input: {}"
    )
    assert extract(both).fmt == "xml"

    later = '<tool_call>{"name": "b", "arguments": {}}</tool_call>\nAction: c\nAction Input: {}'
    assert extract(later).fmt == "hermes"


def test_the_result_object_is_honest_about_itself():
    assert ExtractionResult(code="x = 1").ok is True
    assert ExtractionResult(code=None).ok is False
    assert ExtractionResult(code="x = 1").feedback() == ""
