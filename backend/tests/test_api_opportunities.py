"""Opportunity list filters/sorting/pagination on fabricated rows (no engine involved)."""

from tests.test_api_alerts import fabricate_opportunity

WARNING = [{"code": "price_far_below_market", "severity": "warning", "message": "outlier \"critical\" text"}]
CRITICAL = [{"code": "replica_or_counterfeit", "severity": "critical", "message": "replica"}]


def test_risk_filter_hides_suspicious_but_not_single_warnings(api, db):
    ok = fabricate_opportunity(db, score=50)
    warn = fabricate_opportunity(db, flags=WARNING, suspicious=True, score=60)
    crit = fabricate_opportunity(db, flags=CRITICAL, suspicious=True, score=70)
    page = api.get("/api/v1/opportunities").json()
    assert [o["id"] for o in page["items"]] == [warn.id, ok.id] and page["total"] == 2
    assert page["items"][0]["risk_level"] == "warning" and page["items"][1]["risk_level"] == "none"
    everything = api.get("/api/v1/opportunities?include_suspicious=true").json()
    assert [o["id"] for o in everything["items"]] == [crit.id, warn.id, ok.id]
    assert everything["items"][0]["risk_level"] == "critical"
    summary = api.get("/api/v1/analytics/summary").json()
    assert summary["open_opportunities"] == 2 and summary["potential_profit_open_cents"] == 2 * 235_000
    assert summary["opportunities_by_source"] == {"manual": 2}


def test_status_sort_pagination_and_demo(api, db):
    a = fabricate_opportunity(db, profit=100_000, roi=1000, score=90)
    b = fabricate_opportunity(db, profit=300_000, roi=3000, score=40)
    c = fabricate_opportunity(db, profit=200_000, roi=5000, score=60, status="dismissed")
    d = fabricate_opportunity(db, status="expired")
    demo = fabricate_opportunity(db, is_demo=True)
    assert [o["id"] for o in api.get("/api/v1/opportunities").json()["items"]] == [a.id, b.id]
    assert [o["id"] for o in api.get("/api/v1/opportunities?sort=profit").json()["items"]] == [b.id, a.id]
    assert {o["id"] for o in api.get("/api/v1/opportunities?status=dismissed,expired").json()["items"]} == {c.id, d.id}
    statuses = {o["id"] for o in api.get("/api/v1/opportunities?status=all").json()["items"]}
    assert statuses == {a.id, b.id, c.id, d.id}
    assert api.get("/api/v1/opportunities?status=all&include_demo=true").json()["total"] == 5
    assert demo.id in {o["id"] for o in api.get("/api/v1/opportunities?include_demo=true").json()["items"]}
    page = api.get("/api/v1/opportunities?status=all&sort=roi&limit=2&offset=1").json()
    assert page["total"] == 4 and page["limit"] == 2 and page["offset"] == 1
    assert [o["id"] for o in page["items"]] == [b.id, d.id]  # roi: c 5000, b 3000, d 2350, a 1000
    assert api.get("/api/v1/opportunities?min_score=50&status=all").json()["total"] == 3
    assert api.get("/api/v1/opportunities?min_roi_bp=3000&status=all").json()["total"] == 2
    assert api.get("/api/v1/opportunities?limit=201").status_code == 422
    assert api.get("/api/v1/opportunities?offset=-1").status_code == 422


def test_detail_tolerates_minimal_rows(api, db):
    opp = fabricate_opportunity(db)
    d = api.get(f"/api/v1/opportunities/{opp.id}").json()
    assert d["price_stats"]["n"] == 7 and d["cost_breakdown"]["net_profit_cents"] == 235_000
    assert d["demand"] == {"level": "unknown", "confidence": None, "signals": [], "note": ""}
    assert d["comparables"] == [] and d["score_components"] == []
