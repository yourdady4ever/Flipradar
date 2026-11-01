"""End-to-end check of a RUNNING FLIP RADAR server (exercises every main workflow over HTTP).

Usage (from backend/):
    python scripts/e2e_check.py --url http://127.0.0.1:8000 --token fr_xxx [--mutate]

Without ``--mutate`` only read-only endpoints are called.  ``--mutate`` additionally creates and deletes
inventory, sales, alerts, searches and a CSV import - use it only on a throwaway/demo database.
Exit code 0 = every check passed.
"""

from __future__ import annotations

import argparse
import io
import sys
from typing import Any

import httpx

sys.path.insert(0, __import__("os").path.dirname(__import__("os").path.dirname(__file__)))

from flipradar.finance.calculator import FeeModel, ProfitInputs, calculate_profit  # noqa: E402


class Checker:
    def __init__(self, url: str, token: str) -> None:
        self.http = httpx.Client(base_url=url.rstrip("/"), headers={"Authorization": f"Bearer {token}"}, timeout=60,
                                 trust_env=False)
        self.failures: list[str] = []
        self.passed = 0

    def check(self, name: str, condition: bool, detail: Any = "") -> bool:
        if condition:
            self.passed += 1
            print(f"PASS  {name}")
        else:
            self.failures.append(name)
            print(f"FAIL  {name}  {str(detail)[:400]}")
        return condition

    def call(self, method: str, path: str, expect: int = 200, **kw: Any) -> Any:
        r = self.http.request(method, path, **kw)
        ok = self.check(f"{method} {path} -> {expect}", r.status_code == expect, f"got {r.status_code}: {r.text[:300]}")
        if not ok:
            return None
        return r.json() if r.content and r.headers.get("content-type", "").startswith("application/json") else r.text


