"""Step handlers.

A handler does the work of one pipeline step for one job. It must be idempotent: it can run
again after a failure or a worker restart. It signals a transient problem (network, 5xx) with
`RetryableStepError`; any other exception fails the step.

M0 has only the dummy handler. Real handlers are registered with `@register("proxy")` etc.
in later milestones.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)


class StepError(Exception):
    """The step failed; it will not be retried automatically."""


class RetryableStepError(StepError):
    """Transient failure; the step is retried with backoff."""


@dataclass
class StepContext:
    job_id: int
    step: str
    attempt: int  # 1 for the first run of this step
    job_dir: Path  # storage/jobs/{job_id}
    is_dummy: bool = False
    params: dict[str, Any] = field(default_factory=dict)


Handler = Callable[[StepContext], None]
_HANDLERS: dict[str, Handler] = {}


def register(step: str) -> Callable[[Handler], Handler]:
    def deco(fn: Handler) -> Handler:
        _HANDLERS[step] = fn
        return fn

    return deco


def dummy_handler(ctx: StepContext) -> None:
    """Sleeps; fails on the first attempt if asked to (to try retries).

    params["dummy"] = {"seconds": 2.0, "fail_steps": ["align"], "flaky_steps": ["newsroom"]}
    - fail_steps: fail on attempt 1 (manual retry then succeeds)
    - flaky_steps: transient error on attempt 1 (automatic retry then succeeds)
    """
    opts = ctx.params.get("dummy", {})
    time.sleep(float(opts.get("seconds", 0)))
    if ctx.attempt == 1 and ctx.step in opts.get("fail_steps", []):
        raise StepError(f"dummy failure in {ctx.step}")
    if ctx.attempt == 1 and ctx.step in opts.get("flaky_steps", []):
        raise RetryableStepError(f"dummy transient failure in {ctx.step}")


def resolve_handler(step: str, is_dummy: bool) -> Handler:
    if is_dummy:
        return dummy_handler
    try:
        return _HANDLERS[step]
    except KeyError:
        raise StepError(f"step '{step}' is not implemented yet") from None
