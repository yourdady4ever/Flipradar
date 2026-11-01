"""Purchase -> sale -> profit -> analytics, plus sale deletion and validation."""

import dataclasses
from datetime import UTC, datetime, timedelta

from flipradar.finance.calculator import FeeModel, ProfitInputs, calculate_profit
from flipradar.services.fees import BUILTIN_FEE_PROFILES


class _Row:
    def __init__(self, data):
        self.__dict__.update(data)


ML = FeeModel.from_profile(_Row(next(p for p in BUILTIN_FEE_PROFILES if p["key"] == "ml_clasica")))


def _create(api, **overrides):
    body = {"title": "PS5 Slim 1TB", "quantity": 2, "unit_cost_cents": 800_000, "extra_costs_cents": 20_000,
            "condition": "used", "status": "in_stock", "target_sale_cents": 1_100_000,
            "purchased_at": (datetime.now(UTC) - timedelta(days=10)).isoformat(), **overrides}
    r = api.post("/api/v1/inventory", json=body)
    assert r.status_code == 201, r.text
    return r.json()


def test_purchase_sale_profit_and_analytics(api):
    item = _create(api)
    assert item["quantity_available"] == 2 and item["total_cost_cents"] == 1_620_000
    assert item["days_held"] == 10 and item["realized_profit_cents"] == 0

    r = api.post(f"/api/v1/inventory/{item['id']}/sales", json={
        "quantity": 1, "unit_price_cents": 1_100_000, "fee_profile_key": "ml_clasica", "shipping_cents": 15_000,
        "channel": "mercadolibre"})
    assert r.status_code == 201, r.text
    sale = r.json()
    # The shipping actually paid (15,000) replaces the profile's per-unit shipping tier (6,550): it is not
    # added on top of it (Mercado Libre models the seller's shipping charge as the fixed-fee tier).
    per_unit = calculate_profit(ProfitInputs(buy_price_cents=810_000, sale_price_cents=1_100_000,
                                             fee=dataclasses.replace(ML, fixed_fee_tiers=()),
                                             seller_shipping_override_cents=0))
    lines = {line.key: line.amount_cents for line in per_unit.selling_lines}
    fees = lines["commission"] + lines.get("fixed_fee", 0) + lines["payment_fee"] + lines["iva_on_fees"]
    taxes = lines["isr_withheld"] + lines["iva_withheld"]
    assert sale["fees_cents"] == fees == 165_000
    assert sale["taxes_withheld_cents"] == taxes
    assert sale["cost_basis_cents"] == 810_000
    assert sale["net_proceeds_cents"] == 1_100_000 - fees - 15_000 - taxes
    assert sale["profit_cents"] == sale["net_proceeds_cents"] - 810_000

    item_now = api.get(f"/api/v1/inventory/{item['id']}").json()
    assert item_now["quantity_available"] == 1 and item_now["status"] == "in_stock"
    assert item_now["realized_profit_cents"] == sale["profit_cents"]

    assert api.post(f"/api/v1/inventory/{item['id']}/sales", json={"quantity": 2, "unit_price_cents": 1}).status_code == 409

    r = api.post(f"/api/v1/inventory/{item['id']}/sales", json={
        "quantity": 1, "unit_price_cents": 1_050_000, "fees_cents": 50_000, "taxes_withheld_cents": 0})
    assert r.status_code == 201
    sale2 = r.json()
    assert sale2["profit_cents"] == 1_050_000 - 50_000 - 810_000
    sold = api.get(f"/api/v1/inventory/{item['id']}").json()
    assert sold["status"] == "sold" and sold["quantity_available"] == 0 and len(sold["sales"]) == 2

    summary = api.get("/api/v1/analytics/summary?days=30").json()
    total_profit = sale["profit_cents"] + sale2["profit_cents"]
    assert summary["revenue_cents"] == 2_150_000
    assert summary["realized_profit_cents"] == total_profit
    assert summary["units_sold"] == 2 and summary["sales_count"] == 2
    assert summary["avg_roi_bp"] == round(total_profit * 10000 / 1_620_000)
    assert summary["avg_days_to_sell"] == 10.0
    assert summary["inventory_units"] == 0 and summary["aging"] == {"0-7": 0, "8-30": 0, "31-90": 0, "90+": 0}
    assert summary["top_products_by_profit"][0]["title"] == "PS5 Slim 1TB"
    assert summary["open_opportunities"] == 0 and summary["potential_profit_open_cents"] == 0

    ts = api.get("/api/v1/analytics/timeseries?days=30&bucket=day").json()
    assert ts["bucket"] == "day"
    points = {p["period"]: p for p in ts["points"]}
    today = datetime.now(UTC).date().isoformat()
    purchase_day = (datetime.now(UTC) - timedelta(days=10)).date().isoformat()
    assert points[today]["revenue_cents"] == 2_150_000 and points[today]["units"] == 2
    assert points[today]["profit_cents"] == total_profit
    assert points[purchase_day]["purchases_cents"] == 1_620_000
    assert sum(p["revenue_cents"] for p in ts["points"]) == 2_150_000
    weekly = api.get("/api/v1/analytics/timeseries?days=90&bucket=week").json()
    assert sum(p["revenue_cents"] for p in weekly["points"]) == 2_150_000
    monthly = api.get("/api/v1/analytics/timeseries?days=400&bucket=month").json()
    assert all(p["period"].endswith("-01") for p in monthly["points"])

    # Deleting a sale restores the unit and the status.
    sales = api.get("/api/v1/sales").json()
    assert sales["total"] == 2
    r = api.delete(f"/api/v1/sales/{sale2['id']}")
    assert r.status_code == 200
    restored = api.get(f"/api/v1/inventory/{item['id']}").json()
    assert restored["quantity_available"] == 1 and restored["status"] == "in_stock"
    assert api.delete(f"/api/v1/sales/{sale2['id']}").status_code == 404

    summary = api.get("/api/v1/analytics/summary?days=30").json()
    assert summary["inventory_units"] == 1 and summary["inventory_cost_cents"] == 810_000
    assert summary["aging"]["8-30"] == 1 and summary["inventory_target_value_cents"] == 1_100_000


