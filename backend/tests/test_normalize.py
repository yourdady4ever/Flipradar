"""Tests for flipradar.normalize (text normalization, GTIN, condition, attribute extraction, keys).

Titles are modelled on real Mexican marketplace phrasing (Mercado Libre / Amazon MX / Facebook
Marketplace style).  The expected values encode the *precision-first* policy: when the title does not
pin down the exact sellable product, model/key must be None.
"""

from __future__ import annotations

import pytest

from flipradar.engine.types import NormalizedAttributes, RawListing
from flipradar.matching.matcher import canonical_key
from flipradar.models import Condition
from flipradar.normalize import (
    analyze_listing,
    extract_color,
    extract_gtin,
    extract_ram_gb,
    extract_storage_gb,
    extract_variant_tokens,
    is_accessory,
    is_bundle,
    normalize_gtin,
    normalize_listing,
    normalize_text,
    parse_condition,
    validate_gtin,
)
from flipradar.normalize import vocab as V


def listing(title: str, **kw) -> RawListing:
    return RawListing(source_key="test", external_id="1", title=title, price_cents=100_00, **kw)


def norm(title: str, **kw) -> NormalizedAttributes:
    return normalize_listing(listing(title, **kw))


# ------------------------------------------------------------------------------------------------
# normalize_text
# ------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Apple iPhone 15 Pro Max (256 GB) - Titanio Natural", "apple iphone 15 pro max 256gb titanio natural"),
        ("Edición Digital  Consola", "edicion digital consola"),
        ("Diseño Español Ñandú", "diseno espanol nandu"),
        ("128 GB", "128gb"),
        ("128gb", "128gb"),
        ("128 g b", "128gb"),
        ("128 Gigas", "128gb"),
        ("1 TB", "1tb"),
        ("1tb", "1tb"),
        ("1 Tera", "1tb"),
        ("2 Teras", "2tb"),
        ('MacBook 13.3" ', "macbook 13.3in"),
        ("Pantalla 13,3 pulgadas", "pantalla 13.3in"),
        ("Pantalla de 6.1 Pulgadas", "pantalla de 6.1in"),
        ("Correa 45 mm", "correa 45mm"),
        ("Play 5 Slim", "ps5 slim"),
        ("PlayStation 5", "ps5"),
        ("Play Station 4 Pro", "ps4 pro"),
        ("iPhone15ProMax", "iphone 15 pro max"),
        ("IPHONE 13 PROMAX", "iphone 13 pro max"),
        ("Galaxy S24Ultra", "galaxy s24 ultra"),
        ("Xiaomi 8/256GB", "xiaomi 8/256gb"),
        ("Galaxy S24+ 256GB", "galaxy s24+ 256gb"),
        ("PS5 + 2 controles", "ps5 + 2 controles"),
        ("Wi-Fi + Cellular", "wifi + cellular"),
        ("Dual Sense", "dualsense"),
        ("   muchos     espacios   ", "muchos espacios"),
        ("¡¡OFERTA!! Envío gratis!!!", "oferta envio gratis"),
        ("", ""),
    ],
)
def test_normalize_text(raw: str, expected: str) -> None:
    assert normalize_text(raw) == expected


def test_normalize_text_is_idempotent_and_deterministic() -> None:
    raw = "Consola Sony PlayStation 5 Slim Edición Digital 1 TB (Nueva)"
    once = normalize_text(raw)
    assert normalize_text(once) == once
    assert all(normalize_text(raw) == once for _ in range(5))


# ------------------------------------------------------------------------------------------------
# GTIN
# ------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "code",
    [
        "4006381333931",  # EAN-13 (GS1 reference example)
        "036000291452",  # UPC-A (GS1 reference example)
        "73513537",  # EAN-8
        "00012345600012",  # GTIN-14
        "9780306406157",  # ISBN-13 / Bookland EAN
    ],
)
def test_validate_gtin_accepts_valid_codes(code: str) -> None:
    assert validate_gtin(code)


