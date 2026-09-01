from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from agentic_core.db.models import StudyRun as StudyRunRow
from agentic_core.db.models import ToolCallTrace as ToolCallTraceRow
from agentic_core.db.models import Verdict as VerdictRow
from api.deps import get_db
from api.schemas import StudyRunOut, ToolCallTraceOut

router = APIRouter(prefix="/study-runs", tags=["study-runs"])


def _verdict_id(db: Session, study_run_id: str) -> str | None:
    return db.execute(select(VerdictRow.id).where(VerdictRow.study_run_id == study_run_id)).scalar_one_or_none()


@router.get("/{study_run_id}", response_model=StudyRunOut)
def get_study_run(study_run_id: str, db: Session = Depends(get_db)) -> StudyRunOut:
    """Component 5's poll target (docs/plans/stage-7-plan.md): the frontend
    hits this on an interval while status == 'running' and treats the
    transition to 'completed'/'failed' as the signal to stop polling and
    reveal the result.
    """
    row = db.get(StudyRunRow, study_run_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"no study_run with id {study_run_id!r}")
    return StudyRunOut(
        id=row.id,
        hypothesis_id=row.hypothesis_id,
        study_design_id=row.study_design_id,
        status=row.status,
        step_count=row.step_count,
        failure_reason=row.failure_reason,
        started_at=row.started_at,
        finished_at=row.finished_at,
        verdict_id=_verdict_id(db, row.id),
    )


@router.get("/{study_run_id}/traces", response_model=list[ToolCallTraceOut])
def get_study_run_traces(study_run_id: str, db: Session = Depends(get_db)) -> list[ToolCallTraceOut]:
    run = db.get(StudyRunRow, study_run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"no study_run with id {study_run_id!r}")
    rows = db.execute(
        select(ToolCallTraceRow)
        .where(ToolCallTraceRow.study_run_id == study_run_id)
        .order_by(ToolCallTraceRow.step_index)
    ).scalars()
    return [
        ToolCallTraceOut(
            id=t.id,
            study_run_id=t.study_run_id,
            step_index=t.step_index,
            window_index=t.window_index,
            tool_name=t.tool_name,
            arguments=t.arguments,
            result=t.result,
            is_error=t.is_error,
            called_at=t.called_at,
        )
        for t in rows
    ]
