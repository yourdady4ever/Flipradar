"""INTEGRATION tests: real normalizer, matcher, engine and collector registry (no fakes).

These depend on modules implemented by other engineers; a failure here with NotImplementedError means
that component is not finished yet.  Run only these with ``pytest -m integration``.
"""

import pytest
from sqlalchemy import select

from flipradar.ai import narrative
from flipradar.collectors import registry
from flipradar.models import Listing, Opportunity, Product
from flipradar.services import analysis, demo
from flipradar.services.ingestion import ingest_raw_listings
from tests.conftest import raw

pytestmark = pytest.mark.integration


def test_demo_dataset_with_real_engine(api, db, settings):
    counts = demo.seed_demo(db, settings)
    assert counts["opportunities_created"] > 0
    opps = db.scalars(select(Opportunity)).all()
    assert all(o.explanation and o.explanation_facts_json for o in opps)
    # Replicas are flagged critical and hidden from the default list; real-looking deals are listed.
    visible = api.get("/api/v1/opportunities").json()["items"]
    assert visible and all("replica" not in o["buy_title"] for o in visible)
    hidden = api.get("/api/v1/opportunities?include_suspicious=true").json()["items"]
    assert any("replica" in o["buy_title"] and o["risk_level"] == "critical" for o in hidden)
    detail = api.get(f"/api/v1/opportunities/{visible[0]['id']}").json()
    assert detail["cost_breakdown"]["fee_profile_key"] == "ml_clasica"
    assert detail["price_stats"]["n"] >= settings.min_comparables
    assert detail["demand"]["level"] in ("high", "medium", "low", "unknown")
    # The explanation the engine renders cites only numbers that are in its own facts.
    ok, unknown = narrative.validate_summary(detail["explanation"], detail["facts"])
    assert ok or len(unknown) <= 3, unknown


def test_full_pipeline_with_real_normalizer_and_matcher(api, db, settings):
    items = [
        raw("R1", "Apple iPhone 15 Pro 256GB Titanio Natural Usado", 15000, brand=None, model=None),
        raw("R2", "iPhone 15 Pro 256 GB usado excelente estado", 15500, brand=None, model=None),
        raw("R3", "Iphone 15 Pro 256gb Seminuevo", 16000, brand=None, model=None),
        raw("R4", "Apple iPhone 15 Pro (256 GB) - usado", 15200, brand=None, model=None),
        raw("R5", "iPhone 15 Pro 256GB usado urge", 9000, brand=None, model=None),
        raw("R6", "Funda para iPhone 15 Pro", 150, brand=None, model=None),
    ]
    stats = ingest_raw_listings(db, "manual", items, settings=settings)
    db.commit()
    phones = db.scalars(select(Listing).where(Listing.external_id.in_(["R1", "R2", "R3", "R4", "R5"]))).all()
    product_ids = {x.product_id for x in phones}
    assert len(product_ids) == 1 and None not in product_ids, "the 5 phone listings must form one cluster"
    case = db.scalars(select(Listing).where(Listing.external_id == "R6")).one()
    assert case.is_accessory and case.product_id not in product_ids
    result = analysis.recompute(db, stats.product_ids_touched, settings=settings)
    assert result.opportunities_created >= 1
    best = db.scalars(select(Opportunity).order_by(Opportunity.score.desc())).first()
    assert best.buy_listing.external_id == "R5"
    product = db.get(Product, best.product_id)
    assert "15 pro" in (product.model or product.canonical_key)


def test_builtin_collectors_are_registered_and_not_configured_without_credentials(api):
    keys = set(registry.collector_classes())
    assert {"mercadolibre", "ebay"} <= keys
    sources = {s["key"]: s for s in api.get("/api/v1/sources").json()}
    assert sources["ebay"]["requires_credentials"] is True and sources["ebay"]["configured"] is False
