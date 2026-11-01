"""Collection with a FAKE collector -> ingestion -> analysis -> listings/products/opportunities API."""

import pytest
from sqlalchemy import select

from flipradar.collectors import registry
from flipradar.collectors.jsonld import UrlCheckResult
from flipradar.engine.types import RawListing
from flipradar.finance.calculator import FeeModel, ProfitInputs, calculate_profit
from flipradar.models import CollectionRun, Listing, PriceObservation, Source

pytestmark = pytest.mark.usefixtures("fake_pipeline", "fake_collectors")


def collect(api, **overrides):
    body = {"query": "iphone 15 pro", "source_keys": ["fakesource"], "limit": 50, **overrides}
    r = api.post("/api/v1/collect", json=body)
    assert r.status_code == 200, r.text
    return r.json()


def test_collect_ingests_and_creates_opportunities(api, db):
    result = collect(api)
    assert [run["status"] for run in result["runs"]] == ["ok"]
    run = result["runs"][0]
    assert run["source_key"] == "fakesource" and run["items_fetched"] == 6 and run["items_new"] == 6
    assert result["opportunities_created"] == 2  # A5 (good deal) and A6 (replica, flagged)

    listings = api.get("/api/v1/listings").json()
    assert listings["total"] == 6
    replica = next(item for item in listings["items"] if item["external_id"] == "A6")
    assert replica["is_suspicious"] is True
    assert {f["code"] for f in replica["risk_flags"]} >= {"replica_or_counterfeit"}
    assert api.get("/api/v1/listings?suspicious=true").json()["total"] == 1
    assert api.get("/api/v1/listings?q=oferta").json()["total"] == 1
    assert api.get("/api/v1/listings?source=fakesource&limit=2").json()["items"].__len__() == 2

    detail = api.get(f"/api/v1/listings/{replica['id']}").json()
    assert len(detail["observations"]) == 1 and detail["seller"] == {}
    assert api.get("/api/v1/listings/9999").status_code == 404

    opps = api.get("/api/v1/opportunities").json()
    assert opps["total"] == 1  # suspicious excluded by default
    best = opps["items"][0]
    assert best["buy_title"] == "iPhone 15 Pro 256GB usado oferta" and best["buy_source_key"] == "fakesource"
    # Fake engine: expected sale = median of the 4 fresh, non-critical comparables = $15,350.00.
    fee = FeeModel.from_profile(_profile(db, "ml_clasica"))
    expected = calculate_profit(ProfitInputs(buy_price_cents=950_000, sale_price_cents=1_535_000, fee=fee))
    assert best["expected_sale_cents"] == 1_535_000
    assert best["net_profit_cents"] == expected.net_profit_cents and best["roi_bp"] == expected.roi_bp
    assert best["risk_level"] == "none" and best["demand_level"] == "unknown" and best["status"] == "new"
    assert api.get("/api/v1/opportunities?include_suspicious=true").json()["total"] == 2
    assert api.get("/api/v1/opportunities?min_profit_cents=999999999").json()["total"] == 0
    assert api.get("/api/v1/opportunities?q=oferta&sort=profit").json()["total"] == 1
    assert api.get("/api/v1/opportunities?status=bogus").status_code == 422
    assert api.get("/api/v1/opportunities?sort=bogus").status_code == 422

    d = api.get(f"/api/v1/opportunities/{best['id']}").json()
    assert d["cost_breakdown"] == expected.to_dict()
    assert d["price_stats"]["n"] == 4 and len(d["comparables"]) == 4
    assert d["explanation"].startswith("Fake explanation") and d["facts"]["roi_bp"] == expected.roi_bp
    assert d["ai_summary"] is None and d["demand"]["level"] == "unknown"
    assert api.get("/api/v1/opportunities/9999").status_code == 404

    runs = api.get("/api/v1/runs").json()
    assert runs[0]["status"] == "ok" and runs[0]["source_key"] == "fakesource"

    products = api.get("/api/v1/products").json()
    assert products["total"] == 1
    product = products["items"][0]
    assert product["canonical_key"] == "apple|iphone 15 pro" and product["listing_count"] == 6
    assert product["min_price_mxn_cents"] == 400_000
    pd = api.get(f"/api/v1/products/{product['id']}").json()
    assert len(pd["listings"]) == 6
    assert api.get("/api/v1/products/9999").status_code == 404


