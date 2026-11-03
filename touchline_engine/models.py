from __future__ import annotations
from dataclasses import dataclass, field, asdict, replace
from typing import List, Optional, Dict

FORMATIONS: Dict[str, Dict[str, int]] = {
    "4-3-3": {"DEF": 4, "MID": 3, "FWD": 3},
    "4-2-3-1": {"DEF": 4, "MID": 5, "FWD": 1},
    "4-4-2": {"DEF": 4, "MID": 4, "FWD": 2},
    "3-5-2": {"DEF": 3, "MID": 5, "FWD": 2},
    "5-3-2": {"DEF": 5, "MID": 3, "FWD": 2},
    "4-5-1": {"DEF": 4, "MID": 5, "FWD": 1},
    "3-4-3": {"DEF": 3, "MID": 4, "FWD": 3},
}

OPTIONS = {
    "pressing": ["low", "medium", "high", "counter-press"],
    "defensive_line": ["deep", "balanced", "high"],
    "width": ["narrow", "balanced", "wide"],
    "tempo": ["slow", "balanced", "fast"],
    "build_up": ["short", "mixed", "direct"],
    "attacking_focus": ["left", "centre", "right", "balanced"],
    "transition": ["counterattack", "balanced", "possession"],
    "defensive_approach": ["zonal", "man-oriented", "compact"],
    "set_piece_attack": ["standard", "near-post", "aerial", "short"],
    "set_piece_defence": ["zonal", "man", "mixed"],
}

# role -> lines where it is valid
ROLES = {
    "target-man": ["FWD"], "false-nine": ["FWD"], "poacher": ["FWD"], "runner-in-behind": ["FWD"],
    "inverted-winger": ["FWD", "MID"], "ball-playing-defender": ["DEF"], "wing-back": ["DEF"],
    "box-to-box": ["MID"], "anchor": ["MID"], "playmaker": ["MID"],
}


@dataclass
class Player:
    id: str
    name: str
    group: str  # GK DEF MID FWD
    side: str = "C"  # L C R
    att: int = 60
    defn: int = 60
    pas: int = 60
    pac: int = 60
    sta: int = 70
    aer: int = 60  # aerial ability
    energy: float = 1.0  # 1 fresh -> 0 exhausted

    def eff(self, attr: str) -> float:
        return getattr(self, attr) * (0.72 + 0.28 * self.energy)


@dataclass
class Tactics:
    formation: str = "4-3-3"
    pressing: str = "medium"
    defensive_line: str = "balanced"
    width: str = "balanced"
    tempo: str = "balanced"
    build_up: str = "mixed"
    attacking_focus: str = "balanced"
    transition: str = "balanced"
    defensive_approach: str = "zonal"
    set_piece_attack: str = "standard"
    set_piece_defence: str = "zonal"
    roles: Dict[str, str] = field(default_factory=dict)  # player_id -> role

    def validate(self):
        for pid, r in self.roles.items():
            if r not in ROLES:
                raise ValueError(f"role {r} must be one of {list(ROLES)}")
        if self.formation not in FORMATIONS:
            raise ValueError(f"unknown formation {self.formation}")
        for k, opts in OPTIONS.items():
            if getattr(self, k) not in opts:
                raise ValueError(f"{k} must be one of {opts}")
        return self

    def copy(self, **kw) -> "Tactics":
        kw.setdefault("roles", dict(self.roles))
        return replace(self, **kw)

    def to_dict(self):
        return asdict(self)


@dataclass
class Team:
    name: str
    starters: List[Player]
    bench: List[Player]
    tactics: Tactics = field(default_factory=Tactics)
    strength: float = 1.0  # club-level multiplier (demo value)

    def clone(self) -> "Team":
        return Team(self.name, [replace(p) for p in self.starters],
                    [replace(p) for p in self.bench], self.tactics.copy(), self.strength)

    def substitute(self, out_id: str, in_id: str):
        oi = next(i for i, p in enumerate(self.starters) if p.id == out_id)
        bi = next(i for i, p in enumerate(self.bench) if p.id == in_id)
        incoming = self.bench.pop(bi)
        incoming.energy = 1.0
        out = self.starters[oi]
        self.starters[oi] = incoming
        if out.id in self.tactics.roles:  # incoming player inherits the role
            self.tactics.roles[incoming.id] = self.tactics.roles.pop(out.id)


@dataclass
class Substitution:
    minute: int
    out_id: str
    in_id: str


@dataclass
class MatchState:
    home: Team
    away: Team
    minute: int = 0
    home_goals: int = 0
    away_goals: int = 0
    momentum: float = 0.0  # -1 away .. +1 home

    def clone(self) -> "MatchState":
        return MatchState(self.home.clone(), self.away.clone(), self.minute,
                          self.home_goals, self.away_goals, self.momentum)
