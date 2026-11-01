"""Health, sources, settings, FX, searches and the static dashboard."""

from flipradar.models import FxRate


def test_health_is_public(client):
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok" and body["api_version"] == "1"
    assert body["database"].startswith("sqlite")
    assert body["time"].endswith("Z") or "+00:00" in body["time"]
    assert client.head("/health").status_code == 200  # uptime monitors often use HEAD


def test_dashboard_root_served(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    assert "<html" in r.text.lower()
    assert client.head("/").status_code == 200


def test_dashboard_placeholder_when_template_missing(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from flipradar import main
    from tests.conftest import make_settings

    monkeypatch.setattr(main, "INDEX_HTML", tmp_path / "missing.html")
    monkeypatch.setattr(main, "STATIC_DIR", tmp_path / "missing-static")
    with TestClient(main.create_app(make_settings(tmp_path))) as c:
        r = c.get("/")
        assert r.status_code == 200 and "FLIP RADAR" in r.text
        assert c.get("/static/app.js").status_code == 404


def test_sources_list_patch_and_test(api):
    r = api.get("/api/v1/sources")
    assert r.status_code == 200
    by_key = {s["key"]: s for s in r.json()}
    for key in ("manual", "csv_import", "web_jsonld", "demo"):
        assert key in by_key and by_key[key]["configured"] is True
    assert by_key["web_jsonld"]["capabilities"] == ["url_check"]

    r = api.patch("/api/v1/sources/manual", json={"enabled": False, "default_fee_profile_key": "local_cash"})
    assert r.status_code == 200
    assert r.json()["enabled"] is False and r.json()["default_fee_profile_key"] == "local_cash"
    assert api.patch("/api/v1/sources/manual", json={"default_fee_profile_key": "nope"}).status_code == 422
    assert api.patch("/api/v1/sources/missing", json={"enabled": True}).status_code == 404

    r = api.post("/api/v1/sources/manual/test")
    assert r.status_code == 200 and r.json()["configured"] is True
    assert api.post("/api/v1/sources/missing/test").status_code == 404


def test_source_test_for_collectors(api, fake_collectors):
    r = api.post("/api/v1/sources/needscreds/test")
    assert r.status_code == 200
    assert r.json()["configured"] is False and r.json()["message"] == "not configured"
    by_key = {s["key"]: s for s in api.get("/api/v1/sources").json()}
    assert by_key["fakesource"]["configured"] is True
    assert by_key["needscreds"]["configured"] is False and by_key["needscreds"]["requires_credentials"] is True


def test_settings_get_and_update(api):
    r = api.get("/api/v1/settings")
    assert r.status_code == 200
    s = r.json()
    assert s["default_fee_profile_key"] == "ml_clasica"
    assert s["target_roi_bp"] == 2000 and s["min_profit_cents"] == 20000
    assert s["include_demo_data"] is False and s["fx_usd_mxn_manual"] is None
    assert s["ai_provider"] == "none" and s["scheduler_enabled"] is False

    r = api.put("/api/v1/settings", json={"min_comparables": 4, "fx_usd_mxn_manual": "17.25",
                                          "default_fee_profile_key": "ml_premium", "include_demo_data": True})
    assert r.status_code == 200
    s = r.json()
    assert s["min_comparables"] == 4 and s["fx_usd_mxn_manual"] == "17.25"
    assert s["default_fee_profile_key"] == "ml_premium" and s["include_demo_data"] is True
    assert api.get("/api/v1/settings").json() == s

    assert api.put("/api/v1/settings", json={"min_comparables": 1}).status_code == 422
    assert api.put("/api/v1/settings", json={"default_fee_profile_key": "nope"}).status_code == 422
    assert api.put("/api/v1/settings", json={"fx_usd_mxn_manual": "abc"}).status_code == 422
    assert api.put("/api/v1/settings", json={"fx_usd_mxn_manual": "-3"}).status_code == 422
    r = api.put("/api/v1/settings", json={"fx_usd_mxn_manual": ""})
    assert r.status_code == 200 and r.json()["fx_usd_mxn_manual"] is None


def test_fx_latest_manual_and_stored(api, db):
    assert api.get("/api/v1/fx/latest").status_code == 404
    api.put("/api/v1/settings", json={"fx_usd_mxn_manual": "18.10"})
    r = api.get("/api/v1/fx/latest")
    assert r.status_code == 200 and r.json()["source"] == "manual" and r.json()["rate"] == "18.10"
    from datetime import UTC, datetime

    today = datetime.now(UTC).date().isoformat()
    db.add(FxRate(base="USD", quote="MXN", rate_date=today, rate="17.4512", source="banxico"))
    db.commit()
    r = api.get("/api/v1/fx/latest")
    assert r.json() == {"base": "USD", "quote": "MXN", "rate": "17.4512", "rate_date": today, "source": "banxico",
                        "attribution": None}
    assert api.post("/api/v1/fx/refresh").status_code == 409  # no Banxico token and public sources disabled


def test_tracked_searches_crud(api, fake_collectors):
    r = api.post("/api/v1/searches", json={"query": "iphone 15 pro", "source_keys": ["fakesource"], "interval_minutes": 60})
    assert r.status_code == 201
    search = r.json()
    assert search["query"] == "iphone 15 pro" and search["last_run_at"] is None
    assert api.post("/api/v1/searches", json={"query": "x"}).status_code == 422
    assert api.post("/api/v1/searches", json={"query": "ok query", "source_keys": ["nope"]}).status_code == 422
    assert api.post("/api/v1/searches", json={"query": "ok query", "interval_minutes": 5}).status_code == 422

    r = api.patch(f"/api/v1/searches/{search['id']}", json={"enabled": False, "max_results": 10})
    assert r.status_code == 200 and r.json()["enabled"] is False and r.json()["max_results"] == 10
    assert api.patch("/api/v1/searches/999", json={"enabled": True}).status_code == 404
    assert [s["id"] for s in api.get("/api/v1/searches").json()] == [search["id"]]
    assert api.delete(f"/api/v1/searches/{search['id']}").status_code == 200
    assert api.delete(f"/api/v1/searches/{search['id']}").status_code == 404
    assert api.post("/api/v1/searches/999/run").status_code == 404


def test_openapi_lists_contract_paths(client):
    paths = client.get("/api/v1/openapi.json").json()["paths"]
    for path in ("/api/v1/opportunities", "/api/v1/calculator/profit", "/api/v1/import/csv", "/health",
                 "/api/v1/notifications/read-all", "/api/v1/check-url", "/api/v1/fx/latest"):
        assert path in paths
