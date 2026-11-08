"""One real LLM call, to prove a provider and its keys work.

    uv run python scripts/llm_ping.py --provider-url https://api.groq.com/openai/v1 \
        --model-name llama-3.3-70b-versatile

Prints the answer and the usage numbers the agent will later record in
StepMetrics. Keys are never printed, only the variable names they came from.
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
    load_providers,
    resolve_provider,
)

DEFAULT_PROMPT = "Reply with exactly: pong"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Send one chat completion to a provider.")
    parser.add_argument("--provider-url", default=None, help="base URL; default: first in providers.json")
    parser.add_argument("--model-name", default=None, help="model id; default: first listed for the provider")
    parser.add_argument("--providers", default="configs/providers.json")
    parser.add_argument("--prompt", default=DEFAULT_PROMPT)
    parser.add_argument("--max-tokens", type=int, default=64)
    parser.add_argument("--stop", action="append", default=None, help="stop sequence (repeatable)")
    parser.add_argument("--max-attempts", type=int, default=3)
    parser.add_argument("--verbose", action="store_true", help="log each attempt and key")
    args = parser.parse_args(argv)

    load_env_file()
    try:
        providers = load_providers(args.providers)
        provider = resolve_provider(providers, args.provider_url)
        pool = KeyPool.from_env(provider)
    except ConfigError as exc:
        print(f"config: {exc}", file=sys.stderr)
        return 2

    model = args.model_name or (provider.models[0] if provider.models else None)
    if model is None:
        print(f"config: provider '{provider.name}' lists no models; pass --model-name", file=sys.stderr)
        return 2

    from agent_smith.llm.openai_compat import RetryPolicy

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
        print(f"failed: {exc}", file=sys.stderr)
        print(f"keys:   {pool.describe()}", file=sys.stderr)
        return 1
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
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
