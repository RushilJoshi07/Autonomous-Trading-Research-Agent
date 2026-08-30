"""Stage 6, Component 3 -- the thin, manually-invocable entry point for the
golden set. All real logic lives in eval.harness.run_golden_set; this
script only calls it and sets a process exit code, so it can be dropped
into a CI step or a scheduled job later (Stage 8) without changes.

Real API cost: ~$0.06/case measured directly (see
docs/explanations/stage-6/step-01-golden-cases.md's dry-run record),
~$0.34 for all six. Not run on every commit -- invoked manually before
merging a change that touches agentic_core/, and later on a schedule once
Stage 8's infrastructure exists.
"""
from __future__ import annotations

import asyncio
import sys

from eval.harness import run_golden_set


async def main() -> int:
    report = await run_golden_set()
    return 0 if report.passed == report.total else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
