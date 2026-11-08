"""Tests for the LLM provider layer.

No network: `httpx.MockTransport` answers the requests, so real `httpx` code
builds and sends them and we still control every status code. Time is faked
too, so rate-limit and backoff behaviour is tested in milliseconds.
"""
import json

import httpx
import pytest

from agent_smith.config import ConfigError, load_env_file
from agent_smith.llm import (
    AllKeysExhausted,
    ChatMessage,
    FakeLLM,
    KeyPool,
    LLMError,
    LLMResponse,
    OpenAICompatibleClient,
    ProvidersFile,
    load_providers,
    mask,
    resolve_provider,
)
from agent_smith.llm.keys import KeyState
from agent_smith.llm.openai_compat import RetryPolicy

URL = "https://example.test/v1"
KEY_1 = "not-a-real-key-first-0001"
KEY_2 = "not-a-real-key-second-002"


class FakeClock:
    """A clock that only moves when something sleeps on it."""

    def __init__(self):
        self.now = 1000.0
        self.slept = []

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


def reply(content="hello", *, prompt=11, completion=7, finish="stop", model="served-model"):
    body = {
        "model": model,
        "choices": [{"message": {"role": "assistant", "content": content}, "finish_reason": finish}],
        "usage": {"prompt_tokens": prompt, "completion_tokens": completion},
    }
    return httpx.Response(200, json=body)


def build(responses, *, keys=((("K1", KEY_1)), ("K2", KEY_2)), max_attempts=4, clock=None, **kwargs):
    """A client whose transport replays `responses`, recording every request."""
    clock = clock or FakeClock()
    seen = []
    queue = list(responses)

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        answer = queue.pop(0) if queue else reply()
        if isinstance(answer, Exception):
            raise answer
        return answer

    pool = KeyPool(list(keys), clock=clock, sleep=clock.sleep, default_cooldown=20.0)
    client = OpenAICompatibleClient(
        URL,
        "test-model",
        pool,
        transport=httpx.MockTransport(handler),
        retry=RetryPolicy(max_attempts=max_attempts, base_delay=1.0, jitter=False),
        clock=clock,
        sleep=clock.sleep,
        rand=lambda: 0.5,
        **kwargs,
    )
    return client, pool, seen, clock


def ask(client, **kwargs):
    return client.complete([ChatMessage("user", "ping")], **kwargs)


# --- the happy path and the request we actually send -----------------------


def test_a_successful_call_reports_text_and_usage():
    client, _pool, seen, _clock = build([reply("pong", prompt=120, completion=8)])
    answer = ask(client)

    assert answer.text == "pong"
    assert (answer.input_tokens, answer.output_tokens) == (120, 8)
    assert (answer.requests, answer.retries) == (1, 0)
    assert answer.api_url == URL
    assert answer.model == "served-model"
    assert answer.finish_reason == "stop"
    assert answer.tokens_estimated is False
    assert answer.latency_ms > 0
    assert len(seen) == 1
    assert str(seen[0].url) == f"{URL}/chat/completions"


def test_the_payload_has_the_openai_shape():
    client, _pool, seen, _clock = build([reply()])
    ask(client, max_tokens=256, stop=["<end_code>", "Observation:"])

    body = json.loads(seen[0].content)
    assert body["model"] == "test-model"
    assert body["messages"] == [{"role": "user", "content": "ping"}]
    assert body["max_tokens"] == 256
    assert body["stop"] == ["<end_code>", "Observation:"]
    assert "temperature" in body


def test_more_than_four_stop_sequences_are_cut():
    client, _pool, seen, _clock = build([reply()])
    ask(client, stop=["a", "b", "c", "d", "e"])

    assert json.loads(seen[0].content)["stop"] == ["a", "b", "c", "d"]


def test_no_max_tokens_and_no_stop_means_the_fields_are_absent():
    client, _pool, seen, _clock = build([reply()])
    ask(client)

    body = json.loads(seen[0].content)
    assert "max_tokens" not in body and "stop" not in body


