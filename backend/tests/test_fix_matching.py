"""Regression tests for verified normalize/matching defects (review findings matching#0 .. matching#11).

Each test reproduces a false product match (or a misread condition) that produced a fake arbitrage
opportunity, plus the legitimate titles that must keep clustering after the fix.
"""

from __future__ import annotations

import pytest

from flipradar.engine.types import NormalizedAttributes, RawListing
from flipradar.matching import ProductCandidate, ProductMatcher, canonical_key, compare
from flipradar.normalize import (
    analyze_listing,
    extract_ram_gb,
    extract_storage_gb,
    is_bundle,
    normalize_listing,
    normalize_text,
    parse_condition,
)

VALID_GTIN = "4006381333931"


def norm(title: str, **kw) -> NormalizedAttributes:
    return normalize_listing(RawListing(source_key="t", external_id="1", title=title, price_cents=1, **kw))


def key(title: str, **kw) -> str | None:
    return norm(title, **kw).canonical_key


# ------------------------------------------------------------------------------------------------
# matching#0 - accessories, parts and games named AFTER the device
# ------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "title",
    [
        "AirPods Pro 2 Estuche de carga usado",
        "Nintendo Switch 2 Funda Rigida",
        "iPhone 15 Funda",
        "Apple Watch Series 9 Cargador",
        "Pixel 8 Funda",
        "iPad 10 Teclado",
        "iPhone 15 Pro Max Pantalla Original",
        "GoPro Hero 12 Bateria",
        "DJI Mini 4 Pro Helices",
        "Apple Watch Series 9 45mm Correa",
        "Nintendo Switch Oled Estuche",
        "Samsung Galaxy S24 Ultra Pantalla",
        "PS5 Slim Control",
        "PS5 Control Dualsense",
        "PS5 Dualsense Blanco",
    ],
)
def test_accessory_named_after_the_device_is_an_accessory(title: str) -> None:
    attrs = norm(title)
    assert attrs.is_accessory is True, title
    assert attrs.canonical_key is None, title


@pytest.mark.parametrize("title", ["Nintendo Switch Mario Kart 8 Deluxe", "PS5 Spider-Man 2", "PS5 FC 25"])
def test_game_named_after_the_console_is_not_clustered(title: str) -> None:
    attrs, notes = analyze_listing(RawListing(source_key="t", external_id="1", title=title, price_cents=1))
    assert attrs.canonical_key is None
    assert any("possibly a game or accessory" in n for n in notes), notes


@pytest.mark.parametrize(
    ("title", "expected_key"),
    [
        ("iPhone 13 128gb bateria 85%", "apple|iphone 13|128gb"),
        ("Nintendo Switch Oled Con Estuche y Mica", "nintendo|switch oled"),
        ("AirPods Pro (2nd generation) MagSafe", "apple|airpods pro 2"),
        ("Nintendo Switch Lite Turquesa", "nintendo|switch lite"),
        ("Nintendo Switch Lite Azul Seminueva Excelente Estado", "nintendo|switch lite"),
        ("Consola PS5 Slim Digital 1TB Dualsense Blanco", "sony|ps5 slim|1024gb|digital"),
        ("Apple Watch Series 9 45mm GPS Medianoche", "apple|apple watch series 9|45mm,gps"),
        ("iPhone 15 Pro Max 256gb con funda de regalo", "apple|iphone 15 pro max|256gb"),
        ("Canon EOS R50 Lente 18-45mm", "canon|canon eos r50|lens_kit"),
    ],
)
def test_device_listings_describing_their_parts_stay_devices(title: str, expected_key: str) -> None:
    attrs = norm(title)
    assert attrs.is_accessory is False
    assert attrs.canonical_key == expected_key


def test_charging_case_never_shares_the_airpods_cluster() -> None:
    device = norm("AirPods Pro 2 usados")
    case = norm("AirPods Pro 2 Estuche de carga usado")
    assert device.canonical_key == "apple|airpods pro 2"
    result = compare(device, case)
    assert result.method == "none"
    assert any("accessory" in r for r in result.reasons)


def test_quantity_before_a_plural_accessory_still_means_included() -> None:
    attrs = norm("PS5 Slim Digital 1TB 2 controles")
    assert attrs.is_accessory is False
    assert attrs.is_bundle is True


