from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from agentic_core.db.models import Hypothesis as HypothesisRow
from agentic_core.db.models import StudyRun as StudyRunRow
from api.deps import get_db
from api.schemas import HypothesisOut

router = APIRouter(prefix="/hypotheses", tags=["hypotheses"])


def _latest_study_run_id(db: Session, hypothesis_id: str) -> str | None:
    return db.execute(
        select(StudyRunRow.id)
        .where(StudyRunRow.hypothesis_id == hypothesis_id)
        .order_by(StudyRunRow.started_at.desc())
        .limit(1)
    ).scalar_one_or_none()


def _to_out(db: Session, row: HypothesisRow) -> HypothesisOut:
    return HypothesisOut(
        id=row.id,
        charter_id=row.charter_id,
        rule=row.rule,
        prediction=row.prediction,
        falsification_condition=row.falsification_condition,
        rationale=row.rationale,
        citations=row.citations,
        grounding_tier=row.grounding_tier,
        status=row.status,
        created_at=row.created_at,
        study_run_id=_latest_study_run_id(db, row.id),
    )


@router.get("", response_model=list[HypothesisOut])
def list_hypotheses(charter_id: str = Query(...), db: Session = Depends(get_db)) -> list[HypothesisOut]:
    rows = db.execute(
        select(HypothesisRow)
        .where(HypothesisRow.charter_id == charter_id)
        .order_by(HypothesisRow.created_at.desc())
    ).scalars()
    return [_to_out(db, row) for row in rows]


@router.get("/{hypothesis_id}", response_model=HypothesisOut)
def get_hypothesis(hypothesis_id: str, db: Session = Depends(get_db)) -> HypothesisOut:
    row = db.get(HypothesisRow, hypothesis_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"no hypothesis with id {hypothesis_id!r}")
    return _to_out(db, row)
