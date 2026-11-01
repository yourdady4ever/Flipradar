from flipradar.services.fees import BUILTIN_KEYS

CUSTOM = {
    "key": "tianguis", "name": "Tianguis", "channel": "local", "commission_bp": 0,
    "fixed_fee_tiers": [{"max_price_cents": None, "fee_cents": 5_000}], "notes": "puesto",
}


def test_builtin_profiles_seeded(api):
    r = api.get("/api/v1/fee-profiles")
    assert r.status_code == 200
    profiles = {p["key"]: p for p in r.json()}
    assert BUILTIN_KEYS <= set(profiles)
    ml = profiles["ml_clasica"]
    assert ml["is_builtin"] and ml["commission_bp"] == 1500 and ml["isr_withholding_bp"] == 250
    assert ml["iva_withholding_bp"] == 800 and ml["as_of"] == "2026-10" and "PROVISIONAL" in ml["notes"]
    assert ml["fixed_fee_tiers"][0] == {"max_price_cents": 9899, "fee_cents": 2500}
    assert ml["fixed_fee_tiers"][-1] == {"max_price_cents": None, "fee_cents": 6550}
    assert profiles["ml_premium"]["commission_bp"] == 1950
    mp = profiles["mercadopago_link"]
    assert mp["payment_fee_bp"] == 349 and mp["payment_fixed_cents"] == 400 and mp["iva_on_fees"] is True
    for key in ("facebook_local", "local_cash"):
        assert profiles[key]["commission_bp"] == 0 and profiles[key]["isr_withholding_bp"] == 0


def test_fee_profile_crud(api):
    r = api.post("/api/v1/fee-profiles", json=CUSTOM)
    assert r.status_code == 201
    assert r.json()["is_builtin"] is False and r.json()["fixed_fee_tiers"] == CUSTOM["fixed_fee_tiers"]
    assert api.post("/api/v1/fee-profiles", json=CUSTOM).status_code == 409
    assert api.post("/api/v1/fee-profiles", json={**CUSTOM, "key": "Bad Key"}).status_code == 422

    r = api.put("/api/v1/fee-profiles/tianguis", json={**CUSTOM, "commission_bp": 300})
    assert r.status_code == 200 and r.json()["commission_bp"] == 300
    assert api.put("/api/v1/fee-profiles/tianguis", json={**CUSTOM, "key": "other"}).status_code == 422
    assert api.put("/api/v1/fee-profiles/missing", json={**CUSTOM, "key": "missing"}).status_code == 404

    calc = api.post("/api/v1/calculator/profit", json={"buy_price_cents": 10_000, "sale_price_cents": 20_000,
                                                      "fee_profile_key": "tianguis"})
    assert calc.json()["result"]["total_selling_costs_cents"] == 600 + 5_000

    api.put("/api/v1/settings", json={"default_fee_profile_key": "tianguis"})
    assert api.delete("/api/v1/fee-profiles/tianguis").status_code == 409  # it is the default
    api.put("/api/v1/settings", json={"default_fee_profile_key": "ml_clasica"})
    assert api.delete("/api/v1/fee-profiles/tianguis").status_code == 200
    assert api.delete("/api/v1/fee-profiles/tianguis").status_code == 404


def test_builtin_can_be_edited_but_not_deleted(api, db):
    r = api.put("/api/v1/fee-profiles/ml_clasica", json={
        "key": "ml_clasica", "name": "ML clasica (mine)", "channel": "mercadolibre", "commission_bp": 1600,
        "fixed_fee_tiers": [{"max_price_cents": 29899, "fee_cents": 2500}], "isr_withholding_bp": 250,
        "iva_withholding_bp": 800,
    })
    assert r.status_code == 200 and r.json()["is_builtin"] is True and r.json()["commission_bp"] == 1600
    assert api.delete("/api/v1/fee-profiles/ml_clasica").status_code == 409
    # Re-seeding (app restart) never overwrites the user's edit.
    from flipradar.services.fees import seed_fee_profiles

    assert seed_fee_profiles(db) == 0
    db.commit()
    profile = next(p for p in api.get("/api/v1/fee-profiles").json() if p["key"] == "ml_clasica")
    assert profile["commission_bp"] == 1600