# ------------------------------------------------------------------------------------------------
# matching#1 - broken / repair-needed / carrier-locked devices, schema.org DamagedCondition
# ------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("condition_raw", "title"),
    [
        ("used", "iPhone 14 Pro 256gb usado para reparar no sirve face id"),
        ("used", "iPhone 14 Pro 256gb a reparar"),
        ("used", "iPhone 14 Pro 256gb no sirve"),
        ("used", "iPhone 14 Pro 256gb con falla de señal"),
        ("used", "iPhone 14 Pro 256gb no carga"),
        ("used", "iPhone 14 Pro 256gb mojado"),
        ("used", "iPhone 14 Pro 256gb cristal roto"),
        ("used", "iPhone 14 Pro 256gb tapa rota"),
        ("used", "iPhone 14 Pro 256gb bateria inflada"),
        ("used", "iPhone 14 Pro 256gb no da imagen"),
        ("used", "iPhone 14 Pro 256gb en partes"),
        ("used", "iPhone 14 Pro 256gb sin face id"),
        ("3000", "Apple iPhone 14 Pro 128GB Verizon Locked"),
        ("3000", "Apple iPhone 14 Pro 128GB Carrier Locked"),
        ("3000", "Apple iPhone 14 Pro 128GB AT&T Locked"),
        ("3000", "Apple iPhone 14 Pro 128GB Blacklisted bad ESN"),
        ("3000", "Apple iPhone 14 Pro 128GB cracked screen"),
        ("damaged", "iPhone 14 Pro 256gb"),
        ("damaged", "iPhone 14 Pro 256gb usado"),
    ],
)
def test_broken_or_locked_devices_are_for_parts(condition_raw: str, title: str) -> None:
    assert parse_condition(condition_raw, title) == "for_parts"


@pytest.mark.parametrize(
    ("condition_raw", "title", "expected"),
    [
        ("used", "iPhone 14 Pro 256gb sin fallas", "used"),
        ("used", "iPhone 14 Pro 256gb no esta mojado", "used"),
        ("used", "iPhone 14 Pro 256gb nunca mojado", "used"),
        ("3000", "Apple iPhone 14 Pro 128GB Factory Unlocked", "used"),
        ("3000", "Apple iPhone 14 Pro 128GB not locked", "used"),
        ("used", "iPhone 14 Pro 256gb face id funcionando", "used"),
    ],
)
def test_negated_defects_are_not_for_parts(condition_raw: str, title: str, expected: str) -> None:
    assert parse_condition(condition_raw, title) == expected


def test_description_repair_cue_marks_for_parts() -> None:
    assert parse_condition("used", "iPhone 14 Pro 256gb", "Se vende para reparar, bateria inflada") == "for_parts"


# ------------------------------------------------------------------------------------------------
# matching#2 - negated variant words
# ------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("title", "expected_key"),
    [
        ("Nintendo Switch V2 Neon usado (no es OLED)", "nintendo|switch"),
        ("Nintendo Switch normal (no lite)", "nintendo|switch"),
        ("Nintendo Switch OLED usado", "nintendo|switch oled"),
        ("Nintendo Switch OLED, no lite", "nintendo|switch oled"),
        ("Steam Deck 512gb no oled", None),  # LCD or OLED is then still unknown for 512GB
        ("Steam Deck LCD 512gb no OLED", "valve|steam deck lcd|512gb"),
        ("PS5 Fat 825gb lector de discos no slim", "sony|ps5|825gb|disc"),
        ("PS5 no pro 825gb con lector", "sony|ps5|825gb|disc"),
        ("AirPods 4 sin cancelacion de ruido", "apple|airpods 4"),
    ],
)
def test_negated_variant_words_do_not_promote_the_model(title: str, expected_key: str | None) -> None:
    assert key(title) == expected_key


def test_negated_oled_switch_never_joins_the_oled_cluster() -> None:
    a, b = norm("Nintendo Switch OLED usado"), norm("Nintendo Switch V2 Neon usado (no es OLED)")
    assert compare(a, b).method == "none"


