"""Regression tests for the second review of the matching fixes (findings engine_matching#0 .. #4).

The first round made matching precision-first (accessories, games, bundles and ambiguous suffixes named
after the device never join a device cluster).  The second review found common, real device titles that
those rules dropped.  Each test below reproduces one of those titles and pins the precision cases the first
round fixed, so neither direction regresses.
"""

from __future__ import annotations

import pytest

from flipradar.engine.types import NormalizedAttributes, RawListing
from flipradar.matching import compare
from flipradar.normalize import analyze_listing, normalize_listing


def norm(title: str, **kw) -> NormalizedAttributes:
    return normalize_listing(RawListing(source_key="t", external_id="1", title=title, price_cents=1, **kw))


def key(title: str) -> str | None:
    return norm(title).canonical_key


# ------------------------------------------------------------------------------------------------
# engine_matching#0 - the watch band / earbuds' charging case is part of the product name
# ------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("title", "expected_key"),
    [
        ("Apple Watch Series 9 45mm Correa Loop Deportiva", "apple|apple watch series 9|45mm"),
        (
            "Apple Watch Series 9 (GPS) - Caja de aluminio color medianoche de 45 mm - Correa deportiva color "
            "medianoche - M/L",
            "apple|apple watch series 9|45mm,gps",
        ),
        ("Apple Watch SE 2da Gen 44mm GPS Correa Deportiva", "apple|apple watch se 2|44mm,gps"),
        ("Apple Watch Ultra 2 49mm Titanio Correa Ocean", "apple|apple watch ultra 2|49mm"),
        ("Apple Watch Serie 7 45mm Pulso Deportivo", "apple|apple watch series 7|45mm"),
        ("Samsung Galaxy Watch6 Classic 47mm Bluetooth Correa Hibrida", "samsung|galaxy watch 6 classic|47mm"),
        (
            "Apple AirPods 4 Audífonos Inalámbricos, Audio Espacial Personalizado, Estuche de Carga USB-C",
            "apple|airpods 4",
        ),
        ("Apple AirPods Pro 2nd Generation with MagSafe Charging Case USB-C", "apple|airpods pro 2"),
    ],
)
def test_standard_band_or_charging_case_in_a_device_title(title: str, expected_key: str) -> None:
    attrs = norm(title)
    assert attrs.is_accessory is False, title
    assert attrs.canonical_key == expected_key


@pytest.mark.parametrize(
    "title",
    [
        # the first-round cases: only the part is sold
        "Apple Watch Series 9 45mm Correa",
        "Apple Watch Series 9 45mm Pulso",
        "Apple Watch Series 9 45mm Correa Original",
        "AirPods Pro 2 Estuche de carga usado",
        "AirPods Pro 2 Solo Estuche de Carga",
        # band that fits several watches: a compatibility list, not one watch
        "Apple Watch Series 9 8 7 45mm Correa Deportiva",
        "Apple Watch Series 9 41mm 45mm Correa Loop Deportiva",
        "Apple Watch Series 9 41/45mm Correa Loop Deportiva",
        "Apple Watch Series 9 Correa Deportiva",  # no case size: nothing describes a watch
        "Apple Watch Series 9 45mm Correa de Titanio",  # titanium band, not a titanium case
        "Apple Watch Series 9 Correa Deportiva 45mm Solo Correa",
        "Samsung Galaxy Watch 6 Correa 44mm",
        # a protective case, or only the case, after a description of the earbuds
        "AirPods Pro 2 Audifonos Funda de Silicon",
        "AirPods Pro 2 Audifonos Case Transparente",
        "AirPods Pro 2 Audifonos Bluetooth Estuche de Carga Solo",
        "AirPods Pro 2 Estuche de Carga para Audifonos",
        # Indistinguishable from a replacement-case listing ("AirPods Pro 2da Generación Estuche de Carga
        # MagSafe USB-C"); a case is ~37% of the set's price, so it stays unclustered (precision first).
        "Apple AirPods Pro (2.ª generación) - Estuche de carga MagSafe (USB-C)",
    ],
)
def test_band_or_case_sold_alone_stays_an_accessory(title: str) -> None:
    attrs = norm(title)
    assert attrs.is_accessory is True, title
    assert attrs.canonical_key is None, title


def test_band_listing_never_joins_the_watch_cluster() -> None:
    watch = norm("Apple Watch Series 9 45mm Correa Loop Deportiva")
    band = norm("Apple Watch Series 9 45mm Correa")
    assert watch.canonical_key == "apple|apple watch series 9|45mm"
    assert compare(watch, band).method == "none"