def _profile(db, key):
    from flipradar.models import FeeProfile

    return db.scalars(select(FeeProfile).where(FeeProfile.key == key)).one()


def test_recollect_updates_and_preserves_user_status(api, db):
    collect(api)
    opp = api.get("/api/v1/opportunities").json()["items"][0]
    assert api.patch(f"/api/v1/opportunities/{opp['id']}", json={"status": "watching"}).json()["status"] == "watching"
    assert api.patch(f"/api/v1/opportunities/{opp['id']}", json={"status": "bogus"}).status_code == 422
    assert api.patch("/api/v1/opportunities/9999", json={"status": "seen"}).status_code == 404

    # Same results again: listings updated, no duplicate opportunities, status kept.
    again = collect(api)
    assert again["runs"][0]["items_new"] == 0 and again["runs"][0]["items_updated"] == 6
    assert again["opportunities_created"] == 0
    assert api.get(f"/api/v1/opportunities/{opp['id']}").json()["status"] == "watching"
    assert db.query(PriceObservation).count() == 6  # unchanged prices within 6 h -> no new observations

    # The deal disappears (price went up) -> opportunity expires... unless the user acts on it.
    cls = registry.collector_classes()["fakesource"]
    original = cls.results
    try:
        cls.results = [r if r.external_id != "A5" else RawListing(**{**r.__dict__, "price_cents": 1_540_000})
                       for r in original]
        collect(api)
    finally:
        cls.results = original
    assert api.get(f"/api/v1/opportunities/{opp['id']}").json()["status"] == "expired"
    assert db.query(PriceObservation).count() == 7  # price change recorded
    # Price drops again -> it comes back as new.
    collect(api)
    assert api.get(f"/api/v1/opportunities/{opp['id']}").json()["status"] == "new"


def test_buy_opportunity_creates_inventory(api, db):
    collect(api)
    opp = api.get("/api/v1/opportunities").json()["items"][0]
    r = api.post(f"/api/v1/opportunities/{opp['id']}/buy", json={"quantity": 1, "notes": "pagado"})
    assert r.status_code == 201, r.text
    item = r.json()
    assert item["opportunity_id"] == opp["id"] and item["product_id"] == opp["product_id"]
    assert item["unit_cost_cents"] == 950_000 and item["extra_costs_cents"] == 0
    assert item["purchase_source"] == "fakesource" and item["target_sale_cents"] == opp["expected_sale_cents"]
    assert api.get(f"/api/v1/opportunities/{opp['id']}").json()["status"] == "bought"
    assert api.post("/api/v1/opportunities/9999/buy", json={}).status_code == 404
    assert api.post(f"/api/v1/opportunities/{opp['id']}/buy", json={"quantity": 0}).status_code == 422
    # Bought opportunities never expire and are counted out of "open" analytics.
    summary = api.get("/api/v1/analytics/summary").json()
    assert summary["open_opportunities"] == 0 and summary["inventory_units"] == 1
    assert summary["listings_tracked"] == 6 and summary["products_tracked"] == 1
    assert summary["last_collection_at"] is not None


def test_ai_summary_disabled_by_default(api):
    collect(api)
    opp = api.get("/api/v1/opportunities").json()["items"][0]
    r = api.post(f"/api/v1/opportunities/{opp['id']}/ai-summary")
    assert r.status_code == 200
    assert r.json() == {"opportunity_id": opp["id"], "provider": "none", "summary": None, "validated": False,
                        "message": "AI disabled"}
    assert api.post("/api/v1/opportunities/9999/ai-summary").status_code == 404


def test_skipped_error_and_unknown_sources(api, db):
    result = collect(api, source_keys=["needscreds", "brokensource"])
    statuses = {r["source_key"]: (r["status"], r["error"]) for r in result["runs"]}
    assert statuses["needscreds"][0] == "skipped" and "not configured" in statuses["needscreds"][1]
    assert statuses["brokensource"] == ("error", "HTTP 503 from source")
    broken = db.scalars(select(Source).where(Source.key == "brokensource")).one()
    db.refresh(broken)
    assert broken.last_status == "error" and broken.last_error == "HTTP 503 from source"
    assert api.post("/api/v1/collect", json={"query": "x y", "source_keys": ["nope"]}).status_code == 422
    assert api.post("/api/v1/collect", json={"query": "x"}).status_code == 422
    api.patch("/api/v1/sources/fakesource", json={"enabled": False})
    assert collect(api)["runs"][0]["status"] == "skipped"


