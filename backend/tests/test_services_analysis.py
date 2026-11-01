"""Recompute: user statuses preserved, expiry rules, risk persistence, demo dataset lifecycle."""

from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from flipradar.models import FeeProfile, Listing, Notification, Opportunity, PriceObservation, Product
from flipradar.services import analysis, demo, settings_store
from flipradar.services.errors import Invalid
from flipradar.services.ingestion import ingest_raw_listings
from tests.conftest import FAKE_RESULTS, raw

pytestmark = pytest.mark.usefixtures("fake_pipeline")


def _load(db, items=FAKE_RESULTS):
    stats = ingest_raw_listings(db, "manual", list(items))
    db.commit()
    return stats


def test_recompute_persists_risk_and_opportunities(db, settings):
    _load(db)
    result = analysis.recompute(db, settings=settings)
    assert (result.products_analyzed, result.opportunities_created) == (1, 2)
    replica = db.scalars(select(Listing).where(Listing.external_id == "A6")).one()
    assert replica.is_suspicious and replica.risk_flags_json[0]["code"] == "replica_or_counterfeit"
    good = db.scalars(select(Listing).where(Listing.external_id == "A5")).one()
    assert good.is_suspicious is False
    opp = db.scalars(select(Opportunity).where(Opportunity.buy_listing_id == good.id)).one()
    assert opp.fee_profile_key == "ml_clasica" and opp.comparable_count == 4 and opp.status == "new"
    assert opp.cost_breakdown_json["fee_profile_key"] == "ml_clasica"
    assert opp.price_stats_json["n"] == 4 and opp.demand_json["level"] == "unknown"

    again = analysis.recompute(db, settings=settings)
    assert (again.opportunities_created, again.opportunities_updated, again.opportunities_expired) == (0, 0, 0)


def test_statuses_survive_and_expire_correctly(db, settings):
    _load(db)
    analysis.recompute(db, settings=settings)
    opps = {o.buy_listing.external_id: o for o in db.scalars(select(Opportunity))}
    opps["A5"].status = "dismissed"
    opps["A6"].status = "watching"
    opps["A5"].ai_summary = "resumen viejo"
    db.commit()
    # Every listing now at the same price -> no deal remains.
    _load(db, [raw(r.external_id, r.title, 15_500) for r in FAKE_RESULTS])
    result = analysis.recompute(db, settings=settings)
    db.expire_all()
    opps = {o.buy_listing.external_id: o for o in db.scalars(select(Opportunity))}
    assert opps["A5"].status == "dismissed"  # never expires a dismissed one
    assert opps["A6"].status == "expired" and result.opportunities_expired == 1


def test_changed_facts_clear_ai_summary_and_count_update(db, settings):
    _load(db)
    analysis.recompute(db, settings=settings)
    opp = db.scalars(select(Opportunity).join(Listing).where(Listing.external_id == "A5")).one()
    opp.ai_summary = "resumen"
    db.commit()
    _load(db, [raw("A5", "iPhone 15 Pro 256GB usado oferta", 9_400)])
    result = analysis.recompute(db, settings=settings)
    db.refresh(opp)
    assert result.opportunities_updated >= 1 and opp.ai_summary is None and opp.buy_total_cents == 940_000


def test_recompute_requires_a_fee_profile(db, settings):
    _load(db)
    db.query(FeeProfile).delete()
    db.commit()
    with pytest.raises(Invalid):
        analysis.recompute(db, settings=settings)


def test_analysis_settings_from_store(db, settings):
    settings_store.update(db, {"min_comparables": 6, "min_profit_cents": 1, "pricing_strategy": "market",
                               "unknown_shipping_assumption_cents": 9900})
    a = settings_store.analysis_settings(db, settings)
    assert (a.min_comparables, a.min_profit_cents, a.pricing_strategy) == (6, 1, "market")
    assert a.unknown_shipping_assumption_cents == 9900 and a.include_demo is False and a.min_roi_bp == 1000
    with pytest.raises(Invalid):
        settings_store.set_value(db, "pricing_strategy", "random")
    with pytest.raises(Invalid):
        settings_store.set_value(db, "nope", 1)
    with pytest.raises(Invalid):
        settings_store.set_value(db, "min_comparables", "many")
    # With 6 required comparables the fake engine finds nothing in a 6-listing product.
    _load(db)
    assert analysis.recompute(db, settings=settings).opportunities_created == 0


def test_demo_seed_is_labelled_isolated_idempotent_and_removable(db, settings, api):
    now = datetime(2026, 10, 31, 18, 0, tzinfo=UTC)
    counts = demo.seed_demo(db, settings, now=now)
    assert counts["products"] == 6 and 6 * 6 <= counts["listings"] <= 6 * 12
    assert counts["observations"] >= counts["listings"] * 4
    assert counts["opportunities_created"] > 0
    products = db.scalars(select(Product)).all()
    assert all(p.canonical_key.startswith("demo|") and p.title.startswith("[DEMO] ") for p in products)
    listings = db.scalars(select(Listing)).all()
    assert all(x.is_demo and x.title.startswith("[DEMO] ") and x.url is None for x in listings)
    assert any("replica" in x.title for x in listings if x.is_suspicious)
    sold_growth = [x for x in listings if x.sold_quantity and x.sold_quantity > 0]
    assert sold_growth
    assert all(o.is_demo for o in db.scalars(select(Opportunity)))
    assert settings_store.get_value(db, "include_demo_data", settings) is True
    assert api.get("/api/v1/opportunities").json()["total"] > 0

    snapshot = sorted((x.external_id, x.price_cents) for x in listings)
    again = demo.seed_demo(db, settings, now=now)
    assert again == counts
    assert sorted((x.external_id, x.price_cents) for x in db.scalars(select(Listing))) == snapshot

    # Hidden when include_demo_data is off.
    api.put("/api/v1/settings", json={"include_demo_data": False})
    assert api.get("/api/v1/opportunities").json()["total"] == 0
    assert api.get("/api/v1/listings").json()["total"] == 0
    assert api.get("/api/v1/products").json()["total"] == 0
    assert api.get("/api/v1/opportunities?include_demo=true").json()["total"] > 0

    removed = demo.clear_demo(db)
    assert removed["products"] == 6 and removed["listings"] == counts["listings"]
    assert db.query(Listing).count() == 0 and db.query(PriceObservation).count() == 0
    assert db.query(Opportunity).count() == 0 and db.query(Product).count() == 0
    assert db.query(Notification).count() == 0
    assert settings_store.get_value(db, "include_demo_data", settings) is False


def test_real_listings_never_join_demo_clusters(db, settings):
    demo.seed_demo(db, settings)
    stats = _load(db, [raw("REAL1", "Apple iPhone 15 Pro 256GB", 15000)])
    product = db.get(Product, next(iter(stats.product_ids_touched)))
    assert not product.canonical_key.startswith("demo|")
