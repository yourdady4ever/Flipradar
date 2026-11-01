"""CSV import: good rows imported, bad rows rejected individually with line numbers."""

import pytest
from sqlalchemy import select

from flipradar.models import Listing, PriceObservation, Source

pytestmark = pytest.mark.usefixtures("fake_pipeline")

GOOD_AND_BAD = (
    "Title,Price,Currency,URL,Condition,Shipping,Brand,Model,Seller,Sold_Quantity,Observed_At\n"
    "iPhone 15 Pro usado,\"$15,000.00\",MXN,https://example.com/a,usado,0,Apple,iPhone 15 Pro,ana,3,2026-10-20\n"
    "iPhone 15 Pro usado b,15500,,,used,150,Apple,iPhone 15 Pro,beto,,\n"
    "iPhone 15 Pro usado c,16000,,javascript:alert(1),used,,Apple,iPhone 15 Pro,carla,,\n"
    "iPhone 15 Pro usado d,9500,,,used,0,Apple,iPhone 15 Pro,dani,,\n"
    ",1000,,,,,,,,,\n"
    "Sin precio,,,,,,,,,,\n"
    "Precio malo,abc,,,,,,,,,\n"
    "Negativo,-5,,,,,,,,,\n"
    "Moneda mala,100,PESOS,,,,,,,,\n"
    "Fecha mala,100,,,,,,,,,ayer\n"
    "Cantidad mala,100,,,,,,,,x,\n"
    "USD item,100.50,USD,,new,,Apple,iPhone 15 Pro,ed,,\n"
)


def upload(api, content: bytes | str, name="precios.csv", **params):
    data = content.encode("utf-8") if isinstance(content, str) else content
    return api.post("/api/v1/import/csv", files={"file": (name, data, "text/csv")}, params=params)


def test_import_good_and_bad_rows(api, db):
    r = upload(api, GOOD_AND_BAD)
    assert r.status_code == 200, r.text
    result = r.json()
    assert result["rows_total"] == 12
    assert result["rows_imported"] == 5 and result["rows_updated"] == 0
    assert result["rows_rejected"] == 7
    errors = "\n".join(result["errors"])
    for expected in ("line 6: title is required", "line 7: price is required", "line 8: price is not a valid amount",
                     "line 9: price must be greater than zero", "line 10: invalid currency",
                     "line 11: observed_at is not an ISO date", "line 12: sold_quantity must be a whole number"):
        assert expected in errors

    listings = {row.title: row for row in db.scalars(select(Listing))}
    first = listings["iPhone 15 Pro usado"]
    assert first.price_cents == 1_500_000 and first.url == "https://example.com/a" and first.sold_quantity == 3
    assert first.source.key == "manual" and first.seller_name == "ana" and first.shipping_cents == 0
    assert listings["iPhone 15 Pro usado c"].url is None  # javascript: URL dropped
    usd = listings["USD item"]
    assert usd.currency == "USD" and usd.price_cents == 10_050 and usd.price_mxn_cents is None  # no FX rate
    # The deal at $9,500 is detected from imported data.
    opps = api.get("/api/v1/opportunities").json()
    assert opps["total"] == 1 and opps["items"][0]["buy_title"] == "iPhone 15 Pro usado d"


def test_reimport_appends_history_and_converts_usd(api, db):
    api.put("/api/v1/settings", json={"fx_usd_mxn_manual": "18.00"})
    csv_text = "title,price,currency,observed_at\nCamara usada,500,USD,2026-10-01\n"
    assert upload(api, csv_text).json()["rows_imported"] == 1
    listing = db.scalars(select(Listing)).one()
    assert listing.price_mxn_cents == 900_000 and listing.fx_source == "manual"
    later = "title,price,currency,observed_at\nCamara usada,450,USD,2026-10-15\n"
    assert upload(api, later).json()["rows_updated"] == 1
    older = "title,price,currency,observed_at\nCamara usada,520,USD,2026-09-01\n"
    assert upload(api, older).json()["rows_updated"] == 1
    db.expire_all()
    listing = db.scalars(select(Listing)).one()
    assert listing.price_cents == 45_000  # the older row did not overwrite the newer current price
    observations = db.scalars(select(PriceObservation).order_by(PriceObservation.observed_at)).all()
    assert [o.price_cents for o in observations] == [52_000, 50_000, 45_000]


def test_custom_source_and_semicolon_delimiter(api, db):
    r = upload(api, "titulo;title;price\nx;Bicicleta;2500\n", source="tianguis")
    assert r.status_code == 200 and r.json()["rows_imported"] == 1
    src = db.scalars(select(Source).where(Source.key == "tianguis")).one()
    assert src.kind == "manual"
    r = upload(api, "title,price,source\nCosa,100,demo\nOtra,100,Bad Key\n")
    assert r.json()["rows_rejected"] == 2


def test_import_rejects_bad_files(api):
    assert upload(api, "foo,bar\n1,2\n").status_code == 422
    assert upload(api, "").status_code == 422
    assert upload(api, "title,price\nCafé,10\n".encode("latin-1")).status_code == 422
    assert upload(api, b"title,price\n" + b"x" * (5 * 1024 * 1024 + 10)).status_code == 413
    assert upload(api, "title,price\nx,1\n", source="Bad Key").status_code == 422
    assert upload(api, "title,price\nx,1\n", source="demo").status_code == 422


def test_import_too_many_rows(api, monkeypatch):
    from flipradar.services import imports

    monkeypatch.setattr(imports, "MAX_ROWS", 3)
    r = upload(api, "title,price\n" + "".join(f"item {i},{i + 1}\n" for i in range(4)))
    assert r.status_code == 413


def test_import_requires_auth(client):
    r = client.post("/api/v1/import/csv", files={"file": ("a.csv", b"title,price\nx,1\n", "text/csv")})
    assert r.status_code == 401