def test_collect_all_configured_sources_by_default(api):
    result = collect(api, source_keys=[])
    keys = {r["source_key"] for r in result["runs"]}
    assert "fakesource" in keys and "brokensource" in keys and "needscreds" not in keys


def test_tracked_search_run(api, db):
    search = api.post("/api/v1/searches", json={"query": "iphone 15 pro", "source_keys": ["fakesource"]}).json()
    r = api.post(f"/api/v1/searches/{search['id']}/run")
    assert r.status_code == 200 and r.json()["runs"][0]["status"] == "ok"
    assert api.get("/api/v1/searches").json()[0]["last_run_at"] is not None
    assert db.scalars(select(CollectionRun)).one().tracked_search_id == search["id"]


def test_recompute_endpoint(api):
    collect(api, analyze=False)
    assert api.get("/api/v1/opportunities").json()["total"] == 0
    r = api.post("/api/v1/analyze/recompute")
    assert r.status_code == 200
    assert r.json() == {"products_analyzed": 1, "opportunities_created": 2, "opportunities_updated": 0,
                        "opportunities_expired": 0, "notifications_created": 0}


def test_collect_triggers_alert_notifications(api):
    api.post("/api/v1/alerts", json={"name": "iphone deals", "keywords": "iphone", "min_profit_cents": 10_000})
    result = collect(api)
    assert result["notifications_created"] == 1  # the replica is excluded (exclude_suspicious)
    notes = api.get("/api/v1/notifications").json()
    assert len(notes) == 1
    assert notes[0]["title"].startswith("iPhone 15 Pro 256GB usado: +$") and "(ROI " in notes[0]["title"]


def test_check_url_saves_listing(api, monkeypatch, db):
    from flipradar.collectors import jsonld

    async def fake_check(url, settings, client=None):
        return UrlCheckResult(
            url=url, allowed_by_robots=True, found=True,
            raw=RawListing(source_key="web_jsonld", external_id=url, title="iPhone 15 Pro 256GB usado tienda",
                           price_cents=1_499_900, url=url, brand="apple", model="iphone 15 pro", condition_raw="used"),
            extracted={"name": "iPhone 15 Pro", "price": "14999.00"}, message="ok",
        )

    monkeypatch.setattr(jsonld, "check_url", fake_check)
    r = api.post("/api/v1/check-url", json={"url": "https://tienda.example.com/p/1", "save": True})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["found"] is True and body["listing"]["source_key"] == "web_jsonld"
    assert body["listing"]["price_cents"] == 1_499_900
    assert db.scalars(select(Listing).where(Listing.external_id == "https://tienda.example.com/p/1")).one()
    assert api.post("/api/v1/check-url", json={"url": "not a url"}).status_code == 422


def test_collect_without_configured_sources_records_skipped_run(api, db):
    api.patch("/api/v1/sources/fakesource", json={"enabled": False})
    api.patch("/api/v1/sources/brokensource", json={"enabled": False})
    result = collect(api, source_keys=[])
    assert len(result["runs"]) == 1
    run = result["runs"][0]
    assert run["status"] == "skipped" and run["source_key"] is None and "no enabled source" in run["error"]


def test_failed_ingestion_leaves_no_partial_rows(api, db, monkeypatch):
    from flipradar.services import ingestion

    real = ingestion.ingest_raw_listings

    def explode_after_flush(session, key, raws, **kwargs):
        real(session, key, raws[:2], **kwargs)
        raise RuntimeError("disk full")

    monkeypatch.setattr("flipradar.services.collection.ingest_raw_listings", explode_after_flush)
    result = collect(api)
    assert result["runs"][0]["status"] == "error" and "RuntimeError" in result["runs"][0]["error"]
    assert db.query(Listing).count() == 0
