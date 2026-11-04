from touchline_engine import career as C


def test_l2_full_season():
    st = C.new_career(0, 7, "Me", "l2")
    assert len(st["clubs"]) == 24 and st["weeks"] == 46 and st["clubs"][0]["name"] == "Leyton Marshes"
    for _ in range(st["weeks"]):
        C.quick_week(st) if hasattr(C, "quick_week") else None
        if not hasattr(C, "quick_week"): break


def test_pack_rename():
    st = C.new_career(0, 1, "Me", "l2", [{"name": "My Town", "short": "MYT", "colours": ["#123456", "zz"]}])
    c = st["clubs"][0]
    assert c["name"] == "My Town" and c["colours"][0] == "#123456" and c["colours"][1] == "#FFFFFF"


def test_l2_api_season(client=None):
    import os, tempfile
    from fastapi.testclient import TestClient
    from backend.main import app
    c = TestClient(app)
    r = c.post("/api/career/new", json={"club_id": "c0", "league": "l2", "manager": "O", "seed": 11}).json()
    k = {"X-Career-Key": r["key"]}
    assert c.patch("/api/career/clubs/c0", json={"name": "Marsh Men"}, headers=k).json()["name"] == "Marsh Men"
    for _ in range(120):
        s = c.post("/api/career/advance", headers=k).json()
        if "detail" in s: c.post("/api/career/new-job", headers=k); continue
        if c.get("/api/career/squad", headers=k).json()["season"] > 1: break
    assert c.get("/api/career/squad", headers=k).json()["season"] == 2, s
    assert c.post("/api/career/packs", json={"name": "Real names", "clubs": [{"name": "X"}]}).status_code == 200
