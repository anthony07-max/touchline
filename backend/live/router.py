"""Live match routes (/api/live/*) + background poller for tonight's fixture. Additive; no shared files modified."""
from __future__ import annotations
import asyncio, datetime as dt, json, os, time
from typing import Optional, List
from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from touchline_engine.models import MatchState, Tactics, Substitution, FORMATIONS
from touchline_engine.simulate import compare, SimConfig
from . import provider as af, adapter, squads, matchstate

FIXTURE_ID = int(os.environ.get("LIVE_FIXTURE_ID", "1635695"))
router = APIRouter()
STATE = {"live": None, "fetched_at": None, "error": None, "seen": set(), "squads": {}, "next_poll": None}
LIVE_CODES = {"1H", "HT", "2H", "ET", "BT", "P", "LIVE", "INT", "SUSP"}
DONE = {"FT", "AET", "PEN", "PST", "CANC", "ABD", "AWD", "WO"}
BRANCHES = {}
FORMATION_MAP = {"3-4-2-1": "3-4-3", "3-4-1-2": "3-5-2", "4-1-4-1": "4-5-1", "4-4-1-1": "4-4-2", "4-3-1-2": "4-4-2", "4-1-2-1-2": "4-4-2", "3-1-4-2": "3-5-2", "5-4-1": "5-3-2", "4-3-2-1": "4-3-3", "4-2-2-2": "4-4-2", "3-5-1-1": "3-5-2", "4-2-4": "4-3-3"}


def fetch_fixture(fid: int, max_age: float = 60, force=False):
    from .centre import CLASSIC_RAW
    if fid in CLASSIC_RAW: return adapter.parse_fixture(CLASSIC_RAW[fid])
    d = af.get(f"fixtures?id={fid}", max_age=max_age, force=force)
    if not d.get("response"): raise af.APIError("fixture not found")
    return adapter.parse_fixture(d["response"][0])


def refresh(force=False):
    d = af.get(f"fixtures?id={FIXTURE_ID}", max_age=60, force=force)
    if not d.get("response"): raise af.APIError("fixture not found")
    live = adapter.parse_fixture(d["response"][0], STATE["seen"])
    STATE["seen"] |= {e["id"] for e in live["events"]}
    STATE.update(live=live, fetched_at=time.time(), error=None)
    return live


def interval(live) -> float:
    if live:
        ph = matchstate.phase(live["status"]["short"], live["kickoff_utc"])
        return matchstate.poll_seconds(ph["phase"], ph["seconds_to_ko"], af.quota["remaining"])
    return 600


def _old_interval(live) -> float:
    """Quota-aware poll interval (seconds)."""
    s = live["status"]["short"] if live else "NS"
    if s in DONE: return 3600 * 6
    ko = dt.datetime.fromisoformat(live["kickoff_utc"]) if live else None
    now = dt.datetime.now(dt.timezone.utc)
    if s == "NS" and ko:
        mins = (ko - now).total_seconds() / 60
        if mins > 75: return min(1800, (mins - 70) * 60)
        if mins > 2: return 600 if not live["lineups"]["available"] else max(120, mins * 60 - 60)
        return 90
    if s == "HT": return 300
    rem = af.quota["remaining"] or 50
    return max(90.0, 150 * 60 / max(1, rem - 8))  # spread remaining budget over ~150 min


async def poller():
    while True:
        try: await asyncio.to_thread(refresh, True)
        except Exception as e: STATE["error"] = str(e)[:200]
        wait = interval(STATE["live"]); STATE["next_poll"] = time.time() + wait
        await asyncio.sleep(wait)


def get_squad(team_id):
    if team_id not in STATE["squads"]:
        STATE["squads"][team_id] = squads.squad(team_id)
    return STATE["squads"][team_id]


def _snapshot():
    live = STATE["live"]
    ph = matchstate.phase(live["status"]["short"], live["kickoff_utc"]) if live else None
    return {"live": live, "phase": ph, "fetched_at": STATE["fetched_at"], "next_poll": STATE["next_poll"], "error": STATE["error"],
            "quota": {k: v for k, v in af.quota.items()}, "source": af.active()}


@router.get("/api/live/status")
def live_status():
    try: s = af.status()
    except af.APIError as e: raise HTTPException(503, str(e))
    return {"key_works": True, **s, "quota": af.quota, "fixture_id": FIXTURE_ID}