# ------------------------------------------------------------------------------------------------
# engine_matching#1 - condition, logistics, score and colour words after a bare Switch name
# ------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("title", "expected_key"),
    [
        ("Nintendo Switch Lite Semi Nuevo", "nintendo|switch lite"),
        ("Nintendo Switch OLED Blanco Bien Cuidado", "nintendo|switch oled"),
        ("Nintendo Switch Lite Turquesa Sin Rayones", "nintendo|switch lite"),
        ("Nintendo Switch OLED Usado Buenas Condiciones", "nintendo|switch oled"),
        ("Nintendo Switch OLED Usado 10/10", "nintendo|switch oled"),
        ("Nintendo Switch Lite Turquesa 9/10", "nintendo|switch lite"),
        ("Nintendo Switch OLED Blanco CDMX", "nintendo|switch oled"),
        ("Nintendo Switch OLED Blanco Entrega Inmediata", "nintendo|switch oled"),
        ("Nintendo Switch OLED Blanco Precio Negociable", "nintendo|switch oled"),
        ("Nintendo Switch OLED Seminuevo Envio Hoy", "nintendo|switch oled"),
        ("Nintendo Switch Lite Azul Celeste", "nintendo|switch lite"),
        ("Nintendo Switch Lite Verde Azulado", "nintendo|switch lite"),
    ],
)
def test_describing_words_after_a_bare_switch_name_keep_the_model(title: str, expected_key: str) -> None:
    assert key(title) == expected_key


@pytest.mark.parametrize(
    "title",
    [
        "Nintendo Switch Mario Kart 8 Deluxe",
        "Nintendo Switch Lite Super Mario",
        "Nintendo Switch OLED Pokemon Escarlata Purpura",
        "Nintendo Switch Lite Zacian Zamazenta",  # special edition
        "Nintendo Switch OLED Grip",  # an in-box item sold on its own
        "Nintendo Switch OLED Manual",
        "Nintendo Switch OLED Pro Controller",
    ],
)
def test_games_editions_and_items_after_a_bare_switch_name_stay_unclustered(title: str) -> None:
    assert key(title) is None, title


# ------------------------------------------------------------------------------------------------
# engine_matching#2 - a description of the unit after "con"/"y" is not an extra item
# ------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("title", "expected_key"),
    [
        ("Consola PS5 Slim Digital 1TB con detalle estetico", "sony|ps5 slim|1024gb|digital"),
        ("Consola PS5 Slim Digital 1TB con muy poco uso", "sony|ps5 slim|1024gb|digital"),
        ("Consola PS5 Slim 1TB Disco con ligero rayon", "sony|ps5 slim|1024gb|disc"),
        ("Consola PS5 Slim Digital 1TB con todo lo original", "sony|ps5 slim|1024gb|digital"),
        ("Consola PS5 Slim Digital 1TB con Ticket de compra", "sony|ps5 slim|1024gb|digital"),
        ("Consola PS5 Slim Digital 1TB Nueva Sellada y Facturada", "sony|ps5 slim|1024gb|digital"),
        ("Consola PS5 Slim Digital 1TB con base vertical", "sony|ps5 slim|1024gb|digital"),
        ("Consola PS5 Slim Digital 1TB con 1 año de garantia", "sony|ps5 slim|1024gb|digital"),
        ("Nintendo Switch Lite con estuche y mica de cristal", "nintendo|switch lite"),
        ("Nintendo Switch Lite con un rayon", "nintendo|switch lite"),
        ("Nintendo Switch Oled Blanco Con Joycons Blancos", "nintendo|switch oled"),
        ("Nintendo Switch OLED con caja y manuales", "nintendo|switch oled"),
    ],
)
def test_unit_descriptions_after_con_are_not_bundles(title: str, expected_key: str) -> None:
    attrs = norm(title)
    assert attrs.is_bundle is False, title
    assert attrs.canonical_key == expected_key


