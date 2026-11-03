from fastapi.testclient import TestClient
from backend.main import app

c = TestClient(app)


def _login(email="flow@example.com"):
    r = c.post("/api/auth/register", json={"email": email, "password": "correct-horse-9"})
    return {"Authorization": "Bearer " + r.json()["token"]}


def test_flow():
    m = c.get("/api/match/demo?minute=55").json()
    assert m["score"] == [1, 1] and "DEMO" in m["notice"]
    h = _login()
    b = c.post("/api/branch", json={"minute": 55}, headers=h).json()
    req = {"timeline_id": b["timeline_id"], "tactics": {"formation": "4-2-3-1", "pressing": "high"}, "runs": 1000}
    r = c.post("/api/simulate", json=req).json()
    assert "HYPOTHETICAL" in r["label"] and r["explanation"] and r["after"]["replay_frames"]
    s1 = c.post("/api/scenarios", json={"name": "press", "request": req}, headers=h).json()
    s2 = c.post("/api/scenarios", json={"name": "base", "request": {**req, "tactics": {}}}, headers=h).json()
    cmp = c.get(f"/api/scenarios/compare?ids={s1['id']},{s2['id']}", headers=h).json()
    assert len(cmp["scenarios"]) == 2 and cmp["best"] in (s1["id"], s2["id"])
    assert c.post("/api/simulate", json={**req, "tactics": {"pressing": "bad"}}).status_code == 422
    assert c.get("/api/providers/api-football/fixtures").status_code == 503
    assert c.get("/").status_code == 200
