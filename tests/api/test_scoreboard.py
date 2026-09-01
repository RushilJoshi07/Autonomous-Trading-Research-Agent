"""Response-shape coverage for GET /scoreboard.

The load-bearing thing to verify here isn't the happy path alone -- it's
that 'decayed' is honestly always [] (ScoreboardEntry has no writer until
Stage 8's decay job exists, per docs/plans/stage-7-plan.md's own confirmed
gap) and that rejected/inconclusive/proposed hypotheses don't leak into
either bucket.
"""

from datetime import date, datetime

from agentic_core.db.models import Charter as CharterRow
from agentic_core.db.models import Hypothesis as HypothesisRow
from agentic_core.db.models import StudyDesign as StudyDesignRow
from agentic_core.db.models import StudyRun as StudyRunRow
from agentic_core.db.models import Verdict as VerdictRow
from agentic_core.schemas import Charter, EffectFamily, FalsificationCondition, ParsedCharter, UniverseFilter
from backtester.schema import RSI_14_30_70


def _seed_charter(session, charter_id: str = "c1") -> None:
    charter = Charter(
        parsed=ParsedCharter(universe=UniverseFilter(sector="Technology"), hypothesis_families=[EffectFamily.MEAN_REVERSION]),
        resolved_universe=["AAPL"], screening_as_of=date(2026, 1, 1), screening_group_size=10,
    )
    session.add(CharterRow(
        id=charter_id, mandate_text="mandate", charter=charter.model_dump(mode="json"),
        confirmed=True, created_at=datetime.now(), confirmed_at=datetime.now(),
    ))
    session.commit()


def _seed_hypothesis(session, hypothesis_id: str, charter_id: str, status: str) -> None:
    fc = FalsificationCondition(metric="sharpe_ratio", comparison="less_than", threshold=0.0)
    session.add(HypothesisRow(
        id=hypothesis_id, charter_id=charter_id, rule=RSI_14_30_70.model_dump(mode="json"),
        prediction="prediction", falsification_condition=fc.model_dump(mode="json"),
        rationale="rationale", citations=[], grounding_tier="none", status=status,
        created_at=datetime.now(),
    ))
    session.commit()


def _seed_run_and_verdict(session, run_id: str, hypothesis_id: str, verdict_status: str) -> None:
    design_id = f"{run_id}-design"
    session.add(StudyDesignRow(id=design_id, hypothesis_id=hypothesis_id, design={"placeholder": True}, created_at=datetime.now()))
    session.add(StudyRunRow(
        id=run_id, hypothesis_id=hypothesis_id, study_design_id=design_id, status="completed",
        step_count=3, started_at=datetime(2026, 6, 1), finished_at=datetime(2026, 6, 1),
    ))
    session.add(VerdictRow(
        id=f"{run_id}-verdict", study_run_id=run_id, status=verdict_status, claims=[],
        hypothesis_count_under_charter=1, corrected_significance_threshold=0.05,
        narrative="narrative", caveats=[], created_at=datetime.now(),
    ))
    session.commit()


def test_scoreboard_buckets_and_decayed_note(client, api_db_session):
    _seed_charter(api_db_session)
    _seed_hypothesis(api_db_session, "h-confirmed", "c1", "confirmed")
    _seed_run_and_verdict(api_db_session, "r-confirmed", "h-confirmed", "confirmed")
    _seed_hypothesis(api_db_session, "h-testing", "c1", "testing")
    _seed_hypothesis(api_db_session, "h-rejected", "c1", "rejected")
    _seed_run_and_verdict(api_db_session, "r-rejected", "h-rejected", "rejected")
    _seed_hypothesis(api_db_session, "h-proposed", "c1", "proposed")

    resp = client.get("/scoreboard")

    assert resp.status_code == 200
    body = resp.json()

    assert [e["hypothesis_id"] for e in body["confirmed"]] == ["h-confirmed"]
    assert body["confirmed"][0]["verdict"]["status"] == "confirmed"

    assert [e["hypothesis_id"] for e in body["testing"]] == ["h-testing"]
    assert body["testing"][0]["study_run_id"] is None

    assert body["decayed"] == []
    assert "Stage 8" in body["decayed_note"]


def test_scoreboard_empty_when_no_hypotheses(client):
    resp = client.get("/scoreboard")

    assert resp.status_code == 200
    body = resp.json()
    assert body == {
        "confirmed": [],
        "decayed": [],
        "decayed_note": body["decayed_note"],
        "testing": [],
    }
