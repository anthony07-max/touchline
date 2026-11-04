import json, random
import pytest
from fastapi.testclient import TestClient
from touchline_engine.demo_data import demo_state_at
from touchline_engine import career as C
from backend.main import app
from backend import whatif, webpush, progress, db

client = TestClient(app)
REAL = {"minute": 60, "ended": False, "phase": "live", "score": [1, 1], "goals": [{"minute": 20, "side": "home"}, {"minute": 40, "side": "away"}],
        "home": "Real Madrid", "away": "Man City", "lineups_confirmed": True}


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no network")))
    monkeypatch.setattr(whatif, "_seed", lambda m, fid: (demo_state_at(m), {}, {"home": {"source": "official line-up"}}))
    state = dict(REAL)
    monkeypatch.setattr(whatif, "_real", lambda fid: dict(state))
    monkeypatch.setattr(whatif, "_candidates", lambda: [{"fixture_id": 77, "home": "Real Madrid", "away": "Man City", "score": [1, 2], "classic": False}])
    return state


def _user(email):
    r = client.post("/api/auth/register", json={"email": email, "password": "password123"})
    if r.status_code != 200: r = client.post("/api/auth/login", json={"email": email, "password": "password123"})
    return {"Authorization": "Bearer " + r.json()["token"]}


def test_shadow_live_tracks_real_minute_and_is_deterministic(offline):
    h = _user("shadow@x.io")
    s = client.post("/api/shadow", json={"fixture_id": 77, "side": "home"}, headers=h).json()
    assert s["branch_minute"] == 60 and s["real"]["score_now"] == [1, 1] and s["alt"]["score"] == [1, 1]
    starter = next(p for p in s["you"]["starters"] if p["group"] == "FWD"); bench = s["you"]["bench"][0]
    c = client.post(f"/api/shadow/{s['id']}/change", json={"sub": {"out_id": starter["id"], "in_id": bench["id"]}, "tactics": {"pressing": "high"}}, headers=h)
    assert c.status_code == 200
    offline["minute"] = 75
    a = client.get(f"/api/shadow/{s['id']}").json(); b = client.get(f"/api/shadow/{s['id']}").json()
    assert a["minute"] == 75 and a["alt"] == b["alt"]
    assert any(e["type"] == "sub" for e in a["alt"]["events"]) and any(p["id"] == bench["id"] for p in a["you"]["starters"])
    assert client.post(f"/api/shadow/{s['id']}/change", json={"tactics": {"tempo": "fast"}, "minute": 0}, headers=h).status_code == 200
    offline.update(minute=90, ended=True, phase="fulltime", score=[1, 2], goals=REAL["goals"] + [{"minute": 80, "side": "away"}])
    f = client.get(f"/api/shadow/{s['id']}").json()
    assert f["final"]["real"] == [1, 2] and "I'd have" in f["final"]["headline"]
    card = client.post(f"/api/share/shadow/{s['id']}", headers=h).json()
    assert card["url"].endswith(card["id"]) and "twitter.com/intent/tweet" in card["x_intent"] and "wa.me" in card["whatsapp"]
    png = client.get(f"/api/share/{card['id']}.png"); assert png.status_code == 200 and png.content[:4] == b"\x89PNG"
    page = client.get(f"/s/{card['id']}").text
    assert 'property="og:image"' in page and "summary_large_image" in page


def test_tactics_change_moves_shadow_rates():
    from touchline_engine.simulate import minute_rates
    st = demo_state_at(60); base = minute_rates(st.home, st.away)["lam_h"]
    st.home.tactics = st.home.tactics.copy(pressing="high", tempo="fast")
    assert minute_rates(st.home, st.away)["lam_h"] != base