@pytest.mark.parametrize(
    "code",
    [
        "4006381333932",  # wrong check digit
        "036000291453",
        "73513536",
        "123456789",  # length 9 is not a GTIN
        "40063813339",  # length 11
        "4006381333931X",
        "4006-381333931",
        "",
        "abcdefghijklm",
        "٤٠٠٦٣٨١٣٣٣٩٣١",  # non-ASCII digits must be refused
    ],
)
def test_validate_gtin_rejects_invalid_codes(code: str) -> None:
    assert not validate_gtin(code)


def test_validate_gtin_rejects_non_string() -> None:
    assert not validate_gtin(4006381333931)  # type: ignore[arg-type]


def test_normalize_gtin_canonical_forms() -> None:
    # UPC-A and its zero-padded 13/14 digit forms are the same item.
    assert normalize_gtin("036000291452") == "0036000291452"
    assert normalize_gtin("0036000291452") == "0036000291452"
    assert normalize_gtin("00036000291452") == "0036000291452"
    assert normalize_gtin(" 4006381333931 ") == "4006381333931"
    assert normalize_gtin("4006-3813-33931") == "4006381333931"
    assert normalize_gtin("73513537") == "73513537"


def test_normalize_gtin_rejects_junk_and_restricted() -> None:
    assert normalize_gtin(None) is None
    assert normalize_gtin("0000000000000") is None  # all zeros passes the checksum but is junk
    assert normalize_gtin("2000000000008") is None  # GS1 prefix 2: in-store / restricted circulation
    assert normalize_gtin("4006381333932") is None


def test_extract_gtin_prefers_source_then_title() -> None:
    assert extract_gtin("Bocina 4006381333931", "036000291452") == "0036000291452"
    assert extract_gtin("Bocina JBL EAN 4006381333931 nueva") == "4006381333931"
    assert extract_gtin("Bocina JBL 4006381333932") is None  # bad check digit is never used
    assert extract_gtin("Contacto 5512345678") is None  # 10-digit phone number is not a GTIN length
    assert extract_gtin("sin codigo", "basura", None) is None


def test_listing_gtin_from_attributes() -> None:
    attrs = norm("Bocina JBL Flip 6 Negra", attributes={"GTIN": "4006381333931"})
    assert attrs.gtin == "4006381333931"


# ------------------------------------------------------------------------------------------------
# Condition
# ------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("condition_raw", "title", "description", "expected"),
    [
        # source vocabulary (Mercado Libre)
        ("new", "iPhone 15 128GB", None, "new"),
        ("used", "iPhone 15 128GB", None, "used"),
        ("not_specified", "iPhone 15 128GB Nuevo Sellado", None, "new"),
        ("not_specified", "iPhone 15 128GB", None, "unknown"),
        # eBay labels and condition ids
        ("New", "Nintendo Switch OLED", None, "new"),
        ("Open box", "Nintendo Switch OLED", None, "used"),
        ("Certified - Refurbished", "iPhone 14", None, "refurbished"),
        ("Excellent - Refurbished", "iPhone 14", None, "refurbished"),
        ("Used", "iPhone 14", None, "used"),
        ("For parts or not working", "iPhone 14", None, "for_parts"),
        ("1000", "iPhone 14", None, "new"),
        ("1500", "iPhone 14", None, "used"),
        ("2010", "iPhone 14", None, "refurbished"),
        ("3000", "iPhone 14", None, "used"),
        ("7000", "iPhone 14", None, "for_parts"),
        # Spanish source strings
        ("Nuevo", "Xbox Series S", None, "new"),
        ("Usado", "Xbox Series S", None, "used"),
        ("Reacondicionado", "Xbox Series S", None, "refurbished"),
        # for-parts cues override everything
        ("new", "iPhone 14 Pro para piezas no enciende", None, "for_parts"),
        ("used", "iPhone 12 iCloud bloqueado", None, "for_parts"),
        ("new", "iPhone 13 128GB", "Equipo no enciende, se vende para refacciones", "for_parts"),
        (None, "Samsung A54 pantalla rota", None, "for_parts"),
        (None, "PS5 dañado de la tarjeta madre", None, "for_parts"),
        # source "new" is downgraded by an explicit refurbished/used title (conservative)
        ("new", "iPhone 15 Pro Max 256gb Reacondicionado", None, "refurbished"),
        ("new", "iPhone 15 Pro Max 256gb Seminuevo", None, "used"),
        # title cues only
        (None, "iPhone 15 Pro Max 256gb Titanio Natural Desbloqueado Reacondicionado", None, "refurbished"),
        (None, "iPhone 13 128gb Usado Bateria 85%", None, "used"),
        (None, "iPhone 13 Como Nuevo 128gb", None, "used"),
        (None, "PS5 Slim Digital Caja Abierta", None, "used"),
        (None, "Consola en exhibicion PS5", None, "used"),
        (None, "iPhone 15 Nuevo en caja", None, "new"),
        (None, "Galaxy S24 Sellado", None, "new"),
        (None, "Galaxy S24 brand new sealed", None, "new"),
        (None, "Galaxy S24 Renewed", None, "refurbished"),
        (None, "Galaxy S24 256gb", None, "unknown"),
        # negations
        (None, "iPhone 13 IMEI no reportado 128gb usado", None, "used"),
        (None, "iPhone 13 nunca usado sellado", None, "new"),
        (None, "PS5 no esta danado, solo detalles", None, "unknown"),  # negated: not for_parts
        ("used", "iPhone 13 sin daños", "Si no enciende te devolvemos tu dinero", "used"),
        # "New Balance" is a brand, not a condition
        (None, "Tenis New Balance 550", None, "unknown"),
    ],
)
def test_parse_condition(condition_raw, title, description, expected) -> None:
    assert parse_condition(condition_raw, title, description) == expected


