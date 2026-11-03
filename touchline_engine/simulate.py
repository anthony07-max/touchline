"""Event-by-event (per-minute) possession/chance model with vectorised Monte Carlo."""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Tuple
import math
import numpy as np

from .models import MatchState, Team, Tactics, Substitution
from .ratings import unit_ratings, tactic_modifiers

BASE_SHOT_RATE = 0.21    # shots / team / minute for two equal teams at neutral tactics (~13 per 90 after modifiers)
BASE_XG = 0.115          # average xG per shot (roughly top-flight average)
SP_RATE = 0.03           # set-piece shots / team / minute
SP_XG = 0.08             # mean xG of a set-piece shot
FATIGUE_BASE = 0.0062    # energy lost per minute for a 0-stamina player at neutral tactics
END_MINUTE = 90


def minute_rates(home: Team, away: Team, momentum: float = 0.0) -> Dict:
    rh, ra = unit_ratings(home), unit_ratings(away)
    mh, fh = tactic_modifiers(home.tactics, away.tactics, rh, ra, home, away)
    ma, fa = tactic_modifiers(away.tactics, home.tactics, ra, rh, away, home)
    poss = 0.5 + 0.6 * (rh["control"] - ra["control"]) + mh["pos"] - ma["pos"] + 0.03 * momentum
    poss = float(np.clip(poss, 0.28, 0.72))
    def side(r_own, r_opp, m_own, m_opp, p):
        rate = BASE_SHOT_RATE * (p / 0.5) ** 0.6 * (r_own["attack"] / r_opp["defence"]) ** 1.6 \
            * m_own["att"] * m_opp["exp"]
        q = BASE_XG * m_own["q"] * m_opp["expq"] * (r_own["attack"] / (0.6 * r_opp["defence"] + 0.4 * r_opp["gk"])) ** 0.8
        return rate, float(np.clip(q, 0.03, 0.45))
    lam_h, xg_h = side(rh, ra, mh, ma, poss)
    lam_a, xg_a = side(ra, rh, ma, mh, 1 - poss)
    # set pieces: ~4.5 dangerous set-piece shots per team per match at baseline; rate scales with territory
    sp_h = SP_RATE * (lam_h / BASE_SHOT_RATE) ** 0.5 * mh["sp_rate"]
    sp_a = SP_RATE * (lam_a / BASE_SHOT_RATE) ** 0.5 * ma["sp_rate"]
    spq_h = float(np.clip(SP_XG * mh["sp_q"] * ma["sp_expq"], 0.02, 0.3))
    spq_a = float(np.clip(SP_XG * ma["sp_q"] * mh["sp_expq"], 0.02, 0.3))
    return {"poss": poss, "lam_h": lam_h, "xg_h": xg_h, "lam_a": lam_a, "xg_a": xg_a,
            "sp_h": sp_h, "spq_h": spq_h, "sp_a": sp_a, "spq_a": spq_a,
            "fat_h": mh["fat"], "fat_a": ma["fat"], "factors_h": fh, "factors_a": fa,
            "ratings_h": rh, "ratings_a": ra}


def drain(team: Team, fat_mult: float):
    for p in team.starters:
        rate = 0.0 if p.group == "GK" else FATIGUE_BASE * (1.25 - p.sta / 100) * fat_mult
        p.energy = max(0.0, p.energy - rate)


@dataclass
class SimConfig:
    runs: int = 5000
    seed: Optional[int] = 42
    user_side: str = "home"
    opponent_personality: str = "flexible"
    difficulty: str = "normal"
    opponent_ai: bool = True
    user_subs: List[Substitution] = field(default_factory=list)


def _wilson(p: float, n: int, z: float = 1.96) -> Tuple[float, float]:
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return max(0.0, c - h), min(1.0, c + h)


