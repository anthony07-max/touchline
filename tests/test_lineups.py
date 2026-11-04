"""Lineup watcher (T-60) tests, no network."""
import time, datetime as dt, pytest
from fastapi.testclient import TestClient
from backend.live import lineups, adapter


@pytest.fixture(autouse=True)
def clean(monkeypatch, tmp_path):
    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **k: (_ for _ in ()).throw(AssertionError("net")))
    monkeypatch.setattr(lineups, "PATH", str(tmp_path / "s.json"))
    lineups.STORE.clear(); lineups.CHECKS.clear()


def iso(t): return dt.datetime.fromtimestamp(t, dt.timezone.utc).isoformat()


def fx(fid, ko): return {"fixture_id": fid, "kickoff_utc": iso(ko), "home": {"id": 1, "name": "PAE AEK"}, "away": {"id": 86, "name": "Real Madrid"}, "state": {"ended": False}}


LU = [{"team": {"id": 86, "name": "Real Madrid"}, "formation": "4-3-3", "coach": {"name": "X"},
       "startXI": [{"player": {"id": i, "name": f"P{i}", "number": i, "pos": "M"}} for i in range(1, 12)],
       "substitutes": [{"player": {"id": 99, "name": "Sub", "number": 20, "pos": "F"}}]}]


def test_window_and_cadence():
    now = time.time(); ko = now + 3 * 3600
    assert not lineups.due(1, iso(ko), False, now)                 # too early
    assert lineups.status(1, iso(ko), False, now)["state"] == "scheduled"
    assert lineups.due(1, iso(now + 50 * 60), False, now)           # inside T-60
    lineups.CHECKS["1"] = {"last": now - 60, "n": 1}
    assert not lineups.due(1, iso(now + 50 * 60), False, now)       # checked 1 min ago -> wait 5
    assert lineups.due(1, iso(now + 50 * 60), False, now + 300)
    assert not lineups.due(1, iso(now - 3600), False, now)          # long after KO: give up


def test_found_stops_polling_and_injects():
    now = time.time(); f = fx(575386, now + 40 * 60)
    assert lineups.check(f, fetch=lambda fid: {"lineups": []}) == "empty"
    assert lineups.status(575386, f["kickoff_utc"], False)["state"] == "watching"
    assert lineups.check(f, fetch=lambda fid: {"lineups": LU}) == "found"
    assert not lineups.due(575386, f["kickoff_utc"], False, now + 9999 - 9000)
    import json, os
    from backend.live import football_data as fd
    m = json.load(open(os.path.join(os.path.dirname(__file__), "fixtures", "football_data", "match_finished.json")))
    raw = fd.to_fixture(m); raw["fixture"]["id"] = 575386; raw["lineups"] = []
    LU[0]["team"]["id"] = raw["teams"]["away"]["id"]
    live = adapter.parse_fixture(lineups.inject(raw))
    assert live["lineups"]["available"] and live["lineups"]["by_team"][str(raw["teams"]["away"]["id"])]["formation"] == "4-3-3"
    assert live["lineups"]["by_team"][str(raw["teams"]["away"]["id"])]["substitutes"][0]["name"] == "Sub"
    assert lineups.status(575386, f["kickoff_utc"], True)["label"] == "Confirmed XI"


def test_tick_rate_limited_and_skips_classics():
    now = time.time(); calls = []
    items = [fx(i, now + 30 * 60) for i in range(1, 6)] + [{**fx(9100001, now), "classic": True}]
    orig = lineups.check
    lineups.check = lambda x: calls.append(x["fixture_id"]) or "empty"
    try: lineups.tick(items, now)
    finally: lineups.check = orig
    assert len(calls) == lineups.MAX_PER_TICK and 9100001 not in calls
