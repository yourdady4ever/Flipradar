from datetime import UTC, datetime, timedelta

import pytest

from flipradar.engine.risk import assess_listing, normalize_text, risk_level
from flipradar.engine.stats import robust_stats
from flipradar.engine.types import ListingView, RiskFlag

NOW = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)
GOOD_SELLER = {"transactions_total": 250, "reputation_level": "5_green", "registration_date": "2018-01-01T00:00:00Z",
               "ratings_negative_pct": 1}


def make_listing(title: str = "Apple iPhone 13 128GB", description: str | None = None, **overrides) -> ListingView:
    data = dict(
        id=1,
        source_key="ml",
        title=title,
        description=description,
        price_mxn_cents=1_000_000,
        shipping_mxn_cents=0,
        condition="used",
        product_key="apple-iphone-13-128",
        match_confidence=0.95,
        observed_at=NOW,
        seller_id="s1",
        seller=dict(GOOD_SELLER),
    )
    data.update(overrides)
    return ListingView(**data)


def flags_by_code(listing: ListingView, stats=None) -> dict[str, RiskFlag]:
    return {flag.code: flag for flag in assess_listing(listing, stats)}


def test_clean_listing_has_no_flags():
    assert assess_listing(make_listing(), robust_stats([1_000_000, 1_050_000, 950_000])) == []


def test_normalize_text_strips_accents_and_case():
    assert normalize_text("  RÉPLICA   Compañía\tÍcloud ") == "replica compania icloud"
    assert normalize_text(None) == ""


# ------------------------------------------------------------------------------------------------
# Text rules: (title, description, expected code or None, expected severity)
# ------------------------------------------------------------------------------------------------

POSITIVE_CASES = [
    ("iPhone 13 RÉPLICA calidad AAA", None, "replica_or_counterfeit", "critical"),
    ("iPhone 13 Pro clon", None, "replica_or_counterfeit", "critical"),
    ("AirPods Pro calidad 1:1", None, "replica_or_counterfeit", "critical"),
    ("Samsung Galaxy S21 Genérico", None, "replica_or_counterfeit", "critical"),
    ("Reloj imitación", None, "replica_or_counterfeit", "critical"),
    ("Audífonos tipo original", None, "replica_or_counterfeit", "critical"),
    ("Tenis similar a Jordan", None, "replica_or_counterfeit", "critical"),
    ("iPhone 13", "Es NO ORIGINAL pero funciona", "replica_or_counterfeit", "critical"),
    ("Consola alternativa", None, "replica_or_counterfeit", "critical"),
    ("PS5 SOLO CAJA", None, "box_or_parts_only", "critical"),
    ("iPhone 14 Pro caja vacía", None, "box_or_parts_only", "critical"),
    ("iPhone 14 Pro empty box", None, "box_or_parts_only", "critical"),
    ("iPhone 14 solo la caja", None, "box_or_parts_only", "critical"),
    ("Nintendo Switch solo cargador", None, "box_or_parts_only", "critical"),
    ("iPhone 12 para piezas", None, "box_or_parts_only", "critical"),
    ("iPhone 12", "No enciende, se vende así", "box_or_parts_only", "critical"),
    ("iPhone 12 para refacciones", None, "box_or_parts_only", "critical"),
    ("iPhone 12 display roto", None, "box_or_parts_only", "critical"),
    ("iPhone 12 Pantalla Rota", None, "box_or_parts_only", "critical"),
    ("iPhone 12 con iCloud", None, "locked_or_reported", "critical"),
    ("iPhone 12 bloqueado", None, "locked_or_reported", "critical"),
    ("iPhone 12 bloqueada por icloud", None, "locked_or_reported", "critical"),
    ("Moto G", "IMEI reportado, solo para wifi", "locked_or_reported", "critical"),
    ("Moto G IMEI inválido", None, "locked_or_reported", "critical"),
    ("Galaxy A54 con cuenta Google", None, "locked_or_reported", "critical"),
    ("Galaxy A54 FRP activo", None, "locked_or_reported", "critical"),
    ("iPhone 13 solo Telcel", None, "carrier_locked", "warning"),
    ("iPhone 13 Compañía: AT&T", None, "carrier_locked", "warning"),
    ("iPhone 13 bloqueado a Telcel", None, "carrier_locked", "warning"),
    ("PS5", "Mándame WhatsApp", "off_platform_payment_or_contact", "warning"),
    ("PS5", "info en wa.me/5215512345678", "off_platform_payment_or_contact", "warning"),
    ("PS5", "Escríbeme por Telegram", "off_platform_payment_or_contact", "warning"),
    ("PS5", "Llama al 55 1234 5678", "off_platform_payment_or_contact", "warning"),
    ("PS5", "Tel (33) 1234-5678", "off_platform_payment_or_contact", "warning"),
    ("PS5", "correo vendedor.ps5@gmail.com", "off_platform_payment_or_contact", "warning"),
    ("PS5", "Acepto depósito o transferencia", "off_platform_payment_or_contact", "warning"),
    ("PS5", "Pago en OXXO", "off_platform_payment_or_contact", "warning"),
    ("PS5", "Hacemos pago por fuera para darte descuento", "off_platform_payment_or_contact", "warning"),
    ("PS5", "Solo efectivo por adelantado", "off_platform_payment_or_contact", "warning"),
]


