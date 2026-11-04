"""Real squads + GAME-ESTIMATED attributes (derived from API-Football player season stats where available)."""
from __future__ import annotations
from typing import Dict, List
from touchline_engine.models import Player, Team, Tactics
from . import provider as af

GROUP = {"Goalkeeper": "GK", "Defender": "DEF", "Midfielder": "MID", "Attacker": "FWD", "G": "GK", "D": "DEF", "M": "MID", "F": "FWD"}
STATS_SEASON = 2024  # free plan only exposes 2022-2024 seasons


def _clip(x): return int(max(35, min(95, round(x))))


def season_stats(team_id: int) -> Dict[int, dict]:
    out, page = {}, 1
    while True:
        try: d = af.get(f"players?team={team_id}&season={STATS_SEASON}&page={page}", max_age=86400 * 30)
        except af.APIError: break
        for it in d.get("response", []):
            out[it["player"]["id"]] = it
        if page >= d.get("paging", {}).get("total", 1) or page >= 3: break
        page += 1
    return out


def estimate(group: str, age, stats) -> dict:
    """Heuristic: position baseline nudged by aggregated per-90 stats + average rating. Labelled 'estimated'."""
    base = {"GK": (30, 70, 55, 45, 65, 70), "DEF": (50, 72, 64, 66, 72, 72), "MID": (64, 62, 72, 66, 76, 60), "FWD": (74, 42, 64, 74, 70, 64)}[group]
    att, dfn, pas, pac, sta, aer = base
    src = "position baseline"
    if stats:
        mins = gl = ast = sh = tk = inter = duel_w = duel_t = pacc = rat_n = 0; rat = 0.0; drib = 0
        for s in stats.get("statistics", []):
            g = s.get("games") or {}; m = g.get("minutes") or 0; mins += m
            if g.get("rating"):
                try: rat += float(g["rating"]) * max(m, 1); rat_n += max(m, 1)
                except ValueError: pass
            gl += (s.get("goals") or {}).get("total") or 0; ast += (s.get("goals") or {}).get("assists") or 0
            sh += (s.get("shots") or {}).get("total") or 0
            tk += (s.get("tackles") or {}).get("total") or 0; inter += (s.get("tackles") or {}).get("interceptions") or 0
            duel_w += (s.get("duels") or {}).get("won") or 0; duel_t += (s.get("duels") or {}).get("total") or 0
            drib += (s.get("dribbles") or {}).get("success") or 0
            pa = (s.get("passes") or {}).get("accuracy")
            if pa: pacc = max(pacc, int(pa))
        if mins >= 270:
            p90 = 90 / mins; r = rat / rat_n if rat_n else 6.7
            q = (r - 6.7) * 8  # overall quality shift
            att += q + min(14, (gl + 0.6 * ast) * p90 * 25 + sh * p90 * 2) - (6 if group == "DEF" else 0)
            dfn += q + min(10, (tk + inter) * p90 * 2.5)
            pas += q * 0.8 + (min(12, (pacc - 75) * 0.5) if pacc else 0)
            pac += q * 0.4 + min(10, drib * p90 * 4)
            sta += min(10, mins / 300)
            aer += (duel_w / duel_t - 0.5) * 30 if duel_t > 20 else 0
            src = f"{STATS_SEASON} season stats ({mins} min, avg rating {r:.2f})"
    if age and age >= 32: pac -= (age - 31) * 2; sta -= (age - 31) * 1.5
    return {"att": _clip(att), "defn": _clip(dfn), "pas": _clip(pas), "pac": _clip(pac), "sta": _clip(sta), "aer": _clip(aer), "source": src, "stats_minutes": (sum(((x.get("games") or {}).get("minutes") or 0) for x in stats.get("statistics", [])) if stats else 0)}


def squad(team_id: int) -> List[dict]:
    d = af.get(f"players/squads?team={team_id}", max_age=86400)
    resp = d.get("response") or []
    if not resp: return []
    stats = {}
    try: stats = season_stats(team_id)
    except af.APIError: pass
    out = []
    for p in resp[0]["players"]:
        g = GROUP.get(p.get("position"), "MID")
        e = estimate(g, p.get("age"), stats.get(p["id"]))
        out.append({"id": str(p["id"]), "name": p["name"], "number": p.get("number"), "age": p.get("age"),
                    "group": g, "photo": p.get("photo"), "attributes_estimated": True, **e})
    return out


def probable_xi(sq: List[dict], formation="4-2-3-1"):
    """Highest-estimated players per line. LABEL: probable XI (not official)."""
    from touchline_engine.models import FORMATIONS
    need = {"GK": 1, **FORMATIONS[formation]}
    score = lambda p: {"GK": p["defn"] + p["aer"], "DEF": p["defn"] * 2 + p["pac"], "MID": p["pas"] * 2 + p["att"] + p["defn"], "FWD": p["att"] * 2 + p["pac"]}[p["group"]]
    base = score
    score = lambda p: base(p) + min(p.get("stats_minutes", 0), 3000) / 60
    xi = []
    for g, n in need.items():
        xi += sorted([p for p in sq if p["group"] == g], key=score, reverse=True)[:n]
    return xi


def to_player(p: dict, side="C") -> Player:
    return Player(p["id"], p["name"], p["group"], side, p["att"], p["defn"], p["pas"], p["pac"], p["sta"], p["aer"])


def build_team(name, xi: List[dict], bench: List[dict], formation: str, strength=1.0) -> Team:
    from touchline_engine.models import FORMATIONS
    f = formation if formation in FORMATIONS else "4-2-3-1"
    sides = {}
    for g in ("DEF", "MID", "FWD"):
        line = [p for p in xi if p["group"] == g]
        for i, p in enumerate(line):
            sides[p["id"]] = "L" if (i == 0 and len(line) > 2) else ("R" if (i == len(line) - 1 and len(line) > 2) else "C")
    return Team(name, [to_player(p, sides.get(p["id"], "C")) for p in xi], [to_player(p) for p in bench], Tactics(f), strength)
