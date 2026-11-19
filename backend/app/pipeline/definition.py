"""Pipeline layout (spec 4): stages run in order, the steps inside a stage run in parallel.

    [proxy | analyze | newsroom] → [align] → [metadata | thumbnails] → REVIEW_READY

`analyze` covers spec step 3 (alignment audio, scene cuts, silences). Approval, final render and
upload (spec 6'–10) run per clip, not per job, and are not part of this pipeline.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.models import JobStatus

QUEUES = ("media", "align", "ai", "publish")
# Orchestration callbacks are tiny; they share the I/O-bound `ai` queue so they are never stuck
# behind a long FFmpeg or alignment task.
CONTROL_QUEUE = "ai"


@dataclass(frozen=True)
class StepDef:
    name: str
    queue: str
    max_retries: int = 2  # automatic retries for transient errors (RetryableStepError)


@dataclass(frozen=True)
class StageDef:
    name: str
    status: JobStatus  # job status while this stage runs
    steps: tuple[StepDef, ...]


PIPELINE: tuple[StageDef, ...] = (
    StageDef(
        "processing",
        JobStatus.PROCESSING,
        (StepDef("proxy", "media"), StepDef("analyze", "media"), StepDef("newsroom", "ai")),
    ),
    StageDef("aligning", JobStatus.ALIGNING, (StepDef("align", "align"),)),
    StageDef(
        "generating",
        JobStatus.GENERATING,
        (StepDef("metadata", "ai"), StepDef("thumbnails", "media")),
    ),
)
FINAL_STATUS = JobStatus.REVIEW_READY

STEPS: dict[str, tuple[int, StepDef]] = {
    step.name: (i, step) for i, stage in enumerate(PIPELINE) for step in stage.steps
}
assert all(step.queue in QUEUES for _, step in STEPS.values())
