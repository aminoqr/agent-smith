"""Data models for Agent Smith.

The grader's schema lives in models_public.py, copied unchanged from the
moulinette. This module re-exports it and overrides one thing: the default
sandbox configuration, which the subject defines but the public file leaves empty.
"""
from typing import List

from pydantic import Field

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


__all__ = [
    "MBPPTaskInput",
    "SandboxConfig",
    "SolutionOutput",
    "StepMetrics",
    "SWEBenchTaskInput",
]