def run(c: Checker, mutate: bool) -> None:
    # --- meta / auth -------------------------------------------------------------------------------
    health = c.call("GET", "/health")
    c.check("health ok", bool(health) and health["status"] == "ok", health)
    r = httpx.get(str(c.http.base_url) + "/api/v1/opportunities", timeout=30, trust_env=False)
    c.check("auth required without token", r.status_code == 401 or not health.get("auth_required", True), r.status_code)
    c.call("GET", "/api/v1/auth/check")
    page = c.call("GET", "/")
    c.check("dashboard html served", isinstance(page, str) and "<html" in page.lower(), str(page)[:80])
    sources = c.call("GET", "/api/v1/sources") or []
    c.check("sources listed", any(s["key"] == "mercadolibre" for s in sources), [s["key"] for s in sources])
    settings = c.call("GET", "/api/v1/settings") or {}
    profiles = {p["key"]: p for p in (c.call("GET", "/api/v1/fee-profiles") or [])}
    c.check("builtin fee profiles", {"ml_clasica", "ml_clasica_used", "facebook_local"} <= set(profiles), list(profiles))

    # --- opportunities -----------------------------------------------------------------------------
    opps = c.call("GET", "/api/v1/opportunities", params={"limit": 50, "include_suspicious": "true"}) or {"items": []}
    items = opps["items"]
    c.check("opportunities present (seed demo data or collect first)", len(items) > 0, opps.get("total"))
    visible = (c.call("GET", "/api/v1/opportunities", params={"limit": 50}) or {"items": []})["items"]
    c.check("suspicious hidden by default", all(o["risk_level"] != "critical" for o in visible))
    for o in items[:5]:
        d = c.call("GET", f"/api/v1/opportunities/{o['id']}")
        if not d:
            continue
        fee = profiles.get(d["fee_profile_key"])
        if fee:
            model = FeeModel(
                key=fee["key"], name=fee["name"], commission_bp=fee["commission_bp"],
                fixed_fee_tiers=tuple(__import__("flipradar.finance.calculator", fromlist=["FixedFeeTier"]).FixedFeeTier(
                    t["max_price_cents"], t["fee_cents"]) for t in fee["fixed_fee_tiers"]),
                payment_fee_bp=fee["payment_fee_bp"], payment_fixed_cents=fee["payment_fixed_cents"],
                iva_on_fees=fee["iva_on_fees"], iva_rate_bp=fee["iva_rate_bp"],
                seller_shipping_cents=fee["seller_shipping_cents"],
                free_shipping_threshold_cents=fee["free_shipping_threshold_cents"],
                isr_withholding_bp=fee["isr_withholding_bp"], iva_withholding_bp=fee["iva_withholding_bp"],
            )
            acq = {line["key"]: line["amount_cents"] for line in d["cost_breakdown"]["acquisition_lines"]}
            local = calculate_profit(ProfitInputs(buy_price_cents=acq["buy_price"], buy_shipping_cents=acq["buy_shipping"],
                                                  sale_price_cents=d["expected_sale_cents"], fee=model))
            c.check(f"opportunity {o['id']} profit reproducible locally", local.net_profit_cents == d["net_profit_cents"],
                    (local.net_profit_cents, d["net_profit_cents"]))
        c.check(f"opportunity {o['id']} explained", len(d["explanation"]) > 100 and d["facts"], d["explanation"][:80])
        c.check(f"opportunity {o['id']} has evidence", d["price_stats"]["n"] >= settings.get("min_comparables", 3),
                d["price_stats"]["n"])
        c.check(f"opportunity {o['id']} comparables listed", len(d["comparables"]) == d["price_stats"]["n"],
                (len(d["comparables"]), d["price_stats"]["n"]))

    # --- calculator --------------------------------------------------------------------------------
    calc = c.call("POST", "/api/v1/calculator/profit", json={
        "buy_price_cents": 450000, "sale_price_cents": 620000, "fee_profile_key": "ml_clasica", "target_roi_bp": 2000})
    if calc:
        c.check("calculator break-even below sale", calc["break_even_sale_cents"] < 620000, calc["break_even_sale_cents"])
        c.check("calculator max buy computed", calc["max_buy_for_target_cents"] is not None)
    c.call("POST", "/api/v1/calculator/import-costs",
           json={"item_value_usd_cents": 10000, "shipping_usd_cents": 1500, "rule_key": "courier_usmca", "usd_mxn_rate": "18.00"})

    # --- analytics / lists ---------------------------------------------------------------------------
    summary = c.call("GET", "/api/v1/analytics/summary", params={"days": 30})
    c.check("analytics summary", summary is not None and "realized_profit_cents" in summary)
    c.call("GET", "/api/v1/analytics/timeseries", params={"days": 90, "bucket": "week"})
    listings = c.call("GET", "/api/v1/listings", params={"limit": 5}) or {"items": []}
    if listings["items"]:
        c.call("GET", f"/api/v1/listings/{listings['items'][0]['id']}")
    products = c.call("GET", "/api/v1/products", params={"limit": 5}) or {"items": []}
    if products["items"]:
        c.call("GET", f"/api/v1/products/{products['items'][0]['id']}")
    c.call("GET", "/api/v1/notifications", params={"limit": 10})
    c.call("GET", "/api/v1/runs")

    if not mutate:
        return

    # --- buy -> inventory -> sale -> analytics ------------------------------------------------------
    target = next((o for o in visible if o["status"] in ("new", "seen", "watching")), None)
    if c.check("an open opportunity to buy", target is not None):
        inv = c.call("POST", f"/api/v1/opportunities/{target['id']}/buy", expect=201, json={"quantity": 1})
        if inv:
            sale = c.call("POST", f"/api/v1/inventory/{inv['id']}/sales", expect=201,
                          json={"quantity": 1, "unit_price_cents": target["expected_sale_cents"],
                                "fee_profile_key": target["fee_profile_key"]})
            if sale:
                c.check("realized profit equals predicted profit", sale["profit_cents"] == target["net_profit_cents"],
                        (sale["profit_cents"], target["net_profit_cents"]))
                after = c.call("GET", "/api/v1/analytics/summary", params={"days": 30})
                c.check("analytics counts the sale", after and after["units_sold"] >= 1, after)
                c.call("DELETE", f"/api/v1/sales/{sale['id']}")
            c.call("DELETE", f"/api/v1/inventory/{inv['id']}")
        c.call("PATCH", f"/api/v1/opportunities/{target['id']}", json={"status": "new"})

    # --- alerts -> notifications -----------------------------------------------------------------
    rule = c.call("POST", "/api/v1/alerts", expect=201, json={"name": "e2e any profit", "min_profit_cents": 1})
    c.call("POST", "/api/v1/analyze/recompute")
    if rule:
        c.call("DELETE", f"/api/v1/alerts/{rule['id']}")

    # --- tracked search + collect (sources without keys are reported as skipped, not failed) ------------
    search = c.call("POST", "/api/v1/searches", expect=201, json={"query": "iphone 15 pro", "source_keys": []})
    if search:
        res = c.call("POST", f"/api/v1/searches/{search['id']}/run")
        c.check("collection run recorded", res is not None and len(res["runs"]) >= 1, res)
        c.call("DELETE", f"/api/v1/searches/{search['id']}")

    # --- CSV import -> normalized + clustered --------------------------------------------------------
    csv_text = (
        "title,price,condition,url,seller\n"
        "Apple iPhone 13 128GB Azul,8999.00,usado,https://example.com/a,e2e-a\n"
        "iPhone 13 128 GB azul seminuevo,9300,usado,https://example.com/b,e2e-b\n"
        "Funda para iPhone 13,199,nuevo,https://example.com/c,e2e-c\n"
        ",100,nuevo,,\n"
    )
    res = c.call("POST", "/api/v1/import/csv", params={"source": "e2e_csv"},
                 files={"file": ("e2e.csv", io.BytesIO(csv_text.encode()), "text/csv")})
    if res:
        c.check("csv rows imported/rejected", res["rows_imported"] + res["rows_updated"] == 3 and res["rows_rejected"] == 1, res)

    # --- SSRF guard on URL check -----------------------------------------------------------------
    for target_url in ("http://127.0.0.1/health", "http://169.254.169.254/latest/meta-data/", "http://10.0.0.1/"):
        r = c.http.post("/api/v1/check-url", json={"url": target_url})
        refused = r.status_code in (400, 422) or (r.status_code == 200 and not r.json().get("found"))
        c.check(f"check-url refuses private address {target_url}", refused, (r.status_code, r.text[:200]))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--url", default="http://127.0.0.1:8000")
    parser.add_argument("--token", required=True)
    parser.add_argument("--mutate", action="store_true", help="also create/delete data (throwaway DB only)")
    args = parser.parse_args()
    checker = Checker(args.url, args.token)
    try:
        run(checker, args.mutate)
    except httpx.HTTPError as exc:
        checker.failures.append(f"transport error: {exc}")
    print(f"\n{checker.passed} passed, {len(checker.failures)} failed")
    for name in checker.failures:
        print(f"  - {name}")
    return 1 if checker.failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
