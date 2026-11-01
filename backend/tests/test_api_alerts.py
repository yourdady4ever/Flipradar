"""Alert rules CRUD, rule matching -> notifications, and the notification read flow."""

from sqlalchemy import select

from flipradar.models import Listing, Notification, Opportunity, Product, Source
from flipradar.services import alerts


def fabricate_opportunity(db, *, title="Apple iPhone 15 Pro 256GB", listing_title="iPhone 15 Pro 256 GB Titanio",
                          profit=235_000, roi=2350, score=78.5, confidence="high", flags=None,
                          suspicious=False, is_demo=False, status="new", key="apple|iphone 15 pro|256gb") -> Opportunity:
    source = db.scalars(select(Source).where(Source.key == "manual")).one()
    product = db.scalars(select(Product).where(Product.canonical_key == key)).first()
    if product is None:
        product = Product(canonical_key=key, title=title, brand="apple", model="iphone 15 pro", category="phone")
        db.add(product)
        db.flush()
    listing = Listing(source_id=source.id, external_id=f"x-{db.query(Listing).count() + 1}", title=listing_title,
                      price_cents=1_000_000, price_mxn_cents=1_000_000, shipping_cents=0, condition="used",
                      brand="apple", category="phone", product_id=product.id, is_suspicious=suspicious,
                      is_demo=is_demo, url="https://example.com/item/1")
    db.add(listing)
    db.flush()
    opp = Opportunity(product_id=product.id, buy_listing_id=listing.id, fee_profile_key="ml_clasica",
                      buy_total_cents=1_000_000, expected_sale_cents=1_350_000, net_profit_cents=profit, roi_bp=roi,
                      margin_bp=1741, score=score, confidence=confidence, comparable_count=7,
                      risk_flags_json=flags or [], status=status, is_demo=is_demo,
                      explanation_facts_json={"net_profit_cents": profit})
    db.add(opp)
    db.commit()
    return opp


def test_alert_rule_crud(api):
    r = api.post("/api/v1/alerts", json={"name": "iPhones", "keywords": "iphone pro", "min_profit_cents": 100_000,
                                         "min_confidence": "medium", "channels": ["in_app", "ntfy", "ntfy"]})
    assert r.status_code == 201, r.text
    rule = r.json()
    assert rule["channels"] == ["in_app", "ntfy"] and rule["exclude_suspicious"] is True and rule["enabled"] is True
    assert api.post("/api/v1/alerts", json={"name": "", "keywords": "x"}).status_code == 422
    assert api.post("/api/v1/alerts", json={"name": "x", "channels": ["sms"]}).status_code == 422
    assert api.post("/api/v1/alerts", json={"name": "x", "min_confidence": "great"}).status_code == 422

    r = api.patch(f"/api/v1/alerts/{rule['id']}", json={"min_score": 60, "enabled": False})
    assert r.status_code == 200
    assert r.json()["min_score"] == 60 and r.json()["enabled"] is False and r.json()["keywords"] == "iphone pro"
    assert api.patch(f"/api/v1/alerts/{rule['id']}", json={"min_score": 500}).status_code == 422
    assert api.patch(f"/api/v1/alerts/{rule['id']}", json={"name": None}).status_code == 422
    assert api.patch("/api/v1/alerts/999", json={"enabled": True}).status_code == 404
    assert len(api.get("/api/v1/alerts").json()) == 1
    assert api.delete(f"/api/v1/alerts/{rule['id']}").status_code == 200
    assert api.delete(f"/api/v1/alerts/{rule['id']}").status_code == 404


def test_rule_matching_creates_one_notification(api, db, settings):
    api.post("/api/v1/alerts", json={"name": "iPhones", "keywords": "IPHONE titanio", "brand": "Apple",
                                     "category": "PHONE", "min_profit_cents": 200_000, "min_roi_bp": 2000,
                                     "min_score": 70, "max_buy_cents": 1_200_000, "min_confidence": "medium"})
    opp = fabricate_opportunity(db)
    created = alerts.evaluate(db, [opp], settings)
    db.commit()
    assert len(created) == 1
    note = created[0]
    assert note.title == "Apple iPhone 15 Pro 256GB: +$2,350.00 (ROI 23.5%)"
    assert "Source: manual" in note.body and "Buy: $10,000.00" in note.body
    assert "Expected sale: $13,500.00" in note.body and "Score: 78.5/100" in note.body and "Confidence: high" in note.body
    assert note.delivery_json == {"in_app": "stored"}
    # Re-evaluating never duplicates.
    assert alerts.evaluate(db, [opp], settings) == []
    assert db.query(Notification).count() == 1


def test_rule_thresholds_and_exclusions(api, db, settings):
    api.post("/api/v1/alerts", json={"name": "Strict", "min_profit_cents": 100_000, "min_confidence": "high"})
    low_profit = fabricate_opportunity(db, profit=50_000)
    low_conf = fabricate_opportunity(db, confidence="medium")
    suspicious = fabricate_opportunity(db, suspicious=True)
    flagged = fabricate_opportunity(db, flags=[{"code": "seller_new_or_unrated", "severity": "warning", "message": "x"}])
    info_only = fabricate_opportunity(db, flags=[{"code": "shipping_unknown", "severity": "info", "message": "x"}])
    dismissed = fabricate_opportunity(db, status="dismissed")
    demo = fabricate_opportunity(db, is_demo=True)
    created = alerts.evaluate(db, [low_profit, low_conf, suspicious, flagged, info_only, dismissed, demo], settings)
    assert [n.opportunity_id for n in created] == [info_only.id]


def test_keywords_are_accent_insensitive_and_all_required(db, settings, api):
    api.post("/api/v1/alerts", json={"name": "consolas", "keywords": "pláystation slim"})
    match = fabricate_opportunity(db, title="Sony PlayStation 5 Slim", listing_title="PS5 slim", key="sony|ps5 slim")
    other = fabricate_opportunity(db, title="Sony PlayStation 5", listing_title="PS5 fat", key="sony|ps5")
    created = alerts.evaluate(db, [match, other], settings)
    assert [n.opportunity_id for n in created] == [match.id]


def test_disabled_rules_are_ignored(api, db, settings):
    api.post("/api/v1/alerts", json={"name": "off", "enabled": False})
    assert alerts.evaluate(db, [fabricate_opportunity(db)], settings) == []


def test_notifications_read_flow(api, db, settings):
    api.post("/api/v1/alerts", json={"name": "all"})
    opps = [fabricate_opportunity(db) for _ in range(3)]
    alerts.evaluate(db, opps, settings)
    db.commit()
    notes = api.get("/api/v1/notifications").json()
    assert len(notes) == 3 and notes[0]["id"] > notes[1]["id"] > notes[2]["id"]  # newest first
    assert all(n["read_at"] is None for n in notes)
    first = notes[-1]["id"]
    assert len(api.get(f"/api/v1/notifications?since_id={first}").json()) == 2
    assert len(api.get("/api/v1/notifications?limit=1").json()) == 1
    assert api.post(f"/api/v1/notifications/{first}/read").status_code == 200
    assert len(api.get("/api/v1/notifications?unread_only=true").json()) == 2
    assert api.post("/api/v1/notifications/999/read").status_code == 404
    r = api.post("/api/v1/notifications/read-all")
    assert r.status_code == 200 and r.json()["message"].startswith("2 ")
    assert api.get("/api/v1/notifications?unread_only=true").json() == []
