from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from flipradar.models import ApiToken
from flipradar.security import (
    CONTENT_SECURITY_POLICY,
    RateLimiter,
    create_token,
    hash_token,
    list_tokens,
    revoke_token,
    verify_token,
)
from tests.conftest import make_settings


def test_token_is_hashed_and_verifiable(db):
    row, plain = create_token(db, "phone")
    db.commit()
    assert plain.startswith("fr_") and len(plain) > 40
    assert row.token_hash == hash_token(plain) and plain not in row.token_hash
    assert row.token_prefix == plain[:8]
    assert verify_token(db, plain).id == row.id
    assert verify_token(db, plain + "x") is None
    assert verify_token(db, "fr_" + "A" * 43) is None
    assert verify_token(db, "") is None and verify_token(db, None) is None
    assert verify_token(db, "not-a-token") is None


def test_last_used_is_throttled(db):
    row, plain = create_token(db, "laptop")
    t0 = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    verify_token(db, plain, now=t0)
    assert row.last_used_at == t0
    verify_token(db, plain, now=t0 + timedelta(seconds=30))
    assert row.last_used_at == t0  # not rewritten within 60 s
    verify_token(db, plain, now=t0 + timedelta(seconds=61))
    assert row.last_used_at == t0 + timedelta(seconds=61)


def test_revoke_and_list(db):
    row, plain = create_token(db, "old")
    _, other = create_token(db, "new")
    assert revoke_token(db, row.token_prefix) == 1
    assert verify_token(db, plain) is None
    assert verify_token(db, other) is not None
    assert revoke_token(db, row.token_prefix) == 0
    assert revoke_token(db, "") == 0
    names = {t.name: t.revoked for t in list_tokens(db)}
    assert names == {"old": True, "new": False}
    assert [t.name for t in list_tokens(db, include_revoked=False)] == ["new"]


def test_api_requires_bearer_token(client, token):
    r = client.get("/api/v1/auth/check")
    assert r.status_code == 401
    assert r.headers["www-authenticate"].startswith("Bearer")
    r = client.get("/api/v1/auth/check", headers={"Authorization": "Bearer fr_wrong"})
    assert r.status_code == 401 and 'error="invalid_token"' in r.headers["www-authenticate"]
    r = client.get("/api/v1/auth/check", headers={"Authorization": f"Basic {token}"})
    assert r.status_code == 401
    r = client.get("/api/v1/auth/check", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    assert r.json() == {"ok": True, "token_name": "pytest", "auth_required": True}


def test_revoked_token_is_rejected_by_api(client, token, db):
    row = db.query(ApiToken).one()
    revoke_token(db, row.token_prefix)
    db.commit()
    assert client.get("/api/v1/auth/check", headers={"Authorization": f"Bearer {token}"}).status_code == 401


def test_auth_can_be_disabled(tmp_path):
    from flipradar.main import create_app

    app = create_app(make_settings(tmp_path, require_auth=False))
    with TestClient(app) as c:
        r = c.get("/api/v1/auth/check")
        assert r.status_code == 200
        assert r.json() == {"ok": True, "token_name": None, "auth_required": False}


def test_security_headers_on_every_response(client):
    for path in ("/health", "/api/v1/sources", "/", "/does-not-exist"):
        r = client.get(path)
        assert r.headers["content-security-policy"] == CONTENT_SECURITY_POLICY
        assert r.headers["x-content-type-options"] == "nosniff"
        assert r.headers["referrer-policy"] == "no-referrer"
        assert r.headers["x-frame-options"] == "DENY"
    docs = client.get("/api/docs")
    assert docs.status_code == 200
    assert "cdn.jsdelivr.net" in docs.headers["content-security-policy"]


def test_rate_limiter_sliding_window():
    clock = [1000.0]
    limiter = RateLimiter(3, 60.0, clock=lambda: clock[0])
    assert all(limiter.hit("1.2.3.4").allowed for _ in range(3))
    denied = limiter.hit("1.2.3.4")
    assert not denied.allowed and 1 <= denied.retry_after <= 60
    assert limiter.hit("5.6.7.8").allowed  # other clients unaffected
    clock[0] += 61
    assert limiter.hit("1.2.3.4").allowed
    assert RateLimiter(0).hit("x").allowed  # disabled


def test_rate_limiter_caps_tracked_keys():
    limiter = RateLimiter(5, 60.0, max_keys=2, clock=lambda: 0.0)
    assert limiter.hit("a").allowed and limiter.hit("b").allowed
    assert not limiter.hit("c").allowed


def test_rate_limit_returns_429_with_retry_after(tmp_path):
    from flipradar.main import create_app

    app = create_app(make_settings(tmp_path, rate_limit_per_minute=3))
    with TestClient(app) as c:
        statuses = [c.get("/health").status_code for _ in range(4)]
        assert statuses == [200, 200, 200, 429]
        r = c.get("/api/v1/auth/check")
        assert r.status_code == 429
        assert int(r.headers["retry-after"]) >= 1
        assert r.headers["x-frame-options"] == "DENY"
        assert r.json()["detail"].startswith("Too many requests")


def test_cors_only_for_configured_origins(tmp_path):
    from flipradar.main import create_app

    app = create_app(make_settings(tmp_path, cors_origins="http://allowed.test"))
    with TestClient(app) as c:
        ok = c.options("/api/v1/sources", headers={"Origin": "http://allowed.test", "Access-Control-Request-Method": "GET"})
        assert ok.headers.get("access-control-allow-origin") == "http://allowed.test"
        bad = c.options("/api/v1/sources", headers={"Origin": "http://evil.test", "Access-Control-Request-Method": "GET"})
        assert bad.headers.get("access-control-allow-origin") is None
