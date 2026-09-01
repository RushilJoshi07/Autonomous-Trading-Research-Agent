"""Response-shape and status-code coverage for GET /charters, /charters/{id}."""

from datetime import date, datetime

from agentic_core.db.models import Charter as CharterRow
from agentic_core.schemas import Charter, EffectFamily, ParsedCharter, UniverseFilter


def _seed_charter(session, charter_id: str = "c1", confirmed: bool = False) -> Charter:
    charter = Charter(
        parsed=ParsedCharter(
            universe=UniverseFilter(sector="Technology"),
            hypothesis_families=[EffectFamily.MEAN_REVERSION],
        ),
        resolved_universe=["AAPL", "MSFT"],
        screening_as_of=date(2026, 1, 1),
        screening_group_size=40,
    )
    session.add(
        CharterRow(
            id=charter_id,
            mandate_text="Investigate mean-reversion on liquid tech names.",
            charter=charter.model_dump(mode="json"),
            confirmed=confirmed,
            created_at=datetime.now(),
            confirmed_at=datetime.now() if confirmed else None,
        )
    )
    session.commit()
    return charter


def test_get_charter_found(client, api_db_session):
    _seed_charter(api_db_session, confirmed=True)

    resp = client.get("/charters/c1")

    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == "c1"
    assert body["confirmed"] is True
    assert body["confirmed_at"] is not None
    assert body["charter"]["resolved_universe"] == ["AAPL", "MSFT"]
    assert body["charter"]["parsed"]["hypothesis_families"] == ["mean_reversion"]


def test_get_charter_not_found(client):
    resp = client.get("/charters/does-not-exist")

    assert resp.status_code == 404


def test_list_charters_empty(client):
    resp = client.get("/charters")

    assert resp.status_code == 200
    assert resp.json() == []


def test_list_charters_returns_seeded_rows(client, api_db_session):
    _seed_charter(api_db_session, charter_id="c1", confirmed=False)
    _seed_charter(api_db_session, charter_id="c2", confirmed=True)

    resp = client.get("/charters")

    assert resp.status_code == 200
    ids = {row["id"] for row in resp.json()}
    assert ids == {"c1", "c2"}
