"""Phase 2: manage a real match live (shadow timeline), weekly community challenges on real fixtures.

The shadow timeline is deterministic: it is re-simulated from the branch point with a per-minute seed, so it survives
restarts and every client sees the same alternate score. Real-match data comes only from the server's own cached
snapshots (centre.snapshot / router.seed_state) - this module never adds calls to the data feed.
"""
from __future__ import annotations
import datetime as dt, hashlib, json, random, secrets, time
from typing import Dict, List, Optional
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from touchline_engine.models import Tactics, Substitution, OPTIONS, FORMATIONS
from touchline_engine.simulate import minute_rates, drain, compare, SimConfig
from . import db, auth, progress

router = APIRouter(tags=["whatif"])
SCHEMA = """
CREATE TABLE IF NOT EXISTS shadows (
  id TEXT PRIMARY KEY, user_id INTEGER, fixture_id INTEGER NOT NULL, side TEXT NOT NULL, branch_minute INTEGER NOT NULL,
  seed INTEGER NOT NULL, changes_json TEXT NOT NULL DEFAULT '[]', final_json TEXT, created_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS weekly_challenges (
  week TEXT PRIMARY KEY, fixture_id INTEGER NOT NULL, side TEXT NOT NULL, minute INTEGER NOT NULL, brief TEXT NOT NULL,
  meta_json TEXT NOT NULL, created_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS weekly_entries (
  week TEXT NOT NULL, user_id INTEGER NOT NULL, score REAL NOT NULL, win REAL NOT NULL, draw REAL NOT NULL,
  payload_json TEXT NOT NULL, created_at REAL NOT NULL, PRIMARY KEY (week, user_id));
"""
if SCHEMA not in db.SCHEMA: db.SCHEMA += SCHEMA


# ---------------- data hooks (monkeypatched in tests)
def _seed(minute, fid):
    from .live.router import seed_state
    return seed_state(minute, fid)


def _real(fid) -> Dict:
    """Real match as the server already has it cached: minute, ended, score, goal events."""
    from .live.centre import snapshot
    s = snapshot(fid); live = s["live"]; hid = live["home"]["id"]
    goals = []
    for e in live["events"]:
        if e["type"] == "Goal" and e.get("detail") != "Missed Penalty":
            own = e.get("detail") == "Own Goal"
            goals.append({"minute": e["minute"] or 0, "side": "home" if (e["team_id"] == hid) != own else "away", "player": e.get("player")})
    return {"minute": 90 if s["state"]["ended"] else int(live["status"].get("elapsed") or 0), "ended": s["state"]["ended"],
            "phase": s["state"]["phase"], "score": [live["score"]["home"] or 0, live["score"]["away"] or 0], "goals": goals,
            "home": live["home"]["name"], "away": live["away"]["name"], "lineups_confirmed": bool(live["lineups"]["available"])}


def real_score_at(real, minute):
    h = sum(1 for g in real["goals"] if g["side"] == "home" and g["minute"] <= minute)
    a = sum(1 for g in real["goals"] if g["side"] == "away" and g["minute"] <= minute)
    if minute >= 90 and real["ended"]: return real["score"]
    return [h, a]


# ---------------- shadow engine
def run_shadow(row: Dict, upto: int):
    st, live, info = _seed(row["branch_minute"], row["fixture_id"])
    side = row["side"]; user = st.home if side == "home" else st.away
    changes = json.loads(row["changes_json"]); events = []; score = [st.home_goals, st.away_goals]
    m = row["branch_minute"]
    while m < min(upto, 90):
        for c in [c for c in changes if c["minute"] == m]:
            if c["kind"] == "sub":
                try:
                    user.substitute(c["out_id"], c["in_id"])
                    events.append({"minute": m, "type": "sub", "side": side, "text": c.get("text") or "Substitution"})
                except StopIteration: pass
            else:
                user.tactics = user.tactics.copy(**c["tactics"])
                events.append({"minute": m, "type": "tactic", "side": side, "text": "Tactics: " + ", ".join(f"{k} {v}" for k, v in c["tactics"].items())})
        r = minute_rates(st.home, st.away)
        rng = random.Random(row["seed"] * 1009 + m)
        for i, s in enumerate(("h", "a")):
            p = r[f"lam_{s}"] * r[f"xg_{s}"] + r[f"sp_{s}"] * r[f"spq_{s}"]
            if rng.random() < p:
                score[i] += 1
                team = st.home if i == 0 else st.away
                outfield = [p for p in team.starters if p.group != "GK"]
                sc = max(outfield, key=lambda p: p.att * rng.random()) if outfield else team.starters[0]
                events.append({"minute": m + 1, "type": "goal", "side": "home" if i == 0 else "away", "text": f"GOAL {sc.name}"})
        drain(st.home, r["fat_h"]); drain(st.away, r["fat_a"])
        m += 1
    return st, score, events, info