def test_condition_values_are_model_enum_values() -> None:
    allowed = {c.value for c in Condition}
    for title in ("iPhone nuevo", "iPhone usado", "iPhone reacondicionado", "iPhone para piezas", "iPhone"):
        assert parse_condition(None, title) in allowed


# ------------------------------------------------------------------------------------------------
# Storage / RAM / color / variants / bundle / accessory helpers
# ------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("title", "category", "storage", "ram"),
    [
        ("Celular Xiaomi 8GB RAM 256GB", "phone", 256, 8),
        ("Redmi Note 13 8+256GB", "phone", 256, 8),
        ("Redmi Note 13 8/256GB", "phone", 256, 8),
        ("Redmi Note 13 8GB/256GB", "phone", 256, 8),
        ("Galaxy A15 128gb 4gb ram", "phone", 128, 4),
        ("Moto G54 8gb 256gb", "phone", 256, 8),  # two-value heuristic for phones
        ("ram de 12gb y 512gb", "phone", 512, 12),
        ("Galaxy S24+ 256GB", "phone", 256, None),  # "+" is the model, not RAM
        ("iPhone 15 Pro 1 TB", "phone", 1024, None),
        ("iPhone 15 Pro 1 Tera", "phone", 1024, None),
        ("iPhone 15 128gb 256gb 512gb", "phone", None, None),  # multi-variant listing: ambiguous
        ("Nintendo Switch OLED 64GB + Micro SD 128GB", "console", 64, None),
        ("Moto G 128gb expandible hasta 1tb", "phone", 128, None),
        ("MacBook Air 8GB RAM 256GB SSD", "laptop", 256, 8),
        ("Laptop 16GB 512GB SSD 1TB HDD", "laptop", None, 16),
        ("Laptop 16gb 512gb ssd", "laptop", 512, 16),
        ("iPhone 13", "phone", None, None),
        ("Moto G 4gb", "phone", None, None),  # 4gb alone is not a phone storage size
        ("RTX 4070 12GB", "gpu", None, None),  # GPU memory is VRAM, never storage
    ],
)
def test_storage_and_ram(title: str, category: str, storage, ram) -> None:
    text = normalize_text(title)
    assert extract_storage_gb(text, category) == storage
    assert extract_ram_gb(text, category) == ram


