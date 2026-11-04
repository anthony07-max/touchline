"""football-data.org adapter tests, recorded JSON only (no network)."""
import json, os, pytest
from fastapi.testclient import TestClient
from backend.live import football_data as fd, provider, adapter, centre, matchstate, browse

FX = os.path.join(os.path.dirname(__file__), "fixtures", "football_data")
def load(n):
    with open(os.path.join(FX, n)) as f: return json.load(f)


@pytest.fixture(autouse=True)
def no_net(monkeypatch):
    def boom(*a, **k): raise AssertionError("network call in tests")
    monkeypatch.setattr("urllib.request.urlopen", boom)


def test_finished_match_maps_to_internal_model():
    live = adapter.parse_fixture(fd.to_fixture(load("match_finished.json")))
    assert live["home"]["name"] == "Paris Saint Germain" and live["away"]["name"] == "Inter"
    assert live["status"]["short"] == "FT" and live["score"] == {"home": 5, "away": 0}
    assert live["league"] == "UEFA Champions League" and live["referee"] == "István Kovács" and live["venue"] == "Allianz Arena"
    kinds = [(e["type"], e["detail"]) for e in live["events"]]
    assert ("Goal", "Normal Goal") in kinds and ("Card", "Yellow Card") in kinds and ("subst", "Substitution") in kinds
    og = [e for e in live["events"] if e["detail"] == "Own Goal"][0]
    assert og["team_id"] == 108  # scorer's own team, API-Football convention
    sub = [e for e in live["events"] if e["type"] == "subst"][0]
    assert sub["player"] == "Nicolò Barella" and sub["assist"] == "Davide Frattesi"
    assert not live["lineups"]["available"] and "modelled" in live["lineups"]["note"]
    assert live["source"] == "football-data.org"
    ph = matchstate.phase(live["status"]["short"], live["kickoff_utc"])
    assert ph["ended"] and ph["can_branch"]
    r = centre.recap(live, ph); assert r["headline"].startswith("Paris Saint Germain win 5")


def test_status_state_machine_mapping():
    fx = [fd.to_fixture(m) for m in load("matches_day.json")["matches"]]
    assert [f["fixture"]["status"]["short"] for f in fx] == ["2H", "HT", "NS"]
    assert fx[0]["fixture"]["status"]["elapsed"] == 67 and fx[0]["league"]["id"] == 39
    assert fx[0]["teams"]["home"]["name"] == "Manchester City"
    for f, s in (("FINISHED", "FT"), ("POSTPONED", "PST"), ("SUSPENDED", "SUSP"), ("CANCELLED", "CANC")):
        assert fd._status({"status": f, "score": {"duration": "REGULAR"}})[0] == s
    assert fd._status({"status": "FINISHED", "score": {"duration": "PENALTY_SHOOTOUT"}})[0] == "PEN"


def test_provider_translates_day_query(monkeypatch):
    monkeypatch.setenv("FOOTBALL_DATA_KEY", "test")
    seen = []
    monkeypatch.setattr(fd, "raw", lambda ep, *a, **k: seen.append(ep) or load("matches_day.json"))
    d = provider.get("fixtures?date=2026-11-04&timezone=Europe/London", max_age=60)
    assert seen == ["matches?dateFrom=2026-11-04&dateTo=2026-11-05"]
    assert len(d["response"]) == 2  # next-day match filtered out
    s = [centre._summ(f) for f in d["response"]]
    assert s[0]["elite"] and s[0]["state"]["phase"] == "live"


def test_missing_key_is_graceful(monkeypatch):
    monkeypatch.delenv("FOOTBALL_DATA_KEY", raising=False); monkeypatch.delenv("API_FOOTBALL_KEY", raising=False)
    with pytest.raises(provider.APIError): provider.get("fixtures?date=2026-11-04")
    assert "no live data key" in centre.paused_text(provider.paused_reason())


def test_raw_without_key_raises_not_calls(monkeypatch, tmp_path):
    monkeypatch.delenv("FOOTBALL_DATA_KEY", raising=False); monkeypatch.setattr(fd, "CACHE", str(tmp_path))
    with pytest.raises(fd.APIError): fd.raw("matches/1")


def test_af_fallback_only_when_key_works(monkeypatch):
    monkeypatch.delenv("FOOTBALL_DATA_KEY", raising=False); monkeypatch.setenv("API_FOOTBALL_KEY", "x")
    provider._af_dead_until[0] = 0
    assert provider.active() == "API-Football"
    def suspended(*a, **k): raise provider.afb.APIError("{'access': 'Your account is suspended'}")
    monkeypatch.setattr(provider.afb, "get", suspended)
    with pytest.raises(provider.APIError): provider.get("fixtures?date=2026-11-04")
    assert provider.active() == "none"
    provider._af_dead_until[0] = 0


def test_standings_squad_and_rank(monkeypatch):
    rows = browse.table_rows(load("standings_pl.json"))
    assert len(rows) == 1 and rows[0]["rows"][0]["team"]["name"] == "Arsenal" and rows[0]["rows"][0]["elite"]
    sq = fd.to_squad(load("team_86.json"))["response"][0]["players"]
    assert [p["position"] for p in sq] == ["Goalkeeper", "Defender", "Midfielder", "Attacker"] and sq[0]["age"] >= 33


def test_browse_api(monkeypatch):
    from backend.live_main import app
    monkeypatch.setenv("FOOTBALL_DATA_KEY", "test")
    m = {"competitions/PL/standings": "standings_pl.json", "teams/86": "team_86.json"}
    monkeypatch.setattr(fd, "raw", lambda ep, *a, **k: load(m[ep]) if ep in m else load("matches_day.json"))
    c = TestClient(app)
    assert c.get("/api/competitions").json()["competitions"][0]["code"] == "CL"
    assert c.get("/api/competitions/PL/standings").json()["tables"][0]["rows"][0]["pts"] == 25
    assert c.get("/api/competitions/BL2/standings").status_code == 404
    t = c.get("/api/teams/86").json(); assert t["team"]["name"] == "Real Madrid" and t["team"]["competitions"] == ["PD", "CL"]
    assert c.get("/competitions/PL").status_code == 200 and c.get("/team/86").status_code == 200


def test_classic_merge_keeps_ids():
    cur = centre.CLASSIC_RAW[next(iter(centre.CLASSIC_RAW))]
    m = centre.merge_classic(cur, fd.to_fixture(load("match_finished.json")))
    assert m["fixture"]["id"] == cur["fixture"]["id"] and m["fixture"]["referee"] == "István Kovács"
    assert {e["team"]["id"] for e in m["events"]} <= {cur["teams"]["home"]["id"], cur["teams"]["away"]["id"]}


def test_league_two_note(monkeypatch):
    monkeypatch.setenv("FOOTBALL_DATA_KEY", "t")
    assert "League Two" in centre.coverage_note([])
