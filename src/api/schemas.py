"""Response models for src/api's read endpoints.

Reuse vs. new, decided per row, not by blanket rule: CharterRow.charter is
written as exactly agentic_core.schemas.Charter.model_dump(mode="json")
(see agentic_core/charter.py's create_charter), so CharterOut nests that
schema directly -- there's a real 1:1 match to reuse. HypothesisRow,
StudyRunRow, ToolCallTraceRow, and VerdictRow store their fields as separate
columns rather than one nested blob (see agentic_core/hypothesis.py's
propose_hypothesis and agentic_core/verdict.py's render_verdict), so their
Out models are new and thin, composed from the existing sub-schemas
(StrategyRule, FalsificationCondition, GroundingChunk, Claim) that DO match
column-for-column, rather than re-declaring those shapes here.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel

from agentic_core.schemas import Charter, Claim, FalsificationCondition, GroundingChunk
from backtester.schema import StrategyRule


class CharterOut(BaseModel):
    id: str
    mandate_text: str
    charter: Charter
    confirmed: bool
    created_at: datetime
    confirmed_at: datetime | None
    # Correction chain (Stage 7 Component 2). parent_charter_id is null for
    # the original, round-0 attempt. correction_round tells a client how
    # many correction rounds this row already reflects, which is what lets
    # the confirmation screen know whether to still offer "request another
    # correction" (see agentic_core.charter.MAX_CORRECTION_ROUNDS).
    parent_charter_id: str | None
    correction_round: int
    correction_text: str | None


class CharterCreateIn(BaseModel):
    mandate_text: str


class CharterCorrectIn(BaseModel):
    correction_text: str


class CharterWriteOut(CharterOut):
    """create_charter/correct_charter both return (id, Charter, blocked) --
    this is that third field, surfaced alongside the same row shape
    GET /charters/{id} already returns, rather than a differently-shaped
    response for writes vs. reads of the same resource.
    """

    blocked: bool


class HypothesisOut(BaseModel):
    id: str
    charter_id: str
    rule: StrategyRule
    prediction: str
    falsification_condition: FalsificationCondition
    rationale: str
    citations: list[GroundingChunk]
    grounding_tier: str
    status: str
    created_at: datetime
    # Computed by the route (StudyRun has no column pointing back at its
    # hypothesis's "latest" run -- the FK runs the other way). Schema-honest
    # naming would be a list (models.py's own StudyRun docstring: a re-test
    # is "a new StudyDesign + StudyRun under the same Hypothesis"), but v1
    # never creates more than one, and Component 5's poll target needs
    # exactly this one id, not a list to pick from -- so this is the most
    # recent run's id, nullable for a hypothesis still at status='proposed'.
    study_run_id: str | None


class StudyRunOut(BaseModel):
    id: str
    hypothesis_id: str
    study_design_id: str
    status: str
    step_count: int
    failure_reason: str | None
    started_at: datetime
    finished_at: datetime | None
    # Computed the same way: Verdict.study_run_id points here, not the
    # reverse. render_verdict (agentic_core/verdict.py) writes at most one
    # Verdict per StudyRun, so a plain nullable id -- not a list -- is
    # actually schema-accurate here, unlike HypothesisOut.study_run_id above.
    verdict_id: str | None


class ToolCallTraceOut(BaseModel):
    id: int
    study_run_id: str
    step_index: int
    window_index: int
    tool_name: str
    arguments: dict
    result: dict
    is_error: bool
    called_at: datetime


class VerdictOut(BaseModel):
    id: str
    study_run_id: str
    status: str
    claims: list[Claim]
    hypothesis_count_under_charter: int
    corrected_significance_threshold: float
    narrative: str
    caveats: list[str]
    created_at: datetime


class ScoreboardConfirmedEntry(BaseModel):
    hypothesis_id: str
    charter_id: str
    verdict: VerdictOut


class ScoreboardTestingEntry(BaseModel):
    hypothesis_id: str
    charter_id: str
    study_run_id: str | None


class ScoreboardOut(BaseModel):
    confirmed: list[ScoreboardConfirmedEntry]
    # Always [] today -- see scoreboard.py's _DECAYED_NOTE for why, and
    # docs/plans/stage-7-plan.md's own confirmed gap: ScoreboardEntry has no
    # writer until Stage 8's decay-recheck job exists.
    decayed: list[ScoreboardConfirmedEntry]
    decayed_note: str
    testing: list[ScoreboardTestingEntry]
