"""Demo teams: elite-club squads (real names) with GAME-ESTIMATED attributes and a scripted demo 'real' timeline. Not real players or real statistics."""
from .models import Player, Team, Tactics, MatchState

DEMO_NOTICE = "DEMO DATA: Real Madrid vs Manchester City demo. Player names are real; ratings and match events are game-estimated, not real statistics."


def _p(pid, name, g, side, att, defn, pas, pac, sta):
    return Player(pid, name, g, side, att, defn, pas, pac, sta)


def northbridge() -> Team:
    xi = [
        _p("nb1", "T. Courtois", "GK", "C", 20, 76, 55, 45, 60),
        _p("nb2", "D. Carvajal", "DEF", "R", 55, 72, 64, 78, 75),
        _p("nb3", "É. Militão", "DEF", "C", 35, 79, 60, 62, 70),
        _p("nb4", "A. Rüdiger", "DEF", "C", 38, 77, 66, 66, 72),
        _p("nb5", "F. Mendy", "DEF", "L", 58, 66, 66, 75, 74),
        _p("nb6", "A. Tchouaméni", "MID", "C", 55, 74, 75, 62, 82),
        _p("nb7", "F. Valverde", "MID", "C", 70, 60, 80, 66, 64),
        _p("nb8", "J. Bellingham", "MID", "C", 68, 62, 74, 70, 78),
        _p("nb9", "Rodrygo", "FWD", "R", 77, 35, 68, 86, 70),
        _p("nb10", "K. Mbappé", "FWD", "C", 81, 32, 64, 74, 68),
        _p("nb11", "Vinícius Jr", "FWD", "L", 74, 38, 70, 82, 72),
    ]
    bench = [
        _p("nb12", "A. Lunin", "GK", "C", 18, 70, 50, 40, 60),
        _p("nb13", "D. Huijsen", "DEF", "C", 36, 73, 60, 64, 76),
        _p("nb14", "E. Camavinga", "MID", "C", 64, 66, 72, 68, 85),
        _p("nb15", "A. Güler", "MID", "C", 72, 55, 76, 72, 80),
        _p("nb16", "Endrick", "FWD", "C", 76, 30, 60, 88, 80),
        _p("nb17", "Brahim Díaz", "FWD", "R", 71, 40, 66, 80, 82),
    ]
    return Team("Real Madrid (demo)", xi, bench, Tactics("4-3-3"), 1.0)


def riverside() -> Team:
    xi = [
        _p("rr1", "G. Donnarumma", "GK", "C", 20, 78, 58, 44, 60),
        _p("rr2", "M. Nunes", "DEF", "R", 52, 70, 62, 74, 76),
        _p("rr3", "R. Dias", "DEF", "C", 36, 80, 62, 70, 74),
        _p("rr4", "J. Gvardiol", "DEF", "C", 34, 76, 58, 60, 70),
        _p("rr5", "N. O'Reilly", "DEF", "L", 50, 62, 60, 72, 72),
        _p("rr6", "Rodri", "MID", "C", 50, 76, 72, 60, 80),
        _p("rr7", "T. Reijnders", "MID", "C", 60, 70, 70, 64, 78),
        _p("rr8", "P. Foden", "MID", "C", 72, 58, 78, 70, 72),
        _p("rr9", "Bernardo Silva", "MID", "R", 74, 45, 72, 84, 74),
        _p("rr10", "J. Doku", "MID", "L", 73, 46, 70, 85, 76),
        _p("rr11", "E. Haaland", "FWD", "C", 80, 34, 62, 80, 70),
    ]
    bench = [
        _p("rr12", "J. Trafford", "GK", "C", 18, 70, 52, 40, 60),
        _p("rr13", "N. Aké", "DEF", "C", 34, 72, 58, 62, 74),
        _p("rr14", "N. González", "MID", "C", 62, 68, 70, 66, 84),
        _p("rr15", "Savinho", "MID", "R", 70, 50, 68, 82, 80),
        _p("rr16", "O. Marmoush", "FWD", "C", 77, 30, 58, 76, 78),
    ]
    return Team("Manchester City (demo)", xi, bench, Tactics("4-5-1", "low", "deep", "narrow", "balanced", "direct", "balanced", "counterattack"), 1.0)


# Scripted 'real' demo timeline (stand-in for a provider feed). Minute, team, type, detail.
DEMO_TIMELINE = [
    {"minute": 12, "team": "home", "type": "shot", "detail": "Rodrygo shot saved", "xg": 0.08},
    {"minute": 23, "team": "away", "type": "goal", "detail": "E. Haaland (counterattack)", "xg": 0.31},
    {"minute": 38, "team": "home", "type": "shot", "detail": "K. Mbappé header wide", "xg": 0.12},
    {"minute": 51, "team": "home", "type": "goal", "detail": "K. Mbappé", "xg": 0.44},
    {"minute": 63, "team": "away", "type": "sub", "detail": "Savinho on for Bernardo Silva"},
    {"minute": 71, "team": "home", "type": "shot", "detail": "Vinícius Jr shot blocked", "xg": 0.06},
    {"minute": 79, "team": "away", "type": "goal", "detail": "J. Doku (fast break)", "xg": 0.27},
    {"minute": 88, "team": "home", "type": "shot", "detail": "F. Valverde long-range", "xg": 0.04},
]


AERIAL = {"nb3": 84, "nb4": 80, "nb10": 83, "nb6": 72, "nb13": 78, "nb16": 66, "nb1": 70,
          "rr3": 85, "rr4": 80, "rr11": 79, "rr6": 74, "rr13": 77, "rr16": 81, "rr1": 72,
          "nb9": 55, "nb11": 58, "rr9": 52, "rr10": 54}


def _aer(team):
    for p in team.starters + team.bench:
        p.aer = AERIAL.get(p.id, 62)
    return team


def demo_state_at(minute: int) -> MatchState:
    """Reconstruct demo match state at `minute`: score from the timeline, fatigue from minutes played."""
    from .simulate import drain, minute_rates
    st = MatchState(_aer(northbridge()), _aer(riverside()), 0, 0, 0)
    ev = sorted(DEMO_TIMELINE, key=lambda e: e["minute"])
    for m in range(1, minute + 1):
        for e in ev:
            if e["minute"] == m:
                if e["type"] == "goal":
                    if e["team"] == "home": st.home_goals += 1
                    else: st.away_goals += 1
                if e["type"] == "sub" and e["team"] == "away" and "Savinho" in e["detail"]:
                    st.away.substitute("rr9", "rr15")
        r = minute_rates(st.home, st.away)
        drain(st.home, r["fat_h"]); drain(st.away, r["fat_a"])
    st.minute = minute
    last = [e for e in ev if e["minute"] <= minute and e["type"] == "goal"]
    if last and minute - last[-1]["minute"] < 10:
        st.momentum = 0.5 if last[-1]["team"] == "home" else -0.5
    return st