@pytest.mark.parametrize(("title", "description", "code", "severity"), POSITIVE_CASES)
def test_text_rules_positive(title, description, code, severity):
    flags = flags_by_code(make_listing(title, description))
    assert code in flags, f"{code} not raised for {title!r} / {description!r}: {list(flags)}"
    assert flags[code].severity == severity
    assert flags[code].evidence["matches"]
    assert set(flags[code].evidence["fields"]) <= {"title", "description"}


NEGATIVE_CASES = [
    # Denials common in Mexican listings must NOT raise critical flags.
    ("iPhone 13 100% original, no es réplica ni clon", None),
    ("iPhone 13", "Original, nada de imitaciones"),
    ("Control remoto incluye pilas AAA", None),
    ("Reloj 11:15 hrs", None),
    ("iPhone 14 solo caja abierta", None),
    ("iPhone 12 libre de iCloud", None),
    ("iPhone 12 sin iCloud, no reportado, IMEI limpio", None),
    ("iPhone 12 iCloud: libre", None),
    ("iPhone 12 desbloqueado de fábrica", None),
    ("iPhone 12 no está bloqueado", None),
    ("Galaxy A54 sin cuenta Google", None),
    ("Galaxy A54 cuenta google eliminada", None),
    ("iPhone 13 liberado para cualquier compañía", None),
    ("iPhone 13 funciona con todas las compañías", None),
    ("PS5", "No acepto depósitos ni transferencias, solo compra por la plataforma"),
    ("iPhone 13 128 GB IMEI 356789012345678", None),
    ("iPhone 13 2023 128 256 512", None),
    ("PS5 enciende perfecto, no tiene pantalla rota", None),
]


@pytest.mark.parametrize(("title", "description"), NEGATIVE_CASES)
def test_text_rules_negative(title, description):
    flags = assess_listing(make_listing(title, description), None)
    unexpected = [f.code for f in flags if f.severity in ("critical", "warning")]
    assert unexpected == [], f"unexpected flags for {title!r} / {description!r}: {unexpected}"


def test_generic_accessory_is_info_not_counterfeit():
    flags = flags_by_code(make_listing("iPhone 13 128GB", "Incluye cargador genérico y funda"))
    assert "replica_or_counterfeit" not in flags
    assert flags["non_original_parts"].severity == "info"


def test_for_parts_cues_ignored_when_condition_is_for_parts():
    flags = flags_by_code(make_listing("iPhone 12 para piezas, no enciende", condition="for_parts"))
    assert "box_or_parts_only" not in flags
    # ...but "solo caja" is still critical regardless of condition
    flags = flags_by_code(make_listing("iPhone 12 solo caja", condition="for_parts"))
    assert flags["box_or_parts_only"].severity == "critical"