# ------------------------------------------------------------------------------------------------
# matching#3 - model suffix not adjacent / abbreviated / compound
# ------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("title", "expected_key"),
    [
        ("iPhone 15 PM 256gb", "apple|iphone 15 pro max|256gb"),
        ("iPhone 15 256gb Pro Max", None),
        ("iPhone 15 128gb Pro", None),
        ("Samsung Galaxy S24 256gb Ultra", None),
        ("Samsung Galaxy Tab S9 FE+ 128gb Wifi usado", "samsung|galaxy tab s9 fe+|128gb|wifi"),
        ("Samsung Galaxy Tab S9 FE Plus 128gb Wifi", "samsung|galaxy tab s9 fe+|128gb|wifi"),
        ("Samsung Galaxy Tab S9 FE 128gb Wifi", "samsung|galaxy tab s9 fe|128gb|wifi"),
        ("Tarjeta de video RTX 4070 12gb Super", None),
        ("OnePlus Nord CE4 Lite 5G 256gb", "oneplus|oneplus nord ce 4 lite 5g|256gb"),
        ("Redmi Note 13 Pro 8gb Ram 256gb Rom 5G", "xiaomi|redmi note 13 pro 5g|256gb"),
        ("Galaxy A15 4gb Ram 128gb 5G", "samsung|galaxy a15 5g|128gb"),
        # unchanged: suffix right after the number, or inside an included item
        ("Samsung Galaxy S24 Ultra 12gb 512gb Titanium Gray", "samsung|galaxy s24 ultra|512gb"),
        ("iPhone 15 Pro Max 256gb Titanio Natural", "apple|iphone 15 pro max|256gb"),
        ("Tarjeta de Video MSI GeForce RTX 4070 Ti Super 16GB", "nvidia|rtx 4070 ti super|vram16gb"),
    ],
)
def test_unconsumed_model_suffix_is_ambiguous(title: str, expected_key: str | None) -> None:
    assert key(title) == expected_key


def test_fe_plus_tab_never_joins_the_flagship_cluster() -> None:
    a = norm("Samsung Galaxy Tab S9 128gb Wifi usado")
    b = norm("Samsung Galaxy Tab S9 FE+ 128gb Wifi usado")
    assert compare(a, b).method == "none"


# ------------------------------------------------------------------------------------------------
# matching#4 - screen sizes after the chip / as bare decimals; multi-size lines need a size
# ------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("title", "expected_key"),
    [
        ("iPad Pro M4 11 256gb Wifi usado", "apple|ipad pro m4|256gb|11in,wifi"),
        ("iPad Pro M4 13 256gb Wifi usado", "apple|ipad pro m4|256gb|13in,wifi"),
        ("iPad Air M2 11 128gb wifi", "apple|ipad air m2|128gb|11in,wifi"),
        ("iPad Air M2 13 128gb wifi", "apple|ipad air m2|128gb|13in,wifi"),
        ("MacBook Air 15.3 M2 8gb 256gb", "apple|macbook air m2|256gb|15in,ram8gb"),
        ("MacBook Air 13.6 M2 8gb 256gb", "apple|macbook air m2|256gb|13in,ram8gb"),
        # multi-size lines without a stated size: never clustered
        ("iPad Pro M4 256gb Wifi", None),
        ("MacBook Air M2 8gb 256gb", None),
        ("MacBook Pro M3 Pro 18gb 512gb", None),
        # single-size lines keep clustering without a size
        ("MacBook Air M1 8gb 256gb", "apple|macbook air m1|256gb|ram8gb"),
        ("iPad 10ma Generacion 64GB WiFi", "apple|ipad 10|64gb|wifi"),
    ],
)
def test_screen_size_is_an_identity_for_multi_size_lines(title: str, expected_key: str | None) -> None:
    assert key(title) == expected_key


