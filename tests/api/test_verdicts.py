"""Response-shape and status-code coverage for GET /verdicts/{id}."""

from datetime import date, datetime

from agentic_core.db.models import Charter as CharterRow
from agentic_core.db.models import Hypothesis as HypothesisRow
from agentic_core.db.models import StudyDesign as StudyDesignRow
from agentic_core.db.models import StudyRun as StudyRunRow
from agentic_core.db.models import Verdict as VerdictRow
from agentic_core.schemas import Charter, EffectFamily, FalsificationCondition, ParsedCharter, UniverseFilter
from backtester.schema import RSI_14_30_70


def _seed_verdict(session, verdict_id: str = "v1", status: str = "confirmed") -> None:
    charter = Charter(
        parsed=ParsedCharter(universe=UniverseFilter(sector="Technology"), hypothesis_families=[EffectFamily.MEAN_REVERSION]),
        resolved_universe=["AAPL"], screening_as_of=date(2026, 1, 1), screening_group_size=10,
    )
    session.add(CharterRow(
        id="c1", mandate_text="mandate", charter=charter.model_dump(mode="json"),
        confirmed=True, created_at=datetime.now(), confirmed_at=datetime.now(),
    ))
    fc = FalsificationCondition(metric="sharpe_ratio", comparison="less_than", threshold=0.0)
    session.add(HypothesisRow(
        id="h1", charter_id="c1", rule=RSI_14_30_70.model_dump(mode="json"),
        prediction="prediction", falsification_condition=fc.model_dump(mode="json"),
        rationale="rationale", citations=[], grounding_tier="none", status=status,
        created_at=datetime.now(),
    ))
    session.add(StudyDesignRow(id="d1", hypothesis_id="h1", design={"placeholder": True}, created_at=datetime.now()))
    session.add(StudyRunRow(
        id="r1", hypothesis_id="h1", study_design_id="d1", status="completed",
        step_count=3, started_at=datetime(2026, 6, 1), finished_at=datetime(2026, 6, 1),
    ))
    session.add(VerdictRow(
        id=verdict_id, study_run_id="r1", status=status,
        claims=[{"statement": "Out-of-sample Sharpe was 1.2.", "tool_call_trace_id": 1, "metric": "sharpe_ratio", "value": 1.2}],
        hypothesis_count_under_charter=1, corrected_significance_threshold=0.05,
        narrative="The strategy beat randomized entries out of sample.",
        caveats=["Grounding tier: none -- ungrounded hypotheses face a stricter bar."],
        created_at=datetime.now(),
    ))
    session.commit()


def test_get_verdict_found(client, api_db_session):
    _seed_verdict(api_db_session)

    resp = client.get("/verdicts/v1")

    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == "v1"
    assert body["status"] == "confirmed"
    assert body["claims"][0]["tool_call_trace_id"] == 1
    assert body["claims"][0]["metric"] == "sharpe_ratio"
    assert len(body["caveats"]) == 1


def test_get_verdict_not_found(client):
    resp = client.get("/verdicts/does-not-exist")

    assert resp.status_code == 404