@pytest.mark.parametrize(
    "title",
    [
        "Consola PS5 Slim Digital 1TB con Spiderman 2",
        "Consola PS5 Slim Digital 1TB y FC 25",
        "Consola PS5 Slim Digital 1TB con Astro Bot",
        "Consola PS5 Slim Digital 1TB con Gran Turismo 7",
        "Consola PS5 Slim Digital 1TB con The Last of Us",
        "Consola PS5 Slim Digital 1TB con Super Mario",
        "Consola PS5 Slim Digital 1TB con Black Ops 6",
        "Consola PS5 Slim Digital 1TB con detalle y Spiderman 2",
        "Consola PS5 Slim Digital 1TB con caja y juegos",
        "Consola PS5 Slim Digital 1TB con 2 controles y caja",
    ],
)
def test_games_and_extra_items_after_con_stay_bundles(title: str) -> None:
    assert norm(title).is_bundle is True, title


# ------------------------------------------------------------------------------------------------
# engine_matching#3 - the Switch OLED's own Joy-Con named after a description of the console
# ------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "title",
    [
        "Nintendo Switch OLED Blanco Joy Con Blancos",
        "Nintendo Switch OLED Model White Joy-Con",
        "Nintendo Switch OLED Seminuevo Joy-Con con Drift",
    ],
)
def test_standard_joycon_after_a_console_description_is_the_console(title: str) -> None:
    attrs = norm(title)
    assert attrs.is_accessory is False, title
    assert attrs.canonical_key == "nintendo|switch oled"


@pytest.mark.parametrize(
    "title",
    [
        # Right after the model name the title reads like a Joy-Con listing for that console (the same
        # shape as "PS5 Dualsense Blanco"); a Joy-Con pair is ~25% of the console's price.
        "Nintendo Switch OLED Joy-Con Neon",
        "Nintendo Switch OLED Joy-Con Rojo y Azul Neón",
        "Nintendo Switch Neon Joy-Con",  # base Switch: also the platform name
        "Nintendo Switch OLED Blanco Joy-Con Par",
        "Nintendo Switch OLED Blanco Joy-Con Controllers",
        "Joy-Con Nintendo Switch OLED Blancos",
    ],
)
def test_joycon_listings_stay_accessories(title: str) -> None:
    attrs = norm(title)
    assert attrs.is_accessory is True, title
    assert attrs.canonical_key is None, title


# ------------------------------------------------------------------------------------------------
# engine_matching#4 - "mini detalle" / "mini rayon" (a tiny defect) after the storage
# ------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("title", "expected_key"),
    [
        ("iPhone 13 128gb Mini Detalle En Pantalla", "apple|iphone 13|128gb"),
        ("iPhone 14 128GB Azul Mini Rayon", "apple|iphone 14|128gb"),
        ("iPhone 12 Pro 128gb mini golpe en esquina", "apple|iphone 12 pro|128gb"),
        # right after the number "mini" is still the model name
        ("iPhone 13 Mini 128gb Detalle Estetico", "apple|iphone 13 mini|128gb"),
        # unchanged: a changer word that may belong to the model name keeps the title ambiguous
        ("iPhone 13 128gb Mini", None),
        ("iPhone 13 128gb mini bateria 88%", None),  # could be a 13 mini with its battery health
        ("iPhone 15 256gb Pro Max", None),
        ("iPhone 15 Pro 256gb Max detalle", None),
        # "Max bateria": a Pro Max seller can write "Max" after the storage, so precision wins
        ("iPhone 15 128gb negro Max bateria", None),
    ],
)
def test_mini_before_a_defect_noun_is_an_adjective(title: str, expected_key: str | None) -> None:
    assert key(title) == expected_key


def test_iphone_13_mini_with_a_defect_keeps_its_model() -> None:
    attrs, notes = analyze_listing(
        RawListing(source_key="t", external_id="1", title="iPhone 13 mini detalle estetico", price_cents=1)
    )
    assert attrs.model == "iphone 13 mini"
    assert attrs.canonical_key is None  # storage not stated
    assert any("storage" in n for n in notes), notes


# ------------------------------------------------------------------------------------------------
# Realistic Mexican titles: (title, canonical key, is_accessory, is_bundle)
# ------------------------------------------------------------------------------------------------

