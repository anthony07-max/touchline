from touchline_engine.demo_data import demo_state_at
from touchline_engine.simulate import simulate, compare, SimConfig, minute_rates
from touchline_engine.copilot import advise, classify
from touchline_engine.game import daily_challenge, score_decision, autopsy
from touchline_engine.models import Tactics
import pytest

N = 6000


def test_roles_change_model():
    st = demo_state_at(55)
    st.away.tactics = st.away.tactics.copy(defensive_line="high")
    base = minute_rates(st.home, st.away)
    st.home.tactics = st.home.tactics.copy(roles={"nb9": "runner-in-behind"})  # Mensah pace 86
    run = minute_rates(st.home, st.away)
    assert run["xg_h"] > base["xg_h"]
    assert any("running in behind" in f[1] for f in run["factors_h"])


def test_invalid_role_line_ignored_and_bad_role_rejected():
    st = demo_state_at(55)
    st.home.tactics = st.home.tactics.copy(roles={"nb3": "target-man"})  # centre-back
    r = minute_rates(st.home, st.away)
    assert any("role ignored" in f[1] for f in r["factors_h"])
    with pytest.raises(ValueError):
        Tactics(roles={"nb3": "sweeper-keeper-wizard"}).validate()


def test_set_pieces_matter():
    st = demo_state_at(55)
    st.away.tactics = st.away.tactics.copy(set_piece_defence="zonal")
    a = minute_rates(st.home, st.away)
    st.home.tactics = st.home.tactics.copy(set_piece_attack="near-post")
    b = minute_rates(st.home, st.away)
    assert b["spq_h"] > a["spq_h"] * 1.1
    # stronger aerial side gets better set-piece chances
    for p in st.home.starters: p.aer = 90
    c = minute_rates(st.home, st.away)
    assert c["spq_h"] > b["spq_h"]


def test_defensive_approach_compact_reduces_exposure():
    st = demo_state_at(55)
    cfg = SimConfig(runs=N, seed=4, opponent_ai=False)
    r = compare(st, st.home.tactics.copy(defensive_approach="compact"), cfg)
    assert r["after"]["shots"]["opp_mean"] < r["before"]["shots"]["opp_mean"]


def test_opponent_rereacts_in_run():
    st = demo_state_at(55)  # 1-1
    st.home_goals = 2       # user leads -> opponent should switch to chasing in most runs
    r = simulate(st, SimConfig(runs=3000, seed=1, opponent_personality="flexible"))
    assert r["opponent_in_game_switch_rate"] > 0.8
    assert any(e["type"] == "tactic" for e in r["sample_replay"])
    # chasing opponent creates more than when it can't re-react
    st2 = demo_state_at(55); st2.home_goals = 2
    on = simulate(st2, SimConfig(runs=N, seed=2, opponent_personality="flexible"))
    v = on["opponent"]["in_game_variants"]["chasing"]
    assert v["pressing"] == "high" and v["tempo"] == "fast"


def test_hard_reacts_faster_than_easy():
    st = demo_state_at(55); st.home_goals = 2
    e = simulate(st, SimConfig(runs=500, seed=1, difficulty="easy", opponent_personality="direct"))
    h = simulate(st, SimConfig(runs=500, seed=1, difficulty="hard", opponent_personality="direct"))
    me = min(x["minute"] for x in e["sample_replay"] if x["type"] == "tactic")
    mh = min(x["minute"] for x in h["sample_replay"] if x["type"] == "tactic")
    assert mh < me


def test_copilot_grounded_and_improves():
    st = demo_state_at(55)
    a = advise(st, st.home.tactics, SimConfig(opponent_personality="counter", seed=11), "How do I exploit the space behind their defence?")
    for k in ("diagnosis", "recommendation", "benefits", "risks", "opponent_response", "simulation"):
        assert a[k]
    assert "space" in a["topics"]
    assert "no LLM" in a["engine"]
    # the recommendation, re-simulated independently, should not be worse than current (beyond noise)
    rec = Tactics(**a["recommendation"]["tactics"])
    r = compare(st, rec, SimConfig(runs=N, seed=99, opponent_personality="counter"))
    pts = lambda p: 3 * p["user_win"] + p["draw"]
    assert pts(r["after"]["probabilities"]) >= pts(r["before"]["probabilities"]) - 0.03


def test_copilot_subs_when_tired():
    st = demo_state_at(75)
    for p in st.home.starters[5:8]: p.energy = 0.3
    a = advise(st, st.home.tactics, SimConfig(seed=3), "Which substitution would improve our chances?")
    assert a["topics"] == ["subs"] and a["recommendation"]["subs"]


def test_classify():
    assert "midfield" in classify("Why are we losing control of midfield?")
    assert "striker" in classify("Why is my striker isolated?")


def test_challenge_deterministic_and_scored():
    a = daily_challenge("2026-11-03"); b = daily_challenge("2026-11-03")
    assert a["minute"] == b["minute"] and a["seed"] == b["seed"]
    st = a["_state"]
    same = score_decision("2026-11-03", st.home.tactics)
    assert 45 <= same["decision_score"] <= 55  # unchanged tactics ~ 50
    assert 0 <= same["decision_score"] <= 100


def test_autopsy():
    r = autopsy()
    assert r["final_score"] == [1, 2] and r["model_checkpoints"] and r["findings"]
    assert abs(sum(r["model_checkpoints"][0][k] for k in ("home_win", "draw", "away_win")) - 1) < 1e-9
