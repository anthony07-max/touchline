import numpy as np
import pytest
from touchline_engine.demo_data import demo_state_at
from touchline_engine.simulate import simulate, compare, SimConfig
from touchline_engine.models import Substitution, Tactics
from touchline_engine.opponent_ai import OpponentAI

N = 6000


def probs(r):
    p = r["probabilities"]; return p["user_win"], p["draw"], p["user_loss"]


def test_probabilities_sum_to_one():
    for minute in (0, 30, 55, 85):
        r = simulate(demo_state_at(minute), SimConfig(runs=2000, seed=minute))
        assert abs(sum(probs(r)) - 1) < 1e-9
        assert abs(sum(s["p"] for s in r["score_distribution"])) <= 1 + 1e-9


def test_consistent_across_seeds():
    st = demo_state_at(55)
    wins = [simulate(st, SimConfig(runs=N, seed=s))["probabilities"]["user_win"] for s in range(5)]
    assert max(wins) - min(wins) < 0.04  # Monte Carlo noise only
    a = simulate(st, SimConfig(runs=N, seed=7)); b = simulate(st, SimConfig(runs=N, seed=7))
    assert probs(a) == probs(b)  # deterministic for a fixed seed


def test_tactics_change_outcomes():
    st = demo_state_at(55)
    cfg = SimConfig(runs=N, seed=3, opponent_ai=False)
    attack = compare(st, st.home.tactics.copy(pressing="high", tempo="fast", width="wide"), cfg)
    defend = compare(st, st.home.tactics.copy(formation="5-3-2", pressing="low", defensive_line="deep", tempo="slow"), cfg)
    assert attack["after"]["xg"]["user_mean"] > attack["before"]["xg"]["user_mean"] * 1.1
    assert defend["after"]["xg"]["opp_mean"] < defend["before"]["xg"]["opp_mean"] * 0.9
    assert defend["after"]["probabilities"]["draw"] > defend["before"]["probabilities"]["draw"]
    assert attack["explanation"] and "Win probability" in attack["explanation"][0]


def test_opponent_adapts_to_high_line():
    st = demo_state_at(55)
    ai = OpponentAI("flexible", "normal")
    base, _ = ai.react(st, st.away, st.home)
    st.home.tactics = st.home.tactics.copy(defensive_line="high")
    new, reasons = ai.react(st, st.away, st.home)
    assert new.transition == "counterattack" and base.transition != "counterattack"
    assert any("high line" in r for r in reasons)


def test_opponent_reaction_hurts_user():
    st = demo_state_at(55)
    st.home.tactics = st.home.tactics.copy(defensive_line="high", pressing="high")
    on = simulate(st, SimConfig(runs=N, seed=5, opponent_personality="flexible", difficulty="hard"))
    off = simulate(st, SimConfig(runs=N, seed=5, opponent_personality="flexible", opponent_ai=False))
    assert on["xg"]["opp_mean"] > off["xg"]["opp_mean"]


def test_difficulty_scales_reactions():
    st = demo_state_at(60)
    st.home.tactics = Tactics("3-5-2", "high", "high", "wide", "fast")
    e = OpponentAI("direct", "easy").react(st, st.away, st.home)[1]
    n = OpponentAI("direct", "normal").react(st, st.away, st.home)[1]
    assert len(e) == 1 and len(n) == 2


def test_fatigue_accumulates_and_pressing_tires():
    st = demo_state_at(70)
    assert all(p.energy < 1 for p in st.home.starters if p.group != "GK")
    low = simulate(st, SimConfig(runs=500, seed=1)); 
    st.home.tactics = st.home.tactics.copy(pressing="counter-press")
    high = simulate(st, SimConfig(runs=500, seed=1))
    assert np.mean(list(high["final_energy_user"].values())) < np.mean(list(low["final_energy_user"].values()))


def test_fatigue_reduces_threat():
    fresh = demo_state_at(0); tired = demo_state_at(0)
    for p in tired.home.starters: p.energy = 0.3
    a = simulate(fresh, SimConfig(runs=N, seed=2, opponent_ai=False))
    b = simulate(tired, SimConfig(runs=N, seed=2, opponent_ai=False))
    assert b["xg"]["user_mean"] < a["xg"]["user_mean"] * 0.85
    assert b["probabilities"]["user_win"] < a["probabilities"]["user_win"]


def test_substitution_matters():
    st = demo_state_at(60)
    for p in st.home.starters:
        if p.group == "FWD": p.energy = 0.35
    cfg = SimConfig(runs=N, seed=9, opponent_ai=False)
    without = simulate(st, cfg)
    with_sub = simulate(st, SimConfig(**{**cfg.__dict__, "user_subs": [Substitution(61, "nb10", "nb16"), Substitution(61, "nb9", "nb17")]}))
    assert with_sub["xg"]["user_mean"] > without["xg"]["user_mean"]
    assert len(with_sub["user_substitutions"]) == 2


def test_invalid_inputs():
    st = demo_state_at(55)
    with pytest.raises(ValueError):
        simulate(st, SimConfig(runs=100, user_subs=[Substitution(60, "nope", "nb16")]))
    with pytest.raises(ValueError):
        Tactics(pressing="extreme").validate()


def test_score_already_counted():
    st = demo_state_at(85)
    r = simulate(st, SimConfig(runs=2000, seed=1))
    assert all(int(s["score"].split("-")[0]) >= st.home_goals for s in r["score_distribution"])