def test_case_and_accent_insensitive():
    for title in ("REPLICA", "Réplica", "rÉpLiCa"):
        assert "replica_or_counterfeit" in flags_by_code(make_listing(f"iPhone {title}"))


def test_negation_does_not_cross_clause_boundary():
    flags = flags_by_code(make_listing("iPhone 13", "No tiene detalles. Es réplica"))
    assert "replica_or_counterfeit" in flags


# ------------------------------------------------------------------------------------------------
# Price rule
# ------------------------------------------------------------------------------------------------

STATS = robust_stats([900_000, 950_000, 1_000_000, 1_050_000, 1_100_000])  # median 1,000,000


def test_price_far_below_market_critical():
    flag = flags_by_code(make_listing(price_mxn_cents=300_000), STATS)["price_far_below_market"]
    assert flag.severity == "critical"
    assert flag.evidence["ratio"] == 0.3
    assert flag.evidence["median_cents"] == 1_000_000
    assert flag.evidence["n"] == 5


def test_price_far_below_market_warning_by_ratio():
    flag = flags_by_code(make_listing(price_mxn_cents=500_000), STATS)["price_far_below_market"]
    assert flag.severity == "warning"
    assert flag.evidence["ratio"] == 0.5


def test_statistical_outlier_is_informational_only():
    tight = robust_stats([990_000, 995_000, 1_000_000, 1_005_000, 1_010_000])  # MAD 5,000
    flags = flags_by_code(make_listing(price_mxn_cents=900_000), tight)
    assert "price_far_below_market" not in flags  # 90 % of market is a normal arbitrage gap
    flag = flags["price_statistical_outlier"]
    assert flag.severity == "info"
    assert flag.evidence["ratio"] == 0.9
    assert flag.evidence["robust_z"] < -3.5
    assert {"ratio", "median_cents", "n"} <= set(flag.evidence)


def test_price_rule_uses_total_with_shipping():
    # price 340,000 alone would be < 0.35 (critical); with shipping 20,000 the total ratio is 0.36
    flag = flags_by_code(make_listing(price_mxn_cents=340_000, shipping_mxn_cents=20_000), STATS)
    assert flag["price_far_below_market"].severity == "warning"


def test_price_rule_negative_cases():
    assert "price_far_below_market" not in flags_by_code(make_listing(price_mxn_cents=950_000), STATS)
    assert "price_far_below_market" not in flags_by_code(make_listing(price_mxn_cents=100), None)
    assert "price_far_below_market" not in flags_by_code(make_listing(price_mxn_cents=100), robust_stats([]))


# ------------------------------------------------------------------------------------------------
# Seller, shipping, match, condition, staleness
# ------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("seller", "reason_fragment"),
    [
        ({"transactions_total": 2}, "only 2 completed transaction(s)"),
        ({"reputation_level": "1_red"}, "reputation level 1_red"),
        ({"reputation_level": "2_orange", "transactions_total": 500}, "reputation level 2_orange"),
        ({"registration_date": (NOW - timedelta(days=30)).isoformat()}, "account is 30 days old"),
        ({"registration_date": "2026-09-01T00:00:00.000-04:00"}, "days old"),
        ({"ratings_negative_pct": 15}, "15.00% negative ratings"),
        ({"ratings_negative": 0.2}, "20.00% negative ratings"),
    ],
)
def test_seller_new_or_unrated(seller, reason_fragment):
    flag = flags_by_code(make_listing(seller=seller))["seller_new_or_unrated"]
    assert flag.severity == "warning"
    assert any(reason_fragment in reason for reason in flag.evidence["reasons"])


@pytest.mark.parametrize(
    "seller",
    [
        GOOD_SELLER,
        {"transactions_total": 5},
        {"reputation_level": "3_yellow"},
        {"registration_date": (NOW - timedelta(days=90)).isoformat()},
        {"ratings_negative_pct": 10},
    ],
)
def test_seller_ok(seller):
    codes = flags_by_code(make_listing(seller=seller))
    assert "seller_new_or_unrated" not in codes
    assert "seller_unknown" not in codes


