"""Regression tests for verified engine defects (findings engine#0 .. engine#6).

Each section names the finding it pins down.  Pure-engine tests use small local fixtures; the
service-level tests run the real normalizer, matcher and engine through ``analysis.recompute`` (or the
fake pipeline where the finding is about persistence only).
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select

from flipradar.engine.demand import estimate_demand
from flipradar.engine.opportunities import AnalysisSettings, analyze_product
from flipradar.engine.risk import assess_listing
from flipradar.engine.stats import select_comparables
from flipradar.engine.types import ListingView, RawListing
from flipradar.finance.calculator import FeeModel
from flipradar.finance.import_costs import estimate_import_costs
from flipradar.models import AlertRule, Listing, Notification, Opportunity, Source
from flipradar.services import analysis, settings_store
from flipradar.services.ingestion import ingest_raw_listings
from tests.conftest import FAKE_RESULTS, raw

NOW = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)
KEY = "apple-iphone-13-128"
LOCAL = FeeModel(key="local", name="Local cash sale")
GOOD_SELLER = {"transactions_total": 300, "reputation_level": "5_green", "registration_date": "2017-05-01T00:00:00Z"}


def make_listing(listing_id: int, price: int = 1_000_000, **overrides) -> ListingView:
    data = dict(
        id=listing_id,
        source_key="ml",
        title=f"Apple iPhone 13 128GB usado {listing_id}",
        price_mxn_cents=price,
        shipping_mxn_cents=0,
        condition="used",
        product_key=KEY,
        match_confidence=0.95,
        observed_at=NOW - timedelta(days=1),
        url=f"https://example.com/item/MLM{listing_id}",
        seller_id=f"seller-{listing_id}",
        seller=dict(GOOD_SELLER),
    )
    data.update(overrides)
    return ListingView(**data)


def market(buy_price: int = 600_000, **buy_overrides) -> list[ListingView]:
    """Buy listing #1 plus six comparables at $9,000 .. $11,500 (step $500)."""
    comparables = [make_listing(i, 900_000 + (i - 2) * 50_000) for i in range(2, 8)]
    return [make_listing(1, buy_price, **buy_overrides), *comparables]


def run(listings, fee=LOCAL, settings=None, observations=None, now=NOW):
    return analyze_product(KEY, listings, fee, settings or AnalysisSettings(), observations or {}, now=now)


def by_id(candidates, listing_id):
    matches = [c for c in candidates if c.buy.id == listing_id]
    return matches[0] if matches else None


def severities(title: str, description: str | None = None) -> dict[str, str]:
    listing = make_listing(1, title=title, description=description)
    return {flag.code: flag.severity for flag in assess_listing(listing, None)}


# ------------------------------------------------------------------------------------------------
# engine#0 - a negator governing another noun must not cancel a scam term; replica variants
# ------------------------------------------------------------------------------------------------

SCAM_TITLES = [
    ("Apple iPhone 13 128GB sin detalles replica 1:1", "replica_or_counterfeit"),
    ("AirPods Pro 2 sin caja replica AAA", "replica_or_counterfeit"),
    ("iPhone 13 sin rayones replica", "replica_or_counterfeit"),
    ("iPhone 13 sin detalles clon", "replica_or_counterfeit"),
    ("iPhone 12 128GB sin caja con icloud", "locked_or_reported"),
    ("Samsung S23 sin uso con FRP", "locked_or_reported"),
    ("iPhone 12 sin cargador bloqueado", "locked_or_reported"),
    ("iPhone 13 sin detalles compañía Telcel", "carrier_locked"),
    ("AirPods Pro copia exacta", "replica_or_counterfeit"),
    ("Apple Watch calidad espejo", "replica_or_counterfeit"),
    ("Apple Watch Ultra clone", "replica_or_counterfeit"),
    ("AirPods Pro 2 tipo AAA", "replica_or_counterfeit"),
    ("Nike Jordan 1 fake", "replica_or_counterfeit"),
    ("iPhone 13 no es 100% original", "replica_or_counterfeit"),
    ("iPhone 13 calidad triple A", "replica_or_counterfeit"),
    ("AirPods Pro calidad 1.1", "replica_or_counterfeit"),
    ("AirPods Pro 2 con estuche de carga replica AAA", "replica_or_counterfeit"),
]