def test_the_key_travels_as_a_bearer_token():
    client, _pool, seen, _clock = build([reply()])
    ask(client)

    assert seen[0].headers["authorization"] == f"Bearer {KEY_1}"


def test_missing_usage_is_estimated_and_flagged():
    body = {"choices": [{"message": {"content": "x" * 40}, "finish_reason": "stop"}]}
    client, _pool, _seen, _clock = build([httpx.Response(200, json=body)])
    answer = ask(client)

    assert answer.tokens_estimated is True
    assert answer.output_tokens == 10  # 40 characters / 4
    assert answer.input_tokens > 0


# --- rotation --------------------------------------------------------------


def test_a_rate_limited_key_is_skipped_and_the_next_one_serves():
    client, pool, seen, clock = build(
        [httpx.Response(429, headers={"retry-after": "30"}, text="slow down"), reply("pong")]
    )
    answer = ask(client)

    assert answer.text == "pong"
    assert (answer.requests, answer.retries) == (2, 1)
    assert seen[0].headers["authorization"] == f"Bearer {KEY_1}"
    assert seen[1].headers["authorization"] == f"Bearer {KEY_2}"
    assert pool.keys[0].is_cooling(clock.now)
    assert clock.slept == []  # rotating costs no time


def test_retry_after_sets_the_cooldown_length():
    client, pool, _seen, clock = build(
        [httpx.Response(429, headers={"retry-after": "42"}, text=""), reply()]
    )
    ask(client)

    assert pool.keys[0].cooldown_until == pytest.approx(clock.now + 42)


def test_a_429_without_retry_after_uses_the_default_cooldown():
    client, pool, _seen, clock = build([httpx.Response(429, text=""), reply()])
    ask(client)

    assert pool.keys[0].cooldown_until == pytest.approx(clock.now + pool.default_cooldown)


def test_a_rejected_key_is_disabled_not_retried():
    client, pool, seen, _clock = build([httpx.Response(401, text="bad key"), reply("pong")])
    answer = ask(client)

    assert answer.text == "pong"
    assert pool.keys[0].is_dead and not pool.keys[1].is_dead
    assert seen[1].headers["authorization"] == f"Bearer {KEY_2}"


def test_when_every_key_is_rejected_the_pool_says_so():
    client, pool, _seen, _clock = build(
        [httpx.Response(403, text="forbidden"), httpx.Response(403, text="forbidden")]
    )
    with pytest.raises(AllKeysExhausted) as caught:
        ask(client)

    assert all(key.is_dead for key in pool.keys)
    assert "403" in str(caught.value)


def test_keys_never_appear_in_an_error_message():
    client, _pool, _seen, _clock = build(
        [httpx.Response(401, text="invalid api key"), httpx.Response(401, text="invalid api key")]
    )
    with pytest.raises(AllKeysExhausted) as caught:
        ask(client)

    assert KEY_1 not in str(caught.value) and KEY_2 not in str(caught.value)


def test_requests_are_spread_over_the_keys_in_turn():
    client, _pool, seen, _clock = build([reply(), reply(), reply()])
    for _ in range(3):
        ask(client)

    used = [request.headers["authorization"] for request in seen]
    assert used == [f"Bearer {KEY_1}", f"Bearer {KEY_2}", f"Bearer {KEY_1}"]


def test_cooling_keys_are_waited_for_when_there_is_time():
    clock = FakeClock()
    client, pool, _seen, _clock = build(
        [httpx.Response(429, headers={"retry-after": "5"}, text=""),
         httpx.Response(429, headers={"retry-after": "5"}, text=""),
         reply("pong")],
        clock=clock,
    )
    answer = ask(client)

    assert answer.text == "pong"
    assert clock.slept == [5.0]
    assert answer.retries == 2
    assert pool.keys[0].uses == 2