# ------------------------------------------------------------------------------------------------
# matching#5 - "X GB RAM YGB": the storage after the word "ram" is storage
# ------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("title", "category", "storage", "ram"),
    [
        ("iPhone 11 4gb Ram 128gb usado", "phone", 128, 4),
        ("iPhone 11 4gb Ram 64gb usado", "phone", 64, 4),
        ("iPhone 11 64gb Ram 4gb", "phone", 64, 4),
        ("Galaxy A15 4GB RAM 128GB", "phone", 128, 4),
        ("Laptop 16gb ram 512gb ssd", "laptop", 512, 16),
        ("Celular Xiaomi ram 8gb 256gb", "phone", 256, 8),
    ],
)
def test_ram_word_between_two_sizes(title: str, category: str, storage: int, ram: int) -> None:
    text = normalize_text(title)
    assert extract_storage_gb(text, category) == storage
    assert extract_ram_gb(text, category) == ram


def test_64gb_iphone_never_joins_the_128gb_cluster() -> None:
    a, b = norm("iPhone 11 4gb Ram 128gb usado"), norm("iPhone 11 4gb Ram 64gb usado")
    assert a.canonical_key == "apple|iphone 11|128gb"
    assert b.canonical_key == "apple|iphone 11|64gb"


# ------------------------------------------------------------------------------------------------
# matching#6 - storage-unknown / edition-less keys are not price clusters; GTIN prefers specific
# ------------------------------------------------------------------------------------------------


def test_storage_unknown_and_edition_less_listings_are_not_clustered() -> None:
    attrs, notes = analyze_listing(RawListing(source_key="t", external_id="1", title="Galaxy S23 usado",
                                              price_cents=1))
    assert attrs.model == "galaxy s23"
    assert attrs.canonical_key is None
    assert any("storage" in n for n in notes), notes
    attrs, notes = analyze_listing(RawListing(source_key="t", external_id="1", title="PS5 Slim 1TB usado",
                                              price_cents=1))
    assert attrs.canonical_key is None
    assert any("edition" in n for n in notes), notes
    # fixed-storage models and categories without storage keep their keys
    assert key("Nintendo Switch OLED usado") == "nintendo|switch oled"
    assert key("Sony PlayStation 5 Pro 2TB") == "sony|ps5 pro"
    assert key("Apple AirPods Pro 2da Generacion") == "apple|airpods pro 2"


def test_gtin_never_pulls_a_specific_listing_into_a_less_specific_cluster() -> None:
    m = ProductMatcher()
    first = norm("iPhone 15 Pro usado", gtin=VALID_GTIN)
    assert first.canonical_key is None
    assert m.match(first, "iPhone 15 Pro usado").product_key is None  # no storage-unknown cluster is created
    # a legacy storage-unknown cluster (created before the fix) is no longer a match target
    m.add(ProductCandidate("apple|iphone 15 pro|storage:unknown", "iPhone 15 Pro usado", "apple", "iphone 15 pro",
                           VALID_GTIN, {"category": "phone"}))
    m.add(ProductCandidate("apple|iphone 15 pro|256gb", "iPhone 15 Pro 256gb usado", "apple", "iphone 15 pro",
                           None, {"category": "phone", "storage_gb": 256}))
    second = norm("iPhone 15 Pro 256gb usado", gtin=VALID_GTIN)
    result = m.match(second, "iPhone 15 Pro 256gb usado")
    assert result.product_key == "apple|iphone 15 pro|256gb"
    # a less specific GTIN cluster (edition not part of the legacy key) is not preferred either
    m2 = ProductMatcher([
        ProductCandidate("apple|apple watch series 9", "Apple Watch Series 9", "apple", "apple watch series 9",
                         VALID_GTIN, {"category": "smartwatch"}),
        ProductCandidate("apple|apple watch series 9|45mm", "Apple Watch Series 9 45mm", "apple",
                         "apple watch series 9", None, {"category": "smartwatch", "variant_tokens": ["45mm"]}),
    ])
    watch = norm("Apple Watch Series 9 45mm usado", gtin=VALID_GTIN)
    assert m2.match(watch, "Apple Watch Series 9 45mm usado").product_key == "apple|apple watch series 9|45mm"


def test_gtin_still_matches_a_complete_cluster() -> None:
    m = ProductMatcher([ProductCandidate("apple|iphone 15 pro|256gb", "iPhone 15 Pro 256gb", "apple",
                                         "iphone 15 pro", VALID_GTIN, {"category": "phone", "storage_gb": 256})])
    attrs = norm("iPhone 15 Pro usado", gtin=VALID_GTIN)
    result = m.match(attrs, "iPhone 15 Pro usado")
    assert (result.product_key, result.method) == ("apple|iphone 15 pro|256gb", "gtin")


