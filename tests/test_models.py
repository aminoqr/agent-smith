"""Tests for the data models."""
import pytest
from pydantic import ValidationError

from agent_smith.models import SandboxConfig, SolutionOutput, StepMetrics


def make_solution(**overrides) -> SolutionOutput:
    """Build a valid SolutionOutput, overriding any field for a test."""
    fields = dict(
        task_id="2",
        benchmark="mbpp",
        success=True,
        solution="def f(): return 1",
        iterations=1,
        total_requests=1,
        total_input_tokens=100,
        total_output_tokens=20,
        total_time_seconds=1.5,
    )
    fields.update(overrides)
    return SolutionOutput(**fields)


def test_solution_round_trip():
    step = StepMetrics(
        step=1, input_tokens=100, output_tokens=20, request_time_ms=350.0,
        llm_output="hi"
    )
    solution = make_solution(steps=[step])
    restored = SolutionOutput.model_validate_json(solution.model_dump_json())
    assert restored == solution


def test_sandbox_defaults_are_not_shared():
    first = SandboxConfig()
    second = SandboxConfig()
    first.authorized_imports.append("os")
    assert "os" not in second.authorized_imports


def test_solution_task_id_must_be_a_string():
    with pytest.raises(ValidationError):
        make_solution(task_id=2)