def test_cooling_keys_past_the_deadline_fail_immediately():
    """Understanding check: both keys cool for 5s, the deadline is 3s away."""
    clock = FakeClock()
    client, _pool, _seen, _clock = build(
        [httpx.Response(429, headers={"retry-after": "5"}, text=""),
         httpx.Response(429, headers={"retry-after": "5"}, text="")],
        clock=clock,
    )
    with pytest.raises(AllKeysExhausted) as caught:
        ask(client, deadline=clock.now + 3)

    assert clock.slept == []
    assert "deadline" in str(caught.value)


# --- retries ---------------------------------------------------------------


def test_a_server_error_is_retried_after_a_backoff():
    clock = FakeClock()
    client, _pool, seen, _clock = build(
        [httpx.Response(503, text="overloaded"), reply("pong")], clock=clock
    )
    answer = ask(client)

    assert (answer.text, answer.retries) == ("pong", 1)
    assert clock.slept == [1.0]
    assert len(seen) == 2


def test_a_timeout_is_retried():
    clock = FakeClock()
    client, _pool, _seen, _clock = build(
        [httpx.ReadTimeout("too slow"), reply("pong")], clock=clock
    )
    answer = ask(client)

    assert (answer.text, answer.retries) == ("pong", 1)
    assert clock.slept == [1.0]


def test_a_200_with_no_choices_is_retried():
    client, _pool, _seen, _clock = build([httpx.Response(200, json={"choices": []}), reply("pong")])
    answer = ask(client)

    assert (answer.text, answer.retries) == ("pong", 1)


def test_giving_up_after_the_last_attempt():
    clock = FakeClock()
    client, _pool, seen, _clock = build(
        [httpx.Response(500, text="boom")] * 3, max_attempts=3, clock=clock
    )
    with pytest.raises(LLMError) as caught:
        ask(client)

    assert len(seen) == 3
    assert "3 attempt" in str(caught.value) and "500" in str(caught.value)
    assert clock.slept == [1.0, 2.0]  # no sleep after the final attempt


def test_a_bad_request_is_not_retried():
    client, _pool, seen, _clock = build([httpx.Response(400, text="unknown model")])
    with pytest.raises(LLMError) as caught:
        ask(client)

    assert len(seen) == 1
    assert "unknown model" in str(caught.value)


def test_backoff_grows_and_jitter_stays_inside_the_window():
    policy = RetryPolicy(base_delay=1.0, max_delay=8.0, jitter=False)
    assert [policy.delay_for(n, lambda: 0.0) for n in (1, 2, 3, 4, 5)] == [1.0, 2.0, 4.0, 8.0, 8.0]

    jittered = RetryPolicy(base_delay=1.0, jitter=True)
    assert jittered.delay_for(2, lambda: 0.0) == pytest.approx(1.0)
    assert jittered.delay_for(2, lambda: 1.0) == pytest.approx(3.0)


def test_each_attempt_can_be_logged_without_leaking_the_key():
    lines = []
    client, _pool, _seen, _clock = build(
        [httpx.Response(429, text=""), reply()], on_event=lines.append
    )
    ask(client)

    assert any("K1" in line for line in lines) and any("K2" in line for line in lines)
    assert not any(KEY_1 in line or KEY_2 in line for line in lines)


# --- the key pool on its own -----------------------------------------------


def test_the_pool_reads_only_the_variables_the_provider_names(tmp_path):
    provider = _provider(["A_KEY", "B_KEY", "C_KEY"])
    pool = KeyPool.from_env(provider, env={"A_KEY": "one", "B_KEY": "  ", "C_KEY": "two", "D_KEY": "x"})

    assert [key.name for key in pool.keys] == ["A_KEY", "C_KEY"]


def test_the_same_key_listed_twice_counts_once():
    provider = _provider(["A_KEY", "B_KEY"])
    pool = KeyPool.from_env(provider, env={"A_KEY": "same", "B_KEY": "same"})

    assert [key.name for key in pool.keys] == ["A_KEY"]


