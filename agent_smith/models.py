"""Data models for Agent Smith.

The grader's schema lives in models_public.py, copied unchanged from the
moulinette. This module re-exports it and overrides one thing: the default
sandbox configuration, which the subject defines but the public file leaves empty.
"""
from typing import List

from pydantic import ConfigDict, Field

from pydantic import BaseModel

from agent_smith.models_public import MBPPTaskInput
from agent_smith.models_public import SandboxConfig as _SandboxConfigBase
from agent_smith.models_public import SolutionOutput, StepMetrics, SWEBenchTaskInput


class SandboxConfig(_SandboxConfigBase):
    """Sandbox configuration with the subject's default allowlist."""

    authorized_imports: List[str] = Field(default_factory=lambda: [
        "math", "math.*",
        "collections", "collections.*",
        "itertools", "re", "json",
        "typing", "typing.*",
        "functools", "operator",
        "heapq", "bisect", "copy",
        "string", "random",
        "datetime", "datetime.*",
        "array", "cmath",
    ])
    allowed_directories: List[str] = Field(default_factory=lambda: [
        "/testbed", "/tmp/agent",
    ])


class AgentConfig(BaseModel):
    """Limits for one agent run.

    Ours, not the grader's, so a typo is an error rather than a silent default.
    The defaults sit under the MBPP limits (10 iterations, 6,000 input tokens,
    1,500 output tokens, 120 s) with a margin, because the grader kills the
    process at its limit and a killed run has no `solution.json` worth reading.
    """

    model_config = ConfigDict(extra="forbid")

    max_iterations: int = Field(10, gt=0, description="Hard stop on Thought-Code-Observation cycles")
    max_input_tokens: int = Field(5_500, gt=0, description="Cumulative prompt tokens for the task")
    max_output_tokens: int = Field(1_400, gt=0, description="Cumulative generated tokens for the task")
    max_tokens_per_call: int = Field(700, gt=0, description="Cap on one answer, so one step cannot eat the budget")
    deadline_seconds: float = Field(100.0, gt=0, description="Wall-clock budget, below the grader's kill")
    observation_max_chars: int = Field(1_500, gt=0, description="Truncation size for what we feed back")
    max_repeated_code: int = Field(3, gt=0, description="Consecutive identical code blocks before giving up")
    temperature: float = Field(0.0, ge=0.0, le=2.0)
    stop: List[str] = Field(
        default_factory=lambda: ["<end_code>", "Observation:"],
        description="Stop sequences, so the model waits for real output instead of inventing it",
    )


__all__ = [
    "AgentConfig",
    "MBPPTaskInput",
    "SandboxConfig",
    "SolutionOutput",
    "StepMetrics",
    "SWEBenchTaskInput",
]
