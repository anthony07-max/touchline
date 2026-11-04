from backend.live import centre
from touchline_engine.demo_data import demo_state_at, DEMO_NOTICE


def _x(h, a, phase="upcoming", league=39, ko="2026-11-05T20:00:00+00:00"):
    return {"home": {"name": h}, "away": {"name": a}, "state": {"phase": phase}, "league_id": league, "kickoff_utc": ko,
            "elite": centre.is_elite(h) or centre.is_elite(a)}


def test_is_elite():
    for n in ["Real Madrid", "Manchester City", "Barcelona", "Bayern München", "Paris Saint Germain", "Liverpool", "Arsenal", "Inter"]:
        assert centre.is_elite(n)
    assert not centre.is_elite("Leyton Orient") and not centre.is_elite("Burnley")


def test_rank_elite_first():
    xs = [_x("Burnley", "Brentford", ko="2026-11-05T12:00:00+00:00"), _x("Liverpool", "Fulham"),
          _x("Real Madrid", "Bayern München", league=2), _x("Leyton Orient", "Wigan")]
    names = [x["home"]["name"] for x in sorted(xs, key=centre._rank)]
    assert names == ["Real Madrid", "Liverpool", "Burnley", "Leyton Orient"]


def test_classic_specs_are_elite_matches():
    assert len(centre.CLASSIC_SPECS) >= 6
    for season, rnd, h, a, title in centre.CLASSIC_SPECS:
        assert centre.is_elite(h) or centre.is_elite(a)


def test_demo_uses_elite_squads():
    st = demo_state_at(0)
    assert "Real Madrid" in st.home.name and "Manchester City" in st.away.name
    assert any("Mbappé" in p.name for p in st.home.starters) and any("Haaland" in p.name for p in st.away.starters)
    assert "game-estimated" in DEMO_NOTICE


def test_frozen_classics_load_and_parse():
    from fastapi.testclient import TestClient
    from backend.live_main import app
    assert len(centre.CLASSIC_RAW) >= 7
    c = TestClient(app)
    d = c.get("/api/classics").json()
    titles = [x["title"] for x in d["classics"]]
    assert titles[0] == "2025 Champions League final"
    fid = d["classics"][0]["fixture"]["fixture"]["id"]
    s = centre.snapshot(fid)
    assert s["state"]["ended"] and s["recap"]["score"] == [5, 0] and "Paris" in s["recap"]["headline"]
    rm = [x for x in d["classics"] if x["title"] == "2024 UCL quarter-final, 1st leg"][0]["fixture"]
    assert (rm["goals"]["home"], rm["goals"]["away"]) == (3, 3)
