"""AI tactical copilot: rule-based diagnosis grounded in the engine's rate model, plus
Monte Carlo evaluation of candidate adjustments. No LLM is used; all text is templated
from engine numbers, so every claim traces back to the model."""
from __future__ import annotations
import os
from typing import Dict, List, Optional, Tuple
from .models import MatchState, Tactics, Substitution, FORMATIONS
from .ratings import unit_ratings, assign_slots
from .simulate import minute_rates, compare, SimConfig
from .opponent_ai import OpponentAI

TOPICS = {
    "midfield": ["midfield", "control", "possession", "overrun"],
    "flank": ["winger", "wing", "flank", "full-back", "fullback", "left", "right"],
    "striker": ["striker", "isolated", "forward", "nine"],
    "press": ["press", "pressing", "intensity"],
    "subs": ["sub", "substitution", "bench", "tired", "fatigue", "legs"],
    "formation": ["formation", "shape", "system"],
    "defence": ["defence", "defense", "exposed", "line", "conceding", "behind us"],
    "space": ["space behind", "in behind", "exploit", "weakness", "weak"],
}


def classify(question: Optional[str]) -> List[str]:
    if not question:
        return []
    q = question.lower()
    return [t for t, kws in TOPICS.items() if any(k in q for k in kws)]


def diagnose(state: MatchState, user_side: str, cfg: SimConfig) -> Tuple[List[str], Dict]:
    st = state.clone()
    user = st.home if user_side == "home" else st.away
    opp = st.away if user_side == "home" else st.home
    if cfg.opponent_ai:
        opp.tactics, _ = OpponentAI(cfg.opponent_personality, cfg.difficulty).react(st, opp, user)
    r = minute_rates(st.home, st.away)
    h = user_side == "home"
    ur, orr = (r["ratings_h"], r["ratings_a"]) if h else (r["ratings_a"], r["ratings_h"])
    poss = r["poss"] if h else 1 - r["poss"]
    lam_u, lam_o = (r["lam_h"], r["lam_a"]) if h else (r["lam_a"], r["lam_h"])
    q_u, q_o = (r["xg_h"], r["xg_a"]) if h else (r["xg_a"], r["xg_h"])
    out = []
    facts = {"possession": poss, "xg_per_min_for": lam_u * q_u, "xg_per_min_against": lam_o * q_o,
             "control_ratio": ur["control"] / orr["control"], "attack_vs_def": ur["attack"] / orr["defence"]}
    if poss < 0.46:
        out.append(f"Midfield: you are projected to have {poss*100:.0f}% of the ball; their midfield control rating is "
                   f"{(orr['control']/ur['control']-1)*100:+.0f}% relative to yours.")
    else:
        out.append(f"Midfield: you are controlling the ball ({poss*100:.0f}% projected possession).")
    if q_o > q_u * 1.1:
        out.append(f"Chance quality: their average chance is worth {q_o:.2f} xG vs your {q_u:.2f} - they are getting better looks.")
    if lam_u * q_u < lam_o * q_o:
        out.append(f"Threat: at current settings they generate {lam_o*q_o*10:.2f} xG per 10 min vs your {lam_u*q_u*10:.2f}.")
    weak = "left" if orr["def_L"] < orr["def_R"] else "right"
    out.append(f"Their weaker flank is their {weak} side (defensive rating {min(orr['def_L'], orr['def_R']):.0f} vs {max(orr['def_L'], orr['def_R']):.0f}).")
    if opp.tactics.defensive_line == "high":
        out.append("They hold a high line - there is space in behind for quick forwards.")
    fwd = [p for l, p, _ in assign_slots(user) if l == "FWD"]
    if len(fwd) == 1:
        out.append(f"{fwd[0].name} is the lone forward; with {len([1 for l,*_ in assign_slots(user) if l=='MID'])} midfielders support can arrive late.")
    tired = sorted([p for p in user.starters if p.group != "GK" and p.energy < 0.7], key=lambda p: p.energy)
    if tired:
        out.append("Fatigue: " + ", ".join(f"{p.name} {p.energy*100:.0f}%" for p in tired[:4]) + " energy.")
    if any(pen < 1 for *_, pen in assign_slots(user)):
        out.append("Shape: " + ", ".join(p.name for l, p, pen in assign_slots(user) if pen < 1) + " are out of position.")
    facts.update({"opp_weak_side": weak, "opp_line": opp.tactics.defensive_line, "tired": [p.id for p in tired],
                  "opp_tactics": opp.tactics.to_dict()})
    return out, facts


