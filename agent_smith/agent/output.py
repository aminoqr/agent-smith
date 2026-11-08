"""Building `SolutionOutput` and writing it so a killed run still leaves one.

The grader runs the agent in its own process group and kills the group at the
limit (SIGTERM, then SIGKILL ten seconds later). If `solution.json` were only
written at the end, a run that is one second too slow would leave nothing at
all. So the file is rewritten after every step, atomically, and the worst case
is a file that is one step out of date instead of half-written.
"""
import os
import tempfile
from pathlib import Path
from typing import List, Optional, Sequence

from agent_smith.models import SolutionOutput, StepMetrics


def build_solution(
    *,
    task_id: str,
    benchmark: str,
    success: bool,
    solution: str,
    steps: Sequence[StepMetrics],
    total_time_seconds: float,
    system_prompt: str,
    error: Optional[str] = None,
) -> SolutionOutput:
    """Totals are derived from the steps, never tracked twice.

    `total_requests` counts HTTP requests, so a step that needed two attempts
    contributes two. `iterations` is the number of recorded steps.
    """
    recorded: List[StepMetrics] = list(steps)
    return SolutionOutput(
        task_id=str(task_id),
        benchmark=benchmark,
        success=success,
        solution=solution,
        iterations=len(recorded),
        total_requests=sum(1 + step.retries for step in recorded),
        total_input_tokens=sum(step.input_tokens for step in recorded),
        total_output_tokens=sum(step.output_tokens for step in recorded),
        total_time_seconds=round(total_time_seconds, 3),
        steps=recorded,
        system_prompt=system_prompt,
        error=error,
    )


class SolutionWriter:
    """Writes `solution.json` atomically, as often as you like."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.writes = 0

    def write(self, solution: SolutionOutput) -> None:
        """Write to a temporary file in the same directory, then rename.

        `os.replace` is atomic within a filesystem, so a reader (or a kill
        signal) never sees a partial file. The temporary file has to be a
        sibling: renaming across filesystems is not atomic.
        """
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=self.path.parent,
            prefix=self.path.name + ".",
            suffix=".tmp",
            delete=False,
        )
        try:
            with handle:
                handle.write(solution.model_dump_json(indent=2))
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(handle.name, self.path)
            self.writes += 1
        except BaseException:
            Path(handle.name).unlink(missing_ok=True)
            raise
