"""Fixtures for agentic_core's own test package."""

import pytest
from sqlalchemy import text
from sqlalchemy.orm import sessionmaker


@pytest.fixture
def loop_db_session(test_engine, monkeypatch):
    """Truncates the study-execution tables and points loop_graph's
    SessionFactory at the test database.

    Same monkeypatch-at-the-import-location pattern corpus_db_session uses
    below: loop_graph imports SessionFactory directly, so patching
    data_pipeline.db.session would have no effect on the name it already
    bound.

    Truncation order is irrelevant here because CASCADE handles the FK
    chain (charters -> hypotheses -> study_designs -> study_runs ->
    tool_call_traces), but the chain is why all five are listed: leaving
    charters populated across tests would let a stale hypothesis row
    satisfy a FK it should not.
    """
    with test_engine.connect() as conn:
        conn.execute(text(
            "TRUNCATE tool_call_traces, study_runs, study_designs, hypotheses, charters CASCADE"
        ))
        conn.commit()
    Session = sessionmaker(bind=test_engine)
    monkeypatch.setattr("agentic_core.loop_graph.SessionFactory", Session)
    session = Session()
    yield session
    session.close()


@pytest.fixture
def corpus_db_session(test_engine, monkeypatch):
    """Truncates corpus_papers/corpus_chunks and points agentic_core.corpus's
    module-level SessionFactory at the test database -- same monkeypatch
    pattern the root conftest already uses for data_pipeline.ingest.runner
    (agentic_core.corpus imports SessionFactory directly, the same way that
    module does, so it has to be patched at its own import location, not at
    data_pipeline.db.session's).
    """
    with test_engine.connect() as conn:
        conn.execute(text("TRUNCATE corpus_chunks, corpus_papers CASCADE"))
        conn.commit()
    Session = sessionmaker(bind=test_engine)
    monkeypatch.setattr("agentic_core.corpus.SessionFactory", Session)
    session = Session()
    yield session
    session.close()