def _verdict(side, real, alt, home, away):
    i = 0 if side == "home" else 1
    me, opp = (home, away) if side == "home" else (away, home)
    a = (alt[i], alt[1 - i]); r = (real[i], real[1 - i])
    word = lambda s: "won" if s[0] > s[1] else "drawn" if s[0] == s[1] else "lost"
    return {"team": me, "opponent": opp, "alt": list(a), "real": list(r), "alt_result": word(a), "real_result": word(r),
            "headline": f"I'd have {word(a)} {a[0]}–{a[1]}" + (f" (really {word(r)} {r[0]}–{r[1]})" if a != r else " (same as reality)"),
            "comeback": word(r) == "lost" and word(a) == "won"}


def _pub(row, real=None, upto=None):
    real = real or _real(row["fixture_id"])
    upto = min(90, upto if upto is not None else real["minute"])
    upto = max(upto, row["branch_minute"])
    st, score, events, info = run_shadow(row, upto)
    user = st.home if row["side"] == "home" else st.away
    out = {"id": row["id"], "fixture_id": row["fixture_id"], "side": row["side"], "branch_minute": row["branch_minute"],
           "minute": upto, "real": {**real, "score_now": real_score_at(real, upto)}, "alt": {"score": score, "events": events},
           "you": {"name": user.name, "formation": user.tactics.formation, "tactics": user.tactics.to_dict(),
                   "starters": [{"id": p.id, "name": p.name, "group": p.group, "energy": round(p.energy, 2)} for p in user.starters],
                   "bench": [{"id": p.id, "name": p.name, "group": p.group} for p in user.bench]},
           "seed_info": info, "options": OPTIONS, "formations": list(FORMATIONS), "replay": real["ended"],
           "label": "Your alternate timeline is a simulation; the real match is unaffected."}
    if upto >= 90 and (real["ended"] or real["minute"] >= 90):
        fin = _verdict(row["side"], real["score"], score, real["home"], real["away"])
        out["final"] = fin
        if not row.get("final_json"):
            db.q("UPDATE shadows SET final_json=? WHERE id=?", (json.dumps(fin), row["id"]))
            progress.record(row.get("user_id"), "whatif")
            if fin["comeback"]: progress.record(row.get("user_id"), "comeback")
    return out


def _row(sid):
    r = db.q("SELECT * FROM shadows WHERE id=?", (sid,), one=True)
    if not r: raise HTTPException(404, "timeline not found")
    return r


class ShadowIn(BaseModel):
    fixture_id: int; side: str = Field("home", pattern="^(home|away)$"); minute: Optional[int] = Field(None, ge=0, le=89)


class ChangeIn(BaseModel):
    sub: Optional[Dict[str, str]] = None; tactics: Optional[Dict[str, str]] = None
    minute: Optional[int] = Field(None, ge=0, le=89)  # replay mode: the minute your clock is at


@router.post("/api/shadow")
def shadow_new(b: ShadowIn, user=Depends(auth.optional_user)):
    real = _real(b.fixture_id)
    if real["phase"] in ("upcoming", "prematch", "postponed"): raise HTTPException(409, "the match hasn't kicked off yet")
    minute = b.minute if b.minute is not None else (0 if real["ended"] else min(real["minute"], 89))
    if not real["ended"] and minute > real["minute"]: raise HTTPException(409, "you can't branch from the future")
    sid = secrets.token_urlsafe(9)
    db.q("INSERT INTO shadows(id,user_id,fixture_id,side,branch_minute,seed,changes_json,created_at) VALUES (?,?,?,?,?,?,?,?)",
         (sid, user["id"] if user else None, b.fixture_id, b.side, minute, secrets.randbelow(10 ** 6), "[]", time.time()))
    return _pub(_row(sid), real, minute)


