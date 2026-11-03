import datetime as dt, json
from fastapi.testclient import TestClient
from backend.main import app
from backend import mobile

c = TestClient(app)


def reg(email):
    r = c.post("/api/auth/register", json={"email": email, "password": "s3cure-password"})
    return {"Authorization": "Bearer " + r.json()["token"]}, r.json()["user"]["id"]


def test_cors_preflight():
    r = c.options("/api/meta", headers={"Origin": "http://localhost:8081", "Access-Control-Request-Method": "GET",
                                        "Access-Control-Request-Headers": "authorization"})
    assert r.status_code == 200 and r.headers["access-control-allow-origin"] in ("*", "http://localhost:8081")


def test_push_register_and_prefs_quiet_hours():
    h, uid = reg("push@example.com")
    assert c.post("/api/push/register", json={"token": "garbage-token-123", "platform": "ios"}, headers=h).status_code == 422
    assert c.post("/api/push/register", json={"token": "ExponentPushToken[abc123xyz]", "platform": "ios"}, headers=h).status_code == 200
    p = c.get("/api/me/prefs", headers=h).json()
    assert p["notifications"]["quiet_start"] == "22:30"
    assert c.put("/api/me/prefs", json={"notifications": {"quiet_start": "25:99"}}, headers=h).status_code == 422
    c.put("/api/me/prefs", json={"favourite_team": "Northbridge", "notifications": {"quiet_start": "22:00", "quiet_end": "07:00",
          "types": {"fantasy_goal": True}, "max_per_day": 2}}, headers=h)
    p = c.get("/api/me/prefs", headers=h).json()
    assert p["favourite_team"] == "Northbridge" and p["notifications"]["types"]["fantasy_goal"] and p["notifications"]["types"]["kickoff"]
    night = dt.datetime(2026, 1, 10, 23, 30, tzinfo=dt.timezone.utc)   # 23:30 London (GMT)
    day = dt.datetime(2026, 1, 10, 12, 0, tzinfo=dt.timezone.utc)
    assert mobile.notify(uid, "kickoff", "t", "b", night)["reason"] == "quiet hours"
    assert mobile.notify(uid, "scouting_report", "t", "b", day)["reason"].endswith("turned off")
    assert mobile.notify(uid, "kickoff", "t", "b", day)["status"] == "dry-run"
    assert mobile.notify(uid, "kickoff", "t", "b", day)["status"] == "dry-run"
    assert mobile.notify(uid, "kickoff", "t", "b", day)["reason"] == "daily cap reached"


def test_quiet_hours_wrap():
    n = {"quiet_start": "22:00", "quiet_end": "07:00", "tz": "Europe/London"}
    assert mobile.in_quiet_hours(n, dt.datetime(2026, 7, 1, 5, 0, tzinfo=dt.timezone.utc))      # 06:00 BST
    assert not mobile.in_quiet_hours(n, dt.datetime(2026, 7, 1, 6, 30, tzinfo=dt.timezone.utc))  # 07:30 BST


def test_revenuecat_webhook_entitlements(monkeypatch):
    h, uid = reg("rc@example.com")
    assert c.get("/api/billing/entitlements", headers=h).json()["plan"] == "free"
    assert c.post("/api/billing/restore", headers=h).json()["mode"] == "stub"
    monkeypatch.setenv("REVENUECAT_WEBHOOK_AUTH", "Bearer rc_test_secret")
    ev = lambda i, t, exp=None: {"event": {"id": i, "type": t, "app_user_id": str(uid), "entitlement_ids": ["pro"],
                                            "product_id": "touchline_pro_monthly", "expiration_at_ms": exp}}
    assert c.post("/api/billing/revenuecat/webhook", json=ev("e1", "INITIAL_PURCHASE"), headers={"Authorization": "wrong"}).status_code == 401
    future = (dt.datetime.now().timestamp() + 86400) * 1000
    r = c.post("/api/billing/revenuecat/webhook", json=ev("e1", "INITIAL_PURCHASE", future), headers={"Authorization": "Bearer rc_test_secret"})
    assert r.json()["plan"] == "pro"
    assert c.post("/api/billing/revenuecat/webhook", json=ev("e1", "INITIAL_PURCHASE", future), headers={"Authorization": "Bearer rc_test_secret"}).json()["status"] == "duplicate"
    # pro features now allowed server-side
    assert c.post("/api/simulate", json={"runs": 20000, "difficulty": "hard"}, headers=h).status_code == 200
    # web Stripe entitlement too -> expiring RevenueCat must NOT drop Pro (no duplicate/conflicting entitlements)
    mobile.set_entitlement(uid, "stripe", True, "web_pro")
    c.post("/api/billing/revenuecat/webhook", json=ev("e2", "EXPIRATION", future), headers={"Authorization": "Bearer rc_test_secret"})
    assert c.get("/api/billing/entitlements", headers=h).json()["plan"] == "pro"
    mobile.set_entitlement(uid, "stripe", False, "web_pro")
    assert c.get("/api/billing/entitlements", headers=h).json()["plan"] == "free"
    assert c.post("/api/simulate", json={"runs": 20000}, headers=h).status_code == 402


def test_gdpr_export_delete():
    h, uid = reg("gdpr@example.com")
    c.post("/api/scenarios", json={"name": "x", "request": {"runs": 300}}, headers=h)
    c.post("/api/leagues", json={"name": "L"}, headers=h)
    e = c.get("/api/me/export", headers=h).json()
    assert e["user"]["email"] == "gdpr@example.com" and len(e["scenarios"]) == 1
    assert c.delete("/api/me", headers=h).json()["deleted"]
    assert c.get("/api/me", headers=h).status_code == 401
    assert c.post("/api/auth/login", json={"email": "gdpr@example.com", "password": "s3cure-password"}).status_code == 401


def test_live_proxy_unavailable_is_honest(monkeypatch):
    monkeypatch.setattr(mobile, "LIVE_UPSTREAM", "http://127.0.0.1:9")
    r = c.get("/api/m/live/match")
    assert r.status_code == 503 and "unavailable" in json.dumps(r.json())


def test_dashboard():
    h, _ = reg("dash@example.com")
    d = c.get("/api/m/dashboard", headers=h).json()
    assert d["challenge"]["brief"] and d["plan"] == "free" and "prefs" in d
