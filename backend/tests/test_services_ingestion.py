"""Ingestion rules: upsert, observations cadence, FX, URL sanitising, demo isolation."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select

from flipradar.engine.types import RawListing
from flipradar.models import FxRate, Listing, PriceObservation, Product
from flipradar.services import fx
from flipradar.services.bootstrap import ensure_source
from flipradar.services.ingestion import build_matcher, ingest_raw_listings, safe_url
from flipradar.services.jsonutil import jsonable, truncate_json
from tests.conftest import raw

pytestmark = pytest.mark.usefixtures("fake_pipeline")
T0 = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)


def _ingest(db, items, now=T0, source="manual"):
    stats = ingest_raw_listings(db, source, items, now=now)
    db.commit()
    return stats


def test_upsert_and_observation_cadence(db):
    item = raw("X1", "iPhone 15 Pro usado", 15000)
    stats = _ingest(db, [item])
    assert (stats.new, stats.updated, stats.skipped) == (1, 0, 0)
    assert len(stats.product_ids_touched) == 1 and len(stats.listing_ids) == 1
    listing = db.scalars(select(Listing)).one()
    assert listing.product.canonical_key == "apple|iphone 15 pro" and listing.match_method == "canonical_key"
    assert listing.match_confidence == 0.95 and listing.condition == "used"
    assert listing.attributes_json["match"]["reasons"] == ["fake matcher"]

    # Same values 1 h later: updated, but no new observation.
    stats = _ingest(db, [item], now=T0 + timedelta(hours=1))
    assert (stats.new, stats.updated) == (0, 1)
    assert db.query(PriceObservation).count() == 1
    # Same values 7 h later: periodic observation.
    _ingest(db, [item], now=T0 + timedelta(hours=7))
    assert db.query(PriceObservation).count() == 2
    # Price change: immediate observation.
    _ingest(db, [raw("X1", "iPhone 15 Pro usado", 14000)], now=T0 + timedelta(hours=8))
    assert db.query(PriceObservation).count() == 3
    db.refresh(listing)
    assert listing.price_cents == 1_400_000 and listing.last_seen_at == T0 + timedelta(hours=8)
    assert listing.first_seen_at == T0


def test_invalid_items_are_skipped(db):
    bad = [
        RawListing(source_key="manual", external_id="", title="x", price_cents=100),
        RawListing(source_key="manual", external_id="a", title="", price_cents=100),
        RawListing(source_key="manual", external_id="b", title="x", price_cents=0),
        RawListing(source_key="manual", external_id="c", title="x", price_cents=10, currency="pesos"),
        RawListing(source_key="manual", external_id="d", title="x", price_cents=10, shipping_cents=-1),
    ]
    stats = _ingest(db, bad)
    assert stats.skipped == 5 and stats.new == 0 and len(stats.errors) == 5
    with pytest.raises(LookupError):
        ingest_raw_listings(db, "nope", [raw("Z", "t", 1)])


def test_unclusterable_listing_has_no_product(db):
    stats = _ingest(db, [raw("N1", "Cosa rara", 100, brand=None, model=None)])
    listing = db.scalars(select(Listing)).one()
    assert listing.product_id is None and listing.match_method == "none" and not stats.product_ids_touched


def test_fx_conversion_uses_stored_rate(db):
    from datetime import UTC, datetime, timedelta

    yesterday = (datetime.now(UTC) - timedelta(days=1)).date().isoformat()  # stale rates are ignored
    db.add(FxRate(base="USD", quote="MXN", rate_date=yesterday, rate="18.1234", source="banxico"))
    db.commit()
    _ingest(db, [RawListing(source_key="manual", external_id="u1", title="Camera", price_cents=10_000, currency="usd",
                            shipping_cents=2_000)])
    listing = db.scalars(select(Listing)).one()
    assert listing.currency == "USD" and listing.price_mxn_cents == 181_234
    assert listing.fx_source == "banxico" and listing.fx_rate == pytest.approx(18.1234)
    from flipradar.services.analysis import shipping_mxn_cents

    assert shipping_mxn_cents(listing) == 36_247  # 20.00 USD * 18.1234 = 362.468 -> 362.47
    eur = RawListing(source_key="manual", external_id="e1", title="Euro thing", price_cents=10_000, currency="EUR")
    _ingest(db, [eur])
    assert db.scalars(select(Listing).where(Listing.external_id == "e1")).one().price_mxn_cents is None


def test_raw_json_is_truncated_and_urls_sanitised(db):
    big = {"small": "ok", "huge": "x" * 50_000, **{f"k{i}": "y" * 900 for i in range(40)}}
    item = raw("R1", "iPhone 15 Pro usado", 15000, raw=big, image_url="data:image/png;base64,AAAA")
    item.url = "javascript:alert(1)"
    _ingest(db, [item])
    listing = db.scalars(select(Listing)).one()
    assert listing.url is None and listing.image_url is None
    assert listing.raw_json["small"] == "ok" and listing.raw_json["huge"].startswith("<dropped")
    assert listing.raw_json["_truncated"] is True
    import json

    assert len(json.dumps(listing.raw_json).encode()) <= 20_000


def test_demo_source_uses_separate_namespace(db):
    ensure_source(db, "demo", kind="demo")
    _ingest(db, [raw("D1", "iPhone 15 Pro usado", 15000)], source="demo")
    _ingest(db, [raw("R1", "iPhone 15 Pro usado", 15000)])
    keys = sorted(p.canonical_key for p in db.scalars(select(Product)))
    assert keys == ["apple|iphone 15 pro", "demo|apple|iphone 15 pro"]
    demo_listing = db.scalars(select(Listing).where(Listing.external_id == "D1")).one()
    assert demo_listing.is_demo is True and demo_listing.product.title.startswith("[DEMO] ")
    assert build_matcher(db).keys == {"apple|iphone 15 pro"}
    assert build_matcher(db, demo=True).keys == {"demo|apple|iphone 15 pro"}


def test_safe_url_and_jsonable_helpers():
    assert safe_url("https://a.com/x?y=1") == "https://a.com/x?y=1"
    assert safe_url("HTTP://A.COM") == "HTTP://A.COM"
    for bad in ("javascript:alert(1)", "ftp://a.com", "//a.com", "https://a.com/ x", None, "", "https://" + "a" * 1001):
        assert safe_url(bad) is None
    assert jsonable({"d": Decimal("1.10"), "t": T0, "s": {2, 1}}) == {"d": "1.10", "t": T0.isoformat(), "s": [1, 2]}
    assert truncate_json(None) == {}
    assert fx.convert_to_mxn_cents(12345, "MXN", None) == 12345
    assert fx.convert_to_mxn_cents(100, "USD", None) is None
    assert fx.convert_to_mxn_cents(101, "USD", Decimal("17.5")) == 1768  # 1767.5 -> half up
