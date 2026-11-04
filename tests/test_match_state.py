import datetime as dt, json, os
import pytest
from backend.live import matchstate as ms, adapter, centre

KO = "2026-11-03T20:00:00+00:00"
k = dt.datetime.fromisoformat(KO)
at = lambda mins: k + dt.timedelta(minutes=mins)


@pytest.mark.parametrize("status,mins,phase", [
    ("NS", -600, "upcoming"), ("NS", -60, "prematch"), ("NS", 5, "live"), ("1H", 30, "live"), ("HT", 50, "halftime"),
    ("2H", 80, "live"), ("FT", 120, "fulltime"), ("AET", 140, "fulltime"), ("PEN", 160, "fulltime"),
    ("PST", -30, "postponed"), ("NS", 16 * 60, "stale_finished"), ("2H", 16 * 60, "fulltime")])
def test_phases(status, mins, phase):
    assert ms.phase(status, KO, at(mins))["phase"] == phase


def test_finished_match_is_not_playable_live_but_branchable():
    for s, m in (("FT", 130), ("NS", 15 * 60)):  # yesterday's Man United v Roma with a stale NS feed
        p = ms.phase(s, KO, at(m))
        assert not p["playable_live"] and p["can_branch"] and p["can_replay"] and p["ended"]


def test_upcoming_not_branchable_and_countdown():
    p = ms.phase("NS", KO, at(-90))
    assert not p["can_branch"] and not p["ended"] and p["seconds_to_ko"] == 5400


def test_live_is_playable():
    assert ms.phase("2H", KO, at(70))["playable_live"]


def test_poll_cadence_is_quota_friendly():
    assert ms.poll_seconds("fulltime", 0, 90) >= 3600
    assert ms.poll_seconds("live", 0, 90) >= 90
    assert ms.poll_seconds("upcoming", 86400, 90) >= 900
    # whole live match at worst case cadence fits in the daily budget
    assert 150 * 60 / ms.poll_seconds("live", 0, 40) < 40


FX = os.path.join(os.path.dirname(__file__), "fixtures", "past_fixture.json")


@pytest.mark.skipif(not os.path.exists(FX), reason="no recorded fixture")
def test_timeline_anchors_real_goals_and_is_deterministic():
    live = adapter.parse_fixture(json.load(open(FX))["response"][0])
    t1, t2 = centre.timeline(live), centre.timeline(live)
    assert t1 == t2
    goals = [p for p in t1["possessions"] if p.get("shot", {}).get("goal")]
    real = [e for e in live["events"] if e["type"] == "Goal" and e["detail"] != "Missed Penalty"]
    assert len(goals) == len(real)
    assert all(0 <= p["t0"] < p["t1"] for p in t1["possessions"])
    assert t1["possessions"][-1]["t1"] >= 89 * 60
    ph = ms.phase(live["status"]["short"], live["kickoff_utc"])
    r = centre.recap(live, ph)
    assert r["confirmed"] and r["score"] == [live["score"]["home"], live["score"]["away"]]


def test_fantasy_validation():
    mk = lambda i, g, s, pr=5.0: {"id": str(i), "group": g, "side": s, "price": pr}
    rules = {"GK": [1, 1], "DEF": [2, 3], "MID": [2, 3], "FWD": [1, 3], "max_per_team": 5}
    ok = [mk(1, "GK", "home"), mk(2, "DEF", "home"), mk(3, "DEF", "away"), mk(4, "MID", "home"), mk(5, "MID", "away"),
          mk(6, "MID", "away"), mk(7, "FWD", "home"), mk(8, "FWD", "away")]
    assert centre.validate_team(ok, 60, rules, "7", 8) is None
    assert "budget" in centre.validate_team(ok, 30, rules, "7", 8)
    assert "captain" in centre.validate_team(ok, 60, rules, "99", 8)
    two_gk = ok[:-1] + [mk(9, "GK", "away")]
    assert centre.validate_team(two_gk, 60, rules, "7", 8)