def candidates(state: MatchState, user_side: str, t: Tactics, facts: Dict, topics: List[str]) -> List[Dict]:
    user = state.home if user_side == "home" else state.away
    c: List[Dict] = []
    def add(topic, label, why, subs=None, **chg):
        c.append({"topic": topic, "label": label, "why": why, "tactics": t.copy(**chg), "subs": subs or []})
    focus = "left" if facts["opp_weak_side"] == "right" else "right"  # our left attacks their right
    add("flank", f"Attack down the {focus} against their weaker side", "target the weaker full-back", attacking_focus=focus)
    add("flank", "Go wide to stretch them", "width pulls their block apart", width="wide")
    add("midfield", "Short build-up, slower tempo to control midfield", "keep the ball and limit their attacks",
        build_up="short", tempo="slow", transition="possession")
    add("midfield", "Switch to 4-2-3-1 for an extra midfielder", "more bodies centrally", formation="4-2-3-1")
    add("press", "Raise the press", "win the ball higher up", pressing="high")
    add("press", "Drop the press to medium/low", "save energy and deny space behind", pressing="low")
    add("defence", "Drop the line and stay compact", "remove space in behind",
        defensive_line="deep", defensive_approach="compact")
    add("defence", "Man-oriented marking", "disrupt their build-up", defensive_approach="man-oriented")
    if facts["opp_line"] == "high":
        add("space", "Counterattack into the space behind their high line", "fast breaks vs a high line",
            transition="counterattack", tempo="fast")
    fast = max((p for p in user.starters if p.group == "FWD"), key=lambda p: p.pac, default=None)
    if fast:
        add("space", f"{fast.name} to run in behind", "pace against their back line",
            roles={**t.roles, fast.id: "runner-in-behind"})
    cf = [p for l, p, _ in assign_slots(user) if l == "FWD"]
    if cf:
        lead = max(cf, key=lambda p: p.att)
        add("striker", f"{lead.name} as a false nine to drop and link play", "creates space for runners",
            roles={**t.roles, lead.id: "false-nine"})
        add("striker", "Switch to 4-4-2 to give the striker a partner", "a second forward", formation="4-4-2")
    for f in ("4-3-3", "4-4-2", "3-5-2", "4-2-3-1"):
        if f != t.formation:
            add("formation", f"Change to {f}", "different shape", formation=f)
    add("set", "Near-post corner routine", "exploit their marking", set_piece_attack="near-post")
    # substitutions: most tired outfield players for best like-for-like bench option
    minute = state.minute + 1
    for pid in facts["tired"][:3]:
        p = next(x for x in user.starters if x.id == pid)
        bench = [b for b in user.bench if b.group == p.group]
        if bench:
            b = max(bench, key=lambda x: x.att + x.defn + x.pas)
            add("subs", f"Bring on {b.name} for {p.name} ({p.energy*100:.0f}% energy)", "fresh legs",
                subs=[Substitution(minute, p.id, b.id)])
    if topics:
        picked = [x for x in c if x["topic"] in topics]
        c = picked or c
    return c


