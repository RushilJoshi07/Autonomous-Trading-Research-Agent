"""Response-shape and status-code coverage for GET /hypotheses, /hypotheses/{id}.

Also covers HypothesisOut.study_run_id -- computed by the route (StudyRun
has no column pointing back at "its hypothesis's latest run"; the FK runs
the other way), so it needs its own test rather than trusting the response
model's shape alone.
"""

from datetime import date, datetime

from agentic_core.db.models import Charter as CharterRow
from agentic_core.db.models import Hypothesis as HypothesisRow
from agentic_core.db.models import StudyRun as StudyRunRow
from agentic_core.db.models import StudyDesign as StudyDesignRow
from agentic_core.schemas import Charter, EffectFamily, FalsificationCondition, ParsedCharter, UniverseFilter
from backtester.schema import RSI_14_30_70


def _seed_charter(session, charter_id: str = "c1") -> None:
    charter = Charter(
        parsed=ParsedCharter(universe=UniverseFilter(sector="Technology"), hypothesis_families=[EffectFamily.MEAN_REVERSION]),
        resolved_universe=["AAPL"],
        screening_as_of=date(2026, 1, 1),
        screening_group_size=10,
    )
    session.add(
        CharterRow(
            id=charter_id,
            mandate_text="Investigate mean-reversion on liquid tech names.",
            charter=charter.model_dump(mode="json"),
            confirmed=True,
            created_at=datetime.now(),
            confirmed_at=datetime.now(),
        )
    )
    session.commit()


def _seed_hypothesis(session, hypothesis_id: str = "h1", charter_id: str = "c1", status: str = "proposed") -> None:
    fc = FalsificationCondition(metric="sharpe_ratio", comparison="less_than", threshold=0.0)
    session.add(
        HypothesisRow(
            id=hypothesis_id,
            charter_id=charter_id,
            rule=RSI_14_30_70.model_dump(mode="json"),
            prediction="RSI(14) oversold/overbought reverts within 5 bars on liquid tech names.",
            falsification_condition=fc.model_dump(mode="json"),
            rationale="Short-horizon mean reversion is a well-documented effect family.",
            citations=[],
            grounding_tier="none",
            status=status,
            created_at=datetime.now(),
        )
    )
    session.commit()


def _seed_study_run(session, run_id: str, hypothesis_id: str, started_at: datetime, status: str = "running") -> None:
    design_id = f"{run_id}-design"
    session.add(
        StudyDesignRow(
            id=design_id,
            hypothesis_id=hypothesis_id,
            design={"placeholder": True},
            created_at=started_at,
        )
    )
    session.add(
        StudyRunRow(
            id=run_id,
            hypothesis_id=hypothesis_id,
            study_design_id=design_id,
            status=status,
            step_count=0,
            started_at=started_at,
        )
    )
    session.commit()


def test_get_hypothesis_found(client, api_db_session):
    _seed_charter(api_db_session)
    _seed_hypothesis(api_db_session)

    resp = client.get("/hypotheses/h1")

    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == "h1"
    assert body["charter_id"] == "c1"
    assert body["status"] == "proposed"
    assert body["rule"]["name"] == RSI_14_30_70.name
    assert body["study_run_id"] is None


def test_get_hypothesis_not_found(client):
    resp = client.get("/hypotheses/does-not-exist")

    assert resp.status_code == 404


def test_list_hypotheses_filters_by_charter_id(client, api_db_session):
    _seed_charter(api_db_session, charter_id="c1")
    _seed_charter(api_db_session, charter_id="c2")
    _seed_hypothesis(api_db_session, hypothesis_id="h1", charter_id="c1")
    _seed_hypothesis(api_db_session, hypothesis_id="h2", charter_id="c2")

    resp = client.get("/hypotheses", params={"charter_id": "c1"})

    assert resp.status_code == 200
    body = resp.json()
    assert [row["id"] for row in body] == ["h1"]


def test_hypothesis_study_run_id_is_the_most_recent_run(client, api_db_session):
    _seed_charter(api_db_session)
    _seed_hypothesis(api_db_session)
    _seed_study_run(api_db_session, "run-old", "h1", datetime(2026, 1, 1))
    _seed_study_run(api_db_session, "run-new", "h1", datetime(2026, 6, 1))

    resp = client.get("/hypotheses/h1")

    assert resp.status_code == 200
    assert resp.json()["study_run_id"] == "run-new"
