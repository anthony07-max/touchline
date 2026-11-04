"""Match centre: dynamic fixture discovery, per-fixture state machine snapshot, SSE push,
2D reconstruction timeline, full-time recap, and a fantasy MVP. Quota-aware: every upstream call
goes through api_football's disk cache with phase-aware max_age, so N viewers cost 0 extra calls."""
from __future__ import annotations
import asyncio, datetime as dt, json, math, os, random, threading, time, urllib.parse
from typing import Dict, List, Optional
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse, FileResponse
from pydantic import BaseModel
from . import api_football as af, adapter, matchstate

router = APIRouter()
FRONT = os.path.join(os.path.dirname(__file__), "..", "..", "frontend")
FEATURED = [int(x) for x in os.environ.get("FEATURED_FIXTURES", "1635695").split(",") if x]
CLASSIC = [1374897]  # 2025 Europa League final (recorded), always available as a replay
LEAGUES = {39: "Premier League", 2: "Champions League", 3: "Europa League", 848: "Conference League",
           45: "FA Cup", 48: "League Cup", 41: "League One", 40: "Championship", 140: "La Liga", 135: "Serie A", 78: "Bundesliga"}
BIG = {39, 2, 3}
HOME_CLUB = os.environ.get("HOME_CLUB", "Leyton Orient")
ELITE = ["Real Madrid", "Manchester City", "Barcelona", "Bayern", "Paris Saint Germain", "Liverpool", "Arsenal", "Inter"]
# Famous recent finished elite-club matches: (season, CL round, home, away, title). Resolved server-side once, then
# frozen into data/classics.json in the repo so they cost zero API calls afterwards.
CLASSIC_SPECS = [
    (2024, "Final", "Paris Saint Germain", "Inter", "2025 Champions League final"),
    (2024, "Semi-finals", "Inter", "Barcelona", "2025 UCL semi-final, 2nd leg"),
    (2024, "Quarter-finals", "Arsenal", "Real Madrid", "2025 UCL quarter-final, 1st leg"),
    (2023, "Quarter-finals", "Real Madrid", "Manchester City", "2024 UCL quarter-final, 1st leg"),
    (2023, "Final", "Borussia Dortmund", "Real Madrid", "2024 Champions League final"),
    (2022, "Final", "Manchester City", "Inter", "2023 Champions League final"),
    (2022, "Semi-finals", "Manchester City", "Real Madrid", "2023 UCL semi-final, 2nd leg"),
]
CLASSICS_FILE = os.path.join(os.path.dirname(__file__), "..", "..", "data", "classics.json")
CLASSIC_RAW: Dict[int, dict] = {}   # fid -> raw API-Football fixture object (with events/lineups/stats)
CLASSIC_META: Dict[int, str] = {}


def is_elite(name: str) -> bool:
    n = (name or "").lower()
    return any(e.lower() in n for e in ELITE)


def _load_classics():
    try:
        with open(CLASSICS_FILE) as f: d = json.load(f)
        for it in d.get("classics", []):
            fid = it["fixture"]["fixture"]["id"]; CLASSIC_RAW[fid] = it["fixture"]; CLASSIC_META[fid] = it["title"]
    except (OSError, ValueError, KeyError): pass


_load_classics()
_resolving = threading.Lock()


def resolve_classics() -> dict:
    """Server-side, gentle: one call per distinct (season, round) + one fixtures?ids= call; cached ~forever."""
    if not _resolving.acquire(blocking=False): return {"busy": True}
    try:
        want = [c for c in CLASSIC_SPECS if not any(CLASSIC_META.get(f) == c[4] for f in CLASSIC_META)]
        found = {}
        for season, rnd in dict.fromkeys((c[0], c[1]) for c in want):
            try: d = af.get(f"fixtures?league=2&season={season}&round={urllib.parse.quote(rnd)}", max_age=86400 * 3650)
            except Exception: continue
            for fx in d.get("response") or []:
                h, a = fx["teams"]["home"]["name"], fx["teams"]["away"]["name"]
                for c in want:
                    if c[0] == season and c[1] == rnd and c[2].lower() in h.lower() and c[3].lower() in a.lower():
                        found[fx["fixture"]["id"]] = c[4]
        if found:
            ids = "-".join(str(i) for i in list(found)[:20])
            try:
                d = af.get(f"fixtures?ids={ids}", max_age=86400 * 3650)
                for fx in d.get("response") or []:
                    fid = fx["fixture"]["id"]; CLASSIC_RAW[fid] = fx; CLASSIC_META[fid] = found.get(fid, "Classic")
            except Exception: pass
        DISCOVERY["at"] = 0
        return {"resolved": len(CLASSIC_RAW)}
    finally: _resolving.release()


