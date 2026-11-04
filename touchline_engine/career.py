"""Kickvana Career: fictional league, squads, tactics-driven match engine, transfers, matchday, season loop.

Everything here is generated (club names, badges, players) so no licensing is needed. State is a plain
JSON-serialisable dict so it can be persisted as one row and migrated to Postgres JSONB later.
"""
from __future__ import annotations
import copy, math, random
from typing import Dict, List, Optional, Tuple

ATTRS = ["pace", "shooting", "passing", "dribbling", "defending", "physical", "vision", "stamina"]
POSITIONS = ["GK", "CB", "FB", "DM", "CM", "AM", "W", "ST"]
LINE = {"GK": "GK", "CB": "DEF", "FB": "DEF", "DM": "MID", "CM": "MID", "AM": "MID", "W": "FWD", "ST": "FWD"}
WEIGHTS = {  # overall rating weights per position
    "GK": {"defending": .45, "physical": .25, "vision": .2, "passing": .1},
    "CB": {"defending": .45, "physical": .25, "pace": .1, "passing": .1, "vision": .1},
    "FB": {"defending": .3, "pace": .3, "stamina": .15, "passing": .15, "dribbling": .1},
    "DM": {"defending": .35, "passing": .25, "physical": .2, "vision": .1, "stamina": .1},
    "CM": {"passing": .3, "vision": .25, "stamina": .15, "defending": .1, "dribbling": .1, "shooting": .1},
    "AM": {"vision": .3, "passing": .25, "dribbling": .25, "shooting": .2},
    "W": {"pace": .3, "dribbling": .3, "shooting": .15, "passing": .15, "stamina": .1},
    "ST": {"shooting": .45, "pace": .2, "physical": .15, "dribbling": .1, "vision": .1},
}
ROLES = {
    "GK": ["goalkeeper", "sweeper-keeper"], "CB": ["stopper", "ball-playing"], "FB": ["full-back", "wing-back"],
    "DM": ["anchor", "deep-playmaker"], "CM": ["box-to-box", "playmaker"], "AM": ["trequartista", "shadow-striker"],
    "W": ["winger", "inside-forward"], "ST": ["poacher", "target-man", "false-nine"],
}
# Presets: slot -> (pos, x, y) on a 0-100 pitch (x = left->right, y = own goal(100) -> opp goal(0))
FORMATIONS = {
    "4-3-3": [("GK", 50, 92), ("FB", 14, 72), ("CB", 37, 76), ("CB", 63, 76), ("FB", 86, 72),
              ("DM", 50, 58), ("CM", 30, 48), ("CM", 70, 48), ("W", 16, 24), ("ST", 50, 16), ("W", 84, 24)],
    "4-4-2": [("GK", 50, 92), ("FB", 14, 72), ("CB", 37, 76), ("CB", 63, 76), ("FB", 86, 72),
              ("W", 14, 46), ("CM", 38, 52), ("CM", 62, 52), ("W", 86, 46), ("ST", 38, 18), ("ST", 62, 18)],
    "4-2-3-1": [("GK", 50, 92), ("FB", 14, 72), ("CB", 37, 76), ("CB", 63, 76), ("FB", 86, 72),
                ("DM", 38, 58), ("DM", 62, 58), ("W", 16, 34), ("AM", 50, 36), ("W", 84, 34), ("ST", 50, 15)],
    "3-5-2": [("GK", 50, 92), ("CB", 28, 75), ("CB", 50, 78), ("CB", 72, 75), ("FB", 10, 50),
              ("DM", 50, 58), ("CM", 32, 46), ("CM", 68, 46), ("FB", 90, 50), ("ST", 38, 18), ("ST", 62, 18)],
    "5-3-2": [("GK", 50, 92), ("FB", 10, 66), ("CB", 30, 76), ("CB", 50, 78), ("CB", 70, 76), ("FB", 90, 66),
              ("CM", 30, 50), ("DM", 50, 56), ("CM", 70, 50), ("ST", 38, 18), ("ST", 62, 18)],
}
SLIDERS = ["pressing", "defensive_line", "tempo", "width", "directness"]
TALKS = ["calm", "encourage", "demand", "praise", "criticise"]

FIRST = ["Aled", "Bram", "Caio", "Dario", "Elio", "Femi", "Gael", "Hugo", "Ilan", "Jarek", "Kofi", "Luca", "Mateo",
         "Nilo", "Oskar", "Pavel", "Quinn", "Rafe", "Soren", "Tiago", "Ugo", "Vito", "Wren", "Xavi", "Yann", "Zeno",
         "Arlo", "Bastian", "Cyrus", "Dion", "Ezra", "Finn", "Idris", "Jonas", "Kian", "Leo", "Milo", "Noa", "Otto", "Remy"]
LAST = ["Adeyemi", "Barlow", "Castell", "Dravic", "Esteves", "Falk", "Gironde", "Halvorsen", "Iversen", "Jansky",
        "Kovar", "Lindqvist", "Marlowe", "Novak", "Okafor", "Petrell", "Quaresma", "Rennick", "Salvo", "Tavernier",
        "Ulric", "Varga", "Westbrook", "Yilmaz", "Zoric", "Ashby", "Brennan", "Cardoso", "Delacroix", "Ekdahl",
        "Fontaine", "Garrido", "Holm", "Ibarra", "Kerrigan", "Lazaro", "Moretti", "Nkemelu", "Ortega", "Prado"]
CLUBS = [  # fictional, generated identities
    ("Nova Harbour", "NHB", "#00F5A0", "#05070D", "shield", 82), ("Crimson Vale", "CRV", "#FF4D6D", "#1a0610", "round", 80),
    ("Aurora City", "AUR", "#2DE2FF", "#06121a", "hex", 78), ("Ironbridge Athletic", "IRA", "#FFB547", "#1a1206", "shield", 75),
    ("Solace Rovers", "SOR", "#8B5CF6", "#0d0820", "round", 73), ("Kestrel Park", "KPK", "#FF3DCB", "#1a0614", "hex", 71),
    ("Meridian Town", "MER", "#C6F432", "#0f1406", "shield", 69), ("Blackwater United", "BWU", "#9AA7C7", "#0a0d14", "round", 67),
    ("Eastgate Wanderers", "EGW", "#FF7A59", "#1a0b06", "hex", 66), ("Lumen FC", "LUM", "#FFE14D", "#141206", "shield", 64),
    ("Sable Port", "SBP", "#5BE9B9", "#061410", "round", 62), ("Northfold", "NFD", "#7AA2FF", "#060b1a", "hex", 60),
]
SQUAD_SHAPE = ["GK", "GK", "CB", "CB", "CB", "CB", "FB", "FB", "FB", "DM", "DM", "CM", "CM", "CM", "AM", "AM",
               "W", "W", "W", "ST", "ST", "ST"]