def test_legacy_edition_less_cluster_is_not_a_match_target() -> None:
    m = ProductMatcher([ProductCandidate("sony|ps5 slim|1024gb", "PS5 Slim 1TB", "sony", "ps5 slim", VALID_GTIN,
                                         {"category": "console", "storage_gb": 1024})])
    attrs = norm("PS5 Slim Digital 1TB", gtin=VALID_GTIN)
    result = m.match(attrs, "PS5 Slim Digital 1TB")
    assert result.product_key == "sony|ps5 slim|1024gb|digital"
    assert result.method == "canonical_key"


# ------------------------------------------------------------------------------------------------
# matching#7 - bundle and multi-unit phrasings
# ------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "title",
    [
        "PS5 Slim Digital 1TB con PS VR2 usado",
        "PS5 Slim Digital 1TB con PSVR2 y Horizon",
        "PS5 Slim Digital 1TB con control extra",
        "2 PS5 Slim Digital 1TB usados",
        "Dos iPhone 13 128gb",
        "2 iPhone 15 Pro 256gb",
        "iPhone 15 Pro 256gb x2",
        "iPhone 15 Pro 256gb x 2",
        "iPhone 15 Pro 256gb (2 piezas)",
        "Par de AirPods Pro 2",
        "iPhone 15 Pro 256gb con Apple Watch",
        "iPhone 15 Pro 256gb mas AirPods",
        "PS5 Slim Digital 1TB y FC 25",
        "PS5 Slim Digital 1TB con Spiderman 2",
        "PS5 Pro 2tb con lector",
    ],
)
def test_bundle_phrasings_are_bundles(title: str) -> None:
    assert norm(title).is_bundle is True, title


@pytest.mark.parametrize(
    "title",
    [
        "iPhone 15 Pro 256gb con funda",
        "iPhone 15 Pro 256gb con cargador y funda",
        "PS5 Slim 1TB con control",
        "Xbox Series X 1TB con control",
        "Nintendo Switch OLED con estuche y mica",
        "Xiaomi Poco X6 Pro 256gb",
        "Honor X8 128gb",
        "Xbox Series X 2TB",
        "Joy-Con Nintendo Switch Par Neon",
        "PS5 Slim Digital 1TB usado",
        "LEGO Star Wars Millennium Falcon 75192 7541 piezas",
        "iPhone 13 128gb 1 año de uso",
        "PS5 Pro 2tb sin lector",
    ],
)
def test_single_items_are_not_bundles(title: str) -> None:
    assert norm(title).is_bundle is False, title


# ------------------------------------------------------------------------------------------------
# matching#8 - source MODEL field fallback
# ------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("title", "brand", "model"),
    [
        ("Magsafe Transparente Antigolpes Uso Rudo", "Generica", "iPhone 15 Pro Max"),
        ("Cubo Carga Rapida 20w Original", "Apple", "iPhone 15"),
        ("Consola Portatil Retro 64gb Juegos", "Generica", "Nintendo Switch Lite"),
        ("Estacion De Carga Doble Leds", "Sony", "PS5 Slim"),
    ],
)
def test_source_model_is_not_adopted_without_device_evidence(title: str, brand: str, model: str) -> None:
    attrs = norm(title, brand=brand, model=model)
    assert attrs.canonical_key is None
    assert attrs.model is None


def test_source_model_still_fills_a_device_title() -> None:
    attrs = norm("Celular Apple Titanio Natural 256GB", model="iPhone 15 Pro Max")
    assert attrs.canonical_key == "apple|iphone 15 pro max|256gb"
    attrs = norm("Consola Portatil Nintendo Blanca", brand="Nintendo", model="Nintendo Switch Lite")
    assert attrs.canonical_key == "nintendo|switch lite"


# ------------------------------------------------------------------------------------------------
# matching#9 - Apple Watch case material
# ------------------------------------------------------------------------------------------------