@pytest.mark.parametrize(
    ("title", "color"),
    [
        ("iPhone 15 Pro Titanio Natural", "natural titanium"),
        ("Galaxy S24 Ultra Titanium Gray", "gray titanium"),
        ("MacBook Air Gris Espacial", "space gray"),
        ("Apple Watch Medianoche", "midnight"),
        ("Switch Lite Turquesa", "turquoise"),
        ("PS5 Blanco", "white"),
        ("iPhone 13 Negro", "black"),
        ("Redmi Note 13 sin color", None),
    ],
)
def test_extract_color(title: str, color) -> None:
    assert extract_color(normalize_text(title)) == color


def test_variant_tokens() -> None:
    assert extract_variant_tokens(normalize_text("PS5 Slim Edicion Digital"), "console") == ["digital", "slim"]
    assert extract_variant_tokens(normalize_text("PS5 sin lector de discos"), "console") == ["digital"]
    assert extract_variant_tokens(normalize_text("PS5 con lector de discos"), "console") == ["disc"]
    assert "cellular" in extract_variant_tokens(normalize_text("iPad Air Wi-Fi + Cellular"), "tablet")
    assert "45mm" in extract_variant_tokens(normalize_text("Apple Watch 45 mm GPS"), "smartwatch")
    assert "11in" in extract_variant_tokens(normalize_text('iPad Air 10.9"'), "tablet")
    # "celular" is a device noun for phones, not cellular connectivity
    assert "cellular" not in extract_variant_tokens(normalize_text("Celular Samsung"), "phone")


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("PS5 Slim + 2 controles + 3 juegos", True),
        ("Xbox Series S con juegos", True),
        ("Combo Nintendo Switch OLED", True),
        ("Lote de 5 iPhone 11", True),
        ("Kit Taladro Makita XFD131", True),
        ("Paquete 2x Control Xbox", True),
        ("Nintendo Switch incluye 2 juegos", True),
        ("DJI Mini 4 Pro Fly More Combo", True),
        ("iPhone 15 incluye cargador y funda", False),
        ("Galaxy S24+ 256GB", False),
        ("Redmi Note 13 Pro+ 5G", False),
        ("Redmi Note 13 8+256GB", False),
        ("iPad Pro Wi-Fi + Cellular", False),
        ("PS5 Slim Digital 1TB", False),
    ],
)
def test_is_bundle(title: str, expected: bool) -> None:
    assert is_bundle(normalize_text(title)) is expected


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("Funda Silicon Para iPhone 15 Pro Max", True),
        ("Mica Cristal Templado iPhone 13", True),
        ("Cargador 20W para iPhone 15", True),
        ("Control DualSense PS5 Blanco", True),
        ("Correa para Apple Watch 45mm", True),
        ("Caja vacía iPhone 14 Pro", True),
        ("Juego Spider-Man 2 PS5 Fisico", True),
        ("Samsung Galaxy S24 Ultra Funda Uso Rudo", True),
        ("iPhone 15 Pro Max 256GB con funda de regalo", False),
        ("Nintendo Switch OLED con estuche y mica", False),
        ("PS5 Slim + 2 controles", False),
        ("Consola de videojuegos PS5 Slim 1TB", False),
        ("iPhone 13 128gb bateria 85%", False),
        ("Audífonos Sony WH-1000XM5", False),
    ],
)
def test_is_accessory(title: str, expected: bool) -> None:
    assert is_accessory(normalize_text(title)) is expected


# ------------------------------------------------------------------------------------------------
# Full listing normalization: realistic Mexican marketplace titles
# (title, condition_raw, brand, model, storage_gb, condition, is_accessory, is_bundle, canonical_key)
# ------------------------------------------------------------------------------------------------