WEEKS_PER_SEASON = 22
WINDOWS = [(1, 4), (11, 13)]  # pre-season/summer and mid-season windows (inclusive weeks)
ACADEMY_WEEK = 10


# ---------------------------------------------------------------- players
def overall(p: Dict) -> int:
    w = WEIGHTS[p["pos"]]
    return int(round(sum(p["attrs"][k] * v for k, v in w.items())))


def valuation(p: Dict, season_year: int = 1) -> float:
    """Market value in £m: driven by overall, age curve, potential headroom and contract length."""
    ovr = p["ovr"]; age = p["age"]
    base = 0.02 * math.exp((ovr - 45) / 6.2)
    age_f = 1.35 if age <= 21 else 1.15 if age <= 24 else 1.0 if age <= 28 else 0.75 if age <= 31 else 0.45
    pot_f = 1 + max(0, p.get("potential", ovr) - ovr) / 40
    yrs = max(0, p.get("contract_until", season_year + 2) - season_year)
    con_f = 0.55 if yrs == 0 else 0.85 if yrs == 1 else 1.0
    form_f = 0.9 + p.get("form", 6) / 50
    return round(max(0.1, base * age_f * pot_f * con_f * form_f), 1)


def wage_demand(p: Dict) -> int:
    """Weekly wage demand in £k."""
    return int(max(2, round(0.6 * math.exp((p["ovr"] - 50) / 7.5) + (2 if p["age"] > 27 else 0))))


def _name(rng):
    return f"{rng.choice(FIRST)} {rng.choice(LAST)}"


def make_player(rng, pid: str, pos: str, level: int, age: Optional[int] = None, club_id=None, season=1) -> Dict:
    age = age or rng.randint(18, 33)
    attrs = {}
    for a in ATTRS:
        bonus = 9 if WEIGHTS[pos].get(a, 0) >= .2 else 3 if a in WEIGHTS[pos] else -6
        attrs[a] = max(25, min(96, int(rng.gauss(level + bonus - (5 if age < 21 else 0) - max(0, age - 31) * 2, 6))))
    if pos == "GK":
        attrs["shooting"] = min(attrs["shooting"], 35); attrs["dribbling"] = min(attrs["dribbling"], 45)
    p = {"id": pid, "name": _name(rng), "pos": pos, "age": age, "attrs": attrs, "club_id": club_id,
         "form": round(rng.uniform(5, 7.5), 1), "fitness": 100, "morale": rng.randint(60, 80),
         "contract_until": season + rng.randint(0, 4), "listed": False, "youth": False}
    p["ovr"] = overall(p)
    p["potential"] = min(95, p["ovr"] + (rng.randint(4, 18) if age < 22 else rng.randint(0, 6) if age < 26 else 0))
    p["wage"] = wage_demand(p)
    p["value"] = valuation(p, season)
    return p


def refresh(p: Dict, season=1):
    p["ovr"] = overall(p); p["value"] = valuation(p, season)


# ---------------------------------------------------------------- career creation
def new_career(club_index: int = 4, seed: Optional[int] = None, manager: str = "You") -> Dict:
    seed = seed if seed is not None else random.randrange(1 << 30)
    rng = random.Random(seed)
    clubs, players = [], {}
    n = 0
    for i, (name, short, c1, c2, shape, lvl) in enumerate(CLUBS):
        cid = f"c{i}"
        squad = []
        for pos in SQUAD_SHAPE:
            n += 1; pid = f"p{n}"
            players[pid] = make_player(rng, pid, pos, lvl - rng.randint(0, 9), club_id=cid); squad.append(pid)
        rep = lvl
        clubs.append({"id": cid, "name": name, "short": short, "colours": [c1, c2], "badge": shape, "reputation": rep,
                      "style": ["possession", "pressing", "counter", "direct", "balanced"][i % 5],
                      "balance": round(8 + (lvl - 60) * 2.2, 1), "squad": squad})
    for k in range(36):  # free agents / scouting pool beyond the league
        n += 1; pid = f"p{n}"
        players[pid] = make_player(rng, pid, rng.choice(SQUAD_SHAPE), rng.randint(58, 80), club_id=None)
        players[pid]["contract_until"] = 1; players[pid]["value"] = valuation(players[pid], 1)
    me = clubs[club_index]["id"]
    st = {"version": 1, "seed": seed, "rng_state": n, "manager": manager, "club_id": me, "season": 1, "week": 1,
          "status": "active", "clubs": clubs, "players": players, "next_pid": n + 1,
          "fixtures": round_robin([c["id"] for c in clubs], rng), "results": [],
          "finances": {"balance": 0, "transfer_budget": 0, "wage_budget": 0, "income": [], "expenses": []},
          "board": {"confidence": 65, "expectation": "", "target_pos": 0, "history": []},
          "news": [], "academy": {"intake_done": False, "prospects": []}, "transfers": {"negotiations": {}, "log": []},
          "tactics": {}, "talk_bonus": 0}
    club = club_of(st, me)
    club_sorted = sorted(clubs, key=lambda c: -c["reputation"])
    rank = [c["id"] for c in club_sorted].index(me) + 1
    st["board"]["target_pos"] = max(1, min(10, rank))
    st["board"]["expectation"] = expectation_text(st["board"]["target_pos"])
    wages = sum(players[p]["wage"] for p in club["squad"])
    st["finances"].update(balance=round(25 + club["reputation"] * 0.5, 1), transfer_budget=round(6 + (club["reputation"] - 55) * 0.9, 1),
                          wage_budget=int(wages * 1.15))
    for c in clubs:
        st["tactics"][c["id"]] = auto_tactics(st, c["id"])
    news(st, f"Welcome to {club['name']}. The board expects: {st['board']['expectation']}.")
    return st


