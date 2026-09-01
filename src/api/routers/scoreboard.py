from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from agentic_core.db.models import Hypothesis as HypothesisRow
from agentic_core.db.models import StudyRun as StudyRunRow
from agentic_core.db.models import Verdict as VerdictRow
from api.deps import get_db
from api.routers.verdicts import _to_out as _verdict_to_out
from api.schemas import ScoreboardConfirmedEntry, ScoreboardOut, ScoreboardTestingEntry

router = APIRouter(prefix="/scoreboard", tags=["scoreboard"])

# See docs/plans/stage-7-plan.md's own "confirmed gap": ScoreboardEntry
# (agentic_core/db/models.py) has no writer anywhere in this project --
# the scheduled decay-recheck job that would populate a 'decayed' row is
# Stage 8's own, unbuilt work. Returning [] here is a documented absence of
# a data source, not a query that happens to match nothing.
_DECAYED_NOTE = (
    "Always empty today: 'decayed' comes from Stage 8's scheduled "
    "decay-recheck job re-verifying confirmed strategies against new data, "
    "which doesn't exist yet. This is a disclosed scope boundary, not a "
    "query returning nothing by mistake."
)


def _latest_study_run_id(db: Session, hypothesis_id: str) -> str | None:
    return db.execute(
        select(StudyRunRow.id)
        .where(StudyRunRow.hypothesis_id == hypothesis_id)
        .order_by(StudyRunRow.started_at.desc())
        .limit(1)
    ).scalar_one_or_none()


@router.get("", response_model=ScoreboardOut)
def get_scoreboard(db: Session = Depends(get_db)) -> ScoreboardOut:
    confirmed: list[ScoreboardConfirmedEntry] = []
    for hyp in db.execute(select(HypothesisRow).where(HypothesisRow.status == "confirmed")).scalars():
        run_id = _latest_study_run_id(db, hyp.id)
        verdict_row = (
            db.execute(select(VerdictRow).where(VerdictRow.study_run_id == run_id)).scalar_one_or_none()
            if run_id is not None
            else None
        )
        # A hypothesis only ever reaches status='confirmed' via render_verdict
        # (agentic_core/verdict.py), which writes the Verdict row in the same
        # transaction it sets that status -- so verdict_row is None only if
        # the DB is caught mid-write; skip rather than emit a broken entry.
        if verdict_row is not None:
            confirmed.append(
                ScoreboardConfirmedEntry(hypothesis_id=hyp.id, charter_id=hyp.charter_id, verdict=_verdict_to_out(verdict_row))
            )

    testing: list[ScoreboardTestingEntry] = []
    for hyp in db.execute(select(HypothesisRow).where(HypothesisRow.status == "testing")).scalars():
        testing.append(
            ScoreboardTestingEntry(
                hypothesis_id=hyp.id, charter_id=hyp.charter_id, study_run_id=_latest_study_run_id(db, hyp.id)
            )
        )

    return ScoreboardOut(confirmed=confirmed, decayed=[], decayed_note=_DECAYED_NOTE, testing=testing)