@pytest.mark.parametrize(("title", "code"), SCAM_TITLES)
def test_scam_terms_after_an_unrelated_negation_are_flagged(title, code):
    expected = "warning" if code == "carrier_locked" else "critical"
    assert severities(title).get(code) == expected, severities(title)


DENIALS = [
    # The negator governs the term itself (directly, through a copula/auxiliary chain, or "de").
    ("iPhone 13 100% original, no es réplica ni clon", None),
    ("iPhone 13 original", "Nada de imitaciones"),
    ("iPhone 13", "No es una réplica"),
    ("iPhone 12", "Nunca ha sido reportado ni bloqueado"),
    ("iPhone 12 libre de iCloud", None),
    ("iPhone 12 sin iCloud ni FRP", None),
    ("iPhone 12", "No cuenta con iCloud"),
    ("PS5", "No se aceptan depósitos"),
    ("PS5", "No acepto depósitos ni transferencias, solo compra por la plataforma"),
    ("iPhone 13", "No es de compañía, liberado"),
    # Not replica wording at all.
    ("Control remoto con pilas tipo AAA", None),
    ("Control remoto incluye pilas triple A", None),
    ("iPhone 13", "Incluye copia de la factura"),
    ("Laptop Dell 14 pulgadas pesa 1.1 kg", None),
    ("Hub USB 1.1 de 4 puertos", None),
]


@pytest.mark.parametrize(("title", "description"), DENIALS)
def test_denials_and_benign_wording_raise_no_warning_or_critical(title, description):
    flags = assess_listing(make_listing(1, title=title, description=description), None)
    assert [f.code for f in flags if f.severity in ("critical", "warning")] == []


def test_unrelated_negation_does_not_hide_a_replica_from_the_opportunity():
    candidate = by_id(run(market(title="Apple iPhone 13 128GB sin detalles replica 1:1")), 1)
    assert candidate is not None
    assert ("replica_or_counterfeit", "critical") in [(f.code, f.severity) for f in candidate.risk_flags]
    assert candidate.confidence == "low"


# ------------------------------------------------------------------------------------------------
# engine#3 - ordinary wording must not raise critical replica/parts flags
# ------------------------------------------------------------------------------------------------

BENIGN = [
    ("Apple iPhone 13 128GB estado similar a nuevo", None),
    ("iPhone 13", "Estado similar a nuevo, sin detalles"),
    ("PS5 Slim con juegos AAA", None),
    ("PS5 Slim", "Incluye 3 juegos AAA"),
    ("iPhone 13", "Incluye solo caja y cable, sin cargador"),
    ("iPhone 13", "Garantia de 30 dias: si no enciende te regreso tu dinero"),
    ("iPhone 13", "Si no es original te devuelvo tu dinero"),
    ("iPhone 13 color alternativo", None),
    ("iPhone 13", "Acepto pago en efectivo o como alternativa transferencia"),
    ("iPhone 13", "Incluye mica de regalo generica"),
]


@pytest.mark.parametrize(("title", "description"), BENIGN)
def test_benign_wording_raises_no_critical_flag(title, description):
    found = severities(title, description)
    assert "critical" not in found.values(), found


def test_generic_gift_accessory_is_reported_as_non_original_part():
    found = severities("iPhone 13", "Incluye mica de regalo generica")
    assert found.get("non_original_parts") == "info"


STILL_CRITICAL = [
    ("Tenis similar a Jordan", None, "replica_or_counterfeit"),
    ("iPhone 13 similar al original", None, "replica_or_counterfeit"),
    ("Consola alternativa", None, "replica_or_counterfeit"),
    ("PS5 solo caja y cables", None, "box_or_parts_only"),
    ("iPhone 12", "Casi no enciende", "box_or_parts_only"),
    ("iPhone 12", "No enciende, se vende así", "box_or_parts_only"),
]


@pytest.mark.parametrize(("title", "description", "code"), STILL_CRITICAL)
def test_narrowed_terms_still_catch_real_cases(title, description, code):
    assert severities(title, description).get(code) == "critical"


def test_benign_wording_keeps_the_deal_visible_and_the_comparable_usable():
    candidate = by_id(run(market(title="Apple iPhone 13 128GB estado similar a nuevo")), 1)
    assert candidate is not None and candidate.confidence != "low"
    assert all(f.severity != "critical" for f in candidate.risk_flags)

    comparable = make_listing(3, 1_000_000, description="Garantia: si no enciende te regreso tu dinero")
    flags = assess_listing(comparable, None)
    comparable.attributes["risk_codes"] = [{"code": f.code, "severity": f.severity} for f in flags]
    pool = [make_listing(1, 600_000), make_listing(2, 950_000), comparable, make_listing(4, 1_050_000)]
    comps, excluded = select_comparables(pool[0], pool, now=NOW, max_age_days=30)
    assert 3 in [c.id for c in comps] and "critical_risk" not in excluded