def expectation_text(pos):
    return ("Win the league" if pos == 1 else f"Finish in the top {pos}" if pos <= 4 else
            f"Finish {ordinal(pos)} or higher" if pos <= 8 else "Avoid the bottom two")


def ordinal(n):
    return f"{n}{'th' if 10 <= n % 100 <= 20 else {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')}"


def round_robin(ids: List[str], rng) -> List[Dict]:
    ids = ids[:]; rng.shuffle(ids); n = len(ids); out = []
    arr = ids[:]
    for r in range(n - 1):
        for i in range(n // 2):
            a, b = arr[i], arr[n - 1 - i]
            if r % 2: a, b = b, a
            out.append({"week": r + 1, "home": a, "away": b, "played": False, "score": None})
        arr = [arr[0]] + [arr[-1]] + arr[1:-1]
    second = [{"week": f["week"] + n - 1, "home": f["away"], "away": f["home"], "played": False, "score": None} for f in out]
    fx = out + second
    for i, f in enumerate(fx): f["id"] = i
    return fx


def club_of(st, cid):
    return next(c for c in st["clubs"] if c["id"] == cid)


def news(st, text, kind="info"):
    st["news"].insert(0, {"season": st["season"], "week": st["week"], "text": text, "kind": kind}); del st["news"][40:]


def window_open(st) -> bool:
    return st["status"] == "active" and any(a <= st["week"] <= b for a, b in WINDOWS)


# ---------------------------------------------------------------- tactics
def best_xi(st, cid, formation="4-3-3", exclude=()) -> List[Optional[str]]:
    squad = [st["players"][p] for p in club_of(st, cid)["squad"] if p not in exclude]
    used, xi = set(), []
    for pos, _, _ in FORMATIONS[formation]:
        cands = sorted([p for p in squad if p["id"] not in used],
                       key=lambda p: -(slot_rating(p, pos) * (0.6 + 0.4 * p["fitness"] / 100)))
        pick = cands[0]["id"] if cands else None
        if pick: used.add(pick)
        xi.append(pick)
    return xi


def default_sliders(style="balanced"):
    base = {k: 50 for k in SLIDERS}
    base.update({"possession": {"tempo": 35, "directness": 25, "pressing": 60},
                 "pressing": {"pressing": 85, "defensive_line": 75, "tempo": 65},
                 "counter": {"pressing": 30, "defensive_line": 25, "directness": 80, "tempo": 70},
                 "direct": {"directness": 85, "width": 70}, "balanced": {}}.get(style, {}))
    return base


def auto_tactics(st, cid, formation=None):
    club = club_of(st, cid)
    formation = formation or {"possession": "4-3-3", "pressing": "4-2-3-1", "counter": "5-3-2",
                              "direct": "4-4-2", "balanced": "4-4-2"}.get(club.get("style"), "4-3-3")
    xi = best_xi(st, cid, formation)
    slots = [{"pos": pos, "x": x, "y": y, "player_id": pid, "role": ROLES[pos][0]}
             for (pos, x, y), pid in zip(FORMATIONS[formation], xi)]
    return {"formation": formation, "slots": slots, "sliders": default_sliders(club.get("style", "balanced"))}


def slot_pos(slot) -> str:
    """Positional zone for a (possibly custom-dragged) slot: derived from its pitch coordinates."""
    x, y = slot["x"], slot["y"]
    if y >= 86: return "GK"
    if y >= 64: return "FB" if (x < 25 or x > 75) else "CB"
    if y >= 53: return "FB" if (x < 18 or x > 82) else "DM"
    if y >= 40: return "W" if (x < 22 or x > 78) else "CM"
    if y >= 27: return "W" if (x < 30 or x > 70) else "AM"
    return "W" if (x < 28 or x > 72) else "ST"


def slot_rating(p: Dict, pos: str) -> float:
    if p is None: return 30
    w = WEIGHTS[pos]; r = sum(p["attrs"][k] * v for k, v in w.items())
    if pos == "GK" and p["pos"] != "GK": r -= 30
    if pos != "GK" and p["pos"] == "GK": r -= 30
    elif LINE[p["pos"]] != LINE[pos]: r -= 6
    elif p["pos"] != pos: r -= 2
    return r


def validate_tactics(st, cid, t: Dict) -> Dict:
    if t.get("formation") not in list(FORMATIONS) + ["custom"]: raise ValueError("unknown formation")
    slots = t.get("slots") or []
    if len(slots) != 11: raise ValueError("need exactly 11 slots")
    squad = set(club_of(st, cid)["squad"])
    ids = [s.get("player_id") for s in slots]
    if any(i not in squad for i in ids): raise ValueError("every slot needs a player from your squad")
    if len(set(ids)) != 11: raise ValueError("a player can only fill one slot")
    clean = []
    gk = 0
    for s in slots:
        x = max(2, min(98, float(s["x"]))); y = max(4, min(96, float(s["y"])))
        sl = {"x": round(x, 1), "y": round(y, 1), "player_id": s["player_id"]}
        sl["pos"] = slot_pos(sl); gk += sl["pos"] == "GK"
        role = s.get("role") or ROLES[sl["pos"]][0]
        sl["role"] = role if role in ROLES[sl["pos"]] else ROLES[sl["pos"]][0]
        clean.append(sl)
    if gk != 1: raise ValueError("exactly one goalkeeper slot is required")
    sliders = {k: int(max(0, min(100, (t.get("sliders") or {}).get(k, 50)))) for k in SLIDERS}
    return {"formation": t["formation"], "slots": clean, "sliders": sliders}


# ---------------------------------------------------------------- match engine
def team_profile(st, cid, tactics, live=None) -> Dict:
    """Aggregate unit strengths from the XI on the pitch, adjusted for fitness, morale, form and roles."""
    P = st["players"]
    units = {"GK": [], "DEF": [], "MID": [], "FWD": []}
    pace_def, pace_fwd = [], []
    role_mods = {"att": 0.0, "mid": 0.0, "def": 0.0}
    for s in tactics["slots"]:
        p = P.get(s["player_id"])
        fit = (live or {}).get("fitness", {}).get(s["player_id"], p["fitness"] if p else 50)
        r = slot_rating(p, s["pos"])
        mult = (0.70 + 0.30 * fit / 100) * (0.94 + 0.12 * (p["morale"] if p else 50) / 100) * (0.97 + 0.01 * (p["form"] if p else 5) / 2)
        units[LINE[s["pos"]]].append(r * mult)
        if p and LINE[s["pos"]] == "DEF": pace_def.append(p["attrs"]["pace"])
        if p and LINE[s["pos"]] == "FWD": pace_fwd.append(p["attrs"]["pace"])
        role = s.get("role")
        if role in ("wing-back", "inside-forward", "shadow-striker", "box-to-box"): role_mods["att"] += 1.2; role_mods["def"] -= 0.8
        if role in ("anchor", "stopper", "full-back"): role_mods["def"] += 1.0
        if role in ("playmaker", "deep-playmaker", "trequartista", "false-nine", "ball-playing"): role_mods["mid"] += 1.1
        if role == "target-man" and p: role_mods["att"] += (p["attrs"]["physical"] - 65) / 15
        if role == "poacher" and p: role_mods["att"] += (p["attrs"]["shooting"] - 65) / 15
        if role == "sweeper-keeper" and p: role_mods["def"] += (p["attrs"]["passing"] - 55) / 20
    avg = lambda xs, d=40: (sum(xs) / len(xs)) if xs else d
    n = {k: len(v) for k, v in units.items()}
    att = avg(units["FWD"] + units["MID"][:1]) + 1.6 * (n["FWD"] - 2) + role_mods["att"]
    mid = avg(units["MID"]) + 1.8 * (n["MID"] - 4) + role_mods["mid"]
    dfn = avg(units["DEF"]) + 1.8 * (n["DEF"] - 4) + role_mods["def"]
    return {"att": att, "mid": mid, "def": dfn, "gk": avg(units["GK"], 30), "pace_def": avg(pace_def, 60),
            "pace_fwd": avg(pace_fwd, 60), "sl": tactics["sliders"], "talk": (live or {}).get("talk", 0.0)}


def minute_model(a: Dict, b: Dict) -> Tuple[float, float, float]:
    """Return (possession share for a, P(a shot this minute), mean xG per a shot). Tactics act through here."""
    sa, sb = a["sl"], b["sl"]
    f = lambda v: (v - 50) / 50  # -1..1
    poss = 0.5 + (a["mid"] - b["mid"]) / 90 - 0.06 * f(sa["directness"]) + 0.06 * f(sb["directness"]) \
        + 0.03 * f(sa["pressing"]) - 0.03 * f(sb["pressing"])
    poss = max(0.25, min(0.75, poss))
    tempo = 1 + 0.18 * f(sa["tempo"]) + 0.08 * f(sb["tempo"])
    chance = 0.105 * tempo * (poss / 0.5) ** 0.6
    chance *= 1 + (a["att"] - b["def"]) / 55 + a["talk"] * 0.04
    chance *= 1 + 0.12 * f(sa["pressing"])            # winning the ball high creates chances
    chance *= 1 + 0.10 * f(sa["width"])               # stretching play creates more (lower quality) looks
    chance *= 1 + 0.10 * f(sa["directness"])
    chance *= 1 - 0.10 * f(sb["defensive_line"]) * (1 if b["pace_def"] >= a["pace_fwd"] else -0.6)
    chance *= 1 - 0.08 * f(sb["pressing"]) * (0.5 if a["sl"]["directness"] > 60 else 1)
    xg = 0.11 * (1 + (a["att"] - b["gk"]) / 120)
    xg *= 1 - 0.12 * f(sa["width"]) - 0.12 * f(sa["directness"])
    # a high line against faster forwards concedes big chances in behind
    if f(sb["defensive_line"]) > 0 and a["pace_fwd"] > b["pace_def"]:
        xg *= 1 + 0.35 * f(sb["defensive_line"]) * min(1, (a["pace_fwd"] - b["pace_def"]) / 12)
    return poss, max(0.01, min(0.45, chance)), max(0.03, min(0.6, xg))


def fatigue_rate(sl, stamina):
    f = lambda v: (v - 50) / 50
    return (0.30 + 0.18 * f(sl["pressing"]) + 0.10 * f(sl["tempo"])) * (1.35 - stamina / 150)


def new_live(st, fixture: Dict) -> Dict:
    P = st["players"]
    lv = {"fixture_id": fixture["id"], "home": fixture["home"], "away": fixture["away"], "minute": 0, "score": [0, 0],
          "events": [], "stats": {"shots": [0, 0], "xg": [0.0, 0.0], "poss": [0, 0]}, "subs": {"home": 0, "away": 0},
          "phase": "pre", "talks": {}, "tactics": {}, "fitness": {}, "talk": {"home": 0.0, "away": 0.0}, "ball": [50, 50],
          "subbed_off": []}
    for side in ("home", "away"):
        cid = fixture[side]
        t = copy.deepcopy(st["tactics"].get(cid) or auto_tactics(st, cid))
        # replace unavailable players (sold/missing)
        squad = set(club_of(st, cid)["squad"])
        if any(s["player_id"] not in squad for s in t["slots"]): t = auto_tactics(st, cid, t["formation"] if t["formation"] in FORMATIONS else None)
        lv["tactics"][side] = t
        for p in club_of(st, cid)["squad"]: lv["fitness"][p] = P[p]["fitness"]
    return lv


def _prof(st, lv, side):
    cid = lv[side]
    return team_profile(st, cid, lv["tactics"][side], {"fitness": lv["fitness"], "talk": lv["talk"][side]})


def _scorer(st, rng, tactics):
    P = st["players"]; w = []
    for s in tactics["slots"]:
        p = P[s["player_id"]]
        base = {"ST": 6, "W": 3.5, "AM": 3, "CM": 1.4, "DM": .6, "FB": .5, "CB": .6, "GK": .01}[s["pos"]]
        w.append(base * (p["attrs"]["shooting"] / 70) ** 2)
    return rng.choices(tactics["slots"], weights=w)[0]["player_id"]


def ai_adjust(st, lv, side):
    """Opponent AI: chase the game late when behind, protect a lead late when ahead."""
    i = 0 if side == "home" else 1
    diff = lv["score"][i] - lv["score"][1 - i]
    sl = lv["tactics"][side]["sliders"]
    if lv["minute"] >= 65 and diff < 0 and not lv.get(f"ai_chase_{side}"):
        sl.update(tempo=min(100, sl["tempo"] + 25), directness=min(100, sl["directness"] + 20), pressing=min(100, sl["pressing"] + 15))
        lv[f"ai_chase_{side}"] = True
        lv["events"].append({"minute": lv["minute"], "type": "tactic", "side": side, "text": "Opponent goes for it: higher tempo, more direct."})
    if lv["minute"] >= 75 and diff > 0 and not lv.get(f"ai_shut_{side}"):
        sl.update(defensive_line=max(0, sl["defensive_line"] - 25), tempo=max(0, sl["tempo"] - 20))
        lv[f"ai_shut_{side}"] = True
        lv["events"].append({"minute": lv["minute"], "type": "tactic", "side": side, "text": "Opponent drops deeper to protect the lead."})
    # AI subs on tired players
    if lv["minute"] in (60, 72) and lv["subs"][side] < 3:
        auto_sub(st, lv, side)


def auto_sub(st, lv, side):
    t = lv["tactics"][side]; P = st["players"]
    on = {s["player_id"] for s in t["slots"]}
    bench = [p for p in club_of(st, lv[side])["squad"] if p not in on and p not in lv["subbed_off"]]
    if not bench: return
    tired = min(t["slots"], key=lambda s: lv["fitness"].get(s["player_id"], 100) + (500 if s["pos"] == "GK" else 0))
    if lv["fitness"].get(tired["player_id"], 100) > 72: return
    rep = max(bench, key=lambda b: slot_rating(P[b], tired["pos"]) * (lv["fitness"].get(b, 100) / 100))
    do_sub(st, lv, side, tired["player_id"], rep)


def do_sub(st, lv, side, out_id, in_id):
    if lv["subs"][side] >= 5: raise ValueError("all five substitutions used")
    t = lv["tactics"][side]
    slot = next((s for s in t["slots"] if s["player_id"] == out_id), None)
    if not slot: raise ValueError("that player is not on the pitch")
    if in_id not in club_of(st, lv[side])["squad"] or any(s["player_id"] == in_id for s in t["slots"]) or in_id in lv["subbed_off"]:
        raise ValueError("that player is not available on the bench")
    slot["player_id"] = in_id; lv["subs"][side] += 1; lv["subbed_off"].append(out_id)
    P = st["players"]
    lv["events"].append({"minute": lv["minute"], "type": "sub", "side": side, "out": out_id, "in": in_id,
                         "text": f"{P[in_id]['name']} on for {P[out_id]['name']}"})


def step(st, lv, rng, to_minute: int):
    """Advance the live match minute-by-minute up to to_minute (inclusive)."""
    P = st["players"]
    while lv["minute"] < min(to_minute, 90):
        lv["minute"] += 1
        for side in ("home", "away"):
            if side != lv.get("user_side"): ai_adjust(st, lv, side)
        ph, pa = _prof(st, lv, "home"), _prof(st, lv, "away")
        ph["att"] += 1.5  # home advantage
        poss, ch_h, xg_h = minute_model(ph, pa)
        _, ch_a, xg_a = minute_model(pa, ph)
        lv["stats"]["poss"][0] += poss; lv["stats"]["poss"][1] += 1 - poss
        for i, (side, ch, xg, t) in enumerate((("home", ch_h * poss / 0.5, xg_h, lv["tactics"]["home"]),
                                               ("away", ch_a * (1 - poss) / 0.5, xg_a, lv["tactics"]["away"]))):
            if rng.random() < ch:
                q = max(0.02, min(0.8, rng.expovariate(1 / xg)))
                lv["stats"]["shots"][i] += 1; lv["stats"]["xg"][i] = round(lv["stats"]["xg"][i] + q, 2)
                shooter = _scorer(st, rng, t)
                goal = rng.random() < q
                bx = 50 + rng.uniform(-18, 18); by = 10 if i == 0 else 90
                lv["ball"] = [round(bx, 1), by]
                if goal:
                    lv["score"][i] += 1
                    lv["events"].append({"minute": lv["minute"], "type": "goal", "side": side, "player": shooter,
                                         "xg": round(q, 2), "text": f"GOAL! {P[shooter]['name']}", "x": bx, "y": by})
                else:
                    lv["events"].append({"minute": lv["minute"], "type": "shot", "side": side, "player": shooter,
                                         "xg": round(q, 2), "text": f"{P[shooter]['name']} {'forces a save' if rng.random() < .5 else 'shoots wide'}",
                                         "x": bx, "y": by})
        for side in ("home", "away"):
            sl = lv["tactics"][side]["sliders"]
            for s in lv["tactics"][side]["slots"]:
                pid = s["player_id"]
                lv["fitness"][pid] = round(max(20, lv["fitness"].get(pid, 100) - fatigue_rate(sl, P[pid]["attrs"]["stamina"]) * (0.5 if s["pos"] == "GK" else 1)), 2)
        lv["talk"]["home"] *= 0.985; lv["talk"]["away"] *= 0.985
        if lv["minute"] == 45 and to_minute > 45 and lv["phase"] == "first":
            break
    if lv["minute"] >= 45 and lv["phase"] == "first":
        lv["phase"] = "half"
    elif lv["minute"] >= 90:
        lv["phase"] = "full"
    return lv


def team_talk(st, lv, side, tone, when) -> Dict:
    """Pre-match / half-time talk. Effect depends on context (score, expectation) and changes morale."""
    if tone not in TALKS: raise ValueError("unknown team talk")
    i = 0 if side == "home" else 1
    diff = lv["score"][i] - lv["score"][1 - i]
    me, opp = club_of(st, lv[side]), club_of(st, lv["away" if side == "home" else "home"])
    fav = me["reputation"] - opp["reputation"]
    table = {  # (morale delta, in-match boost)
        "calm": (2, 0.3) if diff >= 0 else (0, 0.2),
        "encourage": (6, 1.0) if (diff < 0 or fav < 0) else (3, 0.5),
        "demand": (-3, 1.5) if diff <= 0 and fav > 0 else (-6, 0.3),
        "praise": (7, 0.6) if diff > 0 else (-2, -0.4),
        "criticise": (-8, 1.2) if diff < 0 and fav > 5 else (-10, -0.6),
    }
    dm, boost = table[tone]
    if when == "pre" and tone == "praise": dm, boost = (1, 0.0)
    for s in lv["tactics"][side]["slots"]:
        p = st["players"][s["player_id"]]; p["morale"] = int(max(5, min(100, p["morale"] + dm)))
    lv["talk"][side] = boost
    lv["talks"][when] = tone
    reaction = ("The players look fired up." if boost >= 1 else "The players respond well." if dm > 0 else
                "Mixed reaction in the dressing room." if dm >= -3 else "Heads drop. Some players look unhappy.")
    lv["events"].append({"minute": lv["minute"], "type": "talk", "side": side, "text": f"{when.title()} talk ({tone}): {reaction}"})
    return {"tone": tone, "morale_delta": dm, "boost": boost, "reaction": reaction}


def quick_match(st, fixture, rng, user_side=None) -> Dict:
    lv = new_live(st, fixture); lv["phase"] = "first"; lv["user_side"] = user_side
    step(st, lv, rng, 45); lv["phase"] = "second"; step(st, lv, rng, 90)
    return lv


# ---------------------------------------------------------------- season loop
def table(st) -> List[Dict]:
    rows = {c["id"]: {"club_id": c["id"], "name": c["name"], "short": c["short"], "colours": c["colours"], "badge": c["badge"],
                      "p": 0, "w": 0, "d": 0, "l": 0, "gf": 0, "ga": 0, "pts": 0, "form": []} for c in st["clubs"]}
    for f in st["fixtures"]:
        if not f["played"]: continue
        h, a = f["score"]; H, A = rows[f["home"]], rows[f["away"]]
        for r, gf, ga in ((H, h, a), (A, a, h)):
            r["p"] += 1; r["gf"] += gf; r["ga"] += ga
            if gf > ga: r["w"] += 1; r["pts"] += 3; r["form"].append("W")
            elif gf == ga: r["d"] += 1; r["pts"] += 1; r["form"].append("D")
            else: r["l"] += 1; r["form"].append("L")
    out = sorted(rows.values(), key=lambda r: (-r["pts"], -(r["gf"] - r["ga"]), -r["gf"], r["name"]))
    for i, r in enumerate(out): r["pos"] = i + 1; r["gd"] = r["gf"] - r["ga"]; r["form"] = r["form"][-5:]
    return out


def user_fixture(st, week=None) -> Optional[Dict]:
    week = week or st["week"]
    return next((f for f in st["fixtures"] if f["week"] == week and st["club_id"] in (f["home"], f["away"])), None)


def apply_result(st, fixture, lv, rng):
    P = st["players"]
    fixture["played"] = True; fixture["score"] = list(lv["score"])
    fixture["events"] = [e for e in lv["events"] if e["type"] in ("goal", "sub")][:20]
    fixture["xg"] = lv["stats"]["xg"]
    for i, side in enumerate(("home", "away")):
        cid = fixture[side]; gf, ga = lv["score"][i], lv["score"][1 - i]
        res = 1 if gf > ga else 0 if gf == ga else -1
        played = {s["player_id"] for s in lv["tactics"][side]["slots"]} | {e["out"] for e in lv["events"] if e.get("type") == "sub" and e["side"] == side}
        for pid in club_of(st, cid)["squad"]:
            p = P[pid]
            if pid in played:
                p["fitness"] = int(max(35, lv["fitness"].get(pid, p["fitness"])))
                goals = sum(1 for e in lv["events"] if e["type"] == "goal" and e.get("player") == pid)
                p["form"] = round(max(1, min(10, p["form"] * 0.7 + 0.3 * (6 + 1.5 * res + 1.2 * goals + rng.uniform(-1, 1)))), 1)
                p["morale"] = int(max(5, min(100, p["morale"] + 4 * res + 3 * goals)))
            else:
                p["morale"] = int(max(5, min(100, p["morale"] - (1 if p["ovr"] > 70 else 0))))


def recover(st):
    for p in st["players"].values():
        p["fitness"] = int(min(100, p["fitness"] + 22 + p["attrs"]["stamina"] / 10))


def finish_week(st, rng) -> Dict:
    """Play every other fixture of the week, run finances/board/academy, advance the calendar."""
    week = st["week"]; me = st["club_id"]
    for f in st["fixtures"]:
        if f["week"] == week and not f["played"]:
            lv = quick_match(st, f, rng)
            apply_result(st, f, lv, rng)
    myf = user_fixture(st, week)
    summary = {"week": week}
    fin = st["finances"]; club = club_of(st, me)
    wages = sum(st["players"][p]["wage"] for p in club["squad"]) / 1000  # £m per week
    gate = (0.55 + club["reputation"] / 250) if (myf and myf["home"] == me) else 0.0
    tv = 0.35
    fin["balance"] = round(fin["balance"] + gate + tv - wages, 2)
    fin["income"].append({"week": week, "season": st["season"], "gate": round(gate, 2), "tv": tv})
    fin["expenses"].append({"week": week, "season": st["season"], "wages": round(wages, 2)})
    del fin["income"][:-30]; del fin["expenses"][:-30]
    # board confidence
    tb = table(st); pos = next(r["pos"] for r in tb if r["club_id"] == me)
    b = st["board"]
    if myf and myf["played"]:
        i = 0 if myf["home"] == me else 1
        gf, ga = myf["score"][i], myf["score"][1 - i]
        opp = club_of(st, myf["away"] if i == 0 else myf["home"])
        exp = max(0.2, min(0.8, 0.5 + (club["reputation"] - opp["reputation"]) / 40 + (0.06 if i == 0 else -0.04)))
        actual = 1 if gf > ga else 0.5 if gf == ga else 0
        delta = 10 * (actual - exp) + (1.5 if pos <= b["target_pos"] else -min(4, pos - b["target_pos"]))
        if fin["balance"] < 0: delta -= 3
        b["confidence"] = int(max(0, min(100, b["confidence"] + delta)))
        b["history"].append({"season": st["season"], "week": week, "confidence": b["confidence"], "pos": pos})
        del b["history"][:-44]
        summary.update(result=[gf, ga], opponent=opp["name"], board_delta=round(delta, 1))
    if b["confidence"] < 15 and week >= 6:
        st["status"] = "sacked"
        news(st, f"The board has lost confidence. {club['name']} have relieved you of your duties.", "bad")
        return summary
    if week == ACADEMY_WEEK and not st["academy"]["intake_done"]:
        academy_intake(st, rng)
    ai_transfer_activity(st, rng)
    recover(st)
    st["week"] += 1
    if st["week"] > WEEKS_PER_SEASON:
        end_season(st, rng, tb)
    summary["table_pos"] = pos
    return summary


def academy_intake(st, rng):
    club = club_of(st, st["club_id"]); st["academy"]["intake_done"] = True
    lvl = club["reputation"] - 22
    pros = []
    for _ in range(rng.randint(3, 5)):
        pid = f"p{st['next_pid']}"; st["next_pid"] += 1
        p = make_player(rng, pid, rng.choice(SQUAD_SHAPE[2:]), lvl, age=rng.randint(16, 17), club_id=None, season=st["season"])
        p["youth"] = True; p["potential"] = min(94, p["ovr"] + rng.randint(12, 32)); p["wage"] = 1
        p["contract_until"] = st["season"] + 3; refresh(p, st["season"])
        st["players"][pid] = p; pros.append(pid)
    st["academy"]["prospects"] = pros
    best = max(pros, key=lambda x: st["players"][x]["potential"])
    news(st, f"Youth intake day: {len(pros)} academy prospects arrive. Pick of the bunch: {st['players'][best]['name']} "
             f"(potential {st['players'][best]['potential']}).", "good")


def promote_youth(st, pid, keep=True):
    if pid not in st["academy"]["prospects"]: raise ValueError("not an academy prospect")
    st["academy"]["prospects"].remove(pid)
    p = st["players"][pid]
    if keep:
        p["club_id"] = st["club_id"]; club_of(st, st["club_id"])["squad"].append(pid)
        news(st, f"{p['name']} signs a first professional contract.", "good")
    else:
        del st["players"][pid]
    return p if keep else None


def ai_transfer_activity(st, rng):
    if not window_open(st): return
    # occasional AI bid for a listed (or random good) user player
    me = club_of(st, st["club_id"])
    listed = [p for p in me["squad"] if st["players"][p]["listed"]]
    if listed and rng.random() < 0.7:
        pid = rng.choice(listed); p = st["players"][pid]
        buyer = rng.choice([c for c in st["clubs"] if c["id"] != me["id"]])
        offer = round(p["value"] * rng.uniform(0.75, 1.1), 1)
        st["transfers"].setdefault("incoming_offers", []).append({"player_id": pid, "club_id": buyer["id"], "fee": offer,
                                                                  "week": st["week"], "season": st["season"]})
        news(st, f"{buyer['name']} bid £{offer}m for {p['name']}.", "transfer")


def end_season(st, rng, tb):
    me = st["club_id"]; pos = next(r["pos"] for r in tb if r["club_id"] == me)
    b = st["board"]; champ = tb[0]["name"]
    verdict = "exceeded" if pos < b["target_pos"] else "met" if pos == b["target_pos"] else "missed"
    st.setdefault("seasons", []).append({"season": st["season"], "pos": pos, "champion": champ, "verdict": verdict,
                                         "pts": next(r["pts"] for r in tb if r["club_id"] == me)})
    if verdict == "missed" and pos - b["target_pos"] >= 4:
        b["confidence"] = max(0, b["confidence"] - 25)
    else:
        b["confidence"] = min(100, b["confidence"] + (15 if verdict == "exceeded" else 5))
    news(st, f"Season {st['season']} complete: you finished {ordinal(pos)} ({verdict} the board's target). {champ} are champions.",
         "good" if verdict != "missed" else "bad")
    if b["confidence"] < 15:
        st["status"] = "sacked"; news(st, "The board has decided to make a change. You have been sacked.", "bad"); return
    # ageing & development
    for p in list(st["players"].values()):
        p["age"] += 1
        grow = (rng.randint(1, 5) if p["age"] <= 23 and p["ovr"] < p["potential"] else rng.randint(-1, 2) if p["age"] <= 29 else -rng.randint(1, 4))
        for a in ATTRS: p["attrs"][a] = max(20, min(97, p["attrs"][a] + grow + rng.randint(-1, 1)))
        refresh(p, st["season"] + 1)
    # expiring contracts leave
    club = club_of(st, me)
    for pid in list(club["squad"]):
        p = st["players"][pid]
        if p["contract_until"] < st["season"] + 1 and len(club["squad"]) > 18:
            club["squad"].remove(pid); p["club_id"] = None; news(st, f"{p['name']} leaves on a free as his contract expires.")
        elif p["contract_until"] < st["season"] + 1:
            p["contract_until"] = st["season"] + 2
    st["season"] += 1; st["week"] = 1
    st["fixtures"] = round_robin([c["id"] for c in st["clubs"]], rng)
    st["academy"] = {"intake_done": False, "prospects": []}
    fin = st["finances"]
    prize = round((13 - pos) * 1.1, 1)
    fin["balance"] = round(fin["balance"] + prize, 1)
    fin["transfer_budget"] = round(max(2, fin["transfer_budget"] * 0.5 + prize + max(0, fin["balance"]) * 0.2), 1)
    b["target_pos"] = max(1, min(10, pos - (1 if verdict != "missed" else 0) if pos > 1 else 1))
    b["expectation"] = expectation_text(b["target_pos"])
    news(st, f"Season {st['season']} begins. New board target: {b['expectation']}. Prize money £{prize}m.")


# ---------------------------------------------------------------- transfers
def asking_price(st, pid) -> float:
    p = st["players"][pid]
    if p["club_id"] is None: return 0.0
    club = club_of(st, p["club_id"])
    rank = sorted(club["squad"], key=lambda x: -st["players"][x]["ovr"]).index(pid)
    key = 1.35 if rank < 6 else 1.15 if rank < 13 else 0.95
    return round(p["value"] * key, 1)


def scout(st, pos=None, max_age=None, min_ovr=None, max_value=None, q=None, limit=40) -> List[Dict]:
    me = st["club_id"]; out = []
    for p in st["players"].values():
        if p["club_id"] == me or p.get("youth") and p["club_id"] is None: continue
        if pos and p["pos"] != pos: continue
        if max_age and p["age"] > max_age: continue
        if min_ovr and p["ovr"] < min_ovr: continue
        if max_value is not None and p["value"] > max_value: continue
        if q and q.lower() not in p["name"].lower(): continue
        out.append(p)
    out.sort(key=lambda p: -(p["ovr"] + (p["potential"] - p["ovr"]) * 0.3))
    return out[:limit]


def bid(st, pid, fee: float, wage: int, rng) -> Dict:
    """User bids for a player. AI club responds (accept / counter / reject), then the player negotiates wages."""
    if not window_open(st): raise ValueError("the transfer window is closed")
    p = st["players"].get(pid)
    if not p or p["club_id"] == st["club_id"]: raise ValueError("unknown player")
    fin = st["finances"]; neg = st["transfers"]["negotiations"].get(pid) or {"rounds": 0, "status": "open"}
    if neg.get("status") == "walked": raise ValueError("talks have collapsed with this club")
    if fee > fin["transfer_budget"] + 1e-9: raise ValueError("bid exceeds your transfer budget")
    my_wages = sum(st["players"][x]["wage"] for x in club_of(st, st["club_id"])["squad"])
    if my_wages + wage > fin["wage_budget"]: raise ValueError("wage offer would break your wage budget")
    neg["rounds"] += 1; ask = min(asking_price(st, pid), neg.get("counter") or 1e9); demand = wage_demand(p) + (2 if p["club_id"] else 0)
    resp = {"player_id": pid, "asking": ask, "fee": fee, "wage": wage, "round": neg["rounds"]}
    seller = club_of(st, p["club_id"])["name"] if p["club_id"] else "Free agent"
    if p["club_id"] and fee < ask:
        if fee >= ask * 0.8 and neg["rounds"] < 4:
            counter = round((ask + fee) / 2 if neg["rounds"] >= 2 else ask, 1)
            neg.update(status="countered", counter=counter); resp.update(club="counter", counter=counter,
                                                                           message=f"{seller} want £{counter}m.")
        elif neg["rounds"] >= 4:
            neg["status"] = "walked"; resp.update(club="walked", message=f"{seller} have ended talks.")
        else:
            neg["status"] = "rejected"; resp.update(club="reject", message=f"{seller} reject the bid as derisory (they value him at about £{ask}m).")
        st["transfers"]["negotiations"][pid] = neg; return resp
    resp["club"] = "accept"
    if wage < demand:
        neg.update(status="wage", wage_demand=demand)
        resp.update(player="counter", wage_demand=demand, message=f"Fee agreed. {p['name']} wants £{demand}k a week.")
        st["transfers"]["negotiations"][pid] = neg; return resp
    complete_signing(st, pid, fee, wage)
    st["transfers"]["negotiations"].pop(pid, None)
    resp.update(player="accept", message=f"Done deal! {p['name']} signs for £{fee}m on £{wage}k a week.", completed=True)
    return resp


def complete_signing(st, pid, fee, wage):
    p = st["players"][pid]; me = club_of(st, st["club_id"])
    if p["club_id"]:
        seller = club_of(st, p["club_id"]); seller["squad"].remove(pid); seller["balance"] = round(seller["balance"] + fee, 1)
        st["tactics"][seller["id"]] = auto_tactics(st, seller["id"])
    p["club_id"] = me["id"]; p["wage"] = wage; p["contract_until"] = st["season"] + 3; p["listed"] = False
    p["morale"] = min(100, p["morale"] + 10); me["squad"].append(pid)
    st["finances"]["transfer_budget"] = round(st["finances"]["transfer_budget"] - fee, 1)
    st["finances"]["balance"] = round(st["finances"]["balance"] - fee, 1)
    st["transfers"]["log"].insert(0, {"season": st["season"], "week": st["week"], "player_id": pid, "name": p["name"],
                                      "dir": "in", "fee": fee, "wage": wage})
    news(st, f"Signed: {p['name']} ({p['pos']}, {p['ovr']}) for £{fee}m.", "transfer")


def walk_away(st, pid):
    st["transfers"]["negotiations"][pid] = {"rounds": 0, "status": "walked"}
    return {"player_id": pid, "status": "walked"}


def sell(st, pid, accept: bool, rng) -> Dict:
    offers = st["transfers"].setdefault("incoming_offers", [])
    off = next((o for o in offers if o["player_id"] == pid), None)
    if not off: raise ValueError("no offer for that player")
    offers.remove(off)
    if not accept:
        news(st, f"You rejected {club_of(st, off['club_id'])['name']}'s bid."); return {"status": "rejected"}
    if not window_open(st): raise ValueError("the transfer window is closed")
    me = club_of(st, st["club_id"]); p = st["players"][pid]
    if len(me["squad"]) <= 16: raise ValueError("squad too small to sell")
    me["squad"].remove(pid); buyer = club_of(st, off["club_id"]); buyer["squad"].append(pid); p["club_id"] = buyer["id"]; p["listed"] = False
    st["finances"]["balance"] = round(st["finances"]["balance"] + off["fee"], 1)
    st["finances"]["transfer_budget"] = round(st["finances"]["transfer_budget"] + off["fee"] * 0.8, 1)
    st["transfers"]["log"].insert(0, {"season": st["season"], "week": st["week"], "player_id": pid, "name": p["name"],
                                      "dir": "out", "fee": off["fee"], "wage": p["wage"]})
    t = st["tactics"].get(me["id"])
    if t and any(s["player_id"] == pid for s in t["slots"]): st["tactics"][me["id"]] = auto_tactics(st, me["id"], t["formation"] if t["formation"] in FORMATIONS else None)
    news(st, f"Sold: {p['name']} to {buyer['name']} for £{off['fee']}m.", "transfer")
    return {"status": "sold", "fee": off["fee"]}