def advise(state: MatchState, user_tactics: Tactics, cfg: SimConfig, question: Optional[str] = None,
           user_side: str = "home", eval_runs: int = 1500) -> Dict:
    s0 = state.clone()
    (s0.home if user_side == "home" else s0.away).tactics = user_tactics
    diag, facts = diagnose(s0, user_side, cfg)
    topics = classify(question)
    cands = candidates(s0, user_side, user_tactics, facts, topics)
    ev_cfg = SimConfig(**{**cfg.__dict__, "runs": eval_runs, "user_subs": list(cfg.user_subs)})
    def points(res): p = res["probabilities"]; return 3 * p["user_win"] + p["draw"]
    scored = []
    base = None
    for cand in cands:
        cc = SimConfig(**{**ev_cfg.__dict__, "user_subs": list(cfg.user_subs) + cand["subs"]})
        res = compare(s0, cand["tactics"], cc, baseline_subs=list(cfg.user_subs))
        base = res["before"]
        scored.append((points(res["after"]) - points(res["before"]), cand, res))
    scored.sort(key=lambda x: -x[0])
    best_gain, best, res = scored[0]
    keep_current = best_gain <= 0.01
    a, b = res["after"], res["before"]
    benefits, risks = [], []
    if a["xg"]["user_mean"] > b["xg"]["user_mean"]:
        benefits.append(f"Your xG {b['xg']['user_mean']:.2f} -> {a['xg']['user_mean']:.2f} for the rest of the match.")
    if a["xg"]["opp_mean"] < b["xg"]["opp_mean"]:
        benefits.append(f"Their xG {b['xg']['opp_mean']:.2f} -> {a['xg']['opp_mean']:.2f}.")
    benefits.append(f"Expected points {points(b):.2f} -> {points(a):.2f} (win {b['probabilities']['user_win']*100:.0f}% -> {a['probabilities']['user_win']*100:.0f}%).")
    if a["xg"]["opp_mean"] > b["xg"]["opp_mean"] * 1.03:
        risks.append(f"More exposed: their xG rises {b['xg']['opp_mean']:.2f} -> {a['xg']['opp_mean']:.2f}.")
    if a["probabilities"]["user_loss"] > b["probabilities"]["user_loss"] + 0.01:
        risks.append(f"Loss probability up {b['probabilities']['user_loss']*100:.0f}% -> {a['probabilities']['user_loss']*100:.0f}%.")
    fb = sum(a["final_energy_user"].values()) / len(a["final_energy_user"]); fa = sum(b["final_energy_user"].values()) / len(b["final_energy_user"])
    if fb < fa - 0.02:
        risks.append(f"Heavier legs: average finishing energy {fa*100:.0f}% -> {fb*100:.0f}%.")
    if not risks:
        risks.append("No material downside detected by the model at this sample size (Monte Carlo noise ~±2 pts).")
    opp_resp = [r for r in a["opponent"]["reactions"]]
    if a["opponent"].get("in_game_variants"):
        v = a["opponent"]["in_game_variants"]
        opp_resp.append(f"If they fall behind they will {v['why_chasing']}; if ahead late they will {v['why_protecting']}.")
    rec = best
    return {
        "engine": "rule-based + Monte Carlo (no LLM configured)" if not os.getenv("OPENAI_API_KEY") else
                  "rule-based + Monte Carlo (LLM key present but not used in Phase 1)",
        "question": question, "topics": topics, "diagnosis": diag,
        "recommendation": {"label": "Keep current set-up - no tested change improves expected points" if keep_current else rec["label"],
                           "why": rec["why"], "tactics": rec["tactics"].to_dict(),
                           "subs": [s.__dict__ for s in rec["subs"]], "keep_current": keep_current,
                           "expected_points_gain": round(best_gain, 3)},
        "benefits": benefits, "risks": risks, "opponent_response": opp_resp,
        "simulation": {"before": b["probabilities"], "after": a["probabilities"],
                       "xg_before": b["xg"], "xg_after": a["xg"], "runs": eval_runs},
        "alternatives": [{"label": c["label"], "expected_points_gain": round(g, 3),
                          "tactics": c["tactics"].to_dict(), "subs": [s.__dict__ for s in c["subs"]]} for g, c, _ in scored[1:5]],
        "label": "HYPOTHETICAL SIMULATION - advice derived from the Touchline model, not a guarantee",
    }
