from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from agentic_core.charter import (
    CharterAlreadyConfirmedError,
    CharterBlockedError,
    CharterNotFoundError,
    CorrectionLimitExceededError,
    confirm_charter,
    correct_charter,
    create_charter,
)
from agentic_core.db.models import Charter as CharterRow
from api.deps import get_db
from api.schemas import CharterCorrectIn, CharterCreateIn, CharterOut, CharterWriteOut

router = APIRouter(prefix="/charters", tags=["charters"])


def _to_out(row: CharterRow) -> CharterOut:
    return CharterOut(
        id=row.id,
        mandate_text=row.mandate_text,
        charter=row.charter,
        confirmed=row.confirmed,
        created_at=row.created_at,
        confirmed_at=row.confirmed_at,
        parent_charter_id=row.parent_charter_id,
        correction_round=row.correction_round,
        correction_text=row.correction_text,
    )


def _get_row_or_404(db: Session, charter_id: str) -> CharterRow:
    row = db.get(CharterRow, charter_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"no charter with id {charter_id!r}")
    return row


@router.get("", response_model=list[CharterOut])
def list_charters(db: Session = Depends(get_db)) -> list[CharterOut]:
    rows = db.execute(select(CharterRow).order_by(CharterRow.created_at.desc())).scalars()
    return [_to_out(row) for row in rows]


@router.get("/{charter_id}", response_model=CharterOut)
def get_charter(charter_id: str, db: Session = Depends(get_db)) -> CharterOut:
    return _to_out(_get_row_or_404(db, charter_id))


@router.post("", response_model=CharterWriteOut, status_code=201)
def post_charter(body: CharterCreateIn, db: Session = Depends(get_db)) -> CharterWriteOut:
    """Wraps agentic_core.charter.create_charter exactly as
    scripts/set_charter.py does today -- the write itself happens inside
    that function's own SessionFactory session; `db` here is only used to
    read the persisted row back for the response, the same Component-1
    read pattern every GET route already uses.
    """
    charter_id, _, blocked = create_charter(body.mandate_text)
    row = _get_row_or_404(db, charter_id)
    return CharterWriteOut(**_to_out(row).model_dump(), blocked=blocked)


@router.post("/{charter_id}/correct", response_model=CharterWriteOut, status_code=201)
def post_correct_charter(charter_id: str, body: CharterCorrectIn, db: Session = Depends(get_db)) -> CharterWriteOut:
    """A correction inserts a NEW charter row (see agentic_core.charter.
    correct_charter's own docstring) -- 201, same as creation, because the
    id in this response is a different resource than charter_id, not an
    update to it.
    """
    try:
        new_charter_id, _, blocked = correct_charter(charter_id, body.correction_text)
    except CharterNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except (CharterAlreadyConfirmedError, CorrectionLimitExceededError) as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    row = _get_row_or_404(db, new_charter_id)
    return CharterWriteOut(**_to_out(row).model_dump(), blocked=blocked)


@router.post("/{charter_id}/confirm", response_model=CharterOut)
def post_confirm_charter(charter_id: str, db: Session = Depends(get_db)) -> CharterOut:
    try:
        confirm_charter(charter_id)
    except CharterNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except CharterBlockedError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    return _to_out(_get_row_or_404(db, charter_id))
