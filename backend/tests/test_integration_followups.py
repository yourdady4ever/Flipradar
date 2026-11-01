"""Integration follow-ups after the review fixes: settings, bought-status protection, future dates, bounds."""

from datetime import UTC, datetime, timedelta

from flipradar.engine.types import RawListing
from flipradar.money import MAX_CENTS
from flipradar.services.ingestion import _validate
from tests.test_api_alerts import fabricate_opportunity


def test_buy_max_age_days_is_editable(api):
    assert api.get("/api/v1/settings").json()["buy_max_age_days"] == 3
    r = api.put("/api/v1/settings", json={"buy_max_age_days": 7})
    assert r.status_code == 200 and r.json()["buy_max_age_days"] == 7
    assert api.put("/api/v1/settings", json={"buy_max_age_days": 0}).status_code == 422


def test_bought_opportunity_status_is_protected(api, db):
    opp = fabricate_opportunity(db)
    r = api.post(f"/api/v1/opportunities/{opp.id}/buy", json={"quantity": 1})
    assert r.status_code == 201
    r = api.patch(f"/api/v1/opportunities/{opp.id}", json={"status": "seen"})
    assert r.status_code == 409
    assert api.get(f"/api/v1/opportunities/{opp.id}").json()["status"] == "bought"


def test_future_dates_are_rejected(api):
    future = (datetime.now(UTC) + timedelta(days=2)).isoformat()
    r = api.post("/api/v1/inventory", json={"title": "X", "unit_cost_cents": 1000, "purchased_at": future})
    assert r.status_code == 422
    item = api.post("/api/v1/inventory", json={"title": "X", "unit_cost_cents": 1000}).json()
    r = api.post(f"/api/v1/inventory/{item['id']}/sales", json={"unit_price_cents": 2000, "sold_at": future})
    assert r.status_code == 422
    ok = api.post(f"/api/v1/inventory/{item['id']}/sales", json={"unit_price_cents": 2000})
    assert ok.status_code == 201


def test_ingestion_rejects_out_of_range_amounts():
    raw = RawListing(source_key="manual", external_id="a", title="X", price_cents=MAX_CENTS + 1)
    assert _validate(raw) == "amount is out of range"
    raw = RawListing(source_key="manual", external_id="a", title="X", price_cents=100, shipping_cents=MAX_CENTS + 1)
    assert _validate(raw) == "amount is out of range"


def test_buying_imported_opportunity_includes_estimated_duties(api, db):
    opp = fabricate_opportunity(db)
    opp.cost_breakdown_json = {"acquisition_lines": [
        {"key": "buy_price", "amount_cents": 1_000_000}, {"key": "import_costs", "amount_cents": 190_000}]}
    db.commit()
    item = api.post(f"/api/v1/opportunities/{opp.id}/buy", json={"quantity": 2}).json()
    assert item["extra_costs_cents"] >= 2 * 190_000
