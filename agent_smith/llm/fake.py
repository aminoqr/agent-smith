"""A scripted LLM, so the agent loop can be tested without a network.

The loop tests in M7.5 are about the loop: does it extract code, does it feed
the observation back, does it stop at the budget. A real provider would make
those tests slow, flaky and quota-bound. `FakeLLM` satisfies the same
`LLMClient` Protocol and replays answers written by hand.
"""
import itertools
from typing import List, Optional, Sequence, Union

from agent_smith.llm.base import ChatMessage, LLMError, LLMResponse, estimate_tokens


class FakeLLM:
    """Hands out `responses` in order, and records what it was asked."""

    def __init__(
        self,
        responses: Sequence[Union[str, LLMResponse, Exception]],
        *,
        model: str = "fake-model",
        api_url: str = "fake://local",
        repeat_last: bool = False,
    ):
        self._responses = list(responses)
        self._index = 0
        self.model = model
        self.api_url = api_url
        self.repeat_last = repeat_last
        self.calls: List[dict] = []

    def complete(
        self,
        messages: Sequence[ChatMessage],
        *,
        stop: Optional[Sequence[str]] = None,
        max_tokens: Optional[int] = None,
        deadline: Optional[float] = None,
    ) -> LLMResponse:
        self.calls.append(
            {
                "messages": list(messages),
                "stop": list(stop) if stop else None,
                "max_tokens": max_tokens,
            }
        )
        scripted = self._take()
        if isinstance(scripted, Exception):
            raise scripted
        if isinstance(scripted, LLMResponse):
            return scripted
        sent = "".join(message.content for message in messages)
        return LLMResponse(
            text=scripted,
            input_tokens=estimate_tokens(sent),
            output_tokens=estimate_tokens(scripted),
            latency_ms=0.0,
            api_url=self.api_url,
            model=self.model,
            finish_reason="stop",
            tokens_estimated=True,
        )

    @property
    def call_count(self) -> int:
        return len(self.calls)

    def last_prompt(self) -> str:
        """The whole last conversation as text, handy for prompt assertions."""
        if not self.calls:
            raise LLMError("FakeLLM has not been called yet")
        return "\n".join(m.content for m in self.calls[-1]["messages"])

    def _take(self):
        if self._index < len(self._responses):
            scripted = self._responses[self._index]
            self._index += 1
            return scripted
        if self.repeat_last and self._responses:
            return self._responses[-1]
        raise LLMError(
            f"FakeLLM ran out of scripted responses after {len(self._responses)} call(s)"
        )


def scripted(*responses: str) -> FakeLLM:
    """Shorthand for `FakeLLM([...])` in tests."""
    return FakeLLM(list(itertools.chain(responses)))