# ------------------------------------------------------------------------------------------------
# engine#1 - cross-border buys must carry estimated import duties
# ------------------------------------------------------------------------------------------------


def test_engine_charges_import_duties_and_explains_them():
    note = "Import duties estimated with rule 'courier_usmca' (test note)."
    listings = market(450_000, shipping_mxn_cents=60_000, source_key="ebay", resale_reference=False,
                      import_required=True, import_costs_mxn_cents=96_900, import_note=note)
    candidate = by_id(run(listings), 1)
    assert candidate is not None
    lines = {line["key"]: line["amount_cents"] for line in candidate.profit["acquisition_lines"]}
    assert lines["import_costs"] == 96_900
    assert candidate.buy_total_cents == 450_000 + 60_000 + 96_900
    assert candidate.net_profit_cents == 962_500 - 606_900  # LOCAL fee model: no selling costs
    assert candidate.facts["import_required"] is True
    assert candidate.facts["import_costs_cents"] == 96_900 and candidate.facts["import_costs"] == "$969.00"
    assert note in candidate.facts["caveats"]
    assert "Import duties / customs: $969.00" in candidate.explanation
    assert "import duties $969.00" in candidate.explanation
    assert _numbers(candidate.explanation) <= _fact_numbers(candidate.facts)


def _numbers(text: str) -> set[str]:
    return {m.group(0).replace(",", "") for m in re.finditer(r"\d[\d,]*(?:\.\d+)?", text)}


def _fact_numbers(value) -> set[str]:
    if isinstance(value, dict):
        return set().union(*(_fact_numbers(v) for v in value.values())) if value else set()
    if isinstance(value, (list, tuple)):
        return set().union(*(_fact_numbers(v) for v in value)) if value else set()
    if isinstance(value, bool) or value is None:
        return set()
    return _numbers(str(value))


def test_service_import_note_numbers_are_all_facts():
    view = analysis.listing_view(_orm_listing("US", "USD", 20_000, 2_000), KEY, NOW)
    listings = [ListingView(**{**make_listing(1).__dict__, **{
        "price_mxn_cents": view.price_mxn_cents, "shipping_mxn_cents": view.shipping_mxn_cents,
        "source_key": "ebay", "resale_reference": False, "import_required": True,
        "import_costs_mxn_cents": view.import_costs_mxn_cents, "import_note": view.import_note}}),
        *market()[1:]]
    candidate = by_id(run(listings), 1)
    assert candidate is not None and view.import_note in candidate.explanation
    assert _numbers(candidate.explanation) <= _fact_numbers(candidate.facts)


def test_engine_skips_imports_whose_duties_cannot_be_estimated():
    listings = market(450_000, source_key="ebay", resale_reference=False, import_required=True,
                      import_costs_mxn_cents=None, import_note="Formal import required.")
    assert by_id(run(listings), 1) is None


def test_domestic_listing_has_no_import_line_or_caveat():
    candidate = by_id(run(market()), 1)
    assert candidate.facts["import_required"] is False and candidate.facts["import_costs_cents"] == 0
    assert "import duties" not in candidate.explanation


def _orm_listing(country: str, currency: str, price_cents: int, shipping_cents: int | None, *,
                 fx_rate: float | None = 18.2142, location: str | None = None) -> Listing:
    source = Source(key="ebay", name="eBay", kind="marketplace", country=country, currency=currency)
    attributes = {"source_attributes": {"item_location_country": location}} if location else {}
    rate = Decimal(repr(fx_rate)) if fx_rate is not None else Decimal(1)
    return Listing(
        id=1, source=source, external_id="E1", title="Apple iPhone 13 128GB used", currency=currency,
        price_cents=price_cents,
        price_mxn_cents=int((Decimal(price_cents) * rate).to_integral_value()) if currency != "MXN" else price_cents,
        fx_rate=fx_rate if currency != "MXN" else None, fx_source="manual", shipping_cents=shipping_cents,
        condition="used", status="active", attributes_json=attributes, risk_flags_json=[], seller_json={},
        last_seen_at=NOW, first_seen_at=NOW,
    )