def test_inventory_validation_and_404(api):
    assert api.post("/api/v1/inventory", json={"title": "", "unit_cost_cents": 1}).status_code == 422
    assert api.post("/api/v1/inventory", json={"title": "x", "unit_cost_cents": -1}).status_code == 422
    assert api.post("/api/v1/inventory", json={"title": "x", "unit_cost_cents": 1, "product_id": 999}).status_code == 422
    assert api.post("/api/v1/inventory", json={"title": "x", "unit_cost_cents": 1,
                                               "purchase_url": "javascript:alert(1)"}).status_code == 422
    assert api.get("/api/v1/inventory/999").status_code == 404
    assert api.patch("/api/v1/inventory/999", json={"notes": "x"}).status_code == 404
    assert api.delete("/api/v1/inventory/999").status_code == 404
    assert api.post("/api/v1/inventory/999/sales", json={"unit_price_cents": 1}).status_code == 404
    item = _create(api, quantity=1)
    assert api.post(f"/api/v1/inventory/{item['id']}/sales",
                    json={"unit_price_cents": 1, "fee_profile_key": "nope"}).status_code == 422
    assert api.post(f"/api/v1/inventory/{item['id']}/sales", json={"unit_price_cents": 1, "quantity": 0}).status_code == 422
    assert api.get("/api/v1/inventory?limit=500").status_code == 422
    assert api.get("/api/v1/inventory?status=bogus").status_code == 422


def test_inventory_update_list_and_delete(api):
    a = _create(api, title="Switch OLED", sku="SW-1")
    _create(api, title="Steam Deck")
    r = api.patch(f"/api/v1/inventory/{a['id']}", json={"status": "listed", "listed_channel": "mercadolibre",
                                                        "unit_cost_cents": 700_000})
    assert r.status_code == 200 and r.json()["status"] == "listed" and r.json()["total_cost_cents"] == 1_420_000
    assert api.patch(f"/api/v1/inventory/{a['id']}", json={"title": None}).status_code == 422
    page = api.get("/api/v1/inventory?status=listed").json()
    assert page["total"] == 1 and page["items"][0]["id"] == a["id"]
    assert api.get("/api/v1/inventory?q=sw-1").json()["total"] == 1
    assert api.get("/api/v1/inventory?limit=1&offset=1").json()["total"] == 2
    assert api.delete(f"/api/v1/inventory/{a['id']}").status_code == 200
    assert api.get("/api/v1/inventory").json()["total"] == 1


def test_returned_item_cannot_be_sold(api):
    item = _create(api, quantity=1)
    api.patch(f"/api/v1/inventory/{item['id']}", json={"status": "returned"})
    r = api.post(f"/api/v1/inventory/{item['id']}/sales", json={"unit_price_cents": 100})
    assert r.status_code == 409
