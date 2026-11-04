"""Phase 3 social: head-to-head manager duels (same draft pool + budget), league tables and global leaderboard."""
from __future__ import annotations
import json, random, secrets, time
from typing import Dict, List, Optional
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from touchline_engine import career as C
from . import db, auth, progress

router = APIRouter(prefix="/api/social", tags=["social"])
SCHEMA = """
CREATE TABLE IF NOT EXISTS duels (
  id INTEGER PRIMARY KEY AUTOINCREMENT, code TEXT UNIQUE NOT NULL, seed INTEGER NOT NULL, league_id INTEGER,
  a_user INTEGER NOT NULL, b_user INTEGER, a_entry TEXT, b_entry TEXT, result_json TEXT, created_at REAL NOT NULL, played_at REAL);
"""
if SCHEMA not in db.SCHEMA: db.SCHEMA += SCHEMA
BUDGET = 120.0
POOL_SHAPE = ["GK"] * 4 + ["CB"] * 7 + ["FB"] * 6 + ["DM"] * 4 + ["CM"] * 6 + ["AM"] * 4 + ["W"] * 6 + ["ST"] * 6


def pool(seed: int) -> List[Dict]:
    rng = random.Random(seed); out = []
    for i, pos in enumerate(POOL_SHAPE):
        p = C.make_player(rng, f"d{i}", pos, rng.randint(62, 86)); p["price"] = round(max(1.0, p["value"] * 0.9), 1); out.append(p)
    return out


def _name(uid):
    r = db.q("SELECT email FROM users WHERE id=?", (uid,), one=True); return r["email"].split("@")[0][:18] if r else "?"


class EntryIn(BaseModel):
    picks: List[str] = Field(..., min_length=11, max_length=11); formation: str = "4-3-3"; sliders: Dict[str, int] = {}


class DuelIn(BaseModel):
    league_id: Optional[int] = None


def _validate(entry: EntryIn, P: Dict[str, Dict]):
    if len(set(entry.picks)) != 11 or any(p not in P for p in entry.picks): raise HTTPException(400, "pick 11 different players from the pool")
    if entry.formation not in C.FORMATIONS: raise HTTPException(400, "unknown formation")
    cost = round(sum(P[p]["price"] for p in entry.picks), 1)
    if cost > BUDGET + 1e-9: raise HTTPException(400, f"over budget: £{cost}m of £{BUDGET}m")
    if sum(P[p]["pos"] == "GK" for p in entry.picks) != 1: raise HTTPException(400, "pick exactly one goalkeeper")
    return cost


def simulate_duel(seed: int, a: Dict, b: Dict, a_name="A", b_name="B") -> Dict:
    """Build a two-club world from the shared pool and play one match + a 200-run projection."""
    P = {p["id"]: p for p in pool(seed)}
    players = {}
    for side, e in (("a", a), ("b", b)):
        for pid in e["picks"]:
            q = json.loads(json.dumps(P[pid])); q["id"] = f"{side}_{pid}"; q["club_id"] = side; players[q["id"]] = q
    st = {"players": players, "clubs": [{"id": "a", "name": a_name, "squad": [f"a_{x}" for x in a["picks"]], "reputation": 70, "style": "balanced"},
                                       {"id": "b", "name": b_name, "squad": [f"b_{x}" for x in b["picks"]], "reputation": 70, "style": "balanced"}], "tactics": {}}
    for side, e in (("a", a), ("b", b)):
        t = C.auto_tactics(st, side, e["formation"]); t["sliders"] = {k: int(max(0, min(100, e.get("sliders", {}).get(k, 50)))) for k in C.SLIDERS}
        st["tactics"][side] = t
    fx = {"id": 0, "home": "a", "away": "b"}
    rng = random.Random(seed * 31 + 7)
    lv = C.quick_match(st, fx, rng)
    w = d = 0
    for _ in range(200):
        x = C.quick_match(st, fx, rng); w += x["score"][0] > x["score"][1]; d += x["score"][0] == x["score"][1]
    return {"score": lv["score"], "xg": lv["stats"]["xg"], "shots": lv["stats"]["shots"],
            "events": [{"minute": e["minute"], "side": "a" if e["side"] == "home" else "b", "text": e["text"]} for e in lv["events"] if e["type"] in ("goal", "red")],
            "projection": {"a_win": w / 200, "draw": d / 200, "b_win": 1 - (w + d) / 200}}


@router.post("/duels")
def duel_new(b: DuelIn, user=Depends(auth.require_user)):
    if b.league_id and not db.q("SELECT 1 FROM league_members WHERE league_id=? AND user_id=?", (b.league_id, user["id"]), one=True):
        raise HTTPException(403, "not in that league")
    code = secrets.token_hex(3).upper(); seed = secrets.randbelow(10 ** 6)
    did = db.q("INSERT INTO duels(code,seed,league_id,a_user,created_at) VALUES (?,?,?,?,?)", (code, seed, b.league_id, user["id"], time.time()))
    return {"id": did, "code": code, "invite": f"/social?duel={code}"}


def _duel(code):
    d = db.q("SELECT * FROM duels WHERE code=?", (code.upper(),), one=True)
    if not d: raise HTTPException(404, "duel not found")
    return d


