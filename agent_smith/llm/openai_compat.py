"""One client for every provider that speaks the OpenAI chat-completions shape.

OpenRouter, Groq, Together, Cerebras, Mistral, Fireworks and a local llama.cpp
all accept `POST {base_url}/chat/completions` with the same JSON. That is why
switching provider in this project is a config change and not a code change.

What this file owns: building the request, deciding which failures are worth
another attempt, and counting what `StepMetrics` needs. Which key to use is the
`KeyPool`'s decision, not this client's.
"""
import json
import random as _random
import time
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
from typing import Callable, List, Optional, Sequence

import httpx

from agent_smith.llm.base import ChatMessage, LLMError, LLMResponse, estimate_tokens
from agent_smith.llm.keys import KeyPool, KeyState

RETRYABLE_STATUSES = frozenset({408, 409, 425, 500, 502, 503, 504, 529})


@dataclass
class RetryPolicy:
    """How stubborn to be. `max_attempts` counts every HTTP request we send."""

    max_attempts: int = 4
    base_delay: float = 0.5
    max_delay: float = 8.0
    jitter: bool = True

    def delay_for(self, attempt: int, rand: Callable[[], float]) -> float:
        """Exponential backoff with jitter: 0.5s, 1s, 2s, 4s, each randomised.

        Jitter matters because our own retries are synchronised by the thing
        that failed. Without it, parallel runs (five models in the benchmark)
        would all come back at the same instant and rebuild the same spike that
        caused the 503 in the first place.
        """
        plain = min(self.max_delay, self.base_delay * (2 ** max(0, attempt - 1)))
        return plain * (0.5 + rand()) if self.jitter else plain