def classics_payload() -> dict:
    order = {c[4]: i for i, c in enumerate(CLASSIC_SPECS)}
    fids = sorted(CLASSIC_RAW, key=lambda f: order.get(CLASSIC_META.get(f), 99))
    return {"classics": [{"title": CLASSIC_META[f], "fixture": CLASSIC_RAW[f]} for f in fids]}
_mem: Dict[int, dict] = {}
_lock = threading.Lock()
DISCOVERY = {"at": 0, "items": [], "note": None}


def _now(): return dt.datetime.now(dt.timezone.utc)


def snapshot(fid: int) -> dict:
    """Cached, phase-aware fixture snapshot."""
    with _lock:
        m = _mem.get(fid)
        if m and time.time() < m["next"]: return m["snap"]
        max_age = 30 if not m else max(30, m["next"] - m["at"])
        err = None
        try:
            if fid in CLASSIC_RAW: live = adapter.parse_fixture(CLASSIC_RAW[fid])
            else:
                d = af.get(f"fixtures?id={fid}", max_age=max_age)
                if not d.get("response"): raise af.APIError("fixture not found")
                live = adapter.parse_fixture(d["response"][0])
        except Exception as e:
            if m: return {**m["snap"], "error": str(e)[:160]}
            raise HTTPException(503, f"data unavailable: {e}")
        ph = matchstate.phase(live["status"]["short"], live["kickoff_utc"])
        wait = matchstate.poll_seconds(ph["phase"], ph["seconds_to_ko"], af.quota["remaining"])
        feed_err = af.quota.get("last_error")
        snap = {"fixture_id": fid, "live": live, "state": ph, "fetched_at": time.time(), "next_refresh": time.time() + wait,
                "quota_remaining": af.quota["remaining"],
                "feed_warning": ("Live feed paused (provider account issue); showing last cached data" if feed_err else None),
                "recap": recap(live, ph) if ph["ended"] else None}
        _mem[fid] = {"snap": snap, "at": time.time(), "next": time.time() + wait}
        return snap


def recap(live: dict, ph: dict) -> dict:
    ev = live["events"]; hid = live["home"]["id"]
    key = [e for e in ev if e["type"] in ("Goal", "Card") and (e["type"] == "Goal" or "Red" in (e["detail"] or ""))]
    st = live["statistics"]["by_team"]
    def side(e):
        own = e.get("detail") == "Own Goal"
        return "home" if (e["team_id"] == hid) != own else "away"
    hs, as_ = live["score"]["home"], live["score"]["away"]
    if ph["phase"] == "stale_finished": headline = "Full time. Final score not yet confirmed by the feed."
    elif hs is None: headline = "Full time."
    else:
        w = live["home"]["name"] if hs > as_ else live["away"]["name"] if as_ > hs else None
        headline = f"{w} win {max(hs, as_)}–{min(hs, as_)}" if w else f"Honours even, {hs}–{as_}"
    motm = None
    scorers = {}
    for e in ev:
        if e["type"] == "Goal" and e["detail"] != "Own Goal" and e["player"]: scorers[e["player"]] = scorers.get(e["player"], 0) + 2
        if e["type"] == "Goal" and e["assist"]: scorers[e["assist"]] = scorers.get(e["assist"], 0) + 1
    if scorers: motm = max(scorers, key=scorers.get)
    return {"headline": headline, "score": [hs, as_], "key_moments": [{**e, "side": side(e)} for e in key],
            "stats": st, "star": motm, "confirmed": ph["phase"] == "fulltime"}


