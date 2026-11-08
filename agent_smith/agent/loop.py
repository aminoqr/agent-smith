"""The agent loop: Thought, Code, Observation, until an answer or a limit.

The loop itself is short. The engineering around it is the interesting part:
the budget has to be checked before every call, every failure mode has to come
back to the model as words it can act on, and the trace we write has to be
truthful enough that a reviewer can replay what happened.

It knows nothing about MBPP. It is handed a system prompt, a task message and a
sandbox, so the same loop will serve SWE-bench in phase 2.
"""
import time
from datetime import datetime
from typing import Callable, List, Optional, Sequence

from agent_smith.agent.budget import Budget
from agent_smith.agent.extraction import extract
from agent_smith.agent.output import SolutionWriter, build_solution
from agent_smith.agent.prompts import REPEATED_CODE_FEEDBACK, build_observation
from agent_smith.llm.base import ChatMessage, LLMError, LLMResponse
from agent_smith.models import AgentConfig, SolutionOutput, StepMetrics

#: What we put in the conversation when a provider returns an empty answer.
#: `StepMetrics.llm_output` keeps the real (empty) text; this is only so the
#: next request is not sent with an empty assistant turn, which some reject.
EMPTY_ANSWER_PLACEHOLDER = "(your previous answer was empty)"

NO_ANSWER_SUFFIX = "final_answer() was never called, so there is no solution"


class Agent:
    """One task, one sandbox, one conversation."""

    def __init__(
        self,
        llm,
        sandbox,
        config: Optional[AgentConfig] = None,
        *,
        writer: Optional[SolutionWriter] = None,
        on_step: Optional[Callable[[StepMetrics, Budget], None]] = None,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.llm = llm
        self.sandbox = sandbox
        self.config = config or AgentConfig()
        self.writer = writer
        self._on_step = on_step
        self._clock = clock

    def run(
        self,
        system_prompt: str,
        task_message: str,
        *,
        task_id: str,
        benchmark: str = "mbpp",
        prelude: str = "",
    ) -> SolutionOutput:
        """Work the task and return the trace. This never raises on its own.

        Any unexpected exception becomes `error` in a valid `SolutionOutput`,
        because the grader reads the file, not our stack trace. Ctrl+C and
        SystemExit are different: the file is written, then they are re-raised,
        so the shell still learns the process was interrupted.
        """
        budget = Budget(self.config, clock=self._clock)
        steps: List[StepMetrics] = []
        messages = [ChatMessage("system", system_prompt), ChatMessage("user", task_message)]
        tools = self._tool_names()
        success = False
        solution = ""
        error: Optional[str] = None
        last_code: Optional[str] = None

        def snapshot() -> SolutionOutput:
            return build_solution(
                task_id=task_id,
                benchmark=benchmark,
                success=success,
                solution=solution,
                steps=steps,
                total_time_seconds=budget.elapsed(),
                system_prompt=system_prompt,
                error=error,
            )

        try:
            if prelude:
                self.sandbox.execute(prelude)

            while True:
                conversation = "".join(message.content for message in messages)
                stop_reason = budget.why_stop(budget.estimate(conversation))
                if stop_reason is not None:
                    error = stop_reason
                    break

                try:
                    response = self.llm.complete(
                        messages,
                        stop=self.config.stop,
                        max_tokens=budget.max_tokens_for_next_call(),
                        deadline=budget.deadline,
                    )
                except LLMError as exc:
                    error = f"the language model could not be reached: {exc}"
                    break
                budget.record(response)

                found = extract(response.text, tools)
                code = found.code or ""
                repeated = bool(code) and code == last_code

                if not code:
                    # No code means nothing ran, so `sandbox_input` and
                    # `sandbox_output` stay empty and the explanation goes into
                    # the conversation instead. The grader checks exactly that.
                    reply = found.feedback()
                    executed, sandbox_output = "", ""
                elif repeated:
                    reply = REPEATED_CODE_FEEDBACK
                    executed, sandbox_output = "", ""
                else:
                    outcome = self.sandbox.execute(code)
                    reply = build_observation(outcome, found.notes, self.config.observation_max_chars)
                    executed, sandbox_output = code, reply
                    last_code = code
                    if outcome.final_answer is not None:
                        success = True
                        solution = outcome.final_answer

                step = self._record(len(steps) + 1, response, executed, sandbox_output)
                steps.append(step)
                budget.record_step()
                messages.append(ChatMessage("assistant", response.text or EMPTY_ANSWER_PLACEHOLDER))
                messages.append(ChatMessage("user", reply))

                if self._on_step is not None:
                    self._on_step(step, budget)
                self._save(snapshot())

                if success:
                    break

            if not success:
                # Both halves matter to whoever reads the file: why we stopped,
                # and that nothing was submitted.
                error = f"{error}; {NO_ANSWER_SUFFIX}" if error else NO_ANSWER_SUFFIX
        except (KeyboardInterrupt, SystemExit):
            error = "the run was interrupted"
            self._save(snapshot())
            raise
        except Exception as exc:  # noqa: BLE001 - the grader needs a file, not a traceback
            error = f"the agent failed: {type(exc).__name__}: {exc}"

        final = snapshot()
        self._save(final)
        return final

    def _tool_names(self) -> Sequence[str]:
        """The names the model may call: the server's tools plus `final_answer`."""
        specs = getattr(self.sandbox, "tool_specs", ()) or ()
        return tuple(spec.python_name for spec in specs) + ("final_answer",)

    def _record(
        self,
        number: int,
        response: LLMResponse,
        sandbox_input: str,
        sandbox_output: str,
    ) -> StepMetrics:
        return StepMetrics(
            step=number,
            input_tokens=response.input_tokens,
            output_tokens=response.output_tokens,
            request_time_ms=response.latency_ms,
            timestamp=datetime.now().isoformat(),
            api_url=response.api_url,
            model_name=response.model,
            llm_output=response.text,
            sandbox_input=sandbox_input,
            sandbox_output=sandbox_output,
            retries=response.retries,
        )

    def _save(self, solution: SolutionOutput) -> None:
        if self.writer is not None:
            self.writer.write(solution)
