import hashlib, hmac, json, time, os
from fastapi.testclient import TestClient
from backend.main import app
from backend import auth

c = TestClient(app)


def reg(email):
    r = c.post("/api/auth/register", json={"email": email, "password": "s3cure-password"})
    assert r.status_code == 200, r.text
    return {"Authorization": "Bearer " + r.json()["token"]}, r.json()["user"]["id"]


def test_password_hashing():
    h = auth.hash_password("hunter2hunter2")
    assert h.startswith("pbkdf2_sha256$") and "hunter2" not in h
    assert auth.verify_password("hunter2hunter2", h) and not auth.verify_password("wrong", h)
    assert auth.hash_password("x" * 8) != auth.hash_password("x" * 8)  # salted


def test_auth_flow_and_errors():
    h, _ = reg("auth@example.com")
    assert c.post("/api/auth/register", json={"email": "auth@example.com", "password": "s3cure-password"}).status_code == 409
    assert c.post("/api/auth/login", json={"email": "auth@example.com", "password": "nope-nope-nope"}).status_code == 401
    assert c.post("/api/auth/login", json={"email": "auth@example.com", "password": "s3cure-password"}).status_code == 200
    assert c.get("/api/me", headers=h).json()["plan"] == "free"
    assert c.get("/api/me").status_code == 401
    c.post("/api/auth/logout", headers=h)
    assert c.get("/api/me", headers=h).status_code == 401


def test_scenarios_are_per_user():
    h1, _ = reg("u1@example.com"); h2, _ = reg("u2@example.com")
    c.post("/api/scenarios", json={"name": "mine", "request": {"runs": 500}}, headers=h1)
    assert len(c.get("/api/scenarios", headers=h1).json()) == 1
    assert c.get("/api/scenarios", headers=h2).json() == []


def test_free_limits_enforced():
    h, _ = reg("free@example.com")
    assert c.post("/api/simulate", json={"runs": 20000}, headers=h).status_code == 402
    assert c.post("/api/simulate", json={"runs": 500, "difficulty": "hard"}, headers=h).status_code == 402
    for i in range(5):
        assert c.post("/api/scenarios", json={"name": f"s{i}", "request": {"runs": 300}}, headers=h).status_code == 200
    assert c.post("/api/scenarios", json={"name": "s6", "request": {"runs": 300}}, headers=h).status_code == 402
    assert c.get("/api/autopsy", headers=h).json()["model_checkpoints"] == []


def test_copilot_endpoint_and_quota():
    h, _ = reg("cop@example.com")
    r = c.post("/api/copilot", json={"minute": 60, "question": "Should I increase the pressing intensity?"}, headers=h)
    assert r.status_code == 200 and r.json()["recommendation"]["label"]
    for _ in range(4): c.post("/api/copilot", json={"minute": 60, "question": "press?"}, headers=h)
    assert c.post("/api/copilot", json={"minute": 60}, headers=h).status_code == 402


def _signed(payload, secret):
    t = int(time.time())
    sig = hmac.new(secret.encode(), f"{t}.".encode() + payload, hashlib.sha256).hexdigest()
    return f"t={t},v1={sig}"


def test_stripe_stub_and_webhook(monkeypatch):
    h, uid = reg("pay@example.com")
    co = c.post("/api/billing/checkout", headers=h).json()
    assert co["mode"] == "stub" and co["checkout_url"] is None
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "whsec_test")
    ev = json.dumps({"id": "evt_1", "type": "checkout.session.completed",
                     "data": {"object": {"client_reference_id": str(uid), "customer": "cus_1"}}}).encode()
    assert c.post("/api/billing/webhook", content=ev, headers={"Stripe-Signature": "t=1,v1=bad"}).status_code == 400
    r = c.post("/api/billing/webhook", content=ev, headers={"Stripe-Signature": _signed(ev, "whsec_test")})
    assert r.json()["status"] == "ok"
    assert c.post("/api/billing/webhook", content=ev, headers={"Stripe-Signature": _signed(ev, "whsec_test")}).json()["status"] == "duplicate"
    assert c.get("/api/me", headers=h).json()["plan"] == "pro"
    assert c.post("/api/simulate", json={"runs": 20000, "difficulty": "hard"}, headers=h).status_code == 200
    assert c.get("/api/autopsy", headers=h).json()["model_checkpoints"]
    assert c.post("/api/billing/dev-upgrade", headers=h).status_code == 403  # dev upgrade off by default


def test_club_challenge_league_leaderboard():
    h1, _ = reg("mgr1@example.com"); h2, _ = reg("mgr2@example.com")
    xi = ["nb1", "nb2", "nb3", "nb4", "nb5", "nb6", "nb7", "nb14", "nb9", "nb16", "nb11"]
    assert c.post("/api/club", json={"name": "Touchline FC", "starters": xi[:10]}, headers=h1).status_code == 422
    assert c.post("/api/club", json={"name": "Touchline FC", "starters": xi}, headers=h1).status_code == 200
    m = c.get("/api/match/demo?minute=10", headers=h1).json()
    assert "Touchline FC" in m["home"]["name"] and any(p["id"] == "nb16" for p in m["home"]["starters"])
    ch = c.get("/api/challenge/today").json()
    assert ch["brief"]
    s1 = c.post("/api/challenge/submit", json={"tactics": {"pressing": "high"}}, headers=h1)
    assert s1.status_code == 200 and 0 <= s1.json()["decision_score"] <= 100
    assert c.post("/api/challenge/submit", json={"tactics": {}}, headers=h1).status_code == 409
    c.post("/api/challenge/submit", json={"tactics": {"defensive_line": "deep"}}, headers=h2)
    lb = c.get("/api/challenge/leaderboard").json()
    assert len(lb) >= 2 and lb[0]["score"] >= lb[1]["score"]
    lg = c.post("/api/leagues", json={"name": "Sunday Tacticians"}, headers=h1).json()
    assert c.post("/api/leagues", json={"name": "Second"}, headers=h1).status_code == 402
    assert c.post(f"/api/leagues/join/{lg['code']}", headers=h2).status_code == 200
    table = c.get("/api/leagues", headers=h2).json()[0]["table"]
    assert len(table) == 2 and table[0]["points"] >= table[1]["points"]