@router.get("/api/live/match")
def live_match():
    if not STATE["live"]:
        try: refresh()
        except af.APIError as e: raise HTTPException(503, f"data unavailable: {e}")
    return _snapshot()


@router.websocket("/ws/live")
async def live_ws(ws: WebSocket):
    await ws.accept(); last = None
    try:
        while True:
            if STATE["fetched_at"] != last:
                last = STATE["fetched_at"]; await ws.send_text(json.dumps(_snapshot(), default=str))
            await asyncio.sleep(3)
    except (WebSocketDisconnect, RuntimeError):
        pass


def _merge(lu_players, sq):
    by = {p["id"]: p for p in sq}
    out = []
    for p in lu_players:
        if p["id"] in by: q = dict(by[p["id"]])
        else:
            g = squads.GROUP.get(p.get("pos") or "M", "MID")
            q = {"id": p["id"], "name": p["name"], "group": g, "attributes_estimated": True, **squads.estimate(g, None, None)}
        if p.get("pos"): q["group"] = squads.GROUP.get(p["pos"], q["group"])
        out.append(q)
    return out


@router.get("/api/live/squads")
def live_squads(fixture_id: Optional[int] = None):
    fid = fixture_id or FIXTURE_ID
    live = live_match()["live"] if fid == FIXTURE_ID else fetch_fixture(fid, max_age=3600)
    res = {}
    for side in ("home", "away"):
        tid = live[side]["id"]
        try: sq = get_squad(tid)
        except af.APIError: sq = []
        lu = live["lineups"]["by_team"].get(str(tid))
        if lu:
            xi = _merge(lu["startXI"], sq)
            for p, l in zip(xi, lu["startXI"]): p["number"] = l.get("number"); p["grid"] = l.get("grid")
            src, form = "official", lu.get("formation")
        else:
            xi = squads.probable_xi(sq) if sq else []; src, form = "probable", "4-2-3-1"
        res[side] = {"team": live[side]["name"], "team_id": tid, "squad": sq, "xi": xi, "xi_source": src, "formation": form,
                     "probable_xi": [p["id"] for p in xi],
                     "label": ("Official starting XI" if src == "official" else "Likely XI (official line-ups ~60 min before kick-off)") +
                              ". Attributes are GAME ESTIMATES from 2024 season stats, not official ratings."}
    return res


def seed_state(at_minute=None, fid=None):
    live = live_match()["live"] if not fid or fid == FIXTURE_ID else fetch_fixture(fid, max_age=600)
    minute = live["status"]["elapsed"] or 0
    if at_minute is not None and not live["events"] and minute == 0: minute = at_minute
    if at_minute is not None and at_minute < minute:  # replay branch from an earlier real minute
        minute = at_minute
        live = {**live, "events": [e for e in live["events"] if (e["minute"] or 0) <= at_minute]}
        g = {"home": 0, "away": 0}
        for e in live["events"]:
            if e["type"] == "Goal" and e["detail"] != "Missed Penalty":
                own = e["detail"] == "Own Goal"
                sd = "home" if (e["team_id"] == live["home"]["id"]) != own else "away"
                g[sd] += 1
        live["score"] = g
    sides, info = {}, {}
    for side in ("home", "away"):
        tid = live[side]["id"]; sq = get_squad(tid)
        lu = live["lineups"]["by_team"].get(str(tid))
        if lu:
            op = adapter.on_pitch(live, tid)
            allp = {p["id"]: p for p in _merge(lu["startXI"] + lu["substitutes"], sq)}
            xi = [allp[i] for i in op["on"] if i in allp]; bench = [allp[i] for i in op["bench"] if i in allp]
            form = op["formation"] or "4-2-3-1"; src = "official line-up + live subs/cards"
        else:
            xi = squads.probable_xi(sq); ids = {p["id"] for p in xi}
            bench = [p for p in sq if p["id"] not in ids][:12]; form = "4-2-3-1"; src = "PROBABLE XI (official line-ups not yet published)"
        form = FORMATION_MAP.get(form, form)
        if form not in FORMATIONS: form = "4-2-3-1"
        # used subs: drop players who already came off from bench
        off = {str(e["player_id"]) for e in live["events"] if e["team_id"] == tid and e["type"] == "subst"} | {str(e["assist_id"]) for e in live["events"] if e["team_id"] == tid and e["type"] == "subst"}
        bench = [p for p in bench if p["id"] not in off]
        t = squads.build_team(live[side]["name"], xi, bench, form)
        # crude fatigue estimate from minute
        for p in t.starters: p.energy = max(0.4, 1 - minute / 90 * (0.45 - (p.sta - 60) / 200))
        sides[side] = t; info[side] = {"source": src, "formation_live": lu["formation"] if lu else None, "players_on_pitch": len(xi)}
    st = MatchState(sides["home"], sides["away"], min(int(minute), 89), live["score"]["home"] or 0, live["score"]["away"] or 0)
    return st, live, info


