"""Fixtures for src/api's own test package.

api_db_session truncates the same table set tests/agentic_core/conftest.py's
loop_db_session does, plus verdicts (that fixture predates Component 7's
Verdict table) -- src/api reads exactly these tables, just over HTTP instead
of a direct import.

Unlike loop_db_session, nothing here is monkeypatched: routes get their
session through the get_db dependency (src/api/deps.py), so the test client
swaps it via app.dependency_overrides -- the whole reason that dependency
exists instead of copying the inline `with SessionFactory()` pattern.
"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

from api.app import app
from api.deps import get_db


@pytest.fixture
def api_db_session(test_engine):
    with test_engine.connect() as conn:
        conn.execute(text(
            "TRUNCATE tool_call_traces, verdicts, study_runs, study_designs, hypotheses, charters CASCADE"
        ))
        conn.commit()
    Session = sessionmaker(bind=test_engine)
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