CASES = [
    # --- phones -------------------------------------------------------------------------------
    ("Apple iPhone 15 Pro Max (256 GB) - Titanio Natural", None, "apple", "iphone 15 pro max", 256, "unknown",
     False, False, "apple|iphone 15 pro max|256gb"),
    ("iPhone 15 Pro Max 256gb Titanio Natural Desbloqueado Reacondicionado", None, "apple", "iphone 15 pro max", 256,
     "refurbished", False, False, "apple|iphone 15 pro max|256gb"),
    ("iPhone 15 Pro 256gb", "new", "apple", "iphone 15 pro", 256, "new", False, False, "apple|iphone 15 pro|256gb"),
    ("iPhone 15 128GB (Producto Nuevo Sellado)", None, "apple", "iphone 15", 128, "new", False, False,
     "apple|iphone 15|128gb"),
    ("Apple iPhone 13 mini 256 G B Rosa", "used", "apple", "iphone 13 mini", 256, "used", False, False,
     "apple|iphone 13 mini|256gb"),
    ("iPhone 16e 128GB Blanco", None, "apple", "iphone 16e", 128, "unknown", False, False, "apple|iphone 16e|128gb"),
    ("iPhone SE 2022 64gb", None, "apple", "iphone se 3", 64, "unknown", False, False, "apple|iphone se 3|64gb"),
    ("iPhone SE 64gb", None, "apple", None, 64, "unknown", False, False, None),
    ("iPhone 13 128gb Usado Bateria 85%", None, "apple", "iphone 13", 128, "used", False, False,
     "apple|iphone 13|128gb"),
    ("iPhone 14 Pro para piezas no enciende", None, "apple", "iphone 14 pro", None, "for_parts", False, False,
     None),  # storage not stated: never a price cluster
    ("Samsung Galaxy S24 Ultra 12gb 512gb Titanium Gray", None, "samsung", "galaxy s24 ultra", 512, "unknown",
     False, False, "samsung|galaxy s24 ultra|512gb"),
    ("Samsung Galaxy S24+ 256GB Violeta", None, "samsung", "galaxy s24+", 256, "unknown", False, False,
     "samsung|galaxy s24+|256gb"),
    ("Galaxy S23 FE 128gb Menta", None, "samsung", "galaxy s23 fe", 128, "unknown", False, False,
     "samsung|galaxy s23 fe|128gb"),
    ("Samsung Galaxy Z Flip6 256GB Azul", None, "samsung", "galaxy z flip6", 256, "unknown", False, False,
     "samsung|galaxy z flip6|256gb"),
    ("Samsung Galaxy A54 5G 128GB", None, "samsung", "galaxy a54 5g", 128, "unknown", False, False,
     "samsung|galaxy a54 5g|128gb"),
    ("Xiaomi Redmi Note 13 Pro+ 5G 8+256GB Negro", None, "xiaomi", "redmi note 13 pro+ 5g", 256, "unknown", False,
     False, "xiaomi|redmi note 13 pro+ 5g|256gb"),
    ("Poco X6 Pro 5G 12gb 512gb", None, "xiaomi", "poco x6 pro 5g", 512, "unknown", False, False,
     "xiaomi|poco x6 pro 5g|512gb"),
    ("Celular Motorola Moto G84 5G 256GB 12GB RAM Azul", None, "motorola", "moto g84 5g", 256, "unknown", False,
     False, "motorola|moto g84 5g|256gb"),
    ("Google Pixel 8a 128GB Obsidiana", None, "google", "pixel 8a", 128, "unknown", False, False,
     "google|pixel 8a|128gb"),
    ("Google Pixel 9 Pro XL 256gb", None, "google", "pixel 9 pro xl", 256, "unknown", False, False,
     "google|pixel 9 pro xl|256gb"),
    # --- consoles / handheld PCs -----------------------------------------------------------------
    ("Consola Sony PlayStation 5 Slim Digital 1TB", None, "sony", "ps5 slim", 1024, "unknown", False, False,
     "sony|ps5 slim|1024gb|digital"),
    ("PS5 Slim Edicion Digital 1 TB", None, "sony", "ps5 slim", 1024, "unknown", False, False,
     "sony|ps5 slim|1024gb|digital"),
    ("PlayStation 5 Slim Estandar con lector 1TB", None, "sony", "ps5 slim", 1024, "unknown", False, False,
     "sony|ps5 slim|1024gb|disc"),
    ("Consola PS5 1TB Edicion Digital", None, "sony", "ps5 slim", 1024, "unknown", False, False,
     "sony|ps5 slim|1024gb|digital"),  # 1TB PS5 can only be the Slim
    ("PlayStation 5 825GB Lector de Discos", None, "sony", "ps5", 825, "unknown", False, False,
     "sony|ps5|825gb|disc"),
    ("Sony PlayStation 5 Pro 2TB", None, "sony", "ps5 pro", 2048, "unknown", False, False, "sony|ps5 pro"),
    ("PS5 Slim Digital 1TB Seminuevo Caja Abierta", None, "sony", "ps5 slim", 1024, "used", False, False,
     "sony|ps5 slim|1024gb|digital"),
    ("PS5 Slim + 2 controles + 3 juegos", None, "sony", "ps5 slim", None, "unknown", False, True,
     None),  # neither storage nor edition stated
    ("Control DualSense PS5 Blanco", None, "sony", "dualsense", None, "unknown", True, False, "acc|sony|dualsense"),
    ("Xbox Series S 512GB Blanco", None, "microsoft", "xbox series s", 512, "unknown", False, False,
     "microsoft|xbox series s|512gb"),
    ("Consola Xbox Series X 1TB Digital", None, "microsoft", "xbox series x", 1024, "unknown", False, False,
     "microsoft|xbox series x|1024gb|digital"),
    ("Nintendo Switch OLED 64GB Blanco", None, "nintendo", "switch oled", 64, "unknown", False, False,
     "nintendo|switch oled"),
    ("Nintendo Switch Lite Turquesa", None, "nintendo", "switch lite", None, "unknown", False, False,
     "nintendo|switch lite"),
    ("Nintendo Switch Oled Con Estuche y Mica", None, "nintendo", "switch oled", None, "unknown", False, False,
     "nintendo|switch oled"),
    ("Nintendo Switch 2 + Mario Kart World Bundle", None, "nintendo", "switch 2", None, "unknown", False, True,
     "nintendo|switch 2"),
    ("Steam Deck OLED 1TB", None, "valve", "steam deck oled", 1024, "unknown", False, False,
     "valve|steam deck oled|1024gb"),
    ("Steam Deck 256GB", None, "valve", "steam deck lcd", 256, "unknown", False, False, "valve|steam deck lcd|256gb"),
    ("Steam Deck 512GB", None, "valve", None, 512, "unknown", False, False, None),  # LCD or OLED?
    ("ASUS ROG Ally Z1 Extreme 512GB", None, "asus", "rog ally z1 extreme", 512, "unknown", False, False,
     "asus|rog ally z1 extreme|512gb"),
    # --- Apple wearables / tablets / laptops ------------------------------------------------------
    ("Apple AirPods Pro 2da Generación USB-C", None, "apple", "airpods pro 2", None, "unknown", False, False,
     "apple|airpods pro 2"),
    ("AirPods Pro", None, "apple", None, None, "unknown", False, False, None),
    ("Apple AirPods 4 con Cancelación Activa de Ruido", None, "apple", "airpods 4 anc", None, "unknown", False, False,
     "apple|airpods 4 anc"),
    ("Apple Watch Series 9 45mm GPS Medianoche", None, "apple", "apple watch series 9", None, "unknown", False,
     False, "apple|apple watch series 9|45mm,gps"),
    ("Apple Watch Ultra 2 49mm Titanio Natural", None, "apple", "apple watch ultra 2", None, "unknown", False,
     False, "apple|apple watch ultra 2|49mm"),
    ("iPad 10ma Generación 64GB WiFi Plata", None, "apple", "ipad 10", 64, "unknown", False, False,
     "apple|ipad 10|64gb|wifi"),
    ('iPad Air 11" M2 128GB Wi-Fi Azul', None, "apple", "ipad air m2", 128, "unknown", False, False,
     "apple|ipad air m2|128gb|11in,wifi"),
    ("iPad Pro 11 M4 256GB Wi-Fi + Cellular", None, "apple", "ipad pro m4", 256, "unknown", False, False,
     "apple|ipad pro m4|256gb|11in,cellular"),
    ("MacBook Air 13 M2 8GB RAM 256GB SSD Gris Espacial", None, "apple", "macbook air m2", 256, "unknown", False,
     False, "apple|macbook air m2|256gb|13in,ram8gb"),
    ("MacBook Pro 14 M3 Pro 18GB 512GB", None, "apple", "macbook pro m3 pro", 512, "unknown", False, False,
     "apple|macbook pro m3 pro|512gb|14in,ram18gb"),
    # --- GPUs, cameras, drones, audio, e-readers, tools, toys, sneakers ---------------------------
    ("Tarjeta de Video MSI GeForce RTX 4070 Ti Super 16GB", None, "nvidia", "rtx 4070 ti super", None, "unknown",
     False, False, "nvidia|rtx 4070 ti super|vram16gb"),
    ("Laptop Gamer ASUS TUF RTX 4060 16GB RAM 512GB SSD Ryzen 7", None, "asus", None, 512, "unknown", False, False,
     None),  # the RTX is a component of the laptop: never a GPU match
    ("AMD Radeon RX 7900 XTX 24GB", None, "amd", "radeon rx 7900 xtx", None, "unknown", False, False,
     "amd|radeon rx 7900 xtx|vram24gb"),
    ("GoPro HERO12 Black", None, "gopro", "gopro hero 12", None, "unknown", False, False, "gopro|gopro hero 12"),
    ("DJI Mini 4 Pro Fly More Combo", None, "dji", "dji mini 4 pro", None, "unknown", False, True,
     "dji|dji mini 4 pro"),
    ("Bocina JBL Flip 6 Negra", None, "jbl", "jbl flip 6", None, "unknown", False, False, "jbl|jbl flip 6"),
    ("Audífonos Sony WH-1000XM5 Negro", None, "sony", "wh-1000xm5", None, "unknown", False, False,
     "sony|wh-1000xm5"),
    ("Kindle Paperwhite 12a Generación 16GB", None, "amazon", "kindle paperwhite 12", 16, "unknown", False, False,
     "amazon|kindle paperwhite 12"),
    ("Taladro Rotomartillo DeWalt DCD7781D2 20V 2 Baterías", None, "dewalt", "dcd7781d2", None, "unknown", False,
     False, "dewalt|dcd7781d2|with_battery"),
    ("LEGO Star Wars Millennium Falcon 75192 7541 piezas", None, "lego", "lego 75192", None, "unknown", False,
     False, "lego|lego 75192"),
    ("Tenis Nike Air Jordan 1 Retro High OG DZ5485-612 Talla 27 MX", None, "nike", "dz5485-612", None, "unknown",
     False, False, "nike|dz5485-612|talla27"),
    # --- accessories, games, boxes: never clustered with devices ----------------------------------
    ("Funda Silicon Para iPhone 15 Pro Max", None, "apple", "iphone 15 pro max", None, "unknown", True, False, None),
    ("Mica Cristal Templado iPhone 13", None, "apple", "iphone 13", None, "unknown", True, False, None),
    ("Hermosa Funda Transparente iPhone 15", None, "apple", "iphone 15", None, "unknown", True, False, None),
    ("Samsung Galaxy S24 Ultra Funda Uso Rudo", None, "samsung", "galaxy s24 ultra", None, "unknown", True, False,
     None),
    ("Cargador Original Apple 20W USB-C para iPhone", None, "apple", None, None, "unknown", True, False, None),
    ("Batería para iPhone 11", None, "apple", "iphone 11", None, "unknown", True, False, None),
    ("Pantalla Display Para Samsung A54", None, "samsung", "galaxy a54", None, "unknown", True, False, None),
    ("Caja Vacia iPhone 14 Pro Max", None, "apple", "iphone 14 pro max", None, "unknown", True, False, None),
    ("Juego Spider-Man 2 PS5 Fisico Nuevo", None, "sony", "ps5", None, "new", True, False, None),
    ("Joy-Con Nintendo Switch Par Neon", None, "nintendo", None, None, "unknown", True, False, None),
    ("Paquete 2x Control Xbox Series X", None, "microsoft", "xbox series x", None, "unknown", True, True, None),
    ("Correa para Apple Watch 45mm Silicón", None, "apple", None, None, "unknown", True, False, None),
    # --- ambiguous titles: recognised family, no cluster ------------------------------------------
    ("FIFA 24 PS5", None, "sony", None, None, "unknown", False, False, None),
    ("God of War Ragnarok PS5", None, "sony", None, None, "unknown", False, False, None),
    ("Producto generico sin marca", None, None, None, None, "unknown", False, False, None),
]


