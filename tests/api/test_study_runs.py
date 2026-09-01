"""Response-shape and status-code coverage for GET /study-runs/{id} and
/study-runs/{id}/traces.

get_study_run is Component 5's poll target -- the frontend hits it on an
interval while status == 'running' and stops on 'completed'/'failed', so its
status field and verdict_id (present only once a Verdict exists) both need
direct coverage, not just a general shape check.
"""

from datetime import date, datetime

from agentic_core.db.models import Charter as CharterRow
from agentic_core.db.models import Hypothesis as HypothesisRow
from agentic_core.db.models import StudyDesign as StudyDesignRow
from agentic_core.db.models import StudyRun as StudyRunRow
from agentic_core.db.models import ToolCallTrace as ToolCallTraceRow
from agentic_core.db.models import Verdict as VerdictRow
from agentic_core.schemas import Charter, EffectFamily, FalsificationCondition, ParsedCharter, UniverseFilter
from backtester.schema import RSI_14_30_70


def _seed_chain(session, status: str = "running") -> None:
    charter = Charter(
        parsed=ParsedCharter(universe=UniverseFilter(sector="Technology"), hypothesis_families=[EffectFamily.MEAN_REVERSION]),
        resolved_universe=["AAPL"],
        screening_as_of=date(2026, 1, 1),
        screening_group_size=10,
    )
    session.add(CharterRow(
        id="c1", mandate_text="mandate", charter=charter.model_dump(mode="json"),
        confirmed=True, created_at=datetime.now(), confirmed_at=datetime.now(),
    ))
    fc = FalsificationCondition(metric="sharpe_ratio", comparison="less_than", threshold=0.0)
    session.add(HypothesisRow(
        id="h1", charter_id="c1", rule=RSI_14_30_70.model_dump(mode="json"),
        prediction="prediction", falsification_condition=fc.model_dump(mode="json"),
        rationale="rationale", citations=[], grounding_tier="none", status="testing",
        created_at=datetime.now(),
    ))
    session.add(StudyDesignRow(id="d1", hypothesis_id="h1", design={"placeholder": True}, created_at=datetime.now()))
    session.add(StudyRunRow(
        id="r1", hypothesis_id="h1", study_design_id="d1", status=status,
        step_count=3, started_at=datetime(2026, 6, 1),
        finished_at=datetime(2026, 6, 1) if status != "running" else None,
        failure_reason="exhausted retries" if status == "failed" else None,
    ))
    session.commit()


def test_get_study_run_running_has_no_verdict(client, api_db_session):
    _seed_chain(api_db_session, status="running")

    resp = client.get("/study-runs/r1")

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "running"
    assert body["finished_at"] is None
    assert body["verdict_id"] is None


def test_get_study_run_completed_with_verdict(client, api_db_session):
    _seed_chain(api_db_session, status="completed")
    api_db_session.add(VerdictRow(
        id="v1", study_run_id="r1", status="confirmed", claims=[],
        hypothesis_count_under_charter=1, corrected_significance_threshold=0.05,
        narrative="narrative", caveats=[], created_at=datetime.now(),
    ))
    api_db_session.commit()

    resp = client.get("/study-runs/r1")

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "completed"
    assert body["verdict_id"] == "v1"


def test_get_study_run_failed_has_failure_reason(client, api_db_session):
    _seed_chain(api_db_session, status="failed")

    resp = client.get("/study-runs/r1")

    assert resp.status_code == 200
    assert resp.json()["failure_reason"] == "exhausted retries"


def test_get_study_run_not_found(client):
    resp = client.get("/study-runs/does-not-exist")

    assert resp.status_code == 404


def test_get_traces_ordered_by_step_index(client, api_db_session):
    _seed_chain(api_db_session, status="completed")
    for step in (2, 0, 1):
        api_db_session.add(ToolCallTraceRow(
            study_run_id="r1", step_index=step, window_index=0, tool_name="run_backtest",
            arguments={"ticker": "AAPL"}, result={"sharpe_ratio": 1.2}, is_error=False,
            called_at=datetime.now(),
        ))
    api_db_session.commit()

    resp = client.get("/study-runs/r1/traces")

    assert resp.status_code == 200
    body = resp.json()
    assert [t["step_index"] for t in body] == [0, 1, 2]


def test_get_traces_not_found(client):
    resp = client.get("/study-runs/does-not-exist/traces")

    assert resp.status_code == 404
