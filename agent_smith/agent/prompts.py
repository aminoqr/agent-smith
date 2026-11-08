"""The words we send the model, and the words we send back after running code.

Everything here is measured in tokens, because MBPP gives the whole task 6,000
input tokens and every step resends the conversation. A sentence added to the
system prompt is paid for again at every step, so this file is deliberately
terse: format, rules, one worked example, then the generated manual.
"""
from typing import Sequence

from agent_smith.models import MBPPTaskInput
from agent_smith.sandbox.controller import ExecutionResult, truncate

SYSTEM_TEMPLATE = """You are a Python coding agent. You solve one task per session, by writing code.

Answer like this, every time:
Thought: one or two sentences about your plan.
```python
# your code
```<end_code>

Rules:
- One Thought and one Python block per message. Stop at <end_code> and wait.
- Never write "Observation:" yourself. Observations come from the sandbox and are real.
- Test your solution before submitting it.
- Submit with final_answer(solution), where solution is a string holding the whole function.
- Keep the exact function name and signature the task asks for.
- Variables persist between steps, so you can build on what already ran.

A good first step does everything at once:
Thought: Write the function as a string, test it, and submit it if the tests pass.
```python
import json
solution = "def add(a, b):\\n    return a + b"
report = json.loads(run_tests(solution, test_list))
print(report)
if report["success"]:
    final_answer(solution)
```<end_code>

{manual}"""


def build_system_prompt(manual: str, extra_rules: Sequence[str] = ()) -> str:
    """Response format, rules, one example, then the sandbox's own manual.

    `extra_rules` exists so a tweak during a demo is one line at the call site
    instead of an edit to this template.
    """
    prompt = SYSTEM_TEMPLATE.format(manual=manual)
    if extra_rules:
        prompt += "\n\nAlso:\n" + "\n".join(f"- {rule}" for rule in extra_rules)
    return prompt


def build_task_message(task: MBPPTaskInput) -> str:
    """The task itself: description, required signature, and the tests.

    The tests are shown *and* injected into the sandbox as `test_list`, so the
    model can pass them to `run_tests` without retyping them. Retyping would
    cost output tokens and is where models introduce typos.
    """
    lines = [
        f"Task {task.task_id}: {task.task_definition}",
        f"Required signature: {task.function_definition}",
    ]
    if task.test_imports:
        lines.append("The tests start with: " + "; ".join(task.test_imports))
    lines.append("Tests that must pass (already available as the variable test_list):")
    lines.extend(f"  {test}" for test in task.test_list)
    return "\n".join(lines)


def build_prelude(task: MBPPTaskInput) -> str:
    """Code we run in the sandbox before the model's first step.

    It only defines data (`test_list`, and the task's test imports). It is our
    code, not the model's, so it is not recorded as a step.
    """
    lines = [f"test_list = {list(task.test_list)!r}"]
    lines.extend(task.test_imports)
    return "\n".join(lines)


def build_observation(outcome: ExecutionResult, notes: Sequence[str], limit: int) -> str:
    """What the model sees after its code ran.

    One message, always in the same shape, and always honest about what
    happened: printed output, the error if any, the value of the last
    expression, and anything we had to fix up while reading the answer. The
    subject requires each of those cases to be visible; a silent failure turns
    into a hallucinated observation on the next step.
    """
    parts = []
    stdout = (outcome.stdout or "").strip()
    if stdout:
        parts.append(truncate(stdout, limit))
    if outcome.value is not None:
        parts.append(f"Value: {truncate(outcome.value, 200)}")
    if outcome.error:
        parts.append(f"Error: {truncate(outcome.error, limit)}")
    if not parts:
        parts.append("(no output)")
    if notes:
        parts.append("Note: " + " ".join(notes))
    return "Observation:\n" + "\n".join(parts)


REPEATED_CODE_FEEDBACK = (
    "Observation:\n"
    "That is exactly the code from the previous step, so I did not run it again: "
    "the result would be identical. Change your approach, or submit with final_answer()."
)
