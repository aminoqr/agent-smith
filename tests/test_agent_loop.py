"""Tests for the agent loop, the budget and the solution file.

Most of these use `FakeLLM` and a stub sandbox, so a whole run takes
microseconds and every failure mode is reachable on purpose. The last test is
the real thing: a real sandbox, the real MBPP MCP server, and a scripted model
answer that solves the task in one step.
"""
import json
import types
from pathlib import Path

import pytest

from agent_smith.agent import Agent, Budget, SolutionWriter, build_observation
from agent_smith.agent.prompts import build_prelude, build_system_prompt, build_task_message
from agent_smith.config import ConfigError, load_json_model
from agent_smith.llm.base import LLMError, LLMResponse
from agent_smith.llm.fake import FakeLLM
from agent_smith.models import AgentConfig, MBPPTaskInput, SolutionOutput
from agent_smith.sandbox.controller import ExecutionResult

SAMPLE = Path("tests/fixtures/mbpp_sample.json")
SOLUTION = "def add(a, b):\n    return a + b"
PASSED = '{"success": true, "output": "1/1 assertions passed"}'


class StubSandbox:
    """A sandbox that returns scripted results and remembers what it was given."""

    def __init__(self, *outcomes, tools=("run_tests",)):
        self._outcomes = list(outcomes)
        self.executed = []
        self.tool_specs = [types.SimpleNamespace(python_name=name) for name in tools]

    def execute(self, code):
        self.executed.append(code)
        if not self._outcomes:
            return ExecutionResult(stdout="")
        outcome = self._outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


def reply(code=None, thought="Thought: here goes."):
    if code is None:
        return "I think the answer is to use a loop, but I will not write code."
    return f"{thought}\n```python\n{code}\n```<end_code>"


def run_agent(llm, sandbox, config=None, tmp_path=None, **kwargs):
    writer = SolutionWriter(tmp_path / "solution.json") if tmp_path else None
    agent = Agent(llm, sandbox, config or AgentConfig(), writer=writer, **kwargs)
    solution = agent.run(
        "SYSTEM PROMPT",
        "TASK MESSAGE",
        task_id="11",
        benchmark="mbpp",
    )
    return solution, writer


# --- the happy path --------------------------------------------------------


def test_one_step_solve(tmp_path):
    llm = FakeLLM([reply(f"final_answer({SOLUTION!r})")])
    sandbox = StubSandbox(ExecutionResult(stdout=PASSED, final_answer=SOLUTION))
    solution, writer = run_agent(llm, sandbox, tmp_path=tmp_path)

    assert solution.success is True
    assert solution.solution == SOLUTION
    assert solution.iterations == 1
    assert solution.error is None
    assert solution.benchmark == "mbpp" and solution.task_id == "11"
    assert solution.system_prompt == "SYSTEM PROMPT"
    assert writer.writes >= 1


def test_every_step_carries_what_the_grader_reads(tmp_path):
    llm = FakeLLM([reply(f"final_answer({SOLUTION!r})")])
    sandbox = StubSandbox(ExecutionResult(stdout=PASSED, final_answer=SOLUTION))
    solution, _writer = run_agent(llm, sandbox, tmp_path=tmp_path)
    step = solution.steps[0]

    assert step.step == 1
    assert step.llm_output.startswith("Thought:")
    assert step.sandbox_input and step.sandbox_output
    assert step.api_url and step.model_name
    assert step.input_tokens > 0 and step.output_tokens > 0
    assert step.timestamp


def test_the_totals_come_from_the_steps(tmp_path):
    canned = LLMResponse(
        text=reply("print(1)"), input_tokens=100, output_tokens=20,
        latency_ms=5.0, requests=3, retries=2, api_url="u", model="m",
    )
    llm = FakeLLM([canned, canned])
    sandbox = StubSandbox(
        ExecutionResult(stdout="1"),
        ExecutionResult(stdout=PASSED, final_answer=SOLUTION),
    )
    # The second answer must differ, or the repeat guard refuses to run it.
    llm._responses[1] = LLMResponse(**{**canned.__dict__, "text": reply("print(2)")})
    solution, _writer = run_agent(llm, sandbox, tmp_path=tmp_path)

    assert solution.iterations == 2
    assert solution.total_input_tokens == 200
    assert solution.total_output_tokens == 40
    assert solution.total_requests == 6  # (1 + 2 retries) twice
    assert solution.total_time_seconds >= 0


# --- failure modes the model must hear about -------------------------------


