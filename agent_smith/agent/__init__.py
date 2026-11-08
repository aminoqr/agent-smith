"""The agent side of the framework: extraction, budget, prompts, the loop."""
from agent_smith.agent.budget import Budget
from agent_smith.agent.extraction import ExtractionResult, extract
from agent_smith.agent.loop import Agent
from agent_smith.agent.output import SolutionWriter, build_solution
from agent_smith.agent.prompts import (
    build_observation,
    build_prelude,
    build_system_prompt,
    build_task_message,
)

__all__ = [
    "Agent",
    "Budget",
    "ExtractionResult",
    "SolutionWriter",
    "build_observation",
    "build_prelude",
    "build_solution",
    "build_system_prompt",
    "build_task_message",
    "extract",
]
