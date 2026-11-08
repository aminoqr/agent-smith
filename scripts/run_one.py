"""Solve one MBPP task with a real model and write solution.json.

    uv run python scripts/run_one.py --task-file tests/fixtures/mbpp_sample.json \
        --output ../cache/sample_solution.json --provider groq --verbose

This is the shape `python -m agent_mbpp` will have in M8; keeping it a script
first means the loop can be driven end to end before the CLI exists.
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent_smith.agent import Agent, SolutionWriter, build_prelude, build_system_prompt  # noqa: E402
from agent_smith.agent.prompts import build_task_message  # noqa: E402
from agent_smith.config import ConfigError, load_env_file, load_json_model  # noqa: E402
from agent_smith.llm import KeyPool, OpenAICompatibleClient, load_providers, resolve_provider  # noqa: E402
from agent_smith.mcp_client.bridge import MCPBridge, MCPError  # noqa: E402
from agent_smith.models import AgentConfig, MBPPTaskInput, SandboxConfig  # noqa: E402
from agent_smith.sandbox.controller import Sandbox  # noqa: E402
from agent_smith.sandbox.manual import build_manual  # noqa: E402


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Run the agent on one MBPP task.")
    parser.add_argument("--task-file", default="tests/fixtures/mbpp_sample.json")
    parser.add_argument("--output", default="../cache/sample_solution.json")
    parser.add_argument("--provider", default=None, help="provider name from providers.json")
    parser.add_argument("--provider-url", default=None, help="provider base URL")
    parser.add_argument("--model-name", default=None)
    parser.add_argument("--providers", default="configs/providers.json")
    parser.add_argument("--agent-config", default="configs/agent_mbpp.json")
    parser.add_argument("--sandbox-config", default="sandbox_template.json")
    parser.add_argument("--mcp-stdio", default="python mcp_tools_mbpp.py")
    parser.add_argument("--max-iterations", type=int, default=None, help="override the config")
    parser.add_argument("--verbose", action="store_true", help="one line per step on stderr")
    args = parser.parse_args(argv)

    load_env_file()
    try:
        task = load_json_model(args.task_file, MBPPTaskInput)
        agent_config = load_json_model(args.agent_config, AgentConfig)
        sandbox_config = load_json_model(args.sandbox_config, SandboxConfig)
        catalogue = load_providers(args.providers)
        provider = _provider(catalogue, args)
        pool = KeyPool.from_env(provider)
    except ConfigError as exc:
        print(f"config: {exc}", file=sys.stderr)
        return 2

    if args.max_iterations is not None:
        agent_config = agent_config.model_copy(update={"max_iterations": args.max_iterations})
    model = args.model_name or (provider.models[0] if provider.models else None)
    if model is None:
        print(f"config: provider '{provider.name}' lists no models; pass --model-name", file=sys.stderr)
        return 2

    llm = OpenAICompatibleClient(
        provider.base_url, model, pool, temperature=agent_config.temperature
    )
    writer = SolutionWriter(args.output)
    try:
        bridge = MCPBridge().connect_stdio(args.mcp_stdio)
    except MCPError as exc:
        print(f"mcp: {exc}", file=sys.stderr)
        return 2

    print(f"task {task.task_id} with {model} at {provider.base_url}", file=sys.stderr)
    try:
        with Sandbox(sandbox_config, bridge) as sandbox:
            agent = Agent(
                llm,
                sandbox,
                agent_config,
                writer=writer,
                on_step=_reporter(args.verbose),
            )
            solution = agent.run(
                build_system_prompt(build_manual(sandbox.config, sandbox.tool_specs, True)),
                build_task_message(task),
                task_id=str(task.task_id),
                benchmark="mbpp",
                prelude=build_prelude(task),
            )
    finally:
        bridge.close()
        llm.close()

    print(
        f"\nsuccess={solution.success} iterations={solution.iterations} "
        f"tokens={solution.total_input_tokens} in / {solution.total_output_tokens} out "
        f"requests={solution.total_requests} time={solution.total_time_seconds:.1f}s"
    )
    if solution.error:
        print(f"error: {solution.error}")
    print(f"written: {args.output}")
    return 0 if solution.success else 1


def _provider(catalogue, args):
    if args.provider:
        for provider in catalogue.providers:
            if provider.name == args.provider:
                return provider
        known = ", ".join(provider.name for provider in catalogue.providers)
        raise ConfigError(f"Unknown provider '{args.provider}'. Known providers: {known}")
    return resolve_provider(catalogue, args.provider_url)


def _reporter(verbose: bool):
    if not verbose:
        return None

    def report(step, budget):
        print(
            f"  step {step.step}: {step.input_tokens} in / {step.output_tokens} out, "
            f"{step.request_time_ms:.0f} ms, {step.retries} retries | {budget.summary()}",
            file=sys.stderr,
        )

    return report


if __name__ == "__main__":
    raise SystemExit(main())
