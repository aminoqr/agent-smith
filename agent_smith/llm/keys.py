"""Rotating several API keys of one provider, because free tiers are small.

The subject makes multi-key support mandatory: one key runs out of quota long
before a benchmark run is done. This pool knows three things about a key: it is
usable, it is cooling down after a 429, or it is dead (401/403). Nothing here
makes HTTP calls, so every branch is testable with a fake clock.
"""
import os
import time
from dataclasses import dataclass, field
from typing import Callable, Dict, Iterable, List, Optional, Sequence, Tuple

from agent_smith.config import ConfigError
from agent_smith.llm.base import AllKeysExhausted
from agent_smith.llm.providers import ProviderConfig

DEFAULT_COOLDOWN_SECONDS = 20.0


def mask(value: str) -> str:
    """A key, safe to print: enough to tell two keys apart, not enough to use.

    Logs normally show the environment variable name instead; this exists for
    the case where the value itself is the only identifier available.
    """
    if len(value) <= 8:
        return "*" * len(value)
    return f"{value[:3]}...{value[-4:]}"


@dataclass
class KeyState:
    """One key and what we have learned about it during this run."""

    name: str
    value: str = field(repr=False)
    cooldown_until: float = 0.0
    dead_reason: Optional[str] = None
    uses: int = 0

    @property
    def is_dead(self) -> bool:
        return self.dead_reason is not None

    def is_cooling(self, now: float) -> bool:
        return self.cooldown_until > now

    def __str__(self) -> str:
        return f"{self.name} ({mask(self.value)})"


class KeyPool:
    """Round-robin over the keys of one provider, with cooldowns.

    Round-robin rather than "use key 1 until it dies" so that requests spread
    over the quotas instead of burning them one at a time; on a per-minute limit
    that alone often avoids the 429.
    """

    def __init__(
        self,
        keys: Sequence[Tuple[str, str]] | Sequence[KeyState],
        *,
        default_cooldown: float = DEFAULT_COOLDOWN_SECONDS,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self.keys: List[KeyState] = [
            key if isinstance(key, KeyState) else KeyState(name=key[0], value=key[1])
            for key in keys
        ]
        if not self.keys:
            raise ConfigError("A KeyPool needs at least one key")
        self.default_cooldown = default_cooldown
        self._clock = clock
        self._sleep = sleep
        self._cursor = 0

    @classmethod
    def from_env(
        cls,
        provider: ProviderConfig,
        env: Optional[Dict[str, str]] = None,
        **kwargs,
    ) -> "KeyPool":
        """Collect the provider's keys from the environment.

        Variables that are unset or empty are skipped, and the same value listed
        twice counts once: duplicated keys would make the pool look healthier
        than it is, and both copies share one quota anyway.
        """
        environment = os.environ if env is None else env
        found: List[Tuple[str, str]] = []
        seen: set = set()
        for name in provider.key_env_vars:
            value = (environment.get(name) or "").strip()
            if not value or value in seen:
                continue
            seen.add(value)
            found.append((name, value))
        if not found:
            wanted = ", ".join(provider.key_env_vars)
            raise ConfigError(
                f"No API key for provider '{provider.name}'. "
                f"Set one of these environment variables (see .env.example): {wanted}"
            )
        return cls(found, **kwargs)

    def next_key(self, deadline: Optional[float] = None) -> KeyState:
        """The next usable key, waiting for a cooldown only if the deadline allows.

        `deadline` is a `clock()` timestamp, normally the agent's wall-clock
        budget. Waiting past it would be pointless: the run would be killed
        before the answer arrived, so we fail fast with `AllKeysExhausted`.
        """
        while True:
            now = self._clock()
            usable = self._pick(now)
            if usable is not None:
                usable.uses += 1
                return usable

            alive = [key for key in self.keys if not key.is_dead]
            if not alive:
                raise AllKeysExhausted(f"every API key was rejected: {self._reasons()}")

            wake_at = min(key.cooldown_until for key in alive)
            if deadline is not None and wake_at > deadline:
                raise AllKeysExhausted(
                    f"all {len(alive)} key(s) are rate limited for another "
                    f"{wake_at - now:.1f}s, past the deadline"
                )
            self._sleep(max(0.0, wake_at - now))

    def report_rate_limited(self, key: KeyState, retry_after: Optional[float] = None) -> None:
        """A 429: park this key for `Retry-After` seconds, or a default guess."""
        wait = self.default_cooldown if retry_after is None else max(0.0, retry_after)
        key.cooldown_until = self._clock() + wait

    def report_invalid(self, key: KeyState, reason: str = "rejected (401/403)") -> None:
        """A 401/403: the key is wrong or revoked, so retrying it wastes attempts."""
        key.dead_reason = reason

    def report_success(self, key: KeyState) -> None:
        """A 200 clears any leftover cooldown on that key."""
        key.cooldown_until = 0.0

    def describe(self) -> str:
        """One line per key, with names and masked values, for logs."""
        now = self._clock()
        parts = []
        for key in self.keys:
            if key.is_dead:
                status = f"dead: {key.dead_reason}"
            elif key.is_cooling(now):
                status = f"cooling {key.cooldown_until - now:.1f}s"
            else:
                status = "ready"
            parts.append(f"{key} {status}, {key.uses} use(s)")
        return "; ".join(parts)

    def _pick(self, now: float) -> Optional[KeyState]:
        """Scan one full turn of the wheel from the cursor."""
        count = len(self.keys)
        for offset in range(count):
            index = (self._cursor + offset) % count
            key = self.keys[index]
            if not key.is_dead and not key.is_cooling(now):
                self._cursor = (index + 1) % count
                return key
        return None

    def _reasons(self) -> str:
        return "; ".join(f"{key.name}: {key.dead_reason}" for key in self.keys)


def all_keys(provider: ProviderConfig, env: Optional[Dict[str, str]] = None) -> Iterable[str]:
    """The provider's key variable names that are actually set. Names only."""
    environment = os.environ if env is None else env
    return [name for name in provider.key_env_vars if (environment.get(name) or "").strip()]
