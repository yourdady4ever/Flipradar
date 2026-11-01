"""Tests for flipradar.matching (canonical keys, pairwise equivalence, ProductMatcher).

The negative pairs are the dangerous ones: each would create a *fake arbitrage opportunity* if it were
matched (a $150 case priced against $20,000 phones, a digital PS5 against the disc edition...).
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from flipradar.engine.types import MatchResult, NormalizedAttributes, RawListing
from flipradar.matching import (
    ProductCandidate,
    ProductMatcher,
    canonical_key,
    compare,
    hard_conflicts,
    soft_differences,
)
from flipradar.normalize import normalize_listing

VALID_GTIN = "4006381333931"  # GS1 reference EAN-13 (check digit valid) - synthetic test identity
OTHER_GTIN = "036000291452"  # GS1 reference UPC-A


def norm(title: str, **kw) -> NormalizedAttributes:
    return normalize_listing(RawListing(source_key="t", external_id="1", title=title, price_cents=1, **kw))


# ------------------------------------------------------------------------------------------------
# Equivalence pairs
# ------------------------------------------------------------------------------------------------

POSITIVE_PAIRS = [
    ("Apple iPhone 15 Pro Max (256 GB) - Titanio Natural",
     "iPhone 15 Pro Max 256gb Titanio Natural Desbloqueado Reacondicionado"),
    ("Consola Sony PlayStation 5 Slim Digital 1TB", "PS5 Slim Edicion Digital 1 TB"),
    ("Samsung Galaxy S24 Ultra 256GB Negro", "Galaxy S24 Ultra 256 GB Titanium Black Desbloqueado"),
    ("Nintendo Switch OLED 64GB Blanco", "Consola Nintendo Switch Modelo OLED Neon"),
    ("Xbox Series X 1TB", "Consola Microsoft Xbox Series X 1 TB Negro"),
    ("Apple AirPods Pro 2da Generación USB-C", "AirPods Pro (2nd generation) MagSafe"),
    ("Apple Watch Series 9 45mm GPS Medianoche", "Apple Watch S9 GPS 45 mm Aluminio"),
    ("iPad 10ma Generación 64GB WiFi Plata", "Apple iPad (10a generación) Wi-Fi 64 GB Azul"),
    ("Steam Deck OLED 1TB", "Valve Steam Deck OLED 1 TB Consola Portatil"),
    ("Redmi Note 13 Pro+ 5G 8+256GB", "Xiaomi Redmi Note 13 Pro Plus 5G 256GB 8GB RAM"),
    ("MacBook Air 13 M2 8GB RAM 256GB SSD", 'Apple MacBook Air M2 13.6" 8GB 256GB'),
    ("GoPro HERO12 Black", "Cámara GoPro Hero 12 Black Sumergible"),
    ("LEGO Star Wars Millennium Falcon 75192", "Lego 75192 Halcon Milenario UCS"),
    ("Moto G84 5G 256GB", "Motorola Moto G84 5G 256 GB 12 GB RAM"),
    ("Google Pixel 8a 128GB", "Pixel 8a 128 GB Bay Desbloqueado"),
    ("Control DualSense PS5 Blanco", "Sony DualSense Control Inalambrico PS5 Midnight Black"),
    ("Consola PS5 1TB Edicion Digital", "PlayStation 5 Slim Digital 1TB Nueva"),
    ("PlayStation 5 Slim Estandar con lector 1TB", "PS5 Slim con lector de discos 1 TB"),
]


@pytest.mark.parametrize(("left", "right"), POSITIVE_PAIRS, ids=[p[0][:40] for p in POSITIVE_PAIRS])
def test_positive_pairs_match(left: str, right: str) -> None:
    a, b = norm(left), norm(right)
    result = compare(a, b)
    assert result.method in ("gtin", "canonical_key"), result
    assert result.confidence >= 0.95
    assert result.product_key == a.canonical_key == b.canonical_key
    assert result.reasons and all(isinstance(r, str) for r in result.reasons)
    # symmetric
    assert compare(b, a).confidence == result.confidence


def test_gtin_pair_matches_even_with_different_titles() -> None:
    a = norm("Bocina JBL Flip 6 Negra", gtin=VALID_GTIN)
    b = norm("Bocina Portátil Bluetooth Original Resistente al agua", gtin=VALID_GTIN)
    result = compare(a, b)
    assert result.method == "gtin"
    assert result.confidence == 0.99
    assert f"identical GTIN {VALID_GTIN}" in result.reasons[0]


# (left, right, substring expected in the reasons)
NEGATIVE_PAIRS = [
    ("Apple iPhone 15 Pro Max (256 GB) - Titanio Natural", "Funda Silicon Para iPhone 15 Pro Max",
     "accessory (case)"),
    ("Apple iPhone 15 Pro Max (256 GB) - Titanio Natural", "iPhone 15 Pro 256gb",
     "model differs (iphone 15 pro max vs iphone 15 pro)"),
    ("Consola Sony PlayStation 5 Slim Digital 1TB", "PlayStation 5 Slim Estandar con lector 1TB",
     "edition differs (digital vs disc)"),
    ("Nintendo Switch OLED 64GB Blanco", "Nintendo Switch Lite Turquesa", "model differs (switch oled vs switch lite)"),
    ("iPhone 15 Pro Max 256GB", "iPhone 15 Pro Max 512GB", "storage differs (256gb vs 512gb)"),
    ("Samsung Galaxy S24 256GB", "Samsung Galaxy S24+ 256GB", "model differs"),
    ("Samsung Galaxy S24 Ultra 256GB", "Samsung Galaxy S23 Ultra 256GB", "model differs"),
    ("Apple Watch Series 9 45mm GPS", "Apple Watch Series 9 41mm GPS", "case size differs (45mm vs 41mm)"),
    ('iPad Air 11" M2 128GB Wi-Fi', 'iPad Air 11" M2 128GB Wi-Fi + Cellular', "connectivity differs"),
    ("MacBook Air M2 13 8GB RAM 256GB", "MacBook Air M2 13 16GB RAM 256GB", "RAM differs (ram8gb vs ram16gb)"),
    ("Tarjeta de video RTX 3060 12GB", "Tarjeta de video RTX 3060 8GB", "VRAM differs"),
    ("Tarjeta de video RTX 4070 12GB", "Tarjeta de video RTX 4070 Ti 12GB", "model differs"),
    ("Apple AirPods Pro 2da Generación", "Apple AirPods 2da Generación", "model differs"),
    ("Xbox Series X 1TB", "Xbox Series S 1TB", "model differs (xbox series x vs xbox series s)"),
    ("Consola PS5 Slim Digital 1TB", "Control DualSense PS5 Blanco", "model differs"),
    ("Mica Cristal Templado iPhone 13", "Funda Uso Rudo iPhone 13", "accessory type differs"),
    ("Redmi Note 13 Pro 5G 256GB", "Redmi Note 13 Pro+ 5G 256GB", "model differs"),
    ("Steam Deck OLED 512GB", "Steam Deck LCD 512GB", "model differs"),
    ("iPhone 13 128GB", "Samsung Galaxy S23 128GB", "brand differs (apple vs samsung)"),
    ("Caja Vacia iPhone 14 Pro Max", "iPhone 14 Pro Max 256GB", "only the box"),
    ("Juego Spider-Man 2 PS5 Fisico", "Consola PS5 Slim Digital 1TB", "accessory (software)"),
    ("Laptop Gamer ASUS RTX 4060 16GB RAM 512GB", "Tarjeta de video RTX 4060 8GB", "brand differs (asus vs nvidia)"),
    ("Samsung Galaxy A15 128GB", "Samsung Galaxy A15 5G 128GB", "model differs (galaxy a15 vs galaxy a15 5g)"),
    ("Taladro DeWalt DCD7781D2", "Taladro DeWalt DCD7781B solo herramienta", "model differs"),
    ("Tenis Nike Air Jordan 1 DZ5485-612 Talla 27 MX", "Tenis Nike Air Jordan 1 DZ5485-612 Talla 28 MX",
     "size differs"),
    ("PlayStation 5 825GB Lector de Discos", "Consola PS5 Slim 1TB con lector", "model differs"),
]


@pytest.mark.parametrize(("left", "right", "reason"), NEGATIVE_PAIRS, ids=[p[0][:40] for p in NEGATIVE_PAIRS])
def test_negative_pairs_do_not_match(left: str, right: str, reason: str) -> None:
    a, b = norm(left), norm(right)
    result = compare(a, b)
    assert result.method == "none", result
    assert result.confidence == 0.0
    assert result.product_key is None
    joined = " | ".join(result.reasons)
    if reason == "only the box":
        assert "accessory (empty_box)" in joined
    else:
        assert reason in joined, joined
    assert a.canonical_key is None or a.canonical_key != b.canonical_key
    assert compare(b, a).method == "none"


def test_pair_tables_are_large_enough() -> None:
    assert len(POSITIVE_PAIRS) >= 15
    assert len(NEGATIVE_PAIRS) >= 20


def test_same_gtin_with_conflicting_attributes_is_rejected() -> None:
    a = norm("iPhone 15 Pro 128GB", gtin=VALID_GTIN)
    b = norm("iPhone 15 Pro 256GB", gtin=VALID_GTIN)
    result = compare(a, b)
    assert result.method == "none"
    assert result.reasons[0].startswith(f"same GTIN {VALID_GTIN} but attributes conflict")
    assert "storage differs (128gb vs 256gb)" in result.reasons


def test_fuzzy_when_information_is_missing_on_one_side() -> None:
    a = norm("iPhone 15 Pro Max Titanio Natural")  # storage unknown
    b = norm("Apple iPhone 15 Pro Max 256GB Titanio Natural")
    assert a.canonical_key == "apple|iphone 15 pro max|storage:unknown"
    assert a.canonical_key != b.canonical_key
    result = compare(a, b)
    assert result.method == "fuzzy"
    assert 0 < result.confidence < 0.9  # always below the matcher's default threshold
    assert any("storage stated on one listing only (256gb)" in r for r in result.reasons)
    assert any(r.startswith("title similarity") for r in result.reasons)


def test_fuzzy_identity_one_sided_is_penalised() -> None:
    a = norm("PS5 Slim 1TB")
    b = norm("PS5 Slim Digital 1TB")
    result = compare(a, b)
    assert result.method == "fuzzy"
    assert result.confidence == pytest.approx(0.81)  # 100/100 * 0.9 * 0.9 (edition stated once)
    assert soft_differences(a, b) == ["edition stated on one listing only (digital)"]


def test_compare_without_model_never_matches() -> None:
    a = norm("AirPods Pro")
    b = norm("AirPods Pro")
    result = compare(a, b)
    assert result.method == "none"
    assert "exact model not identified on both listings" in result.reasons


def test_bundle_and_condition_are_reported_but_do_not_change_the_product() -> None:
    a = norm("PS5 Slim Digital 1TB Nueva", condition_raw="new")
    b = norm("PS5 Slim Digital 1TB + 2 juegos", condition_raw="used")
    result = compare(a, b)
    assert result.method == "canonical_key"
    assert any("bundle" in r for r in result.reasons)
    assert any("conditions differ (new vs used)" in r for r in result.reasons)


def test_hard_conflicts_lists_every_reason() -> None:
    a = norm("Apple iPhone 15 Pro Max 256GB")
    b = norm("Samsung Galaxy S24 Ultra 512GB")
    reasons = hard_conflicts(a, b)
    assert "brand differs (apple vs samsung)" in reasons
    assert "model differs (iphone 15 pro max vs galaxy s24 ultra)" in reasons
    assert "storage differs (256gb vs 512gb)" in reasons


def test_storage_formatting_in_reasons() -> None:
    a = norm("Xbox Series X 1TB")
    b = norm("Xbox Series X 2TB")
    assert "storage differs (1tb vs 2tb)" in compare(a, b).reasons


# ------------------------------------------------------------------------------------------------
# canonical_key
# ------------------------------------------------------------------------------------------------


def attrs(**kw) -> NormalizedAttributes:
    base = dict(normalized_title="x", brand="apple", model="iphone 15", category="phone", condition="new")
    base.update(kw)
    return NormalizedAttributes(**base)


def test_canonical_key_rules() -> None:
    assert canonical_key(attrs(storage_gb=128)) == "apple|iphone 15|128gb"
    assert canonical_key(attrs()) == "apple|iphone 15|storage:unknown"
    assert canonical_key(attrs(brand=None)) is None
    assert canonical_key(attrs(model=None)) is None
    assert canonical_key(attrs(brand="Apple ", model="iPhone 15", storage_gb=128)) == "apple|iphone 15|128gb"
    # identity values are sorted and storage-unknown goes last
    tablet = attrs(model="ipad air m2", category="tablet", variant_tokens=["wifi", "11in", "air"])
    assert canonical_key(tablet) == "apple|ipad air m2|11in,wifi|storage:unknown"
    # categories where storage does not change the price ignore it
    assert canonical_key(attrs(model="airpods pro 2", category="audio", storage_gb=None)) == "apple|airpods pro 2"
    # single-configuration models ignore the (often SD-card) storage mention
    switch = attrs(brand="nintendo", model="switch oled", category="console", storage_gb=128)
    assert canonical_key(switch) == "nintendo|switch oled"


def test_canonical_key_accessories() -> None:
    # accessory listing that only names the device it fits -> no key
    case = attrs(model="iphone 15 pro max", is_accessory=True, variant_tokens=["acc:case"])
    assert canonical_key(case) is None
    # accessory that is itself an identified product -> separate 'acc|' namespace
    pad = attrs(brand="sony", model="dualsense", category="console", is_accessory=True,
                variant_tokens=["acc:controller"])
    assert canonical_key(pad) == "acc|sony|dualsense"
    # a case FOR a DualSense is not a DualSense
    pad_case = replace(pad, variant_tokens=["acc:case"])
    assert canonical_key(pad_case) is None
    assert norm("Funda Silicon Para Control DualSense PS5").canonical_key is None


def test_canonical_key_is_deterministic_and_order_independent() -> None:
    a = attrs(model="apple watch series 9", category="smartwatch", variant_tokens=["gps", "45mm"])
    b = attrs(model="apple watch series 9", category="smartwatch", variant_tokens=["45mm", "gps"])
    assert canonical_key(a) == canonical_key(b) == "apple|apple watch series 9|45mm,gps"


# ------------------------------------------------------------------------------------------------
# ProductMatcher
# ------------------------------------------------------------------------------------------------


def candidate(key: str, title: str, *, gtin: str | None = None, **attributes) -> ProductCandidate:
    parts = key.split("|")
    body = parts[1:] if parts[0] == "acc" else parts
    return ProductCandidate(key=key, title=title, brand=body[0], model=body[1], gtin=gtin, attributes=attributes)


@pytest.fixture
def matcher() -> ProductMatcher:
    return ProductMatcher(
        [
            candidate("apple|iphone 15 pro max|256gb", "Apple iPhone 15 Pro Max 256GB", category="phone",
                      storage_gb=256),
            candidate("apple|iphone 15 pro max|512gb", "Apple iPhone 15 Pro Max 512GB", category="phone"),
            candidate("sony|ps5 slim|1024gb|digital", "PS5 Slim Digital 1TB", gtin=VALID_GTIN, category="console"),
            candidate("acc|sony|dualsense", "Control DualSense", category="console"),
        ]
    )


def test_matcher_gtin_match(matcher: ProductMatcher) -> None:
    a = norm("Consola PlayStation 5 Slim Edicion Digital 1 TB", gtin=VALID_GTIN)
    result = matcher.match(a, "Consola PlayStation 5 Slim Edicion Digital 1 TB")
    assert result == MatchResult(
        "sony|ps5 slim|1024gb|digital", 0.99, "gtin",
        [f"GTIN {VALID_GTIN} matches existing product 'sony|ps5 slim|1024gb|digital'"],
    )


def test_matcher_ignores_gtin_with_conflicting_attributes(matcher: ProductMatcher) -> None:
    # a seller put the console's GTIN on a disc-edition listing: never trust it blindly
    a = norm("PS5 Slim Estandar con lector 1TB", gtin=VALID_GTIN)
    result = matcher.match(a, "PS5 Slim Estandar con lector 1TB")
    assert result.method == "canonical_key"
    assert result.product_key == "sony|ps5 slim|1024gb|disc"
    assert "ignored" in result.reasons[0] and "edition differs (disc vs digital)" in result.reasons[0]


def test_matcher_existing_key(matcher: ProductMatcher) -> None:
    title = "iPhone 15 Pro Max 256gb Titanio Natural Reacondicionado"
    result = matcher.match(norm(title), title)
    assert result.product_key == "apple|iphone 15 pro max|256gb"
    assert result.method == "canonical_key"
    assert result.confidence == 0.95
    assert "matches an existing product" in result.reasons[-1]


def test_matcher_accessory_product(matcher: ProductMatcher) -> None:
    title = "Sony DualSense Control Inalambrico PS5"
    result = matcher.match(norm(title), title)
    assert result.product_key == "acc|sony|dualsense"


def test_matcher_new_cluster_suggestion(matcher: ProductMatcher) -> None:
    title = "iPhone 15 Pro Max 1TB"
    result = matcher.match(norm(title), title)
    assert result.method == "canonical_key"
    assert result.product_key == "apple|iphone 15 pro max|1024gb"
    assert "new product cluster 'apple|iphone 15 pro max|1024gb'" in result.reasons[-1]


def test_matcher_storage_unknown_does_not_merge_by_default(matcher: ProductMatcher) -> None:
    title = "Apple iPhone 15 Pro Max Titanio Natural"
    result = matcher.match(norm(title), title)
    assert result.product_key == "apple|iphone 15 pro max|storage:unknown"
    assert result.method == "canonical_key"
    assert any("not merged" in r for r in result.reasons)


def test_matcher_fuzzy_with_lower_threshold_picks_best_candidate() -> None:
    m = ProductMatcher(
        [
            candidate("apple|iphone 15 pro max|512gb", "iPhone 15 Pro Max 512GB Azul", category="phone"),
            candidate("apple|iphone 15 pro max|256gb", "Apple iPhone 15 Pro Max 256GB Titanio Natural",
                      category="phone"),
        ],
        fuzzy_threshold=0.6,
    )
    title = "Apple iPhone 15 Pro Max Titanio Natural"
    result = m.match(norm(title), title)
    assert result.method == "fuzzy"
    assert result.product_key == "apple|iphone 15 pro max|256gb"
    assert result.confidence >= 0.6
    assert any("uncertain: storage stated on one listing only" in r for r in result.reasons)


def test_matcher_fuzzy_never_crosses_hard_conflicts() -> None:
    m = ProductMatcher(
        [candidate("sony|ps5 slim|1024gb|disc", "PS5 Slim con lector 1TB", category="console")],
        fuzzy_threshold=0.01,
    )
    title = "PS5 Slim Edicion Digital"
    result = m.match(norm(title), title)
    assert result.product_key == "sony|ps5 slim|digital|storage:unknown"
    assert result.method == "canonical_key"


def test_matcher_fuzzy_only_same_brand_and_model() -> None:
    m = ProductMatcher(
        [candidate("apple|iphone 15 pro|256gb", "Apple iPhone 15 Pro Max 256GB Titanio", category="phone")],
        fuzzy_threshold=0.01,
    )
    title = "Apple iPhone 15 Pro Max Titanio"
    result = m.match(norm(title), title)
    assert result.product_key == "apple|iphone 15 pro max|storage:unknown"  # never the 15 Pro cluster


def test_matcher_unclusterable_listing(matcher: ProductMatcher) -> None:
    for title, reason in [
        ("Funda Silicon Para iPhone 15 Pro Max", "accessory (case) for iphone 15 pro max"),
        ("AirPods Pro", "exact model not identified"),
        ("Producto misterioso", "brand not recognised"),
    ]:
        result = matcher.match(norm(title), title)
        assert result == MatchResult(None, 0.0, "none", result.reasons)
        assert any(reason in r for r in result.reasons), result.reasons


def test_matcher_tie_break_is_deterministic() -> None:
    cands = [
        candidate("apple|iphone 15 pro max|256gb", "Apple iPhone 15 Pro Max", category="phone"),
        candidate("apple|iphone 15 pro max|128gb", "Apple iPhone 15 Pro Max", category="phone"),
    ]
    title = "Apple iPhone 15 Pro Max"
    results = {
        ProductMatcher(order, fuzzy_threshold=0.5).match(norm(title), title).product_key
        for order in (cands, list(reversed(cands)))
    }
    assert results == {"apple|iphone 15 pro max|128gb"}  # equal scores -> smallest key wins


def test_matcher_add_and_len() -> None:
    m = ProductMatcher()
    assert len(m) == 0
    c = candidate("apple|iphone 15|128gb", "iPhone 15 128GB", category="phone")
    m.add(c)
    m.add(candidate("apple|iphone 15|128gb", "Duplicate", category="phone"))
    assert len(m) == 1
    title = "Apple iPhone 15 128 GB"
    assert m.match(norm(title), title).product_key == "apple|iphone 15|128gb"


def test_matcher_candidate_without_attributes_uses_key() -> None:
    # Product rows may carry an empty attributes_json: storage/identity come from the key itself.
    m = ProductMatcher([ProductCandidate("apple|iphone 15 pro max|512gb", "iPhone 15 Pro Max", "apple",
                                         "iphone 15 pro max", None, {})], fuzzy_threshold=0.01)
    title = "iPhone 15 Pro Max 256GB"
    result = m.match(norm(title), title)
    assert result.product_key == "apple|iphone 15 pro max|256gb"  # not the 512gb cluster


@pytest.mark.parametrize("threshold", [0.0, -0.1, 1.5])
def test_matcher_rejects_invalid_threshold(threshold: float) -> None:
    with pytest.raises(ValueError):
        ProductMatcher(fuzzy_threshold=threshold)
