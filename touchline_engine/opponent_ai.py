"""Opponent manager AI with tactical personalities and difficulty scaling."""
from __future__ import annotations
from typing import List, Tuple, Dict
from .models import Tactics, Team, MatchState
from .ratings import unit_ratings

PERSONALITIES: Dict[str, Tactics] = {
    "high-press": Tactics("4-3-3", "high", "high", "balanced", "fast", "mixed", "balanced", "balanced"),
    "counter": Tactics("4-5-1", "low", "deep", "narrow", "balanced", "direct", "balanced", "counterattack"),
    "possession": Tactics("4-3-3", "counter-press", "balanced", "wide", "slow", "short", "balanced", "possession"),
    "flexible": Tactics("4-2-3-1", "medium", "balanced", "balanced", "balanced", "mixed", "balanced", "balanced"),
    "direct": Tactics("4-4-2", "medium", "balanced", "wide", "fast", "direct", "balanced", "balanced"),
}
MAX_REACTIONS = {"easy": 1, "normal": 2, "hard": 4}
SUB_MINUTES = {"easy": [70], "normal": [62, 72], "hard": [58, 66, 75]}


class OpponentAI:
    def __init__(self, personality: str = "flexible", difficulty: str = "normal"):
        if personality not in PERSONALITIES:
            raise ValueError(f"personality must be one of {list(PERSONALITIES)}")
        if difficulty not in MAX_REACTIONS:
            raise ValueError("difficulty must be easy/normal/hard")
        self.personality, self.difficulty = personality, difficulty

    def react(self, state: MatchState, me: Team, user: Team) -> Tuple[Tactics, List[str]]:
        """Read the user's set-up and return adjusted tactics plus reasons (in priority order)."""
        t = PERSONALITIES[self.personality].copy()
        u = user.tactics
        my_goals, their_goals = ((state.away_goals, state.home_goals) if me is state.away
                                 else (state.home_goals, state.away_goals))
        ur, mr = unit_ratings(user), unit_ratings(me)
        cands: List[Tuple[str, dict]] = []
        if u.defensive_line == "high" and self.personality != "possession":
            side = "left" if ur["def_R"] < ur["def_L"] else "right"
            cands.append((f"Spotted your high line: switched to counterattacks, faster tempo and targeted your weaker "
                          f"{'right' if side == 'left' else 'left'} side.",
                          {"transition": "counterattack", "tempo": "fast", "attacking_focus": side}))
        if u.pressing in ("high", "counter-press"):
            if self.personality in ("direct", "counter", "flexible", "high-press"):
                cands.append(("Your press is high, so they go direct to play over it.", {"build_up": "direct"}))
            else:
                cands.append(("Your press is high: they keep short build-up but drop the tempo to draw you in.",
                              {"tempo": "slow"}))
        if u.formation in ("3-5-2", "3-4-3", "5-3-2"):
            cands.append((f"You moved to a back three ({u.formation}): they go wide to attack the channels.",
                          {"width": "wide"}))
        if u.tempo == "fast" or u.formation in ("4-3-3", "3-4-3") and u.pressing == "high":
            if my_goals > their_goals:
                cands.append(("They are ahead and you are pushing: they drop deeper and stay compact.",
                              {"defensive_line": "deep", "pressing": "low", "width": "narrow"}))
        if u.defensive_line == "deep" or u.pressing == "low":
            cands.append(("You are sitting deep: they push up and press higher to pin you in.",
                          {"defensive_line": "high", "pressing": "high" if self.personality != "counter" else "medium"}))
        if my_goals < their_goals and state.minute >= 55:
            cands.append(("They are chasing the game: higher press and a faster tempo.",
                          {"pressing": "high", "tempo": "fast"}))
        if u.width == "wide":
            cands.append(("You are playing wide: they narrow up to protect the middle.", {"width": "narrow"}))

        n = MAX_REACTIONS[self.difficulty]
        if self.difficulty == "hard":
            cands = self._rank(cands, t, state, me, user)
        reasons = []
        for why, change in cands[:n]:
            t = t.copy(**change); reasons.append(why)
        if not reasons:
            reasons.append(f"Sticks to its {self.personality} identity - nothing in your set-up demands a change.")
        return t, reasons

    def _rank(self, cands, base, state, me, user):
        """Hard AI: evaluate each candidate with the analytic rate model and keep the most helpful."""
        from .simulate import minute_rates
        def value(change):
            m = me.clone(); m.tactics = base.copy(**change)
            home, away = (user, m) if me is state.away else (m, user)
            r = minute_rates(home, away)
            if me is state.away:
                return r["lam_a"] * r["xg_a"] - r["lam_h"] * r["xg_h"]
            return r["lam_h"] * r["xg_h"] - r["lam_a"] * r["xg_a"]
        b = value({})
        scored = [(value(c) - b, w, c) for w, c in cands]
        return [(w, c) for v, w, c in sorted(scored, key=lambda x: -x[0]) if v > 0]

    def maybe_substitute(self, team: Team, minute: int) -> List[Dict]:
        if minute not in SUB_MINUTES[self.difficulty] or not team.bench:
            return []
        tired = sorted([p for p in team.starters if p.group != "GK"], key=lambda p: p.energy)
        for p in tired:
            if p.energy > 0.75:
                break
            repl = [b for b in team.bench if b.group == p.group]
            if repl:
                best = max(repl, key=lambda b: b.att + b.defn + b.pas)
                team.substitute(p.id, best.id)
                return [{"minute": minute, "out": p.name, "in": best.name,
                         "text": f"{best.name} on for tired {p.name} ({p.energy*100:.0f}% energy)"}]
        return []


def _in_game_variants(self, base: Tactics) -> Dict:
    """How this personality changes when chasing or protecting a result (used per Monte Carlo run)."""
    p = self.personality
    chasing = {"high-press": dict(pressing="counter-press", tempo="fast", defensive_line="high"),
               "counter": dict(tempo="fast", defensive_line="balanced", pressing="medium", transition="balanced"),
               "possession": dict(tempo="balanced", pressing="counter-press", width="wide"),
               "flexible": dict(pressing="high", tempo="fast", defensive_line="high", formation="4-3-3"),
               "direct": dict(tempo="fast", build_up="direct", pressing="high", set_piece_attack="aerial")}[p]
    protect = {"high-press": dict(pressing="medium", defensive_line="balanced", tempo="balanced"),
               "counter": dict(pressing="low", defensive_line="deep", defensive_approach="compact", width="narrow"),
               "possession": dict(tempo="slow", transition="possession", pressing="medium"),
               "flexible": dict(pressing="low", defensive_line="deep", defensive_approach="compact", formation="5-3-2"),
               "direct": dict(pressing="low", defensive_line="deep", tempo="balanced")}[p]
    fmt = lambda d: ", ".join(f"{k.replace('_', ' ')} {v}" for k, v in d.items())
    return {"chasing": base.copy(**chasing), "protecting": base.copy(**protect),
            "why_chasing": fmt(chasing), "why_protecting": fmt(protect)}


OpponentAI.in_game_variants = _in_game_variants
