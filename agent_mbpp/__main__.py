"""`uv run python -m agent_mbpp --task-file ... --output ...`

The contract the grader relies on (V.3, VI):

* `--task-file` and `--output` are the only required arguments. `quickstart.sh`
  omits `--model-name` and `--provider-url` unless you pass them, so both fall
  back to `configs/providers.json`.
* **A `solution.json` is always written**, even when the run fails, and the
  exit code is then 0. `moulinette_eval run-agent` treats a non-zero exit as a
  failure and `quickstart.sh` runs under `set -e`, so exiting non-zero on an
  unsolved task would abort the pipeline before validation - and validation is
  exactly what should report the failure.
* SIGTERM (the grader sends it to the whole process group, then SIGKILL ten
  seconds later) must leave a valid file behind.
"""
import argparse
import signal
import sys
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from agent_smith.agent import Agent, SolutionWriter, build_prelude, build_system_prompt
from agent_smith.agent.output import build_solution
from agent_smith.agent.prompts import build_task_message
from agent_smith.config import ConfigError, load_env_file, load_json_model
from agent_smith.llm import KeyPool, OpenAICompatibleClient, load_providers, resolve_provider
from agent_smith.mcp_client.bridge import MCPBridge, MCPError
from agent_smith.models import AgentConfig, MBPPTaskInput, SandboxConfig
from agent_smith.sandbox.controller import Sandbox
from agent_smith.sandbox.manual import build_manual

DEFAULT_MCP_COMMAND = "python mcp_tools_mbpp.py"


class Terminated(SystemExit):
    """SIGTERM arrived. Raised in the main thread so `finally` blocks still run."""


def main(argv: Optional[List[str]] = None) -> int:
    args = _parse(argv)
    load_env_file(args.env_file)
    writer = SolutionWriter(args.output)

    try:
        task = load_json_model(args.task_file, MBPPTaskInput)
    except ConfigError as exc:
        # Without a task we do not even know the task_id, so there is nothing
        # honest to write. This is the one case that exits non-zero.
        print(f"error: {exc}", file=sys.stderr)
        return 2

    task_id = str(task.task_id)
    try:
        agent_config, client, bridge_command = _build(args)
    except ConfigError as exc:
        return _give_up(writer, task_id, f"configuration error: {exc}")

    signal.signal(signal.SIGTERM, _terminate)

    bridge = None
    try:
        bridge = MCPBridge().connect_stdio(bridge_command)
        with Sandbox(_sandbox_config(args), bridge) as sandbox:
            system_prompt = build_system_prompt(
                build_manual(sandbox.config, sandbox.tool_specs, mcp_connected=True)
            )
            agent = Agent(
                client,
                sandbox,
                agent_config,
                writer=writer,
                on_step=_reporter(args.quiet),
            )
            solution = agent.run(
                system_prompt,
                build_task_message(task),
                task_id=task_id,
                benchmark="mbpp",
                prelude=build_prelude(task),
            )
    except MCPError as exc:
        return _give_up(writer, task_id, f"the tool server could not be started: {exc}")
    except Terminated:
        # The loop already wrote the file on its way out.
        print("terminated: solution.json was written", file=sys.stderr)
        return 0
    finally:
        if bridge is not None:
            bridge.close()
        client.close()

    _report(solution, args.output)
    return 0


def _parse(argv: Optional[List[str]]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="python -m agent_mbpp", description="Solve one MBPP task.")
    parser.add_argument("--task-file", required=True, help="MBPPTaskInput JSON from the moulinette")
    parser.add_argument("--output", required=True, help="where to write solution.json")
    parser.add_argument("--model-name", default=None, help="default: the provider's first model")
    parser.add_argument("--provider-url", default=None, help="default: the first provider in providers.json")
    parser.add_argument("--config", default="configs/agent_mbpp.json", help="AgentConfig JSON")
    parser.add_argument("--sandbox-config", default="sandbox_template.json")
    parser.add_argument("--providers", default="configs/providers.json")
    parser.add_argument("--env-file", default=".env")
    parser.add_argument("--max-iterations", type=int, default=None, help="override the config")
    parser.add_argument("--mcp-stdio", default=DEFAULT_MCP_COMMAND)
    parser.add_argument("--quiet", action="store_true", help="no per-step lines on stderr")
    return parser.parse_args(argv)


def _build(args: argparse.Namespace):
    """Everything that can fail on configuration, in one place."""
    agent_config = load_json_model(args.config, AgentConfig)
    if args.max_iterations is not None:
        agent_config = agent_config.model_copy(update={"max_iterations": args.max_iterations})

    provider = resolve_provider(load_providers(args.providers), args.provider_url)
    pool = KeyPool.from_env(provider)
    model = args.model_name or (provider.models[0] if provider.models else None)
    if model is None:
        raise ConfigError(f"provider '{provider.name}' lists no models; pass --model-name")

    client = OpenAICompatibleClient(
        provider.base_url,
        model,
        pool,
        temperature=agent_config.temperature,
        read_timeout=min(60.0, agent_config.deadline_seconds),
    )
    return agent_config, client, args.mcp_stdio


def _sandbox_config(args: argparse.Namespace) -> SandboxConfig:
    """A missing sandbox file is not fatal: the defaults are the subject's."""
    if Path(args.sandbox_config).exists():
        return load_json_model(args.sandbox_config, SandboxConfig)
    return SandboxConfig()


def _terminate(signum, _frame):
    raise Terminated(0)


def _give_up(writer: SolutionWriter, task_id: str, message: str) -> int:
    """Write a valid, empty, honest solution file and let validation report it."""
    print(f"error: {message}", file=sys.stderr)
    writer.write(
        build_solution(
            task_id=task_id,
            benchmark="mbpp",
            success=False,
            solution="",
            steps=[],
            total_time_seconds=0.0,
            system_prompt="",
            error=message,
        )
    )
    return 0


def _reporter(quiet: bool):
    if quiet:
        return None

    def report(step, budget):
        print(f"  step {step.step}: {budget.summary()}", file=sys.stderr)

    return report


def _report(solution, output: str) -> None:
    print(f"task {solution.task_id}: {'solved' if solution.success else 'not solved'}")
    print(
        f"iterations {solution.iterations}, "
        f"tokens {solution.total_input_tokens} in / {solution.total_output_tokens} out, "
        f"requests {solution.total_requests}, {solution.total_time_seconds:.1f}s"
    )
    if solution.error:
        print(f"error: {solution.error}")
    print(f"written {output} at {datetime.now().isoformat(timespec='seconds')}")


if __name__ == "__main__":
    raise SystemExit(main())
