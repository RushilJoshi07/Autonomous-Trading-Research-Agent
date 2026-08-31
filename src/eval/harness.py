"""Stage 6, Component 2 -- the golden-set harness. Drives each fixture from
src/eval/golden_cases.py through the real execution loop and the real
verdict writer, then scores the three dimensions docs/architecture.md
Section 9 specifies: verdict correctness, fabrication cleanliness, and
required-caveat presence.

Split into a pure half and an impure half, the same way Stage 5 splits
agentic_core.verdict.decide_status (pure) from the loop's own nodes
(impure, only trustworthy live) -- see stage-5-summary.md Section 3.
`_score` takes whatever a run produced and computes the three dimensions
with no I/O; `run_case`/`run_golden_set` are what actually drive Bedrock,
the MCP subprocess, and the database.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Literal, Protocol

from mcp import ClientSession, StdioServerParameters, stdio_client
from pydantic import BaseModel

from agentic_core.loop_graph import LLMCallable, ToolSession, build_graph, initial_state
from agentic_core.schemas import Verdict
from agentic_core.verdict import VerdictValidationError, render_verdict
from eval.fixtures import cleanup, verify_cleanup
from eval.golden_cases import GOLDEN_CASE_BUILDERS, GoldenCase
from llm_client import structured_output

_REPORT_DIR = Path("reports/golden_set")


class CaseResult(BaseModel):
    """One case's outcome. Denormalized (name/category/expected_status
    copied from the GoldenCase) so a saved report is self-contained and
    never needs to be cross-referenced against golden_cases.py to be read.
    """

    name: str
    category: Literal["planted_true", "planted_false", "known_caveat"]
    expected_status: Literal["confirmed", "rejected", "inconclusive"]
    actual_status: Literal["confirmed", "rejected", "inconclusive"] | None
    status_correct: bool
    fabrication_clean: bool
    caveats_ok: bool
    passed: bool
    study_run_id: str | None
    detail: str


class GoldenSetReport(BaseModel):
    run_at: datetime
    results: list[CaseResult]
    total: int
    passed: int
    # Separate from `results` deliberately: CaseResult's category/expected_status
    # fields are Literal-typed against a real, already-built GoldenCase, and a
    # builder that fails before returning one has nothing valid to put there.
    # Forcing a placeholder into a typed field would be worse than a plain list
    # of strings for a failure mode this schema was never meant to represent.
    construction_errors: list[str] = []


class ScorableCase(Protocol):
    """The four fields _score actually reads -- nothing else. GoldenCase
    satisfies this structurally (it has these plus charter/hypothesis/
    design, which _score never touches), and so does
    eval.resumable.ResumeRecord, which is how Stage 6's gate script
    re-scores a sabotage-phase re-render using only persisted, resumable
    state, without needing to reconstruct a full GoldenCase's
    Charter/Hypothesis/StudyDesign objects just to call this function.
    """

    name: str
    category: Literal["planted_true", "planted_false", "known_caveat"]
    expected_status: Literal["confirmed", "rejected", "inconclusive"]
    expected_caveat_substring: str | None


def _score(case: ScorableCase, study_run_id: str | None, verdict: Verdict | None, detail: str) -> CaseResult:
    """Pure: no I/O, no LLM, no database. Testable with a hand-built fake
    Verdict the same way tests/agentic_core/test_verdict.py hand-builds
    fake traces -- see tests/eval/test_harness.py.

    fabrication_clean means exactly one thing: did render_verdict succeed.
    There is no partial credit for "the loop ran but no verdict exists" --
    an absent verdict cannot be asserted fabrication-free, so the honest
    default under uncertainty is False, not True.

    caveats_ok is True whenever nothing was required
    (expected_caveat_substring is None, true for five of the six cases) OR
    a verdict exists and contains the required substring. A case that
    required a caveat but never produced a verdict fails this too --
    "no verdict" cannot satisfy "the caveat appeared in the verdict".
    """
    actual_status = verdict.status if verdict is not None else None
    status_correct = actual_status == case.expected_status
    fabrication_clean = verdict is not None
    caveats_ok = case.expected_caveat_substring is None or (
        verdict is not None and any(case.expected_caveat_substring in c for c in verdict.caveats)
    )
    return CaseResult(
        name=case.name,
        category=case.category,
        expected_status=case.expected_status,
        actual_status=actual_status,
        status_correct=status_correct,
        fabrication_clean=fabrication_clean,
        caveats_ok=caveats_ok,
        passed=status_correct and fabrication_clean and caveats_ok,
        study_run_id=study_run_id,
        detail=detail,
    )


async def run_case(
    case: GoldenCase, session_provider: Callable[[], ToolSession], llm: LLMCallable = structured_output
) -> CaseResult:
    """Impure: drives the real execution loop (build_graph/initial_state --
    real Bedrock choosing every tool call, exactly as GATE5PROBE's own live
    proof did) and the real render_verdict, then scores the result.

    CONTRACT: always returns a CaseResult; never raises. Both real,
    external failure modes this function can hit -- the loop itself
    erroring, and render_verdict erroring -- are caught inside this
    function, not left for a caller to catch. An earlier version only
    caught this on the loop side and relied on VerdictValidationError
    being the only exception render_verdict could produce; a real
    anthropic.RateLimitError proved that assumption wrong live, and
    escaped uncaught through two callers before crashing a script that
    had never touched database state cleanup for the case in flight. See
    docs/explanations/stage-6/step-02-harness.md's own record of that.

    A loop that does not reach status='completed' never reaches
    render_verdict at all -- render_verdict's own guard requires a
    completed run, and a verdict drawn from an incomplete run is exactly
    what that guard exists to prevent (agentic_core/verdict.py's own
    docstring). That is scored here as a hard failure (verdict=None), not
    skipped: a case built on unambiguous, deterministic evidence that the
    live agent cannot even finish executing is itself a meaningful finding.
    """
    study_run_id: str | None = None
    loop_status = "not_started"
    verdict: Verdict | None = None
    detail_parts: list[str] = []

    try:
        graph = build_graph(session_provider, llm, design_id=case.design_id, hypothesis_id=case.hypothesis_id)
        final = await graph.ainvoke(initial_state(case.charter, case.hypothesis, case.design))
        study_run_id = final["study_run_id"]
        loop_status = final["status"]
        if loop_status != "completed":
            reason = final.get("failure_reason")
            detail_parts.append(f"execution loop ended with status={loop_status!r}" + (f" ({reason})" if reason else ""))
    except Exception as e:  # noqa: BLE001 -- deliberately broad; see run_golden_set's own docstring
        loop_status = "loop_exception"
        detail_parts.append(f"execution loop raised: {e!r}")

    if loop_status == "completed":
        try:
            _, verdict = render_verdict(study_run_id, llm=llm)
        except VerdictValidationError as e:
            detail_parts.append(f"verdict validation failed after retries: {e.errors[:2]}")
        except Exception as e:  # noqa: BLE001 -- found live, not designed in: a Bedrock
            # RateLimitError from render_verdict's own LLM call escaped this function
            # entirely on a real run, because only VerdictValidationError was caught
            # here. run_case's own contract is "always returns a CaseResult, never
            # raises" -- the loop-execution branch above already honors that; this
            # branch did not, on the reasoning (recorded in this component's own step
            # explainer) that an unexpected exception should "surface as run_case
            # failing outright" for an outer caller to catch. That reasoning assumed
            # an outer try/except would reliably see it -- it does not: an exception
            # raised inside code nested within the MCP session's own async task group
            # can get wrapped in an ExceptionGroup and surface at the session's OWN
            # teardown, past every try/except nested inside it, including ones in
            # code that calls run_case. Pushing the catch-all down into run_case
            # itself, rather than trusting every future caller to wrap it correctly,
            # is the same lesson eval.fixtures/eval.golden_cases already learned from
            # the construction-failure bug -- applied here to the second half of this
            # function instead of assuming the first half's fix covered both.
            detail_parts.append(f"render_verdict raised unexpectedly: {e!r}")

    detail = "; ".join(detail_parts) or "scored normally"
    return _score(case, study_run_id, verdict, detail)


def _write_report(report: GoldenSetReport, out_dir: Path = _REPORT_DIR) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{report.run_at.strftime('%Y%m%dT%H%M%SZ')}.json"
    path.write_text(report.model_dump_json(indent=2))
    return path


def _print_summary(report: GoldenSetReport) -> None:
    for err in report.construction_errors:
        print(f"[BUILD FAILED] {err}")
    for r in report.results:
        mark = "PASS" if r.passed else "FAIL"
        print(
            f"[{mark}] {r.name:32s} category={r.category:14s} "
            f"expected={r.expected_status:12s} actual={str(r.actual_status):12s} "
            f"status={'ok' if r.status_correct else 'X'} "
            f"fabrication={'ok' if r.fabrication_clean else 'X'} "
            f"caveats={'ok' if r.caveats_ok else 'X'}"
        )
        if r.detail != "scored normally":
            print(f"         {r.detail}")
    print()
    print(f"{report.passed}/{report.total} golden-set cases passed")


async def run_golden_set(
    builders: list[Callable[[], GoldenCase]] = GOLDEN_CASE_BUILDERS,
    llm: LLMCallable = structured_output,
) -> GoldenSetReport:
    """Owns the full lifecycle -- build, run, cleanup -- for every case, the
    same shape scripts/verify_stage5_gate.py already uses for one fixture,
    looped over six. One MCP subprocess and one session are launched ONCE
    and reused across all six cases: the tools are thin wrappers over pure
    functions reading the database plus their arguments, with no
    server-side state that could leak between cases, so six separate
    subprocess launches would only add startup cost with no isolation
    benefit.

    A case whose run_case call raises an exception this module did not
    anticipate is caught, recorded as a failed CaseResult, and the batch
    continues -- one broken case must not silently lose the other five,
    which matters specifically because docs/architecture.md wants this
    harness run continuously in production as a drift detector.

    `case = builder()` has its OWN try/except, separate from run_case's --
    found the hard way, not designed in advance. A first version left this
    call unguarded on the reasoning that "a builder failing means a fixture
    bug, not a runtime case failure" (see docs/explanations/stage-6/
    step-01-golden-cases.md's own design-decisions section, which made
    exactly that argument for a DIFFERENT reason -- skipping Components
    2-4's own LLM calls). That reasoning missed that builder() also
    performs real, non-transactional database writes (seed_price_bars
    commits immediately; the charter/hypothesis/design rows commit
    separately after), which can fail for OPERATIONAL reasons having
    nothing to do with the fixture's own logic -- concretely, a leftover
    row from an earlier crashed run collided on PriceBar's primary key,
    and because that raised OUTSIDE any try block, it propagated straight
    through the MCP session's own async task group (which re-raises an
    unrelated in-flight exception wrapped in an ExceptionGroup on the way
    out), past every except Exception below it, crashing the entire batch
    and -- worse -- leaving that case's already-committed rows uncleaned,
    because the finally block that would have caught them never ran
    either. A construction failure now gets its own guard, logged to
    construction_errors, and the loop moves on to the next builder rather
    than dying.
    """
    params = StdioServerParameters(
        command=os.path.abspath(".venv/bin/python3"),
        args=["-m", "mcp_tools.server"],
        cwd=os.getcwd(),
    )
    results: list[CaseResult] = []
    construction_errors: list[str] = []
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            for builder in builders:
                try:
                    case = builder()
                except Exception as e:  # noqa: BLE001 -- see docstring above
                    construction_errors.append(f"{builder.__name__} failed to build: {e!r}")
                    continue

                try:
                    result = await run_case(case, lambda: session, llm)
                except Exception as e:  # noqa: BLE001 -- see docstring above
                    result = CaseResult(
                        name=case.name,
                        category=case.category,
                        expected_status=case.expected_status,
                        actual_status=None,
                        status_correct=False,
                        fabrication_clean=False,
                        caveats_ok=False,
                        passed=False,
                        study_run_id=None,
                        detail=f"run_case raised unexpectedly: {e!r}",
                    )
                finally:
                    cleanup(case.ticker, case.charter_id, case.hypothesis_id)
                    ok, clean_detail = verify_cleanup(case.ticker, case.charter_id, case.hypothesis_id)
                    if not ok:
                        result.detail = f"{result.detail} | CLEANUP FAILED: {clean_detail}"
                results.append(result)

    report = GoldenSetReport(
        run_at=datetime.now(timezone.utc),
        results=results,
        total=len(results),
        passed=sum(1 for r in results if r.passed),
        construction_errors=construction_errors,
    )
    _write_report(report)
    _print_summary(report)
    return report
