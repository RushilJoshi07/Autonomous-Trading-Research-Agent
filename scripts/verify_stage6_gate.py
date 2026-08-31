"""Stage 6's own gate: "catches a deliberately-broken agent."

Mirrors the established gate-script genre (Stages 2-5): a dedicated,
self-contained script, run manually against real infrastructure, that
deliberately attempts the exact failure the stage's gate is worried about
rather than accumulating indirect confidence from unit tests describing
the system's own expected behavior.

RESUMABLE AND PACED, after three real live attempts hit the same AWS
Bedrock rate limit, always partway through an unpaced six-case burst. See
docs/explanations/stage-6/step-04-gate-script.md for the full account.
State persists to reports/stage6_gate/resume_state.json after every single
case, in both phases -- a case that already succeeded is skipped on the
next invocation, never re-attempted; a case that previously failed is
fully rebuilt from scratch (its leftover rows deleted and verified clean
first), never resumed mid-loop, because Stage 5's own architecture has no
checkpointer and deliberately does not support that
(docs/explanations/stage-5/step-07-execution-loop-state.md). 120 seconds
of pacing between every real Bedrock-calling step; the first sign of a
rate limit stops the batch immediately rather than attempting further
cases that would likely also fail. See eval.resumable for the tested
primitives this relies on.

JOB 1 -- HEALTHY BASELINE: run each golden-set case not yet marked
healthy_passed through the real, unmodified system (real Bedrock, real MCP
subprocess) via eval.harness.run_case. Only once ALL SIX show
healthy_passed=True does the script proceed to Job 2.

JOB 2 -- SABOTAGE: agentic_core.verdict.decide_status is temporarily
replaced, via unittest.mock.patch as a context manager, with a version
that always returns "confirmed" regardless of the real evidence -- the
exact failure mode docs/architecture.md names as the central worry (an
agreeable model, a planted-false hypothesis quietly starting to pass).
The patch is IN-PROCESS-MEMORY ONLY: no file on disk is touched, and
`with patch(...):` guarantees the original function is restored on the
way out of the block whether it exits normally or via an exception --
verified directly below with an assertion after every single sabotaged
case, not just once for the whole batch.

Each case's verdict is RE-RENDERED against its already-completed
study_run_id (decide_status is only ever called inside render_verdict,
after the loop has already finished and every tool call is already
sitting in tool_call_traces, so re-running the loop would duplicate real
Bedrock spend on evidence that would come back identical) -- the same
study_run_id > second Verdict row pattern verify_stage5_gate.py's own Job
2 already established.

THE PROOF, STATED PRECISELY: golden_true_1 and golden_true_2 already
expect "confirmed" -- an always-confirm bug is invisible on a case that
was supposed to confirm anyway, and this script does not pretend
otherwise. The real proof is the other four (golden_false_no_edge,
golden_false_fails_control, golden_false_breaches_bar, expecting
"rejected"; golden_caveat_thin_sample, expecting "inconclusive"): under
the sabotage, every one of them must stop passing.

Only once ALL SIX cases show sabotage_done=True does the script compute
the final gate verdict, clean up every case's database rows, verify the
cleanup by direct query, and delete the resume-state file -- at that
point its references are stale anyway, since cleanup just deleted what
they pointed to. If either phase stops early, the script prints exactly
how many cases are done and exits without touching cleanup or the
resume-state file, so a later invocation has accurate state to resume
from.
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from unittest.mock import patch

from mcp import ClientSession, StdioServerParameters, stdio_client

import agentic_core.verdict as verdict_module
from agentic_core.verdict import VerdictValidationError, render_verdict
from eval.fixtures import cleanup, verify_cleanup
from eval.golden_cases import GOLDEN_CASE_BUILDERS
from eval.harness import _score, run_case
from eval.resumable import ResumeRecord, is_rate_limited, load_resume_state, resume_action, run_with_pacing, save_resume_state
from llm_client import structured_output

RESUME_PATH = Path("reports/stage6_gate/resume_state.json")

# See this module's own docstring and docs/explanations/stage-6/
# step-04-gate-script.md for the full reasoning: three real, independent
# rate-limit hits all landed roughly a third to half of the way through an
# unpaced six-case burst, consistent with a rolling per-minute ceiling
# somewhere in that neighborhood. 120 seconds is double a typical
# 60-second window -- deliberately generous, since the only cost of
# waiting longer than necessary is wall-clock time, not dollars, and the
# cost of waiting too little has already been paid three times.
PACE_SECONDS = 120.0

_results: list[tuple[str, bool, str]] = []


def record(name: str, passed: bool, detail: str = "") -> None:
    _results.append((name, passed, detail))
    print(f"[{'PASS' if passed else 'FAIL'}] {name}" + (f"  -- {detail}" if detail else ""))


async def run_healthy_phase(
    state: dict[str, ResumeRecord],
    session_provider,
    llm=structured_output,
    pace_seconds: float = PACE_SECONDS,
    sleep_fn=asyncio.sleep,
) -> dict[str, ResumeRecord]:
    """Job 1, resumable. For each builder: skip if already healthy_passed;
    otherwise clean up any leftover rows from a previous failed attempt
    (never reused, always rebuilt fresh -- see eval.resumable.resume_action's
    own docstring) and run it live. Persists state after every case, and
    stops attempting further NEW cases the instant one looks rate-limited.
    """
    for builder in GOLDEN_CASE_BUILDERS:
        existing = state.get(builder.__name__)
        if resume_action(existing) == "skip":
            record(f"[SKIP -- already succeeded] {existing.name}", True, "resumed from a prior run")

    pending = [b for b in GOLDEN_CASE_BUILDERS if resume_action(state.get(b.__name__)) != "skip"]

    async def process(builder) -> bool:
        name = builder.__name__
        existing = state.get(name)
        if resume_action(existing) == "cleanup_and_retry":
            cleanup(existing.ticker, existing.charter_id, existing.hypothesis_id)
            ok, detail = verify_cleanup(existing.ticker, existing.charter_id, existing.hypothesis_id)
            record(f"[cleanup before retry] {existing.name}", ok, detail)

        case = builder()
        result = await run_case(case, session_provider, llm)
        record(f"[healthy] {case.name}", result.passed, result.detail)

        state[name] = ResumeRecord(
            name=case.name, category=case.category, expected_status=case.expected_status,
            expected_caveat_substring=case.expected_caveat_substring,
            ticker=case.ticker, charter_id=case.charter_id, hypothesis_id=case.hypothesis_id,
            design_id=case.design_id,
            healthy_passed=result.passed, healthy_detail=result.detail,
            study_run_id=result.study_run_id,
        )
        save_resume_state(RESUME_PATH, state)

        if is_rate_limited(result.detail):
            record(f"CIRCUIT BREAKER: rate limit detected on {case.name}, stopping before attempting further cases", False)
            return True
        return False

    await run_with_pacing(pending, process, pace_seconds, sleep_fn)
    return state


async def run_sabotage_phase(
    state: dict[str, ResumeRecord],
    pace_seconds: float = PACE_SECONDS,
    sleep_fn=asyncio.sleep,
) -> dict[str, ResumeRecord]:
    """Job 2, resumable and synchronous under the hood (render_verdict
    calls no MCP tool, only the LLM -- no subprocess/session needed here
    at all). Unlike the healthy phase, a case whose sabotage attempt fails
    needs no cleanup before retrying: render_verdict either writes a new
    Verdict row after full validation or writes nothing at all, so a
    failed attempt leaves the underlying study_run_id exactly as it was.
    """
    pending = [name for name, r in state.items() if not r.sabotage_done]
    real_decide_status = verdict_module.decide_status

    def _always_confirms(evaluations, condition, threshold):
        # Real gates are computed and shown to the model for prompt
        # coherence -- only the STATUS is the lie.
        _, real_gates = real_decide_status(evaluations, condition, threshold)
        return "confirmed", real_gates

    async def process(name: str) -> bool:
        rec = state[name]
        with patch("agentic_core.verdict.decide_status", _always_confirms):
            try:
                _, corrupted_verdict = render_verdict(rec.study_run_id, llm=structured_output)
                sab_result = _score(
                    rec, rec.study_run_id, corrupted_verdict,
                    "re-rendered with decide_status forced to always return 'confirmed'",
                )
            except VerdictValidationError as e:
                sab_result = _score(rec, rec.study_run_id, None, f"sabotaged render_verdict failed validation: {e.errors[:2]}")
            except Exception as e:  # noqa: BLE001 -- see eval.harness.run_case's own docstring for why this must be broad
                sab_result = _score(rec, rec.study_run_id, None, f"sabotaged render_verdict raised unexpectedly: {e!r}")
        # OUTSIDE the `with patch(...)` block, deliberately -- checking
        # restoration INSIDE it (as an earlier version of this script did)
        # checks nothing: the patch is still active in there by
        # definition, so the check would read False every single time
        # regardless of whether restoration actually works. That was a
        # bug in this verification code itself, caught by a live run that
        # failed an assertion it should never have been able to fail --
        # see docs/explanations/stage-6/step-04-gate-script.md for the
        # full account, including why "the check itself was wrong" was
        # the correct diagnosis and not "the patch mechanism is broken".
        restored = verdict_module.decide_status is real_decide_status

        # THE CONTAINMENT CHECK, checked after EVERY sabotaged case, not
        # once for the whole batch: not trusted, checked.
        record(f"decide_status restored after sabotaging {rec.name}", restored)
        assert restored, "decide_status was NOT restored -- this would be a real, serious problem"

        record(f"[sabotaged] {rec.name}", True,
               f"expected={rec.expected_status} actual={sab_result.actual_status} passed={sab_result.passed}")

        rec.sabotage_done = True
        rec.sabotage_passed = sab_result.passed
        rec.sabotage_actual_status = sab_result.actual_status
        rec.sabotage_detail = sab_result.detail
        save_resume_state(RESUME_PATH, state)

        return is_rate_limited(sab_result.detail)

    await run_with_pacing(pending, process, pace_seconds, sleep_fn)
    return state


async def main_async() -> int:
    state = load_resume_state(RESUME_PATH)

    params = StdioServerParameters(
        command=os.path.abspath(".venv/bin/python3"),
        args=["-m", "mcp_tools.server"],
        cwd=os.getcwd(),
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            state = await run_healthy_phase(state, lambda: session)

    all_healthy = all(b.__name__ in state and state[b.__name__].healthy_passed for b in GOLDEN_CASE_BUILDERS)
    if not all_healthy:
        done = sum(1 for b in GOLDEN_CASE_BUILDERS if b.__name__ in state and state[b.__name__].healthy_passed)
        print(f"\n{done}/6 cases have a healthy pass so far. Re-run this script to continue -- "
              f"already-succeeded cases will be skipped, not re-attempted.")
        return 1

    state = await run_sabotage_phase(state)

    all_sabotaged = all(state[b.__name__].sabotage_done for b in GOLDEN_CASE_BUILDERS)
    if not all_sabotaged:
        done = sum(1 for b in GOLDEN_CASE_BUILDERS if state[b.__name__].sabotage_done)
        print(f"\nAll 6 healthy, but only {done}/6 sabotage re-renders done so far. Re-run this script to continue.")
        return 1

    records = [state[b.__name__] for b in GOLDEN_CASE_BUILDERS]
    should_still_pass = [r for r in records if r.expected_status == "confirmed"]
    must_now_fail = [r for r in records if r.expected_status != "confirmed"]

    record("healthy baseline: 6/6 golden-set cases pass on the real, unmodified agent",
           all(r.healthy_passed for r in records), f"{sum(r.healthy_passed for r in records)}/6 passed")
    record("the two confirm-expected cases still pass under sabotage (correct, but not informative here)",
           all(r.sabotage_passed for r in should_still_pass),
           f"{sum(bool(r.sabotage_passed) for r in should_still_pass)}/{len(should_still_pass)} still passing")
    record("THE GATE: every non-confirm-expected case stops passing once decide_status always confirms",
           all(not r.sabotage_passed for r in must_now_fail),
           "; ".join(f"{r.name}: expected={r.expected_status} now_actual={r.sabotage_actual_status}" for r in must_now_fail))

    for r in records:
        cleanup(r.ticker, r.charter_id, r.hypothesis_id)
        ok, detail = verify_cleanup(r.ticker, r.charter_id, r.hypothesis_id)
        record(f"[cleanup] {r.name}", ok, detail)

    # References in the resume file are stale the moment cleanup runs --
    # delete it regardless of the final verdict below, so a future
    # invocation starts genuinely fresh rather than skipping cases whose
    # underlying rows no longer exist.
    RESUME_PATH.unlink(missing_ok=True)

    print()
    print("=" * 78)
    passed = sum(1 for _, ok, _ in _results if ok)
    total = len(_results)
    print(f"{passed}/{total} checks passed")
    if passed == total:
        print("Stage 6 gate: PASSED -- golden set works live, and catches a deliberately-broken agent.")
        return 0
    print("Stage 6 gate: FAILED")
    return 1


def main() -> None:
    sys.exit(asyncio.run(main_async()))


if __name__ == "__main__":
    main()
