"""Fixtures for src/api's own test package.

api_db_session truncates the same table set tests/agentic_core/conftest.py's
loop_db_session does, plus verdicts (that fixture predates Component 7's
Verdict table) -- src/api reads exactly these tables, just over HTTP instead
of a direct import.

Component 1's routes were all reads through the get_db dependency, so the
test client only ever needed to override that. Component 2 added write
routes (POST /charters and friends) that call straight into
agentic_core.charter's own functions -- per docs/plans/stage-7-plan.md's own
design, the API is a thin transport over that existing logic, not a
reimplementation of it, so those functions still own and commit through
their own SessionFactory-bound sessions, entirely outside get_db. Without
patching agentic_core.charter.SessionFactory too, a write-route test would
silently write into the REAL dev database instead of the test one -- the
same import-location monkeypatch tests/agentic_core/conftest.py's
charter_db_session already needs, for the same reason, just applied here
because tests/api/ is a sibling package that can't reach that fixture
directly (pytest conftest inheritance only flows downward).
"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

from api.app import app
from api.deps import get_db


@pytest.fixture
def api_db_session(test_engine, monkeypatch):
    with test_engine.connect() as conn:
        conn.execute(text(
            "TRUNCATE tool_call_traces, verdicts, study_runs, study_designs, hypotheses, charters CASCADE"
        ))
        conn.commit()
    Session = sessionmaker(bind=test_engine)
    monkeypatch.setattr("agentic_core.charter.SessionFactory", Session)
    session = Session()
    yield session
    session.close()


@pytest.fixture
def client(api_db_session):
    def _override_get_db():
        yield api_db_session

    app.dependency_overrides[get_db] = _override_get_db
    yield TestClient(app)
    app.dependency_overrides.clear()