@pytest.mark.parametrize(
    ("title", "condition_raw", "brand", "model", "storage", "condition", "accessory", "bundle", "key"),
    CASES,
    ids=[c[0][:48] for c in CASES],
)
def test_normalize_listing_cases(title, condition_raw, brand, model, storage, condition, accessory, bundle, key):
    attrs = norm(title, condition_raw=condition_raw)
    assert attrs.brand == brand
    assert attrs.model == model
    assert attrs.storage_gb == storage
    assert attrs.condition == condition
    assert attrs.is_accessory is accessory
    assert attrs.is_bundle is bundle
    assert attrs.canonical_key == key
    # the stored key is exactly what the matcher would compute
    assert canonical_key(attrs) == key


def test_case_table_is_large_enough() -> None:
    assert len(CASES) >= 45


def test_categories_are_from_vocabulary() -> None:
    for case in CASES:
        attrs = norm(case[0])
        assert attrs.category is None or attrs.category in V.CATEGORIES, case[0]


def test_analyze_listing_explains_unclustered_listings() -> None:
    _, notes = analyze_listing(listing("AirPods Pro"))
    assert any("generation not stated" in n for n in notes)
    _, notes = analyze_listing(listing("Funda Silicon Para iPhone 15 Pro Max"))
    assert any("para" in n for n in notes)
    _, notes = analyze_listing(listing("FIFA 24 PS5"))
    assert any("possibly a game" in n for n in notes)
    _, notes = analyze_listing(listing("Consola PS5 1TB Edicion Digital"))
    assert any("inferred as Slim" in n for n in notes)