@router.get("/api/shadow/{sid}")
def shadow_get(sid: str, clock: Optional[int] = None):
    """clock: replay mode (finished matches) - how far your timeline has played. Live matches follow the real minute."""
    row = _row(sid); real = _real(row["fixture_id"])
    upto = None if not real["ended"] else (clock if clock is not None else 90)
    return _pub(row, real, upto)


@router.post("/api/shadow/{sid}/change")
def shadow_change(sid: str, c: ChangeIn, user=Depends(auth.optional_user)):
    row = _row(sid); real = _real(row["fixture_id"])
    now = (c.minute if real["ended"] and c.minute is not None else real["minute"])
    now = max(now, row["branch_minute"])
    if now >= 89 or row.get("final_json"): raise HTTPException(409, "the match is over")
    changes = json.loads(row["changes_json"])
    if changes and now < max(x["minute"] for x in changes): raise HTTPException(409, "you can't change the past")
    st, _, _, _ = run_shadow(row, now); me = st.home if row["side"] == "home" else st.away
    if c.sub:
        if sum(1 for x in changes if x["kind"] == "sub") >= 5: raise HTTPException(409, "all five substitutions used")
        o = next((p for p in me.starters if p.id == c.sub.get("out_id")), None); i = next((p for p in me.bench if p.id == c.sub.get("in_id")), None)
        if not o or not i: raise HTTPException(400, "pick a player on the pitch and one on the bench")
        changes.append({"minute": now, "kind": "sub", "out_id": o.id, "in_id": i.id, "text": f"{i.name} on for {o.name}"})
    if c.tactics:
        clean = {k: v for k, v in c.tactics.items() if (k in OPTIONS and v in OPTIONS[k]) or (k == "formation" and v in FORMATIONS)}
        if not clean: raise HTTPException(400, "no valid tactical change")
        changes.append({"minute": now, "kind": "tactics", "tactics": clean})
    if not (c.sub or c.tactics): raise HTTPException(400, "nothing to change")
    db.q("UPDATE shadows SET changes_json=? WHERE id=?", (json.dumps(changes), sid))
    if user: progress.record(user["id"], "live_change")
    return _pub(_row(sid), real, now if real["ended"] else None)


# ---------------- weekly community challenge
def week_id(d: Optional[dt.date] = None) -> str:
    y, w, _ = (d or dt.date.today()).isocalendar(); return f"{y}-W{w:02d}"


ELITE_LEAGUES = {2, 3, 39, 140, 135, 78, 61}  # UCL, UEL, PL, LaLiga, Serie A, Bundesliga, Ligue 1


def _candidates() -> List[Dict]:
    """Finished elite fixtures the server already knows (discovery cache), else the bundled classics."""
    from .live import centre
    out = []
    try:
        for x in centre.discover().get("items") or []:
            if x["state"]["ended"] and x.get("elite") and x.get("league_id") in ELITE_LEAGUES and None not in x["score"] and x["score"][0] != x["score"][1]:
                out.append({"fixture_id": x["fixture_id"], "home": x["home"]["name"], "away": x["away"]["name"], "score": x["score"], "classic": bool(x.get("classic"))})
    except Exception: pass
    if not out:
        for fid in sorted(centre.CLASSIC_RAW):
            g = centre.CLASSIC_RAW[fid]["goals"]; t = centre.CLASSIC_RAW[fid]["teams"]
            if g["home"] != g["away"]:
                out.append({"fixture_id": fid, "home": t["home"]["name"], "away": t["away"]["name"], "score": [g["home"], g["away"]], "classic": True})
    return out