def test_no_key_at_all_is_a_config_error_that_names_the_variables():
    provider = _provider(["A_KEY", "B_KEY"])
    with pytest.raises(ConfigError) as caught:
        KeyPool.from_env(provider, env={})

    assert "A_KEY" in str(caught.value) and "B_KEY" in str(caught.value)


def test_masking_keeps_the_middle_hidden():
    assert mask(KEY_1) == "not...0001"
    assert mask("short") == "*****"
    assert KEY_1 not in mask(KEY_1)


def test_describe_shows_status_without_the_values():
    clock = FakeClock()
    pool = KeyPool([("K1", KEY_1), ("K2", KEY_2)], clock=clock, sleep=clock.sleep)
    pool.report_rate_limited(pool.keys[0], 10)
    pool.report_invalid(pool.keys[1])
    line = pool.describe()

    assert "cooling" in line and "dead" in line
    assert KEY_1 not in line and KEY_2 not in line


def test_a_success_clears_a_leftover_cooldown():
    clock = FakeClock()
    pool = KeyPool([("K1", KEY_1)], clock=clock, sleep=clock.sleep)
    pool.report_rate_limited(pool.keys[0], 10)
    pool.report_success(pool.keys[0])

    assert pool.next_key() is pool.keys[0]


def test_a_key_state_never_prints_its_value():
    state = KeyState("K1", KEY_1)
    assert KEY_1 not in repr(state) and KEY_1 not in str(state)


# --- providers.json --------------------------------------------------------


def test_the_committed_providers_file_is_valid():
    providers = load_providers("configs/providers.json")

    assert len(providers.providers) >= 2
    assert all(provider.key_env_vars for provider in providers.providers)
    assert all("KEY" in name for provider in providers.providers for name in provider.key_env_vars)


def test_a_provider_is_found_by_url_despite_a_trailing_slash_or_case():
    providers = load_providers("configs/providers.json")
    found = resolve_provider(providers, "https://API.groq.com/openai/v1/")

    assert found.name == "groq"


def test_no_url_means_the_first_provider():
    providers = load_providers("configs/providers.json")
    assert resolve_provider(providers, None) is providers.providers[0]


def test_an_unknown_url_lists_the_known_ones():
    providers = load_providers("configs/providers.json")
    with pytest.raises(ConfigError) as caught:
        resolve_provider(providers, "https://nope.test/v1")

    assert "Known URLs" in str(caught.value) and "openrouter" in str(caught.value)


def test_a_typo_in_providers_json_is_rejected():
    with pytest.raises(Exception):
        ProvidersFile.model_validate(
            {"providers": [{"name": "x", "base_url": "https://x.test/v1",
                            "key_env_vars": ["X_KEY"], "modles": []}]}
        )


def test_a_provider_needs_at_least_one_key_variable():
    with pytest.raises(Exception):
        ProvidersFile.model_validate(
            {"providers": [{"name": "x", "base_url": "https://x.test/v1", "key_env_vars": []}]}
        )


def test_a_base_url_must_be_http():
    with pytest.raises(Exception):
        ProvidersFile.model_validate(
            {"providers": [{"name": "x", "base_url": "ftp://x.test", "key_env_vars": ["X_KEY"]}]}
        )


def test_two_providers_cannot_share_a_name():
    entry = {"name": "x", "base_url": "https://x.test/v1", "key_env_vars": ["X_KEY"]}
    with pytest.raises(Exception):
        ProvidersFile.model_validate({"providers": [entry, dict(entry, base_url="https://y.test/v1")]})


# --- .env ------------------------------------------------------------------


def test_the_env_file_fills_only_missing_variables(tmp_path):
    path = tmp_path / ".env"
    path.write_text(
        "# a comment\n\nexport A_KEY=from-file\nB_KEY='quoted'\nC_KEY=kept\nnot a pair\n"
    )
    env = {"C_KEY": "from-the-real-environment"}
    loaded = load_env_file(path, env=env)

    assert sorted(loaded) == ["A_KEY", "B_KEY"]
    assert env["A_KEY"] == "from-file"
    assert env["B_KEY"] == "quoted"
    assert env["C_KEY"] == "from-the-real-environment"