def test_listing_view_estimates_duties_for_foreign_listings():
    view = analysis.listing_view(_orm_listing("US", "USD", 20_000, 2_000), KEY, NOW)
    expected = estimate_import_costs(20_000, 2_000, "courier_usmca", Decimal("18.2142"))
    assert view.import_required is True
    assert view.import_costs_mxn_cents == expected["duties_mxn_cents"] == 76_135  # 220 USD * 18.2142 * 19 %
    assert "courier_usmca" in view.import_note and "19.00%" in view.import_note

    china = analysis.listing_view(_orm_listing("US", "USD", 20_000, 2_000, location="CN"), KEY, NOW)
    assert china.import_costs_mxn_cents == 134_239  # 220 USD * 18.2142 * 33.5 %
    assert "courier_non_fta" in china.import_note

    formal = analysis.listing_view(_orm_listing("US", "USD", 260_000, 0), KEY, NOW)
    assert formal.import_required is True and formal.import_costs_mxn_cents is None
    assert "formal import" in formal.import_note.lower()

    priced_in_pesos = analysis.listing_view(_orm_listing("US", "MXN", 600_000, 0), KEY, NOW)
    assert priced_in_pesos.import_required is True and priced_in_pesos.import_costs_mxn_cents is None

    domestic = analysis.listing_view(_orm_listing("MX", "MXN", 600_000, 0), KEY, NOW)
    assert domestic.import_required is False and domestic.import_costs_mxn_cents is None

    seller_in_mexico = analysis.listing_view(_orm_listing("US", "USD", 20_000, 2_000, location="MX"), KEY, NOW)
    assert seller_in_mexico.import_required is False


def _cross_border_raw(src, eid, title, cents, ship, currency, *, location=None, seller=None, at=None):
    return RawListing(
        source_key=src, external_id=eid, title=title, price_cents=cents, currency=currency,
        url=f"https://example.com/{eid}", condition_raw="used", shipping_cents=ship, brand="apple",
        model="iphone 13", seller_id=seller or f"seller-{eid}", seller_name=seller or eid,
        seller={"transactions_total": 900, "reputation_level": "5_green"},
        attributes={"item_location_country": location} if location else {}, observed_at=at, status="active",
    )


def test_recompute_includes_import_duties_for_ebay_buys(db, settings):
    settings_store.set_value(db, "fx_usd_mxn_manual", "18.2142")
    db.commit()
    ml = [_cross_border_raw("mercadolibre", f"MLM{i}", "Apple iPhone 13 128GB usado", p * 100, 0, "MXN")
          for i, p in enumerate([9000, 9500, 10000, 10500, 11500], 1)]
    ingest_raw_listings(db, "mercadolibre", ml, settings=settings)
    ebay = [
        _cross_border_raw("ebay", "EB1", "Apple iPhone 13 128GB Unlocked used", 20_000, 2_000, "USD"),
        _cross_border_raw("ebay", "EB2", "Apple iPhone 13 128GB Unlocked used", 20_000, 2_000, "USD", location="CN"),
        # USD 330 + 33: profitable only while duties are ignored (verified reproduction).
        _cross_border_raw("ebay", "EB3", "Apple iPhone 13 128GB Unlocked used", 33_000, 3_300, "USD"),
    ]
    ingest_raw_listings(db, "ebay", ebay, settings=settings)
    db.commit()
    analysis.recompute(db, settings=settings, dispatch=False)

    def opp_for(eid):
        return db.scalars(select(Opportunity).join(Listing, Opportunity.buy_listing_id == Listing.id)
                          .where(Listing.external_id == eid)).first()

    for eid, duties in (("EB1", 76_135), ("EB2", 134_239)):
        opp = opp_for(eid)
        assert opp is not None, eid
        line = next(x for x in opp.cost_breakdown_json["acquisition_lines"] if x["key"] == "import_costs")
        assert line["amount_cents"] == duties
        assert opp.explanation_facts_json["import_costs_cents"] == duties
        assert opp.buy_total_cents == opp.cost_breakdown_json["total_acquisition_cents"]
        assert "import duties" in opp.explanation
    assert opp_for("EB3") is None


# ------------------------------------------------------------------------------------------------
# engine#2 - stale buy listings never become opportunities; existing ones expire
# ------------------------------------------------------------------------------------------------