def test_weekly_challenge_and_leaderboard():
    h = _user("weekly@x.io")
    w = client.get("/api/weekly", headers=h).json()
    assert w["fixture_id"] == 77 and w["side"] == "home" and "Turn around" in w["brief"]
    r = client.post("/api/weekly/enter", json={"tactics": {"pressing": "high", "tempo": "fast"}}, headers=h).json()
    assert 0 <= r["score"] <= 100 and r["rank"] == 1
    assert client.get("/api/weekly").json()["leaderboard"][0]["manager"] == "weekly"
    assert client.post("/api/share/weekly", headers=h).status_code == 200


def test_duel_flow_and_league_table():
    a, b = _user("duela@x.io"), _user("duelb@x.io")
    lg = client.post("/api/leagues", json={"name": "Mates"}, headers=a).json()
    client.post(f"/api/leagues/join/{lg['code']}", headers=b)
    d = client.post("/api/social/duels", json={"league_id": lg["id"]}, headers=a).json()
    pool = client.get(f"/api/social/duels/{d['code']}").json()["pool"]
    def pick():
        cheap = sorted(pool, key=lambda p: p["price"]); gk = next(p for p in cheap if p["pos"] == "GK")
        return [gk["id"]] + [p["id"] for p in cheap if p["pos"] != "GK"][:10]
    over = [p["id"] for p in sorted(pool, key=lambda p: -p["price"]) if p["pos"] != "GK"][:10] + [next(p["id"] for p in pool if p["pos"] == "GK")]
    assert client.post(f"/api/social/duels/{d['code']}/entry", json={"picks": over}, headers=a).status_code == 400
    assert client.post(f"/api/social/duels/{d['code']}/entry", json={"picks": pick(), "sliders": {"tempo": 90}}, headers=a).status_code == 200
    r = client.post(f"/api/social/duels/{d['code']}/entry", json={"picks": pick(), "formation": "4-4-2"}, headers=b).json()
    assert r["result"] and len(r["result"]["score"]) == 2
    t = client.get(f"/api/social/leagues/{lg['id']}", headers=a).json()
    assert len(t["table"]) == 2 and t["table"][0]["points"] >= t["table"][1]["points"]
    assert client.get("/api/social/leaderboard").status_code == 200
    assert client.post(f"/api/share/duel/{r['id']}").status_code == 200


def test_objectives_streaks_achievements():
    h = _user("prog@x.io"); uid = client.get("/api/me", headers=h).json()["id"]
    day = "2026-11-02"; kinds = progress.daily_objectives(day)
    r = progress.record(uid, kinds[0], day=day)
    assert r["objectives_completed"] == [kinds[0]]
    progress.record(uid, progress.daily_objectives("2026-11-03")[0], day="2026-11-03")
    progress.record(uid, progress.daily_objectives("2026-11-04")[0], day="2026-11-04")
    s = progress.summary(uid, "2026-11-04")
    assert s["streak"]["current"] == 3 and any(a["code"] == "streak3" and a["unlocked"] for a in s["achievements"])
    progress.record(uid, "career_win")
    assert any(a["code"] == "first_win" and a["unlocked"] for a in client.get("/api/progress", headers=h).json()["achievements"])


def test_career_claim_links_account():
    h = _user("claim@x.io")
    k = client.post("/api/career/new", json={"club_id": "c3", "seed": 9}).json()["key"]
    assert client.post("/api/career/claim", headers={**h, "X-Career-Key": k}).json()["linked"]
    assert client.get("/api/career/mine", headers=h).json()["careers"][0]["key"] == k
    other = _user("thief@x.io")
    assert client.post("/api/career/claim", headers={**other, "X-Career-Key": k}).status_code == 409