# ---------------- discovery ----------------
def _summ(fx: dict) -> dict:
    f, t, g, lg = fx["fixture"], fx["teams"], fx["goals"], fx["league"]
    ph = matchstate.phase(f["status"]["short"], f["date"])
    return {"fixture_id": f["id"], "kickoff_utc": f["date"], "league": lg["name"], "league_id": lg["id"], "round": lg.get("round"),
            "venue": (f.get("venue") or {}).get("name"), "status": f["status"]["short"], "elapsed": f["status"].get("elapsed"),
            "home": {"id": t["home"]["id"], "name": t["home"]["name"], "logo": t["home"]["logo"]},
            "away": {"id": t["away"]["id"], "name": t["away"]["name"], "logo": t["away"]["logo"]},
            "score": [g["home"], g["away"]], "state": ph,
            "elite": is_elite(t["home"]["name"]) or is_elite(t["away"]["name"])}


def _rank(x):
    hc = HOME_CLUB.lower() in (x["home"]["name"] + x["away"]["name"]).lower()
    p = {"live": 0, "halftime": 0, "prematch": 1, "upcoming": 2, "fulltime": 3, "stale_finished": 3, "postponed": 4}[x["state"]["phase"]]
    el = 0 if (is_elite(x["home"]["name"]) and is_elite(x["away"]["name"])) else 1 if x.get("elite") else 2
    return (p, 0 if hc else 1, el, 0 if x["league_id"] in BIG else 1, x["kickoff_utc"])


def discover(days: int = 3) -> dict:
    """Upcoming/live big fixtures. One fixtures?date= call per day, cached 6h (3 days = ~12 calls/day max)."""
    if time.time() - DISCOVERY["at"] < 600 and DISCOVERY["items"]: return DISCOVERY
    items, note = {}, None
    blocked = "access" in json.dumps(af.quota.get("last_error") or {})
    for i in range(0 if blocked else days):
        day = (_now() + dt.timedelta(days=i)).date().isoformat()
        try:
            d = af.get(f"fixtures?date={day}&timezone=Europe/London", max_age=3600 * 6 if i else 1800)
            for fx in d.get("response") or []:
                nm = fx["teams"]["home"]["name"] + fx["teams"]["away"]["name"]
                if fx["league"]["id"] in LEAGUES or HOME_CLUB.lower() in nm.lower():
                    s = _summ(fx); items[s["fixture_id"]] = s
        except Exception as e:
            note = "Fixture feed unavailable right now; showing saved matches."
    if len(CLASSIC_RAW) < len(CLASSIC_SPECS) and not blocked and not _resolving.locked():
        threading.Thread(target=resolve_classics, daemon=True).start()
    for fid in FEATURED + CLASSIC:
        if fid in items: continue
        try:
            d = af.get(f"fixtures?id={fid}", max_age=3600 * 6)
            if d.get("response"):
                s = _summ(d["response"][0]); s["classic"] = fid in CLASSIC; items[fid] = s
        except Exception: pass
    out = sorted(items.values(), key=_rank)
    # keep it tight: live + next 12 + recent finished 4
    up = [x for x in out if not x["state"]["ended"]][:12]; done = [x for x in out if x["state"]["ended"] and x["fixture_id"] not in CLASSIC_RAW][:4]
    classics = []
    for fid in classics_order():
        try: c = _summ(CLASSIC_RAW[fid]); c["classic"] = True; c["classic_title"] = CLASSIC_META[fid]; classics.append(c)
        except Exception: pass
    done += classics
    DISCOVERY.update(at=time.time(), items=up + done, note=note if (note or af.quota.get("last_error")) else None)
    if af.quota.get("last_error") and not note: DISCOVERY["note"] = "Live feed paused (provider account issue); showing saved matches."
    return DISCOVERY


def classics_order():
    order = {c[4]: i for i, c in enumerate(CLASSIC_SPECS)}
    return sorted(CLASSIC_RAW, key=lambda f: order.get(CLASSIC_META.get(f), 99))