class OpenAICompatibleClient:
    """An `LLMClient` for any OpenAI-compatible endpoint.

    Everything non-deterministic is injectable (`transport`, `clock`, `sleep`,
    `rand`), which is what makes the retry and rotation tests fast and real:
    they drive actual `httpx` request handling through `httpx.MockTransport`
    instead of patching our own methods.
    """

    def __init__(
        self,
        base_url: str,
        model: str,
        keys: KeyPool,
        *,
        temperature: float = 0.2,
        retry: Optional[RetryPolicy] = None,
        connect_timeout: float = 10.0,
        read_timeout: float = 120.0,
        transport: Optional[httpx.BaseTransport] = None,
        extra_headers: Optional[dict] = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        rand: Callable[[], float] = _random.random,
        on_event: Optional[Callable[[str], None]] = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.keys = keys
        self.temperature = temperature
        self.retry = retry or RetryPolicy()
        self.extra_headers = dict(extra_headers or {})
        self._clock = clock
        self._sleep = sleep
        self._rand = rand
        self._on_event = on_event
        self._http = httpx.Client(
            transport=transport,
            timeout=httpx.Timeout(read_timeout, connect=connect_timeout),
        )

    def complete(
        self,
        messages: Sequence[ChatMessage],
        *,
        stop: Optional[Sequence[str]] = None,
        max_tokens: Optional[int] = None,
        deadline: Optional[float] = None,
    ) -> LLMResponse:
        """Send the conversation, return the text and the usage numbers.

        `deadline` is a `clock()` timestamp. It bounds both waiting for a
        cooling key and sleeping between retries, so a call can never outlive
        the agent's wall-clock budget by more than one in-flight request.
        """
        payload = self._payload(messages, stop, max_tokens)
        started = time.perf_counter()
        attempts: List[str] = []
        last_problem = "no attempt was made"

        for attempt in range(1, self.retry.max_attempts + 1):
            if deadline is not None and self._clock() >= deadline:
                raise LLMError(f"out of time before attempt {attempt}: {last_problem}")
            key = self.keys.next_key(deadline=deadline)
            self._announce(f"request {attempt} with {key}")
            try:
                response = self._http.post(
                    f"{self.base_url}/chat/completions",
                    json=payload,
                    headers=self._headers(key),
                )
            except httpx.TimeoutException as exc:
                last_problem = f"timeout: {exc.__class__.__name__}"
                attempts.append(f"{key.name}: {last_problem}")
                self._backoff(attempt, deadline)
                continue
            except httpx.HTTPError as exc:
                last_problem = f"transport error: {exc}"
                attempts.append(f"{key.name}: {last_problem}")
                self._backoff(attempt, deadline)
                continue

            status = response.status_code
            if status == 200:
                try:
                    parsed = self._parse(response, payload)
                except LLMError as exc:
                    last_problem = str(exc)
                    attempts.append(f"{key.name}: {last_problem}")
                    self._backoff(attempt, deadline)
                    continue
                self.keys.report_success(key)
                attempts.append(f"{key.name}: 200")
                parsed.latency_ms = (time.perf_counter() - started) * 1000
                parsed.requests = attempt
                parsed.retries = attempt - 1
                parsed.attempts = attempts
                return parsed

            detail = _short(response.text)
            if status == 429:
                retry_after = _retry_after(response)
                self.keys.report_rate_limited(key, retry_after)
                last_problem = f"429 rate limited (retry after {retry_after})"
                attempts.append(f"{key.name}: {last_problem}")
                continue  # another key may be ready right now; no sleep here
            if status in (401, 403):
                self.keys.report_invalid(key, f"HTTP {status}: {detail}")
                last_problem = f"{status} key rejected"
                attempts.append(f"{key.name}: {last_problem}")
                continue
            if status in RETRYABLE_STATUSES:
                last_problem = f"HTTP {status}: {detail}"
                attempts.append(f"{key.name}: {last_problem}")
                self._backoff(attempt, deadline)
                continue

            # 400, 404, 422: our request is wrong. Retrying sends the same
            # broken request again, so fail loudly with the provider's words.
            raise LLMError(f"{self.base_url} rejected the request: HTTP {status}: {detail}")

        raise LLMError(
            f"giving up after {self.retry.max_attempts} attempt(s) to {self.base_url}: {last_problem}"
        )

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> "OpenAICompatibleClient":
        return self

    def __exit__(self, *_exc) -> None:
        self.close()

    def _payload(
        self,
        messages: Sequence[ChatMessage],
        stop: Optional[Sequence[str]],
        max_tokens: Optional[int],
    ) -> dict:
        payload = {
            "model": self.model,
            "messages": [message.as_dict() for message in messages],
            "temperature": self.temperature,
        }
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens
        if stop:
            # Providers commonly cap this at four strings and some ignore it
            # entirely, so extraction must never depend on it (see M7).
            payload["stop"] = list(stop)[:4]
        return payload

    def _headers(self, key: KeyState) -> dict:
        headers = {
            "Authorization": f"Bearer {key.value}",
            "Content-Type": "application/json",
        }
        headers.update(self.extra_headers)
        return headers

    def _parse(self, response: httpx.Response, payload: dict) -> LLMResponse:
        try:
            body = response.json()
        except (json.JSONDecodeError, ValueError) as exc:
            raise LLMError(f"200 with a body that is not JSON: {exc}") from exc
        choices = body.get("choices") or []
        if not choices:
            raise LLMError(f"200 with no choices: {_short(response.text)}")
        message = choices[0].get("message") or {}
        text = message.get("content") or ""
        usage = body.get("usage") or {}
        prompt_tokens = usage.get("prompt_tokens")
        completion_tokens = usage.get("completion_tokens")
        estimated = prompt_tokens is None or completion_tokens is None
        if estimated:
            sent = "".join(m["content"] for m in payload["messages"])
            prompt_tokens = estimate_tokens(sent)
            completion_tokens = estimate_tokens(text)
        return LLMResponse(
            text=text,
            input_tokens=int(prompt_tokens),
            output_tokens=int(completion_tokens),
            latency_ms=0.0,
            api_url=self.base_url,
            model=body.get("model") or self.model,
            finish_reason=choices[0].get("finish_reason") or "",
            tokens_estimated=estimated,
        )

    def _backoff(self, attempt: int, deadline: Optional[float]) -> None:
        if attempt >= self.retry.max_attempts:
            return
        wait = self.retry.delay_for(attempt, self._rand)
        if deadline is not None:
            wait = min(wait, max(0.0, deadline - self._clock()))
        self._announce(f"waiting {wait:.2f}s before attempt {attempt + 1}")
        self._sleep(wait)

    def _announce(self, line: str) -> None:
        if self._on_event is not None:
            self._on_event(line)


def _retry_after(response: httpx.Response) -> Optional[float]:
    """`Retry-After` is either a number of seconds or an HTTP date."""
    raw = response.headers.get("retry-after")
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError:
        pass
    try:
        when = parsedate_to_datetime(raw)
    except (TypeError, ValueError):
        return None
    if when is None:
        return None
    import datetime as _dt

    now = _dt.datetime.now(tz=when.tzinfo or _dt.timezone.utc)
    return max(0.0, (when - now).total_seconds())


def _short(text: str, limit: int = 200) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= limit else text[:limit] + "..."
