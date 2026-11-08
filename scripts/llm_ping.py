"""Real LLM calls, to prove the providers and their keys work.

One provider:

    uv run python scripts/llm_ping.py --provider groq --verbose

Every provider that has a key set, which is the M6 check ("works against two
different providers"):

    uv run python scripts/llm_ping.py --all

Prints the answer and the usage numbers the agent later records in StepMetrics.
Keys are never printed, only the variable names they came from and a mask.
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent_smith.config import ConfigError, load_env_file  # noqa: E402
from agent_smith.llm import (  # noqa: E402
    ChatMessage,
    KeyPool,
    LLMError,
    OpenAICompatibleClient,
    ProviderConfig,
    load_providers,
    resolve_provider,
)
from agent_smith.llm.keys import all_keys  # noqa: E402
from agent_smith.llm.openai_compat import RetryPolicy  # noqa: E402

DEFAULT_PROMPT = "Reply with exactly: pong"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Send one chat completion to a provider.")
    parser.add_argument("--provider-url", default=None, help="base URL; default: first in providers.json")
    parser.add_argument("--provider", default=None, help="provider name from providers.json")
    parser.add_argument("--model-name", default=None, help="model id; default: first listed for the provider")
    parser.add_argument("--providers", default="configs/providers.json")
    parser.add_argument("--all", action="store_true", help="ping every provider that has a key set")
    parser.add_argument("--prompt", default=DEFAULT_PROMPT)
    parser.add_argument("--max-tokens", type=int, default=64)
    parser.add_argument("--stop", action="append", default=None, help="stop sequence (repeatable)")
    parser.add_argument("--max-attempts", type=int, default=3)
    parser.add_argument("--verbose", action="store_true", help="log each attempt and key")
    args = parser.parse_args(argv)

    load_env_file()
    try:
        catalogue = load_providers(args.providers)
        if args.all:
            chosen = list(catalogue.providers)
        elif args.provider:
            chosen = [_by_name(catalogue, args.provider)]
        else:
            chosen = [resolve_provider(catalogue, args.provider_url)]
    except ConfigError as exc:
        print(f"config: {exc}", file=sys.stderr)
        return 2

    results = [_ping(provider, args) for provider in chosen]

    if not args.all:
        return 0 if results[0]["ok"] else (2 if results[0]["skipped"] else 1)

    print()
    print(f"{'provider':<20} {'model':<34} result")
    print("-" * 72)
    for outcome in results:
        print(f"{outcome['provider']:<20} {outcome['model'][:34]:<34} {outcome['summary']}")
    working = sum(1 for outcome in results if outcome["ok"])
    print()
    if working >= 2:
        print(f"{working} providers answered. The M6 check wants at least two: satisfied.")
        return 0
    print(
        f"only {working} provider(s) answered. The M6 check wants at least two, so add keys "
        f"for another provider (see .env.example) or pick a model the provider still serves.",
        file=sys.stderr,
    )
    return 1


def _ping(provider: ProviderConfig, args) -> dict:
    """One call to one provider. Never raises: `--all` must survive a dead provider."""
    outcome = {"provider": provider.name, "model": args.model_name or "", "ok": False, "skipped": False}

    if not all_keys(provider):
        outcome["skipped"] = True
        outcome["summary"] = f"skipped: no key in {', '.join(provider.key_env_vars)}"
        if not args.all:
            print(
                f"config: No API key for provider '{provider.name}'. Set one of these environment "
                f"variables (see .env.example): {', '.join(provider.key_env_vars)}",
                file=sys.stderr,
            )
        return outcome

    model = args.model_name or (provider.models[0] if provider.models else None)
    if model is None:
        outcome["summary"] = "no model: pass --model-name"
        return outcome
    outcome["model"] = model

    pool = KeyPool.from_env(provider)
    print(f"provider: {provider.name}  url: {provider.base_url}  model: {model}")
    print(f"keys:     {pool.describe()}")
    client = OpenAICompatibleClient(
        provider.base_url,
        model,
        pool,
        retry=RetryPolicy(max_attempts=args.max_attempts),
        on_event=(lambda line: print(f"  . {line}")) if args.verbose else None,
    )
    try:
        answer = client.complete(
            [ChatMessage("user", args.prompt)],
            stop=args.stop,
            max_tokens=args.max_tokens,
        )
    except LLMError as exc:
        print(f"failed:   {exc}", file=sys.stderr)
        print(f"keys:     {pool.describe()}", file=sys.stderr)
        outcome["summary"] = f"FAIL  {_short(str(exc))}"
        return outcome
    finally:
        client.close()

    marker = " (estimated)" if answer.tokens_estimated else ""
    print("-" * 60)
    print(answer.text.strip() or "(empty answer)")
    print("-" * 60)
    print(f"input tokens:  {answer.input_tokens}{marker}")
    print(f"output tokens: {answer.output_tokens}{marker}")
    print(f"latency:       {answer.latency_ms:.0f} ms")
    print(f"requests:      {answer.requests} (retries: {answer.retries})")
    print(f"finish reason: {answer.finish_reason}")
    print(f"attempts:      {'; '.join(answer.attempts)}")
    print()
    outcome["ok"] = True
    outcome["summary"] = (
        f"ok    {answer.input_tokens} in / {answer.output_tokens} out{marker}, "
        f"{answer.latency_ms:.0f} ms, {answer.retries} retr{'y' if answer.retries == 1 else 'ies'}"
    )
    return outcome


def _by_name(catalogue, name: str) -> ProviderConfig:
    for provider in catalogue.providers:
        if provider.name == name:
            return provider
    known = ", ".join(provider.name for provider in catalogue.providers)
    raise ConfigError(f"Unknown provider '{name}'. Known providers: {known}")


def _short(text: str, limit: int = 44) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[:limit] + "..."


if __name__ == "__main__":
    raise SystemExit(main())