def simulate(state: MatchState, cfg: SimConfig) -> Dict:
    from .opponent_ai import OpponentAI
    st = state.clone()
    user = st.home if cfg.user_side == "home" else st.away
    opp = st.away if cfg.user_side == "home" else st.home
    for t in (st.home.tactics, st.away.tactics):
        t.validate()
    ai = OpponentAI(cfg.opponent_personality, cfg.difficulty)
    reactions = []
    if cfg.opponent_ai:
        new_t, reactions = ai.react(st, opp, user)
        opp.tactics = new_t
    base_opp_tactics = opp.tactics.copy()
    variants = ai.in_game_variants(base_opp_tactics) if cfg.opponent_ai else {}
    subs_by_min: Dict[int, List[Substitution]] = {}
    for s in cfg.user_subs:
        subs_by_min.setdefault(max(s.minute, st.minute), []).append(s)

    rng = np.random.default_rng(cfg.seed)
    rrng = np.random.default_rng(None if cfg.seed is None else cfg.seed + 7919)  # replay-only randomness
    n = cfg.runs
    gh = np.full(n, st.home_goals); ga = np.full(n, st.away_goals)
    xgh = np.zeros(n); xga = np.zeros(n); sh = np.zeros(n); sa = np.zeros(n)
    mode = np.zeros(n, dtype=int)            # opponent in-game state: 0 plan, 1 chasing, 2 protecting
    last_goal = np.full(n, st.minute)
    switched = np.zeros(n, dtype=bool)
    delay = {"easy": 10, "normal": 5, "hard": 2}[cfg.difficulty]
    unit_track, poss_track, opp_subs, user_sub_log = [], [], [], []
    sample, frames = [], []
    first_rates = None
    ball = 0.5
    opp_is_home = cfg.user_side == "away"
    for minute in range(st.minute + 1, END_MINUTE + 1):
        for s in subs_by_min.get(minute, []):
            try:
                user.substitute(s.out_id, s.in_id); user_sub_log.append({"minute": minute, "out": s.out_id, "in": s.in_id})
                sample.append({"minute": minute, "type": "sub", "team": user.name, "text": f"{s.in_id} on for {s.out_id}"})
            except StopIteration:
                raise ValueError(f"invalid substitution {s.out_id}->{s.in_id}")
        if cfg.opponent_ai:
            for sub in ai.maybe_substitute(opp, minute):
                opp_subs.append(sub)
                sample.append({"minute": minute, "type": "sub", "team": opp.name, "text": sub["text"]})
        mom = st.momentum if minute - st.minute < 10 else 0.0
        r = minute_rates(st.home, st.away, mom)
        rate_sets = [r]
        if variants:
            for key in ("chasing", "protecting"):
                opp.tactics = variants[key]
                rate_sets.append(minute_rates(st.home, st.away, mom))
            opp.tactics = base_opp_tactics
        if first_rates is None:
            first_rates = r
        poss_track.append(r["poss"])
        unit_track.append(r["ratings_h"] if cfg.user_side == "home" else r["ratings_a"])
        # opponent re-assesses after goals (with a difficulty-dependent delay) and late in the game
        diff = gh - ga
        opp_diff = -diff if not opp_is_home else diff
        if variants:
            due = (minute - last_goal) >= delay
            target = np.where(opp_diff < 0, 1, np.where((opp_diff > 0) & (minute >= 60), 2, 0))
            change = due & (target != mode)
            if change[0]:
                label = {0: "returns to its game plan", 1: "chases the game: " + variants["why_chasing"],
                         2: "protects the lead: " + variants["why_protecting"]}[int(target[0])]
                sample.append({"minute": minute, "type": "tactic", "team": opp.name, "text": f"AI {label}"})
            switched |= change & (target != 0)
            mode = np.where(change, target, mode)
        def pick(key):
            if len(rate_sets) == 1:
                return np.full(n, r[key])
            return np.choose(mode, [rs[key] for rs in rate_sets])
        late = 1.0 if minute >= 60 else 0.5
        lam_h, lam_a = pick("lam_h"), pick("lam_a")
        if not variants:  # no AI: generic game-state effects for both sides
            lam_h = lam_h * np.where(diff < 0, 1 + 0.15 * late, np.where(diff > 0, 1 - 0.08 * late, 1.0))
            lam_a = lam_a * np.where(diff > 0, 1 + 0.15 * late, np.where(diff < 0, 1 - 0.08 * late, 1.0))
        else:  # the user's tactics are fixed by the user; only small psychological game-state effect
            u_diff = diff if not opp_is_home else -diff
            push = np.where(u_diff < 0, 1 + 0.05 * late, 1.0)
            if opp_is_home: lam_a = lam_a * push
            else: lam_h = lam_h * push
        streams = (
            (lam_h, pick("xg_h"), pick("sp_h"), pick("spq_h"), gh, xgh, sh, st.home.name, "home"),
            (lam_a, pick("xg_a"), pick("sp_a"), pick("spq_a"), ga, xga, sa, st.away.name, "away"))
        scored = np.zeros(n, dtype=bool)
        for lam, mean_xg, sp_lam, sp_q, goals, xgs, shots, name, side_ in streams:
            for kind, rate, q in (("open", lam, mean_xg), ("set-piece", sp_lam, sp_q)):
                shot = rng.random(n) < np.minimum(rate, 0.9)
                # Beta(a, b) with mean q; small a gives realistic skew (many low-xG shots, few big chances)
                shot_xg = rng.beta(0.9, 0.9 * (1 - q) / q)
                goal = shot & (rng.random(n) < shot_xg)
                xgs += np.where(shot, shot_xg, 0.0); shots += shot; goals += goal; scored |= goal
                if shot[0]:
                    sample.append({"minute": minute, "type": "goal" if goal[0] else "shot", "team": name, "side": side_,
                                   "kind": kind, "xg": round(float(shot_xg[0]), 2)})
        last_goal = np.where(scored, minute, last_goal)
        # replay frame for run 0: possession owner and ball position (0 = home goal, 1 = away goal)
        poss_side = "home" if rrng.random() < r["poss"] else "away"
        drift = (0.12 if poss_side == "home" else -0.12) * rrng.random()
        ball = float(np.clip(ball * 0.6 + 0.2 + drift + rrng.normal(0, 0.08), 0.05, 0.95))
        frames.append({"minute": minute, "poss": poss_side, "ball": round(ball, 3),
                       "score": [int(gh[0]), int(ga[0])]})
        drain(st.home, r["fat_h"]); drain(st.away, r["fat_a"])

    win_h = float(np.mean(gh > ga)); draw = float(np.mean(gh == ga)); win_a = float(np.mean(gh < ga))
    u_win, u_loss = (win_h, win_a) if cfg.user_side == "home" else (win_a, win_h)
    u_xg, o_xg = (xgh, xga) if cfg.user_side == "home" else (xga, xgh)
    u_g, o_g = (gh, ga) if cfg.user_side == "home" else (ga, gh)
    scores, counts = np.unique(np.stack([gh, ga], 1), axis=0, return_counts=True)
    order = np.argsort(-counts)[:8]
    fr = first_rates or minute_rates(st.home, st.away)
    u_lam, o_lam = (fr["lam_h"], fr["lam_a"]) if cfg.user_side == "home" else (fr["lam_a"], fr["lam_h"])
    u_q, o_q = (fr["xg_h"], fr["xg_a"]) if cfg.user_side == "home" else (fr["xg_a"], fr["xg_h"])
    poss = float(np.mean(poss_track)) if poss_track else fr["poss"]
    return {
        "label": "HYPOTHETICAL SIMULATION - not a real match event or guaranteed prediction",
        "runs": n, "seed": cfg.seed, "from_minute": st.minute,
        "start_score": [state.home_goals, state.away_goals],
        "probabilities": {
            "user_win": u_win, "draw": draw, "user_loss": u_loss,
            "ci95": {"user_win": _wilson(u_win, n), "draw": _wilson(draw, n), "user_loss": _wilson(u_loss, n)},
        },
        "home_win": win_h, "away_win": win_a,
        "xg": {"user_mean": float(u_xg.mean()), "opp_mean": float(o_xg.mean()),
               "user_p10_p90": [float(np.percentile(u_xg, 10)), float(np.percentile(u_xg, 90))],
               "opp_p10_p90": [float(np.percentile(o_xg, 10)), float(np.percentile(o_xg, 90))]},
        "p_user_scores": float(np.mean(u_g > (state.home_goals if cfg.user_side == "home" else state.away_goals))),
        "p_user_concedes": float(np.mean(o_g > (state.away_goals if cfg.user_side == "home" else state.home_goals))),
        "shots": {"user_mean": float((sh if cfg.user_side == "home" else sa).mean()),
                  "opp_mean": float((sa if cfg.user_side == "home" else sh).mean())},
        "possession_user": poss if cfg.user_side == "home" else 1 - poss,
        "chance_rate_user": u_lam, "chance_rate_opp": o_lam,
        "xg_per_shot_user": u_q, "xg_per_shot_opp": o_q,
        "score_distribution": [{"score": f"{int(s[0])}-{int(s[1])}", "p": float(c / n)} for s, c in zip(scores[order], counts[order])],
        "opponent": {"personality": cfg.opponent_personality, "difficulty": cfg.difficulty,
                     "tactics": opp.tactics.to_dict(), "reactions": reactions, "substitutions": opp_subs,
                     "in_game_variants": {k: (v.to_dict() if hasattr(v, "to_dict") else v) for k, v in variants.items()}},
        "user_tactics": user.tactics.to_dict(), "user_substitutions": user_sub_log,
        "factors_user": [{"key": k, "why": w, "value": v} for k, w, v in (fr["factors_h"] if cfg.user_side == "home" else fr["factors_a"])],
        "units_user": {k: float(np.mean([u[k] for u in unit_track])) for k in ("attack", "control", "defence")} if unit_track else {},
        "out_of_position_user": [p.name for l, p, pen in __import__("touchline_engine.ratings", fromlist=["x"]).assign_slots(user) if pen < 1],
        "final_energy_user": {p.name: round(p.energy, 2) for p in user.starters},
        "sample_replay": sample, "replay_frames": frames,
        "opponent_in_game_switch_rate": float(switched.mean()),
    }