@pytest.mark.parametrize("seller", [{}, {"reputation_level": None}, {"transactions_total": "n/a"},
                                    {"ratings_negative_pct": "NaN"}, {"registration_date": "not a date"}])
def test_seller_unknown(seller):
    flags = flags_by_code(make_listing(seller=seller))
    assert flags["seller_unknown"].severity == "info"
    assert "seller_new_or_unrated" not in flags


def test_shipping_unknown_info():
    assert flags_by_code(make_listing(shipping_mxn_cents=None))["shipping_unknown"].severity == "info"
    assert "shipping_unknown" not in flags_by_code(make_listing(shipping_mxn_cents=0))


def test_low_match_confidence():
    assert flags_by_code(make_listing(match_confidence=0.7))["low_match_confidence"].severity == "warning"
    assert flags_by_code(make_listing(match_confidence=None))["low_match_confidence"].severity == "info"
    assert "low_match_confidence" not in flags_by_code(make_listing(match_confidence=0.85))


def test_condition_unknown():
    assert flags_by_code(make_listing(condition="unknown"))["condition_unknown"].severity == "info"
    assert "condition_unknown" not in flags_by_code(make_listing(condition="used"))


def test_stale_listing():
    assert flags_by_code(make_listing(attributes={"hours_since_seen": 100}))["stale_listing"].severity == "info"
    assert "stale_listing" not in flags_by_code(make_listing(attributes={"hours_since_seen": 72}))
    assert "stale_listing" not in flags_by_code(make_listing(attributes={"hours_since_seen": "junk"}))


# ------------------------------------------------------------------------------------------------
# Ordering, levels, determinism
# ------------------------------------------------------------------------------------------------


def test_flags_sorted_by_severity_then_code_and_unique():
    listing = make_listing("iPhone réplica, mándame whatsapp", shipping_mxn_cents=None, condition="unknown",
                           seller={})
    flags = assess_listing(listing, STATS)
    severities = [f.severity for f in flags]
    rank = {"critical": 0, "warning": 1, "info": 2}
    assert severities == sorted(severities, key=rank.__getitem__)
    codes = [f.code for f in flags]
    assert len(codes) == len(set(codes))


def test_risk_level():
    assert risk_level([]) == "none"
    assert risk_level([RiskFlag("a", "info", "")]) == "info"
    assert risk_level([RiskFlag("a", "info", ""), RiskFlag("b", "warning", "")]) == "warning"
    assert risk_level([RiskFlag("b", "warning", ""), RiskFlag("c", "critical", "")]) == "critical"


def test_assess_is_deterministic():
    listing = make_listing("iPhone 13 réplica AAA", "Whatsapp 55 1234 5678", price_mxn_cents=200_000,
                           seller={"transactions_total": 1})
    first = [f.to_dict() for f in assess_listing(listing, STATS)]
    second = [f.to_dict() for f in assess_listing(listing, STATS)]
    assert first == second


def test_never_raises_on_missing_data():
    listing = ListingView(id=9, source_key="x", title="", price_mxn_cents=0, shipping_mxn_cents=None,
                          condition="unknown", product_key=None, match_confidence=None, observed_at=NOW,
                          seller=None, attributes=None)  # type: ignore[arg-type]
    codes = {f.code for f in assess_listing(listing, None)}
    assert {"seller_unknown", "shipping_unknown", "low_match_confidence", "condition_unknown"} <= codes


def test_is_suspicious_definition():
    from flipradar.engine.risk import is_suspicious
    from flipradar.engine.types import RiskFlag

    w = RiskFlag("a", "warning", "x")
    c = RiskFlag("b", "critical", "x")
    i = RiskFlag("c", "info", "x")
    assert not is_suspicious([])
    assert not is_suspicious([w, i, i])
    assert is_suspicious([w, RiskFlag("d", "warning", "y")])
    assert is_suspicious([c])
    assert is_suspicious([{"severity": "critical"}])
