"""Counting what a run has spent, and refusing the step that would overspend.

MBPP allows 6,000 input tokens for the *whole* task, and every call resends the
whole conversation, so the cost of step N is roughly the sum of everything said
before it. That is the single fact that shapes this file: the budget has to be
checked *before* a call, with an estimate, because afterwards it is too late.
"""
import time
from typing import Callable, Optional

from agent_smith.llm.base import LLMResponse, estimate_tokens
from agent_smith.models import AgentConfig

#: Below this, an answer cannot hold a solution plus a `final_answer` call.
MINIMUM_USEFUL_OUTPUT_TOKENS = 64


class Budget:
    """Iterations, tokens and wall-clock time for one task.

    The clock is injected so the deadline paths can be tested without waiting.
    """

    def __init__(self, config: AgentConfig, clock: Callable[[], float] = time.monotonic):
        self.config = config
        self._clock = clock
        self.started = clock()
        self.deadline = self.started + config.deadline_seconds
        self.input_tokens = 0
        self.output_tokens = 0
        self.requests = 0
        self.steps = 0

    def record(self, response: LLMResponse) -> None:
        """Add what one call cost. Retried requests count: the provider charged them."""
        self.input_tokens += response.input_tokens
        self.output_tokens += response.output_tokens
        self.requests += response.requests

    def record_step(self) -> None:
        self.steps += 1

    def elapsed(self) -> float:
        return self._clock() - self.started

    def time_left(self) -> float:
        return self.deadline - self._clock()

    def remaining_input(self) -> int:
        return self.config.max_input_tokens - self.input_tokens

    def remaining_output(self) -> int:
        return self.config.max_output_tokens - self.output_tokens

    def max_tokens_for_next_call(self) -> int:
        """What to put in `max_tokens`: never more output budget than is left."""
        return max(0, min(self.config.max_tokens_per_call, self.remaining_output()))

    def estimate(self, text: str) -> int:
        return estimate_tokens(text)

    def why_stop(self, estimated_input: int) -> Optional[str]:
        """The reason not to make another call, or None to go ahead.

        The reason is a sentence, not a code: it goes straight into
        `SolutionOutput.error`, where a human reads it.
        """
        if self.steps >= self.config.max_iterations:
            return f"stopped after {self.steps} iterations, the configured maximum"
        if self.time_left() <= 0:
            return (
                f"ran out of time after {self.elapsed():.1f} s "
                f"(budget {self.config.deadline_seconds:.0f} s)"
            )
        if self.remaining_output() < MINIMUM_USEFUL_OUTPUT_TOKENS:
            return (
                f"ran out of output tokens: {self.output_tokens} of "
                f"{self.config.max_output_tokens} used"
            )
        if estimated_input > self.remaining_input():
            return (
                f"the next prompt would need about {estimated_input} input tokens but only "
                f"{self.remaining_input()} of {self.config.max_input_tokens} are left"
            )
        return None

    def summary(self) -> str:
        """One line for a log: where the run stands right now."""
        return (
            f"step {self.steps}/{self.config.max_iterations}, "
            f"tokens {self.input_tokens}/{self.config.max_input_tokens} in and "
            f"{self.output_tokens}/{self.config.max_output_tokens} out, "
            f"{self.time_left():.0f} s left"
        )