@router.get("/api/classics")
def classics():
    """Frozen classic replays (raw fixture data). Also used to bake data/classics.json into the repo."""
    return {**classics_payload(), "pending": [c[4] for c in CLASSIC_SPECS if c[4] not in CLASSIC_META.values()]}


@router.get("/api/fixtures")
def fixtures():
    d = discover()
    return {"fixtures": d["items"], "note": d["note"], "home_club": HOME_CLUB, "generated": time.time()}


@router.get("/api/centre/{fid}")
def centre(fid: int):
    return snapshot(fid)


@router.get("/api/centre/{fid}/stream")
async def stream(fid: int, request: Request):
    """SSE: pushes a snapshot whenever the cached data changes, heartbeat every 15s."""
    async def gen():
        last = None; beat = 0
        while True:
            if await request.is_disconnected(): break
            try:
                s = await asyncio.to_thread(snapshot, fid)
                if s["fetched_at"] != last:
                    last = s["fetched_at"]; yield f"event: snapshot\ndata: {json.dumps(s, default=str)}\n\n"
                elif beat % 3 == 0:
                    yield f"event: tick\ndata: {json.dumps({'server_time': time.time(), 'state': matchstate.phase(s['live']['status']['short'], s['live']['kickoff_utc'])})}\n\n"
            except HTTPException as e:
                yield f"event: error\ndata: {json.dumps({'detail': e.detail})}\n\n"
            beat += 1
            await asyncio.sleep(5)
    return StreamingResponse(gen(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# ---------------- 2D reconstruction timeline ----------------
SLOTS = {  # x: 0 own goal -> 100 opp goal, y: 0..100 width
    "GK": [(5, 50)], "DEF4": [(22, 15), (20, 38), (20, 62), (22, 85)], "DEF3": [(20, 25), (18, 50), (20, 75)], "DEF5": [(28, 8), (20, 30), (18, 50), (20, 70), (28, 92)],
}


def shape(formation: str):
    try: lines = [int(x) for x in (formation or "4-2-3-1").split("-")]
    except ValueError: lines = [4, 2, 3, 1]
    pts = [(5, 50)]; n = len(lines)
    for i, k in enumerate(lines):
        x = 20 + i * (60 / max(1, n - 1)) if n > 1 else 50
        for j in range(k): pts.append((round(x, 1), round((j + 1) * 100 / (k + 1), 1)))
    return pts[:11]


def timeline(live: dict, seed: Optional[int] = None, upto: Optional[int] = None) -> dict:
    """Deterministic reconstruction: REAL events (goals/cards/subs, minute-exact) + modelled ball movement between them,
    shaped by real possession/shot stats when available. Clearly labelled as a reconstruction."""
    rnd = random.Random(seed if seed is not None else live["fixture_id"])
    hid = live["home"]["id"]; st = live["statistics"]["by_team"]
    def num(v, d):
        try: return float(str(v).rstrip("%"))
        except (TypeError, ValueError): return d
    hst, ast = st.get(str(hid), {}), st.get(str(live["away"]["id"]), {})
    poss_h = num(hst.get("possession"), 50) / 100
    shots = {"home": int(num(hst.get("shots"), 12)), "away": int(num(ast.get("shots"), 11))}
    end = upto if upto is not None else (live["status"]["elapsed"] or (90 if matchstate.phase(live["status"]["short"], live["kickoff_utc"])["ended"] else 0))
    end = max(0, min(end, 120))
    teams = {}
    for side in ("home", "away"):
        lu = live["lineups"]["by_team"].get(str(live[side]["id"]))
        names = [p["name"] for p in lu["startXI"]] if lu else [f"{side[0].upper()}{i+1}" for i in range(11)]
        nums = [p.get("number") for p in lu["startXI"]] if lu else list(range(1, 12))
        teams[side] = {"name": live[side]["name"], "formation": (lu or {}).get("formation") or "4-2-3-1", "players": names, "numbers": nums,
                       "slots": shape((lu or {}).get("formation") or "4-2-3-1"), "official": bool(lu)}
    real = []
    for e in live["events"]:
        m = (e["minute"] or 0) + (e["extra"] or 0) * 0.01
        if m > end: continue
        sd = "home" if e["team_id"] == hid else "away"
        if e["type"] == "Goal" and e["detail"] == "Own Goal": sd = "away" if sd == "home" else "home"
        real.append({**e, "side": sd, "m": m})
    goals = [e for e in real if e["type"] == "Goal" and e["detail"] != "Missed Penalty"]
    seq, t = [], 0.0
    shot_budget = {k: max(0, int(v * end / 90) - sum(1 for g in goals if g["side"] == k)) for k, v in shots.items()}
    gi = 0
    while t < end * 60:
        nxt = goals[gi] if gi < len(goals) else None
        side = nxt["side"] if nxt and nxt["m"] * 60 - t < 45 else ("home" if rnd.random() < poss_h else "away")
        n = rnd.randint(2, 7); idx = rnd.randint(1, 4)
        poss = {"side": side, "t0": round(t, 1), "passes": []}
        for k in range(n):
            j = min(10, max(1, idx + rnd.choice([-1, 1, 1, 2, 2, 3])))
            poss["passes"].append([idx, j]); idx = j; t += rnd.uniform(2.2, 4.5)
        goal_now = nxt and t >= nxt["m"] * 60 - 4
        left_min = max(1, end - t / 60)
        shoot = goal_now or rnd.random() < min(0.9, shot_budget[side] / max(1, left_min * 0.55))
        if shoot:
            xg = round(rnd.uniform(0.25, 0.6) if goal_now else rnd.choice([0.03, 0.05, 0.08, 0.12, 0.2, 0.35]), 2)
            y = rnd.gauss(50, 14 if goal_now else 20); x = 100 - (rnd.uniform(6, 12) if goal_now else rnd.uniform(7, 14) + (0.4 - min(xg, 0.4)) * 50)
            poss["shot"] = {"x": round(max(60, min(96, x)), 1), "y": round(max(15, min(85, y)), 1), "xg": xg, "goal": bool(goal_now),
                            "player": nxt["player"] if goal_now else None, "real": bool(goal_now)}
            if goal_now: gi += 1
            elif shot_budget[side] > 0: shot_budget[side] -= 1
            t += 3
        poss["t1"] = round(t, 1)
        seq.append(poss); t += rnd.uniform(1, 4)
    flashes = [{"t": round(e["m"] * 60, 1), "side": e["side"], "type": e["type"], "detail": e["detail"], "player": e["player"], "minute": e["minute"], "extra": e["extra"]} for e in real]
    return {"fixture_id": live["fixture_id"], "end_minute": end, "teams": teams, "possessions": seq, "events": flashes,
            "label": "2D reconstruction: real goals, cards and subs at their real minutes; ball movement between them is modelled from real possession/shot stats.",
            "stats_source": "real" if st else "estimated"}


@router.get("/api/centre/{fid}/timeline")
def centre_timeline(fid: int, upto: Optional[int] = None):
    s = snapshot(fid)
    return timeline(s["live"], upto=upto)


# ---------------- Fantasy MVP ----------------
PRICE_BASE = {"GK": 4.5, "DEF": 4.5, "MID": 5.5, "FWD": 6.5}


def price(p: dict) -> float:
    q = (p.get("att", 60) + p.get("defn", 60) + p.get("pas", 60)) / 3
    return round(PRICE_BASE.get(p.get("group"), 5) + max(0, (q - 58)) * 0.18, 1)


def fantasy_points(player_id: str, group: str, live: dict, team_side: str) -> dict:
    """Simplified FPL-style scoring from REAL events: appearance 2, goal GK/DEF 6, MID 5, FWD 4, assist 3,
    clean sheet GK/DEF 4 MID 1, yellow -1, red -3, own goal -2."""
    pts, why = 0, []
    ev = live["events"]; started = live["status"]["short"] not in ("NS", "TBD", "PST", "CANC")
    if started: pts += 2; why.append("appearance +2")
    for e in ev:
        if str(e.get("player_id")) == player_id:
            if e["type"] == "Goal" and e["detail"] == "Own Goal": pts -= 2; why.append("own goal -2")
            elif e["type"] == "Goal" and e["detail"] != "Missed Penalty":
                g = {"GK": 6, "DEF": 6, "MID": 5, "FWD": 4}.get(group, 4); pts += g; why.append(f"goal +{g}")
            elif e["type"] == "Card" and "Yellow" in (e["detail"] or ""): pts -= 1; why.append("yellow -1")
            elif e["type"] == "Card" and "Red" in (e["detail"] or ""): pts -= 3; why.append("red -3")
        if str(e.get("assist_id")) == player_id and e["type"] == "Goal": pts += 3; why.append("assist +3")
    conceded = live["score"]["away" if team_side == "home" else "home"]
    ph = matchstate.phase(live["status"]["short"], live["kickoff_utc"])
    if ph["phase"] == "fulltime" and conceded == 0 and group in ("GK", "DEF", "MID"):
        c = 1 if group == "MID" else 4; pts += c; why.append(f"clean sheet +{c}")
    return {"points": pts, "breakdown": why}


class FantasyIn(BaseModel):
    fixture_id: int
    picks: List[str]
    captain: str


@router.get("/api/fantasy/{fid}/pool")
def fantasy_pool(fid: int):
    from .router import live_squads
    s = snapshot(fid); sq = live_squads(fid)
    pool = []
    for side in ("home", "away"):
        ids = {p["id"] for p in sq[side]["squad"]}
        src = sq[side]["squad"] + [p for p in sq[side]["xi"] if p["id"] not in ids]  # official XI players who have since left still score
        for p in src:
            pool.append({"id": p["id"], "name": p["name"], "group": p["group"], "side": side, "team": sq[side]["team"],
                         "photo": p.get("photo"), "number": p.get("number"), "price": price(p),
                         "likely_starter": p["id"] in set(sq[side]["probable_xi"])})
    return {"fixture_id": fid, "budget": 60.0, "squad_size": 8, "rules": {"GK": [1, 1], "DEF": [2, 3], "MID": [2, 3], "FWD": [1, 3], "max_per_team": 5},
            "pool": pool, "state": s["state"], "home": s["live"]["home"], "away": s["live"]["away"]}


def validate_team(picks: List[dict], budget: float, rules: dict, captain: str, size: int):
    if len(picks) != size: return f"pick exactly {size} players"
    if len({p["id"] for p in picks}) != size: return "duplicate player"
    cost = round(sum(p["price"] for p in picks), 1)
    if cost > budget: return f"over budget ({cost} > {budget})"
    for g, lim in rules.items():
        if g == "max_per_team": continue
        lo, hi = lim
        n = sum(1 for p in picks if p["group"] == g)
        if not lo <= n <= hi: return f"{g}: need {lo}-{hi}, have {n}"
    for sd in ("home", "away"):
        if sum(1 for p in picks if p["side"] == sd) > rules["max_per_team"]: return f"max {rules['max_per_team']} per team"
    if captain not in {p["id"] for p in picks}: return "captain must be in your team"
    return None


@router.post("/api/fantasy/score")
def fantasy_score(b: FantasyIn):
    pool = fantasy_pool(b.fixture_id); by = {p["id"]: p for p in pool["pool"]}
    picks = [by[i] for i in b.picks if i in by]
    err = validate_team(picks, pool["budget"], pool["rules"], b.captain, pool["squad_size"])
    if err: raise HTTPException(422, err)
    live = snapshot(b.fixture_id)["live"]; total = 0; rows = []
    for p in picks:
        r = fantasy_points(p["id"], p["group"], live, p["side"])
        mult = 2 if p["id"] == b.captain else 1; total += r["points"] * mult
        rows.append({**p, **r, "captain": mult == 2})
    return {"total": total, "players": rows, "state": pool["state"], "cost": round(sum(p["price"] for p in picks), 1)}


@router.get("/match/{fid}", include_in_schema=False)
def match_page(fid: int):
    return FileResponse(os.path.join(FRONT, "match.html"))


@router.get("/fantasy", include_in_schema=False)
def fantasy_page():
    return FileResponse(os.path.join(FRONT, "fantasy.html"))