def test_stale_buy_listing_is_not_a_candidate():
    assert by_id(run(market(observed_at=NOW - timedelta(days=200))), 1) is None
    assert by_id(run(market(observed_at=NOW - timedelta(days=3, seconds=1))), 1) is None
    assert by_id(run(market(observed_at=NOW - timedelta(days=3))), 1) is not None
    # The buy window is never looser than the comparable window.
    tight = AnalysisSettings(comparable_max_age_days=1)
    assert by_id(run(market(observed_at=NOW - timedelta(days=2)), settings=tight), 1) is None


def test_opportunity_expires_when_its_buy_listing_is_no_longer_seen(db, settings):
    t0 = datetime(2026, 4, 15, 12, tzinfo=UTC)
    prices = [9000, 9500, 10000, 10500, 11000, 11500]

    def comparables(at):
        return [raw(f"C{i}", "Apple iPhone 15 Pro 256GB usado", p, brand=None, model=None, observed_at=at)
                for i, p in enumerate(prices)]

    buy = raw("B1", "Apple iPhone 15 Pro 256GB usado", 6000, brand=None, model=None, observed_at=t0)
    ingest_raw_listings(db, "manual", [*comparables(t0), buy], now=t0, settings=settings)
    db.commit()
    analysis.recompute(db, now=t0, settings=settings, dispatch=False)
    opp = db.scalars(select(Opportunity).join(Listing, Opportunity.buy_listing_id == Listing.id)
                     .where(Listing.external_id == "B1")).one()
    assert opp.status == "new"

    later = t0 + timedelta(days=4)
    ingest_raw_listings(db, "manual", comparables(later), now=later, settings=settings)
    db.commit()
    result = analysis.recompute(db, now=later, settings=settings, dispatch=False)
    db.refresh(opp)
    assert opp.status == "expired" and result.opportunities_expired == 1


def test_stale_opportunity_expires_even_when_its_product_is_not_recomputed(db, settings):
    t0 = datetime(2026, 4, 15, 12, tzinfo=UTC)
    phones = [raw(f"C{i}", "Apple iPhone 15 Pro 256GB usado", p, brand=None, model=None, observed_at=t0)
              for i, p in enumerate([9000, 9500, 10000, 10500, 11000, 11500])]
    phones.append(raw("B1", "Apple iPhone 15 Pro 256GB usado", 6000, brand=None, model=None, observed_at=t0))
    ingest_raw_listings(db, "manual", phones, now=t0, settings=settings)
    db.commit()
    analysis.recompute(db, now=t0, settings=settings, dispatch=False)
    opp = db.scalars(select(Opportunity)).one()

    later = t0 + timedelta(days=4)
    other = [raw(f"S{i}", "Sony PlayStation 5 Slim usado", p, brand=None, model=None, observed_at=later)
             for i, p in enumerate([7000, 7500, 8000])]
    stats = ingest_raw_listings(db, "manual", other, now=later, settings=settings)
    db.commit()
    assert opp.product_id not in stats.product_ids_touched
    result = analysis.recompute(db, sorted(stats.product_ids_touched), now=later, settings=settings, dispatch=False)
    db.refresh(opp)
    assert opp.status == "expired" and result.opportunities_expired == 1


# ------------------------------------------------------------------------------------------------
# engine#4 - user statuses survive a change of fee profile key and a product move
# ------------------------------------------------------------------------------------------------


@pytest.mark.usefixtures("fake_pipeline")
def test_statuses_survive_a_fee_profile_change(db, settings):
    ingest_raw_listings(db, "manual", list(FAKE_RESULTS))
    db.add(AlertRule(name="any", enabled=True, exclude_suspicious=False))
    db.commit()
    analysis.recompute(db, settings=settings, dispatch=False)
    opps = {o.buy_listing.external_id: o for o in db.scalars(select(Opportunity))}
    opps["A5"].status = "dismissed"
    opps["A6"].status = "watching"
    ids = {eid: o.id for eid, o in opps.items()}
    db.commit()
    notes_before = db.query(Notification).count()

    settings_store.set_value(db, "default_fee_profile_key", "ml_premium")
    db.commit()
    result = analysis.recompute(db, settings=settings, dispatch=False)
    db.expire_all()
    rows = sorted((o.buy_listing.external_id, o.id, o.fee_profile_key, o.status) for o in db.scalars(select(Opportunity)))
    assert rows == [("A5", ids["A5"], "ml_premium", "dismissed"), ("A6", ids["A6"], "ml_premium", "watching")]
    assert (result.opportunities_created, result.opportunities_expired) == (0, 0)
    assert db.query(Notification).count() == notes_before