def test_a_reply_without_code_is_explained_and_the_run_continues(tmp_path):
    llm = FakeLLM([reply(None), reply(f"final_answer({SOLUTION!r})")])
    sandbox = StubSandbox(ExecutionResult(stdout=PASSED, final_answer=SOLUTION))
    solution, _writer = run_agent(llm, sandbox, tmp_path=tmp_path)

    assert solution.success is True
    assert solution.iterations == 2
    first = solution.steps[0]
    assert first.sandbox_input == "" and first.sandbox_output == ""
    assert "No valid code block" in llm.calls[1]["messages"][-1].content
    assert sandbox.executed == [f"final_answer({SOLUTION!r})"]


def test_identical_code_is_not_run_twice(tmp_path):
    llm = FakeLLM([reply("print(1)"), reply("print(1)"), reply("print(2)")])
    sandbox = StubSandbox(ExecutionResult(stdout="1"), ExecutionResult(stdout="2"))
    config = AgentConfig(max_iterations=3)
    solution, _writer = run_agent(llm, sandbox, config, tmp_path=tmp_path)

    assert sandbox.executed == ["print(1)", "print(2)"]
    assert solution.steps[1].sandbox_input == ""
    assert "did not run it again" in llm.calls[2]["messages"][-1].content


def test_a_timeout_is_passed_through_as_an_observation(tmp_path):
    timed_out = ExecutionResult(
        stdout="partial output",
        error="Timeout: execution took longer than 5 s. Output printed so far was kept;",
    )
    llm = FakeLLM([reply("while True: pass"), reply(f"final_answer({SOLUTION!r})")])
    sandbox = StubSandbox(timed_out, ExecutionResult(final_answer=SOLUTION))
    solution, _writer = run_agent(llm, sandbox, tmp_path=tmp_path)

    observation = llm.calls[1]["messages"][-1].content
    assert "partial output" in observation and "Timeout" in observation
    assert solution.steps[0].sandbox_output == observation


def test_long_output_is_truncated_with_a_marker():
    outcome = ExecutionResult(stdout="x" * 5_000)
    observation = build_observation(outcome, [], 500)

    assert "output truncated" in observation
    assert len(observation) < 700


def test_extraction_notes_reach_the_model(tmp_path):
    llm = FakeLLM(["Thought: go\n```python\nfinal_answer('x')"])  # unclosed fence
    sandbox = StubSandbox(ExecutionResult(final_answer="x"))
    solution, _writer = run_agent(llm, sandbox, tmp_path=tmp_path)

    assert solution.success is True
    assert "never closed" in solution.steps[0].sandbox_output


# --- limits ----------------------------------------------------------------


def test_the_input_budget_stops_the_run_before_the_call(tmp_path):
    llm = FakeLLM([reply("print(1)")])
    sandbox = StubSandbox()
    config = AgentConfig(max_input_tokens=5)
    solution, writer = run_agent(llm, sandbox, config, tmp_path=tmp_path)

    assert llm.call_count == 0
    assert solution.iterations == 0 and solution.success is False
    assert "input tokens" in solution.error
    assert writer.writes == 1  # a valid file exists even with no steps


def test_the_iteration_limit_ends_the_run(tmp_path):
    llm = FakeLLM([reply(None)], repeat_last=True)
    sandbox = StubSandbox()
    config = AgentConfig(max_iterations=3)
    solution, _writer = run_agent(llm, sandbox, config, tmp_path=tmp_path)

    assert solution.iterations == 3
    assert "3 iterations" in solution.error
    assert solution.success is False


def test_the_deadline_ends_the_run(tmp_path):
    elapsed = {"now": 0.0}

    def clock():
        """Every look at the clock costs four seconds, so the deadline arrives."""
        elapsed["now"] += 4.0
        return elapsed["now"]

    llm = FakeLLM([reply("print(1)"), reply("print(2)"), reply("print(3)")])
    sandbox = StubSandbox(
        ExecutionResult(stdout="1"), ExecutionResult(stdout="2"), ExecutionResult(stdout="3")
    )
    config = AgentConfig(deadline_seconds=10)
    solution, _writer = run_agent(llm, sandbox, config, tmp_path=tmp_path, clock=clock)

    assert "ran out of time" in solution.error
    assert 1 <= solution.iterations <= 2
    assert llm.call_count == solution.iterations


def test_max_tokens_shrinks_with_the_output_budget(tmp_path):
    canned = LLMResponse(
        text=reply("print(1)"), input_tokens=10, output_tokens=900,
        latency_ms=1.0, api_url="u", model="m",
    )
    second = LLMResponse(**{**canned.__dict__, "text": reply("print(2)")})
    llm = FakeLLM([canned, second])
    sandbox = StubSandbox(ExecutionResult(stdout="1"), ExecutionResult(stdout="2"))
    config = AgentConfig(max_output_tokens=1_000, max_tokens_per_call=700, max_iterations=5)
    solution, _writer = run_agent(llm, sandbox, config, tmp_path=tmp_path)

    assert llm.calls[0]["max_tokens"] == 700
    assert llm.calls[1]["max_tokens"] == 100  # only 100 of the output budget left
    assert "output tokens" in solution.error