def _pct(a, b):
    return f"{a*100:.0f}% -> {b*100:.0f}%"


def explain(before: Dict, after: Dict) -> List[str]:
    out = []
    pb, pa = before["probabilities"], after["probabilities"]
    d = pa["user_win"] - pb["user_win"]
    out.append(f"Win probability {_pct(pb['user_win'], pa['user_win'])} ({'+' if d >= 0 else ''}{d*100:.1f} pts); "
               f"draw {_pct(pb['draw'], pa['draw'])}; loss {_pct(pb['user_loss'], pa['user_loss'])}.")
    cr = after["chance_rate_user"] / before["chance_rate_user"] - 1
    ex = after["chance_rate_opp"] / before["chance_rate_opp"] - 1
    q = after["xg_per_shot_user"] / before["xg_per_shot_user"] - 1
    oq = after["xg_per_shot_opp"] / before["xg_per_shot_opp"] - 1
    out.append(f"Your chance creation {cr*100:+.0f}% and average chance quality {q*100:+.0f}%; "
               f"defensive exposure (their shot rate) {ex*100:+.0f}% with their chance quality {oq*100:+.0f}%.")
    out.append(f"Expected goals for the rest of the match: you {before['xg']['user_mean']:.2f} -> {after['xg']['user_mean']:.2f}, "
               f"them {before['xg']['opp_mean']:.2f} -> {after['xg']['opp_mean']:.2f}.")
    ub, ua = before.get("units_user", {}), after.get("units_user", {})
    for k, label in (("attack", "attacking unit"), ("control", "midfield control"), ("defence", "defensive unit")):
        if ub.get(k) and abs(ua[k] / ub[k] - 1) >= 0.02:
            out.append(f"Personnel/shape: your {label} rating (fatigue- and position-adjusted, averaged over the rest of the match) {(ua[k]/ub[k]-1)*100:+.0f}%.")
    if after.get("out_of_position_user"):
        out.append("Out of position in this shape: " + ", ".join(after["out_of_position_user"]) + " (-14% effectiveness).")
    old = {f["why"] for f in before["factors_user"]}
    new = [f for f in after["factors_user"] if f["why"] not in old]
    gone = [f for f in before["factors_user"] if f["why"] not in {g["why"] for g in after["factors_user"]}]
    for f in sorted(new, key=lambda f: -abs((f["value"] - 1) if f["key"] != "pos" else f["value"] * 3))[:4]:
        out.append("Now: " + f["why"] + ".")
    for f in gone[:2]:
        out.append("No longer: " + f["why"] + ".")
    for r in after["opponent"]["reactions"]:
        if r not in before["opponent"]["reactions"]:
            out.append("Opponent response: " + r)
    fe_b, fe_a = before["final_energy_user"], after["final_energy_user"]
    tired_b = sum(1 for v in fe_b.values() if v < 0.6); tired_a = sum(1 for v in fe_a.values() if v < 0.6)
    if tired_a != tired_b:
        out.append(f"Fatigue: {tired_a} of your players finish below 60% energy (was {tired_b}).")
    return out


def compare(state: MatchState, user_tactics: Tactics, cfg: SimConfig,
            baseline_subs: Optional[List[Substitution]] = None) -> Dict:
    """Baseline (current tactics, no user subs) vs alternate (new tactics + subs), common random numbers."""
    base_cfg = SimConfig(**{**cfg.__dict__, "user_subs": baseline_subs or []})
    before = simulate(state, base_cfg)
    alt = state.clone()
    if cfg.user_side == "home":
        alt.home.tactics = user_tactics
    else:
        alt.away.tactics = user_tactics
    after = simulate(alt, cfg)
    return {"label": before["label"], "before": before, "after": after, "explanation": explain(before, after)}