@pytest.mark.usefixtures("fake_pipeline")
def test_watching_survives_a_move_to_another_product_in_the_same_recompute(db, settings):
    ingest_raw_listings(db, "manual", list(FAKE_RESULTS))
    db.commit()
    analysis.recompute(db, settings=settings, dispatch=False)
    a5 = next(o for o in db.scalars(select(Opportunity)) if o.buy_listing.external_id == "A5")
    a5.status = "watching"
    db.commit()
    # A5 is re-matched to a newer product (model "x") while product A is re-seen in the same batch.
    items = [r for r in FAKE_RESULTS if r.external_id in ("A1", "A2", "A3", "A4")]
    items += [raw(f"C{i}", f"iPhone X usado {i}", p, model="x") for i, p in enumerate([15000, 15500, 16000, 15200])]
    items += [raw("A5", "iPhone X usado oferta", 9500, model="x")]
    stats = ingest_raw_listings(db, "manual", items)
    db.commit()
    assert len(stats.product_ids_touched) == 2
    result = analysis.recompute(db, sorted(stats.product_ids_touched), settings=settings, dispatch=False)
    db.expire_all()
    moved = db.get(Opportunity, a5.id)
    assert moved.status == "watching" and moved.product_id == moved.buy_listing.product_id
    assert result.opportunities_expired == 0


# ------------------------------------------------------------------------------------------------
# engine#5 - demand only from resale-market listings of the candidate's condition class
# ------------------------------------------------------------------------------------------------


def _growth(listing_id: int, start: int, end: int, price: int = 1_000_000):
    return {listing_id: [(NOW - timedelta(days=10), price, start, "active"), (NOW - timedelta(days=1), price, end, "active")]}


def test_demand_ignores_other_conditions_foreign_sources_and_for_parts():
    official_new = make_listing(20, 1_800_000, condition="new", seller_id="official-store")
    ebay = make_listing(30, 700_000, source_key="ebay", resale_reference=False, seller_id="ebayer")
    parts = [make_listing(40 + i, 150_000, condition="for_parts", status="sold", seller_id=f"fp{i}") for i in range(2)]
    observations = {**_growth(20, 100, 400), **_growth(30, 100, 400)}
    candidate = by_id(run([*market(), official_new, ebay, *parts], observations=observations), 1)
    assert candidate.demand.level == "unknown"
    active = next(s for s in candidate.demand.signals if s["name"] == "active_listings")
    assert active["value"] == 7 and "ebay" not in active["source"]

    # Used resale evidence still counts for the used candidate.
    used = by_id(run(market(), observations=_growth(3, 10, 19)), 1)
    assert used.demand.level == "high"


def test_new_candidate_uses_new_unit_demand():
    listings = [make_listing(i, 1_000_000 + i * 10_000, condition="new") for i in range(2, 8)]
    listings.append(make_listing(1, 600_000, condition="new"))
    listings.append(make_listing(9, 900_000, condition="used"))
    candidate = by_id(run(listings, observations=_growth(9, 0, 100)), 1)
    assert candidate.demand.level == "unknown"
    candidate = by_id(run(listings, observations=_growth(3, 0, 100)), 1)
    assert candidate.demand.level == "high"


# ------------------------------------------------------------------------------------------------
# engine#6 - demand confidence comes from the evidence that set the level
# ------------------------------------------------------------------------------------------------


def test_demand_confidence_follows_the_stream_that_set_the_level():
    listings = [make_listing(1), make_listing(2, status="sold"), make_listing(3, status="sold")]
    obs = {1: [(NOW - timedelta(days=4), None, 10, "active"), (NOW - timedelta(days=1), None, 14, "active")]}
    estimate = estimate_demand(listings, obs, now=NOW)
    assert (estimate.level, estimate.confidence) == ("high", "low")


def test_agreeing_streams_keep_the_higher_confidence():
    listings = [make_listing(i, status="sold") for i in range(1, 8)] + [make_listing(8)]
    obs = {8: [(NOW - timedelta(days=4), None, 0, "active"), (NOW - timedelta(days=1), None, 1, "active")]}
    estimate = estimate_demand(listings, obs, now=NOW)  # (a) 0.33/day low conf, (b) 7/30 = 0.23/day medium conf
    assert (estimate.level, estimate.confidence) == ("medium", "medium")
