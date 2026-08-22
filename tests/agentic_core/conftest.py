"""Fixtures for agentic_core's own test package."""

import pytest
from sqlalchemy import text
from sqlalchemy.orm import sessionmaker


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