def test_a_missing_env_file_is_not_an_error(tmp_path):
    assert load_env_file(tmp_path / "nope") == []


def test_the_example_env_file_has_names_but_no_values():
    lines = [line.strip() for line in open(".env.example", encoding="utf-8")]
    pairs = [line for line in lines if line and not line.startswith("#")]

    assert pairs, ".env.example should list the variable names"
    assert all(line.endswith("=") for line in pairs)


# --- the fake client -------------------------------------------------------


def test_the_fake_client_replays_its_script_and_records_the_calls():
    fake = FakeLLM(["first", "second"])

    assert fake.complete([ChatMessage("user", "a")]).text == "first"
    assert fake.complete([ChatMessage("user", "b")], stop=["X"], max_tokens=10).text == "second"
    assert fake.call_count == 2
    assert fake.calls[1]["stop"] == ["X"] and fake.calls[1]["max_tokens"] == 10
    assert fake.last_prompt() == "b"


def test_the_fake_client_complains_when_the_script_runs_out():
    fake = FakeLLM(["only one"])
    fake.complete([ChatMessage("user", "a")])

    with pytest.raises(LLMError):
        fake.complete([ChatMessage("user", "b")])


def test_the_fake_client_can_repeat_or_raise_on_purpose():
    looping = FakeLLM(["again"], repeat_last=True)
    assert [looping.complete([ChatMessage("user", "x")]).text for _ in range(3)] == ["again"] * 3

    angry = FakeLLM([LLMError("provider is down")])
    with pytest.raises(LLMError):
        angry.complete([ChatMessage("user", "x")])


def test_the_fake_client_can_hand_back_a_full_response():
    canned = LLMResponse(text="t", input_tokens=5, output_tokens=6, latency_ms=1.0, retries=2)
    fake = FakeLLM([canned])

    assert fake.complete([ChatMessage("user", "x")]).retries == 2


def test_chat_messages_reject_an_unknown_role():
    with pytest.raises(ValueError):
        ChatMessage("tool", "x")


def _provider(key_env_vars):
    from agent_smith.llm import ProviderConfig

    return ProviderConfig(name="p", base_url="https://p.test/v1", key_env_vars=key_env_vars)


# --- the ping script, which is the M6 "done when" check --------------------


def _ping_script():
    """Import scripts/llm_ping.py by path: it is a script, not a package module."""
    import importlib.util

    spec = importlib.util.spec_from_file_location("llm_ping", "scripts/llm_ping.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _lonely_providers_file(tmp_path):
    """A providers file whose keys are guaranteed unset, so no real call happens."""
    path = tmp_path / "providers.json"
    path.write_text(json.dumps({"providers": [
        {"name": "nowhere", "base_url": "https://nowhere.test/v1",
         "key_env_vars": ["AGENT_SMITH_TEST_KEY_UNSET"], "models": ["m"]},
    ]}))
    return str(path)


def test_the_ping_script_names_the_providers_it_knows(tmp_path):
    script = _ping_script()
    code = script.main(["--provider", "typo", "--providers", _lonely_providers_file(tmp_path)])

    assert code == 2


def test_the_ping_script_skips_providers_without_keys(tmp_path, capsys):
    script = _ping_script()
    code = script.main(["--all", "--providers", _lonely_providers_file(tmp_path)])
    printed = capsys.readouterr()

    assert code == 1
    assert "skipped: no key" in printed.out
    assert "at least two" in printed.err


def test_the_ping_script_reports_a_missing_key_for_one_provider(tmp_path, capsys):
    script = _ping_script()
    code = script.main(["--provider", "nowhere", "--providers", _lonely_providers_file(tmp_path)])

    assert code == 2
    assert "AGENT_SMITH_TEST_KEY_UNSET" in capsys.readouterr().err
