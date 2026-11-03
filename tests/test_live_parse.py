import json, os, pytest
from backend.live import adapter
FX = os.path.join(os.path.dirname(__file__), "fixtures", "past_fixture.json")

@pytest.mark.skipif(not os.path.exists(FX), reason="no recorded fixture")
def test_parse_past_fixture():
    fx = json.load(open(FX))["response"][0]
    live = adapter.parse_fixture(fx)
    assert live["status"]["short"] == "FT"
    assert live["lineups"]["available"] and live["statistics"]["available"]
    goals = [e for e in live["events"] if e["type"] == "Goal" and e["detail"] != "Missed Penalty"]
    assert len(goals) == live["score"]["home"] + live["score"]["away"]
    assert len({e["id"] for e in live["events"]}) == len(live["events"])
    for side in ("home", "away"):
        op = adapter.on_pitch(live, live[side]["id"])
        assert 9 <= len(op["on"]) <= 11