def test_a_run_that_never_submits_says_so(tmp_path):
    llm = FakeLLM([reply("print(1)")])
    sandbox = StubSandbox(ExecutionResult(stdout="1"))
    config = AgentConfig(max_iterations=1)
    solution, _writer = run_agent(llm, sandbox, config, tmp_path=tmp_path)

    assert solution.success is False and solution.solution == ""
    assert "1 iterations" in solution.error
    assert "final_answer() was never called" in solution.error


# --- the run never crashes -------------------------------------------------


def test_a_dead_provider_becomes_an_error_in_the_file(tmp_path):
    llm = FakeLLM([LLMError("all keys are rate limited")])
    sandbox = StubSandbox()
    solution, _writer = run_agent(llm, sandbox, tmp_path=tmp_path)

    assert solution.success is False and solution.iterations == 0
    assert "could not be reached" in solution.error
    on_disk = SolutionOutput.model_validate_json((tmp_path / "solution.json").read_text())
    assert on_disk.error == solution.error


def test_an_unexpected_failure_becomes_an_error_in_the_file(tmp_path):
    llm = FakeLLM([reply("print(1)")])
    sandbox = StubSandbox(RuntimeError("the worker exploded"))
    solution, _writer = run_agent(llm, sandbox, tmp_path=tmp_path)

    assert solution.success is False
    assert "RuntimeError" in solution.error and "exploded" in solution.error


def test_an_interruption_writes_the_file_and_then_propagates(tmp_path):
    llm = FakeLLM([reply("print(1)")])
    sandbox = StubSandbox(KeyboardInterrupt())
    writer = SolutionWriter(tmp_path / "solution.json")
    agent = Agent(llm, sandbox, AgentConfig(), writer=writer)

    with pytest.raises(KeyboardInterrupt):
        agent.run("SYSTEM", "TASK", task_id="11")

    saved = SolutionOutput.model_validate_json((tmp_path / "solution.json").read_text())
    assert "interrupted" in saved.error


def test_the_file_is_rewritten_after_every_step(tmp_path):
    llm = FakeLLM([reply("print(1)"), reply("print(2)"), reply(f"final_answer({SOLUTION!r})")])
    sandbox = StubSandbox(
        ExecutionResult(stdout="1"),
        ExecutionResult(stdout="2"),
        ExecutionResult(final_answer=SOLUTION),
    )
    solution, writer = run_agent(llm, sandbox, tmp_path=tmp_path)

    assert solution.iterations == 3
    assert writer.writes == 4  # one per step, plus the final write


def test_the_written_file_is_never_half_written(tmp_path):
    """Atomic means: no temporary files left behind, and valid JSON on disk."""
    llm = FakeLLM([reply(f"final_answer({SOLUTION!r})")])
    sandbox = StubSandbox(ExecutionResult(final_answer=SOLUTION))
    run_agent(llm, sandbox, tmp_path=tmp_path)

    files = sorted(path.name for path in tmp_path.iterdir())
    assert files == ["solution.json"]
    json.loads((tmp_path / "solution.json").read_text())


# --- what the grader's `display` checks ------------------------------------


def test_the_trace_passes_the_graders_consistency_rules(tmp_path):
    llm = FakeLLM([reply(None), reply("print(1)"), reply(f"final_answer({SOLUTION!r})")])
    sandbox = StubSandbox(
        ExecutionResult(stdout="1"),
        ExecutionResult(stdout=PASSED, final_answer=SOLUTION),
    )
    solution, _writer = run_agent(llm, sandbox, tmp_path=tmp_path)

    assert solution.system_prompt
    assert all(step.llm_output for step in solution.steps)
    for step in solution.steps:
        if step.sandbox_output:
            assert step.sandbox_input
    timestamps = [step.timestamp for step in solution.steps]
    assert timestamps == sorted(timestamps)
    assert len({step.model_name for step in solution.steps}) == 1
    pairs = zip(solution.steps, solution.steps[1:])
    assert all(
        not (a.sandbox_input and b.sandbox_input and a.sandbox_input == b.sandbox_input)
        for a, b in pairs
    )


# --- the budget on its own -------------------------------------------------


def test_the_budget_reports_the_first_limit_it_meets():
    budget = Budget(AgentConfig(max_iterations=1))
    assert budget.why_stop(10) is None

    budget.record_step()
    assert "1 iterations" in budget.why_stop(10)