REAL_TITLES = [
    # Apple Watch / Galaxy Watch with the band named
    ("Apple Watch Series 9 45mm Correa Loop Deportiva", "apple|apple watch series 9|45mm", False, False),
    ("Apple Watch Series 9 45mm GPS con correa deportiva y funda", "apple|apple watch series 9|45mm,gps", False,
     False),
    ("Apple Watch Series 8 41mm GPS Correa Deportiva Medianoche Usado", "apple|apple watch series 8|41mm,gps",
     False, False),
    ("Apple Watch Series 9 45mm Caja de Acero Inoxidable Correa Milanesa",
     "apple|apple watch series 9|45mm,steel", False, False),
    ("Apple Watch Serie 9 45mm Aluminio Rosa Correa deportiva", "apple|apple watch series 9|45mm", False, False),
    ("Apple Watch Series 10 46mm GPS Funda Incluida", "apple|apple watch series 10|46mm,gps", False, False),
    ("Apple Watch Ultra 2 49mm Titanio Correa Ocean", "apple|apple watch ultra 2|49mm", False, False),
    ("Galaxy Watch 6 44mm Bluetooth Correa Deportiva Grafito", "samsung|galaxy watch 6|44mm", False, False),
    ("Apple Watch Series 9 45mm Correa", None, True, False),
    ("Samsung Galaxy Watch 6 Correa 44mm", None, True, False),
    # AirPods / Galaxy Buds with the charging case named
    ("AirPods Pro 2 con estuche de carga", "apple|airpods pro 2", False, False),
    ("AirPods Pro 2da Generacion con Estuche de Carga MagSafe USB-C", "apple|airpods pro 2", False, False),
    ("AirPods Pro 2 con el estuche de carga", "apple|airpods pro 2", False, False),
    ("AirPods 4 Audifonos con Cancelacion de Ruido Estuche de Carga", "apple|airpods 4 anc", False, False),
    ("Samsung Galaxy Buds 2 Pro Audifonos Bluetooth con Estuche", "samsung|galaxy buds 2 pro", False, False),
    ("AirPods Pro 2 Estuche de carga usado", None, True, False),
    # Nintendo Switch OLED / Lite with colours, condition words and Joy-Con
    ("Nintendo Switch OLED Blanco", "nintendo|switch oled", False, False),
    ("Nintendo Switch OLED Neon Rojo Azul Excelente Estado", "nintendo|switch oled", False, False),
    ("Nintendo Switch Lite Coral Como Nueva Poco Uso", "nintendo|switch lite", False, False),
    ("Nintendo Switch Lite Semi Nuevo", "nintendo|switch lite", False, False),
    ("Nintendo Switch OLED Usado Buenas Condiciones", "nintendo|switch oled", False, False),
    ("Nintendo Switch OLED Usado 10/10", "nintendo|switch oled", False, False),
    ("Nintendo Switch Lite Turquesa Sin Rayones", "nintendo|switch lite", False, False),
    ("Nintendo Switch OLED Blanco Entrega Inmediata", "nintendo|switch oled", False, False),
    ("Nintendo Switch Lite Verde Azulado", "nintendo|switch lite", False, False),
    ("Nintendo Switch Lite con un rayon", "nintendo|switch lite", False, False),
    ("Nintendo Switch Oled Blanco Con Joycons Blancos", "nintendo|switch oled", False, False),
    ("Nintendo Switch OLED Blanco Joy Con Blancos", "nintendo|switch oled", False, False),
    ("Nintendo Switch OLED Joy-Con Neon", None, True, False),
    ("Nintendo Switch Mario Kart 8 Deluxe", None, False, False),
    # PS5 Slim
    ("PS5 Slim con 2 controles y caja", None, False, True),  # storage/edition not stated, and a bundle
    ("Consola PS5 Slim Digital 1TB con 2 controles y caja", "sony|ps5 slim|1024gb|digital", False, True),
    ("Consola PS5 Slim Digital 1TB con control y caja", "sony|ps5 slim|1024gb|digital", False, False),
    ("Consola PS5 Slim Digital 1TB con detalle estetico", "sony|ps5 slim|1024gb|digital", False, False),
    ("Consola PS5 Slim 1TB Disco con ligero rayon", "sony|ps5 slim|1024gb|disc", False, False),
    ("Consola PS5 Slim Digital 1TB Nueva Sellada y Facturada", "sony|ps5 slim|1024gb|digital", False, False),
    ("Consola PS5 Slim Digital 1TB con Spiderman 2", "sony|ps5 slim|1024gb|digital", False, True),
    ("PS5 Dualsense Blanco", None, True, False),
    # iPhone
    ("iPhone 13 128gb Mini Detalle En Pantalla", "apple|iphone 13|128gb", False, False),
    ("iPhone 14 128GB Azul Mini Rayon", "apple|iphone 14|128gb", False, False),
    ("iPhone 13 Mini 128gb Detalle Estetico", "apple|iphone 13 mini|128gb", False, False),
    ("iPhone 13 mini detalle estetico", None, False, False),  # storage not stated
    ("iPhone 15 Pro Max 256gb Titanio Natural con funda", "apple|iphone 15 pro max|256gb", False, False),
    ("iPhone 15 256gb Pro Max", None, False, False),
]


