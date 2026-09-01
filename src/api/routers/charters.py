from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from agentic_core.db.models import Charter as CharterRow
from api.deps import get_db
from api.schemas import CharterOut

router = APIRouter(prefix="/charters", tags=["charters"])


def _to_out(row: CharterRow) -> CharterOut:
    return CharterOut(
        id=row.id,
        mandate_text=row.mandate_text,
        charter=row.charter,
        confirmed=row.confirmed,
        created_at=row.created_at,
        confirmed_at=row.confirmed_at,
    )


@router.get("", response_model=list[CharterOut])
def list_charters(db: Session = Depends(get_db)) -> list[CharterOut]:
    rows = db.execute(select(CharterRow).order_by(CharterRow.created_at.desc())).scalars()
    return [_to_out(row) for row in rows]


@router.get("/{charter_id}", response_model=CharterOut)
def get_charter(charter_id: str, db: Session = Depends(get_db)) -> CharterOut:
    row = db.get(CharterRow, charter_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"no charter with id {charter_id!r}")
    return _to_out(row)