class LiveBranchIn(BaseModel):
    side: str = Field("home", pattern="^(home|away)$")
    minute: Optional[int] = Field(None, ge=0, le=89)  # optional: branch from an earlier real minute
    fixture_id: Optional[int] = None


class LiveSimIn(BaseModel):
    branch_id: str
    tactics: dict = {}
    subs: List[dict] = []
    opponent_personality: str = "flexible"
    difficulty: str = "normal"
    runs: int = Field(3000, ge=200, le=20000)
    seed: Optional[int] = 42


def _tj(team):
    return {"name": team.name, "tactics": team.tactics.to_dict(),
            "starters": [{"id": p.id, "name": p.name, "group": p.group, "att": p.att, "defn": p.defn, "pas": p.pas, "pac": p.pac, "sta": p.sta, "aer": p.aer, "energy": round(p.energy, 2)} for p in team.starters],
            "bench": [{"id": p.id, "name": p.name, "group": p.group} for p in team.bench]}


@router.post("/api/live/branch")
def live_branch(b: LiveBranchIn):
    fid = b.fixture_id or FIXTURE_ID
    lv = live_match()["live"] if fid == FIXTURE_ID else fetch_fixture(fid, max_age=600)
    ph = matchstate.phase(lv["status"]["short"], lv["kickoff_utc"])
    if ph["phase"] in ("upcoming", "postponed"): raise HTTPException(409, "match has not started: set up in the pre-match screen")
    minute = b.minute
    if ph["ended"] and minute is None: minute = 60  # finished games: what-if from a real minute, never "live"
    st, live, info = seed_state(minute, fid)
    bid = f"live-{fid}-{int(time.time()*1000)}"
    BRANCHES[bid] = {"state": st, "side": b.side}
    return {"branch_id": bid, "side": b.side, "phase": ph, "fixture_id": fid, "minute": st.minute, "score": [st.home_goals, st.away_goals],
            "status": live["status"], "home": _tj(st.home), "away": _tj(st.away), "seed_info": info,
            "label": "HYPOTHETICAL alternate timeline branched from the real match state. Player attributes are estimates; model not fitted/backtested."}


@router.post("/api/live/simulate")
def live_sim(r: LiveSimIn):
    br = BRANCHES.get(r.branch_id)
    if not br: raise HTTPException(404, "branch not found (branch again)")
    st = br["state"].clone()
    if st.minute >= 89: raise HTTPException(422, "match is over / too late to branch")
    user = st.home if br["side"] == "home" else st.away
    try:
        tac = user.tactics.copy(**{k: v for k, v in r.tactics.items() if k != "roles"}).validate()
        subs = [Substitution(max(st.minute, int(s.get("minute", st.minute))), str(s["out_id"]), str(s["in_id"])) for s in r.subs]
        cfg = SimConfig(runs=r.runs, seed=r.seed, user_side=br["side"], opponent_personality=r.opponent_personality,
                        difficulty=r.difficulty, opponent_ai=True, user_subs=subs)
        res = compare(st, tac, cfg)
    except (ValueError, StopIteration, KeyError, TypeError) as e:
        raise HTTPException(422, str(e))
    a = res["after"]
    return {"label": "Hypothetical simulation from the real current state", "before": res["before"]["probabilities"],
            "after": a["probabilities"], "xg": a["xg"], "before_xg": res["before"]["xg"], "explanation": res["explanation"],
            "score_distribution": a["score_distribution"][:6],
            "opponent": {k: v for k, v in a["opponent"].items() if k != "in_game_variants"}}


@router.get("/live/play")
def live_play():
    return FileResponse(os.path.join(os.path.dirname(__file__), "..", "..", "frontend", "live.html"))


@router.get("/live")
def live_page():
    return FileResponse(os.path.join(os.path.dirname(__file__), "..", "..", "frontend", "welcome.html"))