def current_challenge(week: Optional[str] = None) -> Dict:
    week = week or week_id()
    r = db.q("SELECT * FROM weekly_challenges WHERE week=?", (week,), one=True)
    if not r:
        cands = sorted(_candidates(), key=lambda c: (not c["classic"], c["fixture_id"]))
        real_first = [c for c in cands if not c["classic"]] or cands
        if not real_first: raise HTTPException(503, "no fixtures available for a challenge yet")
        c = real_first[int(hashlib.sha256(week.encode()).hexdigest(), 16) % len(real_first)]
        side = "home" if c["score"][0] < c["score"][1] else "away"
        loser = c[side]; winner = c["away" if side == "home" else "home"]; lo, hi = sorted(c["score"])
        brief = f"Turn around this result: {loser} lost {hi}–{lo} to {winner}. Take over at 60' and find the plan that wins it."
        db.q("INSERT OR IGNORE INTO weekly_challenges(week,fixture_id,side,minute,brief,meta_json,created_at) VALUES (?,?,?,?,?,?,?)",
             (week, c["fixture_id"], side, 60, brief, json.dumps(c), time.time()))
        r = db.q("SELECT * FROM weekly_challenges WHERE week=?", (week,), one=True)
    return {**{k: r[k] for k in ("week", "fixture_id", "side", "minute", "brief")}, "meta": json.loads(r["meta_json"])}


class EntryIn(BaseModel):
    tactics: Dict[str, str] = {}; subs: List[Dict] = []


@router.get("/api/weekly")
def weekly(user=Depends(auth.optional_user)):
    ch = current_challenge(); st, _, info = _seed(ch["minute"], ch["fixture_id"])
    me = st.home if ch["side"] == "home" else st.away
    mine = db.q("SELECT score,win,draw FROM weekly_entries WHERE week=? AND user_id=?", (ch["week"], user["id"]), one=True) if user else None
    return {**ch, "state": {"minute": st.minute, "score": [st.home_goals, st.away_goals]}, "seed_info": info,
            "you": {"name": me.name, "tactics": me.tactics.to_dict(), "starters": [{"id": p.id, "name": p.name, "group": p.group} for p in me.starters],
                    "bench": [{"id": p.id, "name": p.name, "group": p.group} for p in me.bench]},
            "options": OPTIONS, "formations": list(FORMATIONS), "my_entry": mine, "leaderboard": board(ch["week"])}


def board(week, limit=20):
    rows = db.q("SELECT e.user_id,e.score,e.win,e.draw,u.email FROM weekly_entries e JOIN users u ON u.id=e.user_id WHERE week=? ORDER BY score DESC, e.created_at LIMIT ?", (week, limit))
    return [{"rank": i + 1, "manager": r["email"].split("@")[0][:18], "score": r["score"], "win": r["win"], "user_id": r["user_id"]} for i, r in enumerate(rows)]


@router.post("/api/weekly/enter")
def weekly_enter(e: EntryIn, user=Depends(auth.require_user)):
    ch = current_challenge(); st, _, _ = _seed(ch["minute"], ch["fixture_id"])
    me = st.home if ch["side"] == "home" else st.away
    try:
        tac = me.tactics.copy(**{k: v for k, v in e.tactics.items() if k in OPTIONS or k == "formation"}).validate()
        subs = [Substitution(max(st.minute, int(s.get("minute", st.minute))), str(s["out_id"]), str(s["in_id"])) for s in e.subs[:5]]
        seed = int(hashlib.sha256(ch["week"].encode()).hexdigest(), 16) % 100000
        res = compare(st, tac, SimConfig(runs=2000, seed=seed, user_side=ch["side"], user_subs=subs))
    except (ValueError, KeyError, StopIteration, TypeError) as ex:
        raise HTTPException(422, str(ex))
    p = res["after"]["probabilities"]; score = round(100 * (3 * p["user_win"] + p["draw"]) / 3, 1)
    prev = db.q("SELECT score FROM weekly_entries WHERE week=? AND user_id=?", (ch["week"], user["id"]), one=True)
    if not prev or score > prev["score"]:
        db.q("INSERT OR REPLACE INTO weekly_entries(week,user_id,score,win,draw,payload_json,created_at) VALUES (?,?,?,?,?,?,?)",
             (ch["week"], user["id"], score, p["user_win"], p["draw"], json.dumps(e.model_dump()), time.time()))
    prog = progress.record(user["id"], "challenge")
    rank = next((r["rank"] for r in board(ch["week"], 1000) if r["user_id"] == user["id"]), None)
    return {"score": score, "best": max(score, prev["score"] if prev else 0), "win": p["user_win"], "draw": p["draw"], "rank": rank,
            "before": res["before"]["probabilities"], "explanation": res["explanation"], "progress": prog}