@pytest.mark.parametrize(("title", "expected_key", "accessory", "bundle"), REAL_TITLES,
                         ids=[t[0][:60] for t in REAL_TITLES])
def test_realistic_mexican_titles(title: str, expected_key: str | None, accessory: bool, bundle: bool) -> None:
    attrs = norm(title)
    assert (attrs.canonical_key, attrs.is_accessory, attrs.is_bundle) == (expected_key, accessory, bundle)


def test_real_title_table_is_large_enough() -> None:
    assert len(REAL_TITLES) >= 40


# ------------------------------------------------------------------------------------------------
# End to end: recovered titles are comparables and buy candidates again; the parts still are not
# ------------------------------------------------------------------------------------------------

E2E_RECALL = [
    # (finding, comparable titles, comparable price, buy title, buy price)
    ("#0", "Apple Watch Series 9 45mm Medianoche Usado", 6500, "Apple Watch Series 9 45mm Correa Loop Deportiva",
     4200),
    ("#1", "Nintendo Switch OLED Blanco Bien Cuidado", 5800, "Nintendo Switch OLED Usado 10/10", 3900),
    ("#2", "Consola PS5 Slim Digital 1TB con detalle estetico", 7600, "Consola PS5 Slim Digital 1TB con muy poco uso",
     5200),
    ("#3", "Nintendo Switch OLED Blanco Joy Con Blancos", 5800, "Nintendo Switch OLED Model White Joy-Con", 3900),
    ("#4", "iPhone 13 128gb Azul Usado", 6500, "iPhone 13 128gb Mini Detalle En Pantalla", 4300),
]


def _ingest(db, settings, items):
    from flipradar.services import analysis
    from flipradar.services.ingestion import ingest_raw_listings

    stats = ingest_raw_listings(db, "manual", items, settings=settings)
    db.commit()
    analysis.recompute(db, stats.product_ids_touched, settings=settings, dispatch=False)
    db.commit()


@pytest.mark.parametrize(("finding", "comp_title", "comp_price", "title", "price"), E2E_RECALL,
                         ids=[c[0] for c in E2E_RECALL])
def test_recovered_title_becomes_an_opportunity(db, settings, finding, comp_title, comp_price, title, price):
    from sqlalchemy import select

    from flipradar.models import Listing, Opportunity
    from tests.conftest import raw

    items = [raw(f"C{i}", comp_title, comp_price + 25 * i, brand=None, model=None) for i in range(4)]
    items.append(raw("X", title, price, brand=None, model=None))
    _ingest(db, settings, items)
    buy = db.scalars(select(Listing).where(Listing.external_id == "X")).one()
    comparable = db.scalars(select(Listing).where(Listing.external_id == "C0")).one()
    assert buy.product_id is not None and buy.product_id == comparable.product_id, finding
    assert [o for o in db.scalars(select(Opportunity)).all() if o.buy_listing.external_id == "X"], finding


@pytest.mark.parametrize(
    ("comp_title", "comp_price", "title", "price"),
    [
        ("Apple Watch Series 9 45mm Correa Loop Deportiva", 6500, "Apple Watch Series 9 45mm Correa", 900),
        ("Apple AirPods 4 Audifonos Inalambricos Estuche de Carga USB-C", 2600, "AirPods 4 Estuche de carga usado",
         900),
        ("Nintendo Switch OLED Blanco Joy Con Blancos", 5800, "Nintendo Switch OLED Joy-Con Neon", 1400),
    ],
)
def test_parts_still_never_become_opportunities(db, settings, comp_title, comp_price, title, price):
    from sqlalchemy import select

    from flipradar.models import Listing, Opportunity
    from tests.conftest import raw

    items = [raw(f"C{i}", comp_title, comp_price + 25 * i, brand=None, model=None) for i in range(4)]
    items.append(raw("X", title, price, brand=None, model=None))
    _ingest(db, settings, items)
    part = db.scalars(select(Listing).where(Listing.external_id == "X")).one()
    comparable = db.scalars(select(Listing).where(Listing.external_id == "C0")).one()
    assert comparable.product_id is not None
    assert part.product_id is None or part.product_id != comparable.product_id
    assert not [o for o in db.scalars(select(Opportunity)).all() if o.buy_listing.external_id == "X"]