def _duel_pub(d, uid=None):
    res = json.loads(d["result_json"]) if d["result_json"] else None
    me = "a" if uid == d["a_user"] else "b" if uid == d["b_user"] else None
    return {"id": d["id"], "code": d["code"], "a": _name(d["a_user"]), "b": _name(d["b_user"]) if d["b_user"] else None,
            "a_ready": bool(d["a_entry"]), "b_ready": bool(d["b_entry"]), "you": me, "result": res, "budget": BUDGET,
            "my_entry": json.loads(d[f"{me}_entry"]) if me and d[f"{me}_entry"] else None}


@router.get("/duels/{code}")
def duel_get(code: str, user=Depends(auth.optional_user)):
    d = _duel(code)
    return {**_duel_pub(d, user["id"] if user else None),
            "pool": [{k: p[k] for k in ("id", "name", "pos", "age", "ovr", "attrs", "price")} for p in pool(d["seed"])],
            "formations": list(C.FORMATIONS), "sliders": C.SLIDERS}


@router.post("/duels/{code}/entry")
def duel_entry(code: str, e: EntryIn, user=Depends(auth.require_user)):
    d = _duel(code)
    if d["result_json"]: raise HTTPException(409, "this duel has been played")
    if user["id"] == d["a_user"]: side = "a"
    elif d["b_user"] in (None, user["id"]):
        side = "b"; db.q("UPDATE duels SET b_user=? WHERE id=?", (user["id"], d["id"]))
    else: raise HTTPException(403, "this duel already has two managers")
    P = {p["id"]: p for p in pool(d["seed"])}; cost = _validate(e, P)
    db.q(f"UPDATE duels SET {side}_entry=? WHERE id=?", (json.dumps(e.model_dump()), d["id"]))
    d = _duel(code)
    if d["a_entry"] and d["b_entry"]:
        a_n, b_n = _name(d["a_user"]), _name(d["b_user"])
        res = simulate_duel(d["seed"], json.loads(d["a_entry"]), json.loads(d["b_entry"]), a_n, b_n)
        win = "a" if res["score"][0] > res["score"][1] else "b" if res["score"][1] > res["score"][0] else None
        res.update(a_name=a_n, b_name=b_n, winner=win, winner_name={"a": a_n, "b": b_n}.get(win))
        db.q("UPDATE duels SET result_json=?, played_at=? WHERE id=?", (json.dumps(res), time.time(), d["id"]))
        for s, uid in (("a", d["a_user"]), ("b", d["b_user"])):
            progress.record(uid, "duel")
            if win == s: progress.record(uid, "duel_win")
        d = _duel(code)
    return {**_duel_pub(d, user["id"]), "cost": cost}


@router.get("/duels")
def my_duels(user=Depends(auth.require_user)):
    rows = db.q("SELECT * FROM duels WHERE a_user=? OR b_user=? ORDER BY id DESC LIMIT 30", (user["id"], user["id"]))
    return {"duels": [_duel_pub(d, user["id"]) for d in rows]}


def _manager_row(uid):
    duels = db.q("SELECT a_user,b_user,result_json FROM duels WHERE (a_user=? OR b_user=?) AND result_json IS NOT NULL", (uid, uid))
    w = l = dr = 0
    for d in duels:
        r = json.loads(d["result_json"]); me = "a" if d["a_user"] == uid else "b"
        if r["winner"] is None: dr += 1
        elif r["winner"] == me: w += 1
        else: l += 1
    from .whatif import week_id
    wk = db.q("SELECT score FROM weekly_entries WHERE week=? AND user_id=?", (week_id(), uid), one=True)
    ach = db.q("SELECT COUNT(*) n FROM user_achievements WHERE user_id=?", (uid,), one=True)["n"]
    stk = db.q("SELECT current,best FROM user_streaks WHERE user_id=?", (uid,), one=True) or {"current": 0, "best": 0}
    pts = 3 * w + dr + (wk["score"] / 10 if wk else 0) + 2 * ach
    return {"user_id": uid, "manager": _name(uid), "duel_w": w, "duel_d": dr, "duel_l": l, "weekly": wk["score"] if wk else None,
            "achievements": ach, "streak": stk["current"], "points": round(pts, 1)}


@router.get("/leagues/{lid}")
def league_table(lid: int, user=Depends(auth.require_user)):
    lg = db.q("SELECT * FROM leagues WHERE id=?", (lid,), one=True)
    if not lg or not db.q("SELECT 1 FROM league_members WHERE league_id=? AND user_id=?", (lid, user["id"]), one=True):
        raise HTTPException(404, "league not found")
    rows = sorted((_manager_row(m["user_id"]) for m in db.q("SELECT user_id FROM league_members WHERE league_id=?", (lid,))), key=lambda r: -r["points"])
    for i, r in enumerate(rows): r["rank"] = i + 1
    return {"id": lid, "name": lg["name"], "code": lg["code"], "invite": f"/social?league={lg['code']}", "table": rows,
            "scoring": "3 per duel win, 1 per draw, weekly challenge score ÷ 10, 2 per achievement"}


@router.get("/leaderboard")
def leaderboard():
    ids = {r["user_id"] for r in db.q("SELECT user_id FROM user_counters")} | {r["user_id"] for r in db.q("SELECT user_id FROM weekly_entries")}
    rows = sorted((_manager_row(u) for u in ids), key=lambda r: -r["points"])[:50]
    for i, r in enumerate(rows): r["rank"] = i + 1
    return {"table": rows}