def test_injuries_suspensions_and_unavailable_excluded():
    st = C.new_career(2, seed=4); me = C.club_of(st, st["club_id"])
    star = max(me["squad"], key=lambda p: st["players"][p]["ovr"])
    st["players"][star]["injury_weeks"] = 2; st["players"][star]["injury"] = "calf tear"
    assert star not in C.best_xi(st, st["club_id"], "4-3-3")
    t = json.loads(json.dumps(st["tactics"][st["club_id"]]))
    if star not in [s["player_id"] for s in t["slots"]]: t["slots"][5]["player_id"] = star
    with pytest.raises(ValueError, match="injured"): C.validate_tactics(st, st["club_id"], t)
    C.recover(st); C.recover(st)
    assert C.available(st["players"][star])
    # cards + injuries happen over a season of quick matches
    rng = random.Random(1); reds = injuries = 0
    for f in st["fixtures"][:60]:
        lv = C.quick_match(st, f, rng); reds += len(lv["sent_off"]); C.apply_result(st, f, lv, rng)
    injuries = sum(1 for p in st["players"].values() if p.get("injury_weeks"))
    assert reds >= 1 and injuries >= 1


def test_red_card_weakens_team():
    st = C.new_career(2, seed=4); cid = st["club_id"]; t = st["tactics"][cid]
    full = C.team_profile(st, cid, t); short = C.team_profile(st, cid, {**t, "slots": t["slots"][:10]})
    assert short["att"] < full["att"] and short["def"] < full["def"]


def test_contract_renewal_flow():
    k = client.post("/api/career/new", json={"club_id": "c4", "seed": 2}).json()["key"]; h = {"X-Career-Key": k}
    pid = client.get("/api/career/squad", headers=h).json()["players"][0]["id"]
    q = client.get(f"/api/career/contract/{pid}", headers=h).json()
    lo = client.post("/api/career/contract/renew", json={"player_id": pid, "years": 3, "wage": 1}, headers=h).json()
    assert lo["status"] in ("counter", "refused")
    if lo["status"] == "counter":
        ok = client.post("/api/career/contract/renew", json={"player_id": pid, "years": 3, "wage": lo["wage_demand"]}, headers=h)
        assert ok.status_code in (200, 400)
        if ok.status_code == 200: assert ok.json()["status"] == "signed" and ok.json()["contract_until"] == q["season"] + 3


def test_webpush_encryption_roundtrip_and_dedupe(monkeypatch):
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    ua = ec.generate_private_key(ec.SECP256R1()); ua_pub = ua.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    auth = b"0123456789abcdef"
    body = webpush.encrypt(b'{"t":1}', webpush.b64u(ua_pub), webpush.b64u(auth))
    salt, idlen = body[:16], body[20]; as_pub = body[21:21 + idlen]; ct = body[21 + idlen:]
    shared = ua.exchange(ec.ECDH(), ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), as_pub))
    ikm = webpush._hkdf(auth, shared, b"WebPush: info\x00" + ua_pub + as_pub, 32)
    pt = AESGCM(webpush._hkdf(salt, ikm, b"Content-Encoding: aes128gcm\x00", 16)).decrypt(webpush._hkdf(salt, ikm, b"Content-Encoding: nonce\x00", 12), ct, None)
    assert pt == b'{"t":1}\x02'
    assert webpush.vapid_header("https://fcm.googleapis.com/fcm/send/x").startswith("vapid t=")
    k = client.get("/api/webpush/key").json(); assert len(webpush.ub64(k["public_key"])) == 65
    monkeypatch.setenv("WEBPUSH_DRY_RUN", "1")
    sub = {"endpoint": "https://push.example/abc", "keys": {"p256dh": webpush.b64u(ua_pub), "auth": webpush.b64u(auth)}}
    assert client.post("/api/webpush/subscribe", json={"subscription": sub, "fixtures": [5]}).status_code == 200
    item = {"fixture_id": 5, "home": {"name": "A"}, "away": {"name": "B"}, "score": [2, 1], "elite": True, "state": {"phase": "fulltime", "ended": True}}
    assert webpush.tick([item]) == [(5, "fulltime", 1)]
    assert webpush.tick([item]) == [(5, "fulltime", 0)]  # deduped