def test_the_budget_clamps_the_next_answer_size():
    budget = Budget(AgentConfig(max_output_tokens=500, max_tokens_per_call=700))
    assert budget.max_tokens_for_next_call() == 500

    budget.record(LLMResponse(text="", input_tokens=0, output_tokens=480, latency_ms=0))
    assert budget.max_tokens_for_next_call() == 20
    assert "output tokens" in budget.why_stop(1)


def test_the_budget_counts_retried_requests():
    budget = Budget(AgentConfig())
    budget.record(LLMResponse(text="", input_tokens=10, output_tokens=5, latency_ms=0, requests=3, retries=2))

    assert budget.requests == 3
    assert budget.summary().startswith("step 0/10")


# --- prompts and config ----------------------------------------------------


def test_the_system_prompt_holds_the_format_the_rules_and_the_manual():
    prompt = build_system_prompt("MANUAL HERE", extra_rules=["be brief"])

    assert "Thought:" in prompt and "<end_code>" in prompt
    assert "final_answer(solution)" in prompt
    assert "MANUAL HERE" in prompt
    assert "- be brief" in prompt


def test_the_system_prompt_stays_small_enough_to_afford():
    from agent_smith.llm.base import estimate_tokens

    prompt = build_system_prompt("Tools:\n  run_tests(code: str, test_list: list)\n")
    assert estimate_tokens(prompt) < 500


def test_the_task_message_shows_the_signature_and_the_tests():
    task = MBPPTaskInput.model_validate_json(SAMPLE.read_text())
    message = build_task_message(task)

    assert "def remove_Occ(s, ch):" in message
    assert 'assert remove_Occ("hello","l") == "heo"' in message
    assert "test_list" in message


def test_the_prelude_defines_the_tests_in_the_sandbox():
    task = MBPPTaskInput.model_validate_json(SAMPLE.read_text())
    prelude = build_prelude(task)

    namespace = {}
    exec(prelude, namespace)  # noqa: S102 - our own generated code, in a test
    assert len(namespace["test_list"]) == 3


def test_the_committed_agent_config_stays_under_the_mbpp_limits():
    config = load_json_model("configs/agent_mbpp.json", AgentConfig)

    assert config.max_iterations <= 10
    assert config.max_input_tokens <= 6_000
    assert config.max_output_tokens <= 1_500
    assert config.deadline_seconds <= 120
    assert "<end_code>" in config.stop


def test_a_typo_in_the_agent_config_is_an_error(tmp_path):
    path = tmp_path / "agent.json"
    path.write_text('{"max_iteratons": 3}')

    with pytest.raises(ConfigError):
        load_json_model(path, AgentConfig)


# --- the real thing --------------------------------------------------------


def test_a_scripted_model_solves_a_task_through_the_real_sandbox(tmp_path):
    """No stubs below the LLM: real worker process, real MCP server, real tools."""
    from agent_smith.mcp_client.bridge import MCPBridge
    from agent_smith.models import SandboxConfig
    from agent_smith.sandbox.controller import Sandbox
    from agent_smith.sandbox.manual import build_manual

    task = MBPPTaskInput.model_validate_json(SAMPLE.read_text())
    answer = (
        "def remove_Occ(s, ch):\\n"
        "    first = s.find(ch)\\n"
        "    if first != -1:\\n"
        "        s = s[:first] + s[first + 1:]\\n"
        "    last = s.rfind(ch)\\n"
        "    if last != -1:\\n"
        "        s = s[:last] + s[last + 1:]\\n"
        "    return s"
    )
    scripted = (
        "Thought: write it, test it, submit it.\n"
        "```python\n"
        "import json\n"
        f'solution = "{answer}"\n'
        "report = json.loads(run_tests(solution, test_list))\n"
        "print(report)\n"
        'if report["success"]:\n'
        "    final_answer(solution)\n"
        "```<end_code>"
    )

    bridge = MCPBridge().connect_stdio("python mcp_tools_mbpp.py")
    writer = SolutionWriter(tmp_path / "solution.json")
    try:
        with Sandbox(SandboxConfig(), bridge) as sandbox:
            agent = Agent(FakeLLM([scripted]), sandbox, AgentConfig(), writer=writer)
            solution = agent.run(
                build_system_prompt(build_manual(sandbox.config, sandbox.tool_specs, True)),
                build_task_message(task),
                task_id=str(task.task_id),
                prelude=build_prelude(task),
            )
    finally:
        bridge.close()

    assert solution.success is True, solution.error
    assert "def remove_Occ" in solution.solution
    assert "'success': True" in solution.steps[0].sandbox_output
    assert solution.iterations == 1

    # The solution really passes the task's tests.
    namespace = {}
    exec(solution.solution, namespace)  # noqa: S102
    for assertion in task.test_list:
        exec(assertion, namespace)  # noqa: S102
