"""Status-code and response-shape coverage for the charter WRITE routes:
POST /charters, POST /charters/{id}/correct, POST /charters/{id}/confirm.

agentic_core.charter.parse_charter and resolve_universe are monkeypatched
with simple deterministic fakes -- whether a correction's combined prompt
actually changes what parse_charter returns is already proven at the
domain-logic level by tests/agentic_core/test_charter.py; this file is
about the HTTP layer on top: status codes, response shapes, and the
not-found/already-confirmed/round-limit/blocked error mappings.
"""

from datetime import date

import pytest

from agentic_core.schemas import Charter, EffectFamily, ParsedCharter, UniverseFilter


def _fake_parse_charter(mandate_text: str) -> ParsedCharter:
    return ParsedCharter(universe=UniverseFilter(sector="Technology"), hypothesis_families=[EffectFamily.MEAN_REVERSION])


def _fake_resolve_universe(parsed: ParsedCharter, as_of=None) -> Charter:
    return Charter(
        parsed=parsed, resolved_universe=["AAPL", "MSFT"], screening_as_of=date(2026, 1, 1), screening_group_size=40
    )


def _fake_resolve_universe_empty(parsed: ParsedCharter, as_of=None) -> Charter:
    return Charter(parsed=parsed, resolved_universe=[], screening_as_of=date(2026, 1, 1), screening_group_size=0)


@pytest.fixture(autouse=True)
def _patch_llm_and_screener(monkeypatch):
    monkeypatch.setattr("agentic_core.charter.parse_charter", _fake_parse_charter)
    monkeypatch.setattr("agentic_core.charter.resolve_universe", _fake_resolve_universe)


def test_post_charter_creates_round_zero(client):
    resp = client.post("/charters", json={"mandate_text": "Investigate mean-reversion on tech names."})

    assert resp.status_code == 201
    body = resp.json()
    assert body["confirmed"] is False
    assert body["correction_round"] == 0
    assert body["parent_charter_id"] is None
    assert body["blocked"] is False
    assert body["charter"]["resolved_universe"] == ["AAPL", "MSFT"]


def test_post_charter_blocked(client, monkeypatch):
    monkeypatch.setattr("agentic_core.charter.resolve_universe", _fake_resolve_universe_empty)

    resp = client.post("/charters", json={"mandate_text": "Investigate nothing that exists."})

    assert resp.status_code == 201
    assert resp.json()["blocked"] is True


def test_post_correct_charter_creates_chained_row(client):
    created = client.post("/charters", json={"mandate_text": "Investigate mean-reversion on tech names."}).json()

    resp = client.post(f"/charters/{created['id']}/correct", json={"correction_text": "widen it"})

    assert resp.status_code == 201
    body = resp.json()
    assert body["id"] != created["id"]
    assert body["parent_charter_id"] == created["id"]
    assert body["correction_round"] == 1
    assert body["correction_text"] == "widen it"
    assert body["mandate_text"] == created["mandate_text"]


def test_post_correct_charter_not_found(client):
    resp = client.post("/charters/does-not-exist/correct", json={"correction_text": "widen it"})

    assert resp.status_code == 404


def test_post_correct_charter_after_limit_returns_409(client):
    round0 = client.post("/charters", json={"mandate_text": "mandate"}).json()
    round1 = client.post(f"/charters/{round0['id']}/correct", json={"correction_text": "widen"}).json()
    round2 = client.post(f"/charters/{round1['id']}/correct", json={"correction_text": "widen more"}).json()
    assert round2["correction_round"] == 2

    resp = client.post(f"/charters/{round2['id']}/correct", json={"correction_text": "one more"})

    assert resp.status_code == 409


def test_post_correct_charter_on_confirmed_returns_409(client):
    created = client.post("/charters", json={"mandate_text": "mandate"}).json()
    client.post(f"/charters/{created['id']}/confirm")

    resp = client.post(f"/charters/{created['id']}/correct", json={"correction_text": "too late"})

    assert resp.status_code == 409


def test_post_confirm_charter_happy_path(client):
    created = client.post("/charters", json={"mandate_text": "mandate"}).json()

    resp = client.post(f"/charters/{created['id']}/confirm")

    assert resp.status_code == 200
    body = resp.json()
    assert body["confirmed"] is True
    assert body["confirmed_at"] is not None


def test_post_confirm_charter_not_found(client):
    resp = client.post("/charters/does-not-exist/confirm")

    assert resp.status_code == 404


def test_post_confirm_blocked_charter_returns_409(client, monkeypatch):
    monkeypatch.setattr("agentic_core.charter.resolve_universe", _fake_resolve_universe_empty)
    created = client.post("/charters", json={"mandate_text": "mandate"}).json()
    assert created["blocked"] is True

    resp = client.post(f"/charters/{created['id']}/confirm")

    assert resp.status_code == 409
