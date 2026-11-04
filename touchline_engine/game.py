"""Game modes: daily tactical challenge (decision score) and post-match autopsy."""
from __future__ import annotations
import hashlib
from typing import Dict, Optional
from .demo_data import demo_state_at, DEMO_TIMELINE
from .models import Tactics
from .simulate import compare, SimConfig, simulate
from .opponent_ai import PERSONALITIES

CHALLENGE_RUNS = 3000


def daily_challenge(date: str) -> Dict:
    h = int(hashlib.sha256(date.encode()).hexdigest(), 16)
    minute = 50 + h % 26
    pers = list(PERSONALITIES)[(h >> 8) % len(PERSONALITIES)]
    diff = ["normal", "hard"][(h >> 16) % 2]
    st = demo_state_at(minute)
    delta = [(0, 0), (0, 1), (1, 0), (0, 0)][(h >> 20) % 4]
    st.home_goals += delta[0]; st.away_goals += delta[1]
    return {"date": date, "minute": minute, "score": [st.home_goals, st.away_goals], "opponent_personality": pers,
            "difficulty": diff, "seed": h % 100000,
            "brief": f"{minute}' - Real Madrid {st.home_goals}-{st.away_goals} Man City ({pers} AI, {diff}). Make the decision that maximises expected points.",
            "_state": st}


def _points(res):
    p = res["probabilities"]; return 3 * p["user_win"] + p["draw"]


def score_decision(date: str, tactics: Tactics, subs=None, reference: Optional[Dict] = None) -> Dict:
    """Decision score 0-100: 50 = no better than current set-up, 100 = matches/beats the copilot's best tested option."""
    from .copilot import advise
    ch = daily_challenge(date)
    st = ch["_state"]
    cfg = SimConfig(runs=CHALLENGE_RUNS, seed=ch["seed"], opponent_personality=ch["opponent_personality"],
                    difficulty=ch["difficulty"], user_subs=subs or [])
    res = compare(st, tactics, cfg)
    gain = _points(res["after"]) - _points(res["before"])
    ref = reference or advise(st, st.home.tactics, SimConfig(**{**cfg.__dict__, "user_subs": []}), eval_runs=1200)
    best = max(ref["recommendation"]["expected_points_gain"], 0.05)
    score = 50 + 50 * gain / best if gain >= 0 else 50 + 50 * gain / best
    return {"date": date, "decision_score": round(max(0, min(100, score)), 1), "expected_points_gain": round(gain, 3),
            "best_known_gain": round(best, 3), "best_known": ref["recommendation"]["label"],
            "before": res["before"]["probabilities"], "after": res["after"]["probabilities"],
            "explanation": res["explanation"], "label": res["label"]}


def autopsy(alternate: Optional[Dict] = None) -> Dict:
    """Post-match report on the (demo) real match, optionally contrasted with an alternate-timeline result."""
    ev = DEMO_TIMELINE
    xg = {"home": 0.0, "away": 0.0}; goals = {"home": 0, "away": 0}; cum = []
    for e in sorted(ev, key=lambda e: e["minute"]):
        if "xg" in e: xg[e["team"]] += e["xg"]
        if e["type"] == "goal": goals[e["team"]] += 1
        cum.append({"minute": e["minute"], "home_xg": round(xg["home"], 2), "away_xg": round(xg["away"], 2)})
    # model view: what the engine thought at key moments of the real match (no user changes)
    checkpoints = []
    for m in (0, 30, 55, 75):
        r = simulate(demo_state_at(m), SimConfig(runs=3000, seed=m, opponent_personality="counter"))
        checkpoints.append({"minute": m, "home_win": r["probabilities"]["user_win"], "draw": r["probabilities"]["draw"],
                            "away_win": r["probabilities"]["user_loss"]})
    findings = [
        f"Recorded xG (demo feed shots): Real Madrid {xg['home']:.2f} vs Man City {xg['away']:.2f}; goals {goals['home']}-{goals['away']}.",
        "Man City scored twice from counterattacks/fast breaks - their deep block + counter plan worked (demo events at 23' and 79').",
        f"The model gave Real Madrid {checkpoints[2]['home_win']*100:.0f}% to win from 55' at 1-1; the result was a defeat.",
    ]
    if xg["away"] > goals["away"] * 0 and goals["away"] > xg["away"]:
        findings.append(f"Man City overperformed their xG by {goals['away'] - xg['away']:.2f} goals - finishing variance, not necessarily dominance.")
    out = {"label": "POST-MATCH AUTOPSY - demo match (game-estimated data); model views are hypothetical",
           "final_score": [goals["home"], goals["away"]], "xg": xg, "xg_timeline": cum,
           "key_moments": [e for e in ev if e["type"] in ("goal", "sub")], "model_checkpoints": checkpoints,
           "findings": findings}
    if alternate:
        out["alternate_comparison"] = {"scenario": alternate.get("name"),
                                       "alt_win": alternate["result"]["probabilities"]["user_win"],
                                       "real_outcome": "loss",
                                       "note": "Alternate timeline is a simulation; the real (demo) result is unchanged."}
    return out
