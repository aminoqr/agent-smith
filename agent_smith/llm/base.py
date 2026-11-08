"""What every LLM client in this project promises to do.

The agent loop only ever sees `ChatMessage` in and `LLMResponse` out. Swapping
provider, or swapping the real client for a fake one in a test, changes nothing
above this line.
"""
from dataclasses import dataclass, field
from typing import List, Optional, Protocol, Sequence


class LLMError(Exception):
    """A call could not be completed, after every retry we were willing to make."""


class AllKeysExhausted(LLMError):
    """Every API key is rate limited, disabled, or would cool down past the deadline."""


@dataclass(frozen=True)
class ChatMessage:
    """One turn of the conversation, in the roles the chat APIs understand."""

    role: str
    content: str

    def __post_init__(self) -> None:
        if self.role not in ("system", "user", "assistant"):
            raise ValueError(f"unknown chat role: {self.role!r}")

    def as_dict(self) -> dict:
        return {"role": self.role, "content": self.content}


@dataclass
class LLMResponse:
    """One completed call, plus everything `StepMetrics` wants to know about it.

    `requests` counts every HTTP request the call needed and `retries` is
    `requests - 1`, which is exactly the subject's definition: the number of
    retries before a successful response, so 0 means the first attempt worked.
    """

    text: str
    input_tokens: int
    output_tokens: int
    latency_ms: float
    requests: int = 1
    retries: int = 0
    api_url: str = ""
    model: str = ""
    finish_reason: str = ""
    tokens_estimated: bool = False
    attempts: List[str] = field(default_factory=list)


class LLMClient(Protocol):
    """A structural type: anything with this `complete` is an LLM client.

    A Protocol rather than a base class, so the fake client in the tests does
    not have to inherit from anything to be accepted by the agent loop.
    """

    def complete(
        self,
        messages: Sequence[ChatMessage],
        *,
        stop: Optional[Sequence[str]] = None,
        max_tokens: Optional[int] = None,
        deadline: Optional[float] = None,
    ) -> LLMResponse:
        ...


def estimate_tokens(text: str) -> int:
    """Rough token count for providers that return no `usage` block.

    Four characters per token is the usual English approximation. Anything
    counted this way is flagged with `tokens_estimated`, because the budget in
    `SolutionOutput` would otherwise look more precise than it is.
    """
    return max(1, (len(text) + 3) // 4) if text else 0
