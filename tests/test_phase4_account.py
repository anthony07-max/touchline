from fastapi.testclient import TestClient
from backend.main import app
from backend import db

c = TestClient(app)


def _reg(email, age=True):
    return c.post("/api/auth/register", json={"email": email, "password": "password123", "age_ok": age})


def test_age_gate():
    assert _reg("kid@example.com", False).status_code == 403


def test_verify_reset_export_delete(monkeypatch):
    monkeypatch.delenv("SMTP_HOST", raising=False)
    r = _reg("p4@example.com").json(); h = {"Authorization": "Bearer " + r["token"]}
    a = c.get("/api/account", headers=h).json(); assert a["verified"] is False and a["age_confirmed"]
    mails = db.q("SELECT * FROM email_outbox WHERE to_addr=?", ("p4@example.com",))
    assert mails and mails[-1]["sent"] == 0
    tok = mails[-1]["body"].split("token=")[1].split()[0]
    assert c.post("/api/auth/verify", json={"token": tok}).json()["verified"]
    assert c.get("/api/account", headers=h).json()["verified"]
    assert c.post("/api/auth/verify", json={"token": tok}).status_code == 400
    c.post("/api/auth/forgot", json={"email": "p4@example.com"})
    tok = db.q("SELECT body FROM email_outbox WHERE to_addr=? ORDER BY id DESC", ("p4@example.com",))[0]["body"].split("token=")[1].split()[0]
    assert c.post("/api/auth/reset", json={"token": tok, "password": "newpassword9"}).json()["ok"]
    assert c.get("/api/account", headers=h).status_code == 401
    h = {"Authorization": "Bearer " + c.post("/api/auth/login", json={"email": "p4@example.com", "password": "newpassword9"}).json()["token"]}
    k = c.post("/api/career/new", json={"club_id": "c1"}).json()["key"]
    c.post("/api/career/claim", headers={**h, "X-Career-Key": k})
    ex = c.get("/api/me/export", headers=h).json()
    assert "careers" in ex and "account_flags" in ex and "duels" in ex
    assert c.delete("/api/me", headers=h).json()["deleted"]
    assert not db.q("SELECT * FROM account_flags WHERE user_id=?", (r["user"]["id"],))


def test_pages_and_headers():
    for p in ("/privacy", "/terms", "/support", "/account", "/account/reset", "/offline"):
        r = c.get(p); assert r.status_code == 200 and "anthony00.aduseipoku@gmail.com" in r.text or p in ("/account", "/account/reset", "/offline")
    r = c.get("/static/brand/theme.css", headers={"Accept-Encoding": "gzip"})
    assert r.headers.get("content-encoding") == "gzip" and "max-age" in r.headers["cache-control"]