def test_apple_watch_case_material_is_an_identity() -> None:
    steel = norm("Apple Watch Series 9 45mm Acero Inoxidable usado")
    alu = norm("Apple Watch Series 9 45mm Aluminio usado")
    plain = norm("Apple Watch Series 9 45mm usado")
    assert steel.canonical_key == "apple|apple watch series 9|45mm,steel"
    assert alu.canonical_key == plain.canonical_key == "apple|apple watch series 9|45mm"
    assert compare(steel, alu).method == "none"
    assert any("material differs" in r for r in compare(steel, alu).reasons) or compare(steel, alu).reasons
    assert key("Apple Watch Series 10 46mm Titanio Natural") == "apple|apple watch series 10|46mm,titanium"
    assert key("Apple Watch Series 9 45mm Hermes") == "apple|apple watch series 9|45mm,hermes"


def test_steel_band_is_not_a_steel_case() -> None:
    assert key("Apple Watch Series 9 45mm con correa de acero milanesa") == "apple|apple watch series 9|45mm"
    # both a premium and the aluminium case material stated: ambiguous, never clustered
    assert key("Apple Watch Series 9 45mm aluminio acero inoxidable") is None
    # Ultra is titanium only: material is not a separate dimension there
    assert key("Apple Watch Ultra 2 49mm Titanio Natural") == "apple|apple watch ultra 2|49mm"


# ------------------------------------------------------------------------------------------------
# matching#10 - laptop RAM above 32 GB
# ------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("title", "storage", "ram", "expected_key"),
    [
        ("MacBook Pro 14 M3 Max 36gb 1tb", 1024, 36, "apple|macbook pro m3 max|1024gb|14in,ram36gb"),
        ("MacBook Pro 14 M3 Max 48gb 1tb", 1024, 48, "apple|macbook pro m3 max|1024gb|14in,ram48gb"),
        ("MacBook Pro 14 M4 Pro 48gb 512gb", 512, 48, "apple|macbook pro m4 pro|512gb|14in,ram48gb"),
        ("MacBook Pro 16 M3 Max 64gb 2tb", 2048, 64, "apple|macbook pro m3 max|2048gb|16in,ram64gb"),
        ("MacBook Pro 14 M4 Pro 24gb 512gb", 512, 24, "apple|macbook pro m4 pro|512gb|14in,ram24gb"),
    ],
)
def test_laptop_ram_above_32gb(title: str, storage: int, ram: int, expected_key: str) -> None:
    attrs = norm(title)
    assert (attrs.storage_gb, attrs.ram_gb) == (storage, ram)
    assert attrs.canonical_key == expected_key


def test_laptop_dual_drive_is_not_read_as_ram() -> None:
    text = normalize_text("Laptop HP 128gb ssd 1tb hdd")
    assert extract_ram_gb(text, "laptop") is None


# ------------------------------------------------------------------------------------------------
# matching#11 - condition misreads
# ------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("iPhone 14 Pro 256gb nuevo caja dañada", "new"),
        ("iPhone 14 Pro 256gb nuevo empaque dañado", "new"),
        ("iPhone 14 Pro 256gb nuevo caja un poco dañada", "new"),
        ("iPhone 14 Pro 256gb Monterrey Nuevo Leon", "unknown"),
        ("iPhone 14 Pro 256gb Nuevo Laredo", "unknown"),
        ("iPhone 14 Pro 256gb nuevo abierto", "used"),
        ("iPhone 14 Pro 256gb nuevo, solo abierto", "used"),
        ("iPhone 14 Pro 256gb nuevo sin caja", "used"),
        ("iPhone 14 Pro 256gb nuevo sin sellos", "used"),
        ("iPhone 14 Pro 256gb nuevo abierto a ofertas", "new"),
        ("iPhone 14 Pro 256gb pantalla dañada", "for_parts"),
        ("PS5 dañado de la tarjeta madre", "for_parts"),
    ],
)
def test_condition_misreads(title: str, expected: str) -> None:
    assert parse_condition(None, title) == expected


def test_canonical_key_matches_analyze_listing() -> None:
    for title in ("iPhone 15 Pro 256gb", "Galaxy S23 usado", "PS5 Slim Digital 1TB", "iPad Pro M4 256gb"):
        attrs = norm(title)
        assert canonical_key(attrs) == attrs.canonical_key
