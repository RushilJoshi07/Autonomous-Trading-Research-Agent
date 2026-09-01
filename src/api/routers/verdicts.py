from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from agentic_core.db.models import Verdict as VerdictRow
from api.deps import get_db
from api.schemas import VerdictOut

router = APIRouter(prefix="/verdicts", tags=["verdicts"])


def _to_out(row: VerdictRow) -> VerdictOut:
    return VerdictOut(
        id=row.id,
        study_run_id=row.study_run_id,
        status=row.status,
        claims=row.claims,
        hypothesis_count_under_charter=row.hypothesis_count_under_charter,
        corrected_significance_threshold=row.corrected_significance_threshold,
        narrative=row.narrative,
        caveats=row.caveats,
        created_at=row.created_at,
    )


@router.get("/{verdict_id}", response_model=VerdictOut)
def get_verdict(verdict_id: str, db: Session = Depends(get_db)) -> VerdictOut:
    row = db.get(VerdictRow, verdict_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"no verdict with id {verdict_id!r}")
    return _to_out(row)
