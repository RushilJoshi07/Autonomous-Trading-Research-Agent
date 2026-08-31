"""Fixtures for eval's own test package."""

import pytest
from sqlalchemy import text
from sqlalchemy.orm import sessionmaker


@pytest.fixture
def eval_db_session(test_engine, monkeypatch):
    """Truncates the study-execution tables plus price_bars, and points
    BOTH eval.fixtures' and eval.golden_cases' own SessionFactory at the
    test database.

    Two monkeypatch targets, not one, for the same reason
    tests/agentic_core/conftest.py's corpus_db_session patches
    agentic_core.corpus separately from loop_db_session's own
    agentic_core.loop_graph target: SessionFactory is imported directly
    into both eval.fixtures (build_charter_and_hypothesis/
    build_study_design/cleanup/verify_cleanup) and eval.golden_cases
    (_seed) -- patching data_pipeline.db.session would have no effect on
    either name once each module has already bound its own.

    price_bars is added to the truncation list alongside the agentic_core
    cascade chain loop_db_session already truncates, because golden-set
    fixtures are the first thing in tests/agentic_core/ or tests/eval/ to
    write PriceBar rows as part of building a Charter/Hypothesis fixture.
    """
    with test_engine.connect() as conn:
        conn.execute(text(
            "TRUNCATE tool_call_traces, study_runs, study_designs, hypotheses, charters, "
            "price_bars CASCADE"
        ))
        conn.commit()
    Session = sessionmaker(bind=test_engine)
    monkeypatch.setattr("eval.fixtures.SessionFactory", Session)
    monkeypatch.setattr("eval.golden_cases.SessionFactory", Session)
    session = Session()
    yield session
    session.close()