def test_source_fields_are_used_only_as_fallback() -> None:
    # Storage missing in the title but reported by the source's structured attributes.
    attrs = norm("Apple iPhone 15 Pro Max Titanio Natural", attributes={"INTERNAL_MEMORY": "256 GB", "RAM": "8 GB"})
    assert attrs.storage_gb == 256
    assert attrs.canonical_key == "apple|iphone 15 pro max|256gb"
    # Title wins over the attribute when both are present.
    attrs = norm("iPhone 15 Pro Max 512GB", attributes={"INTERNAL_MEMORY": "256 GB"})
    assert attrs.storage_gb == 512
    # Model from the source's model field when the title alone is not enough.
    attrs = norm("Celular Apple Titanio Natural 256GB", model="iPhone 15 Pro Max")
    assert attrs.model == "iphone 15 pro max"
    assert attrs.canonical_key == "apple|iphone 15 pro max|256gb"
    # Brand from the source's brand field; generic brands are ignored.
    assert norm("Audifonos inalambricos", brand="Sony").brand == "sony"
    assert norm("Funda generica", brand="Genérico").brand is None


def test_accessory_source_category_marks_accessory() -> None:
    attrs = norm("iPhone 15 Pro Max Transparente", category_name="Fundas y Estuches")
    assert attrs.is_accessory is True
    assert attrs.canonical_key is None


def test_normalize_listing_is_deterministic() -> None:
    raw = listing("Consola Sony PlayStation 5 Slim Digital 1TB Nueva", condition_raw="new")
    first = normalize_listing(raw).to_dict()
    for _ in range(5):
        assert normalize_listing(raw).to_dict() == first


def test_vocab_brand_lookup() -> None:
    assert V.find_brand("consola ps5 slim") == ("sony", 8)
    assert V.find_brand("celular redmi note 13")[0] == "xiaomi"
    assert V.find_brand("iphone poco uso") == ("apple", 0)  # "poco uso" (little use) is not POCO
    assert V.canonical_brand("generico") is None
    assert V.canonical_brand("playstation") == "sony"
