"""Resumability, pacing, and fail-fast primitives for live, rate-limit-prone
Stage 6 scripts.

Born from scripts/verify_stage6_gate.py hitting the same real AWS Bedrock
rate limit three times in a row, always partway through a rapid, unpaced
six-case burst, and from a hard requirement that followed: prove the
pacing and stop-early logic actually works BEFORE it ever touches a real
Bedrock call, not after. Everything here is deliberately generic --
nothing in this module knows about GoldenCase, MCP, or Bedrock -- which is
what makes run_with_pacing fully testable with a fake sleep function and a
fake per-item callback, at zero real cost and zero real wall-clock wait.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Awaitable, Callable, Literal, TypeVar

from pydantic import BaseModel

T = TypeVar("T")


class ResumeRecord(BaseModel):
    """Persisted, per-case state for a live run that may be interrupted
    and resumed across separate script invocations.

    Deliberately NOT eval.golden_cases.GoldenCase: a case that already
    succeeded needs its ticker/ids/study_run_id and its scoring fields
    (name, category, expected_status, expected_caveat_substring) to be
    skipped or sabotage-re-rendered -- it never again needs the full
    Charter/Hypothesis/StudyDesign Pydantic objects GoldenCase carries,
    because those exist only to drive a live execution loop, and a case
    with a completed study_run_id never runs that loop a second time.
    """

    name: str
    category: Literal["planted_true", "planted_false", "known_caveat"]
    expected_status: Literal["confirmed", "rejected", "inconclusive"]
    expected_caveat_substring: str | None = None
    ticker: str
    charter_id: str
    hypothesis_id: str
    design_id: str
    healthy_passed: bool
    healthy_detail: str
    study_run_id: str | None = None
    sabotage_done: bool = False
    sabotage_passed: bool | None = None
    sabotage_actual_status: str | None = None
    sabotage_detail: str | None = None


def load_resume_state(path: Path) -> dict[str, ResumeRecord]:
    if not path.exists():
        return {}
    raw = json.loads(path.read_text())
    return {name: ResumeRecord.model_validate(record) for name, record in raw.items()}


def save_resume_state(path: Path, state: dict[str, ResumeRecord]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {name: json.loads(record.model_dump_json()) for name, record in state.items()}
    path.write_text(json.dumps(payload, indent=2))


def is_rate_limited(detail: str) -> bool:
    """A plain string match, not a typed signal -- disclosed as a
    heuristic, not asserted as a guarantee. It checks for the exact class
    name this project's own real failures have repr'd as
    ("RateLimitError(...)" inside a CaseResult's own detail string); a
    differently-worded throttle error from a future SDK version, or a
    different underlying exception class for a similar condition, would
    not trip this. Kept this simple deliberately: this is a gate-script
    circuit breaker deciding whether to keep spending real money, not a
    safety-critical classifier, and a heuristic that is honestly named as
    one is more trustworthy than a falsely precise-looking abstraction
    over the same string check.
    """
    return "RateLimitError" in detail


def resume_action(existing: ResumeRecord | None) -> Literal["skip", "cleanup_and_retry", "build_fresh"]:
    """The one decision a resumable, cleanup-aware caller needs per case:

    - no record yet -> build_fresh (nothing has ever been attempted)
    - a record that already succeeded -> skip (untouched; exactly as
      trustworthy as it always was)
    - a record that previously failed -> cleanup_and_retry (its leftover
      DB rows are deleted and verified clean, then it is rebuilt from
      scratch and re-run in full -- never resumed mid-loop, because Stage
      5's own architecture has no checkpointer and deliberately does not
      support that; see docs/explanations/stage-5/
      step-07-execution-loop-state.md)

    There is no fourth action, by design: nothing in this module ever
    reuses a partially-completed attempt.
    """
    if existing is None:
        return "build_fresh"
    if existing.healthy_passed:
        return "skip"
    return "cleanup_and_retry"


async def run_with_pacing(
    items: list[T],
    process: Callable[[T], Awaitable[bool]],
    pace_seconds: float,
    sleep_fn: Callable[[float], Awaitable[None]],
) -> int:
    """Runs process(item) for each item in order, waiting pace_seconds
    before every item except the first one actually attempted -- never
    before the first (nothing to protect yet), never after the last
    (nothing left to protect). Stops immediately, with no further items
    even attempted, the moment process() returns True -- callers use this
    to signal a fatal, don't-continue condition (a detected rate limit),
    never an ordinary per-item failure that is still worth continuing
    past (an ordinary failure returns False and the loop moves on).

    sleep_fn has no default -- callers must decide explicitly between
    asyncio.sleep (live) and a fake recorder (tests). A default of
    asyncio.sleep here would make it too easy to accidentally write a
    test that actually waits real wall-clock seconds without noticing.

    Returns the number of items actually processed, which is less than
    len(items) exactly when process() signalled stop before the end.
    """
    processed = 0
    for item in items:
        if processed > 0:
            await sleep_fn(pace_seconds)
        should_stop = await process(item)
        processed += 1
        if should_stop:
            break
    return processed
