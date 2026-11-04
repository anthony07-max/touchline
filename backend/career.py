"""Career mode API: squad, tactics board, transfers, live matchday and the season loop.

A career is keyed by an unguessable token the client keeps (X-Career-Key header), so it works without an
account; if a bearer token is sent the career is also linked to that user. State lives in one JSON row
(careers) plus an audit table for transfers and a row for the in-progress live match.
"""
from __future__ import annotations
import json, random, secrets, time
from typing import Dict, List, Optional
from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field

from touchline_engine import career as C
from . import db, auth, progress

router = APIRouter(prefix="/api/career", tags=["career"])

SCHEMA = """
CREATE TABLE IF NOT EXISTS careers (
  key TEXT PRIMARY KEY, user_id INTEGER, club_id TEXT NOT NULL, season INTEGER NOT NULL, week INTEGER NOT NULL,
  status TEXT NOT NULL, state_json TEXT NOT NULL, created_at REAL NOT NULL, updated_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS career_live (
  career_key TEXT PRIMARY KEY REFERENCES careers(key), state_json TEXT NOT NULL, updated_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS league_packs (
  id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, league TEXT NOT NULL, clubs_json TEXT NOT NULL, user_id INTEGER, created_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS career_transfers (
  id INTEGER PRIMARY KEY AUTOINCREMENT, career_key TEXT NOT NULL, season INTEGER NOT NULL, week INTEGER NOT NULL,
  player_id TEXT NOT NULL, direction TEXT NOT NULL, fee REAL NOT NULL, wage INTEGER NOT NULL, created_at REAL NOT NULL);
"""
if SCHEMA not in db.SCHEMA:
    db.SCHEMA += SCHEMA  # created idempotently on every connection -> safe on a fresh disk


# ---------------- persistence
def _owner(key):
    r = db.q("SELECT user_id FROM careers WHERE key=?", (key,), one=True); return r["user_id"] if r else None


def _rec(key, kind, n=1):
    try: return progress.record(_owner(key), kind, n)
    except Exception: return {}


def _load(key: Optional[str]):
    if not key: raise HTTPException(401, "missing X-Career-Key")
    r = db.q("SELECT state_json FROM careers WHERE key=?", (key,), one=True)
    if not r: raise HTTPException(404, "career not found")
    return json.loads(r["state_json"])


def _save(key, st):
    db.q("UPDATE careers SET state_json=?, season=?, week=?, status=?, club_id=?, updated_at=? WHERE key=?",
         (json.dumps(st, separators=(",", ":")), st["season"], st["week"], st["status"], st["club_id"], time.time(), key))


def _rng(st):
    st["rng_n"] = st.get("rng_n", 0) + 1
    return random.Random(st["seed"] * 7919 + st["rng_n"])


def _live(key):
    r = db.q("SELECT state_json FROM career_live WHERE career_key=?", (key,), one=True)
    return json.loads(r["state_json"]) if r else None


def _save_live(key, lv):
    with db.conn() as c:
        if lv is None: c.execute("DELETE FROM career_live WHERE career_key=?", (key,))
        else: c.execute("INSERT INTO career_live(career_key,state_json,updated_at) VALUES (?,?,?) ON CONFLICT(career_key) "
                        "DO UPDATE SET state_json=excluded.state_json, updated_at=excluded.updated_at", (key, json.dumps(lv), time.time()))


def _active(st):
    if st["status"] != "active": raise HTTPException(409, "your career has ended - start a new one")


def _err(fn, *a):
    try: return fn(*a)
    except ValueError as e: raise HTTPException(400, str(e))


def _pub(p: Dict, st=None) -> Dict:
    out = {k: p[k] for k in ("id", "name", "pos", "age", "attrs", "ovr", "potential", "form", "fitness", "morale",
                             "wage", "value", "contract_until", "listed", "youth", "club_id")}
    out["unavailable"] = C.unavailable_reason(p); out["yellows"] = p.get("yellows", 0)
    if st and p["club_id"]:
        c = C.club_of(st, p["club_id"]); out["club"] = {"id": c["id"], "name": c["name"], "short": c["short"], "colours": c["colours"], "badge": c["badge"]}
    return out


def _club_pub(c):
    return {k: c[k] for k in ("id", "name", "short", "colours", "badge", "reputation", "style")}


def _summary(st, key=None):
    me = C.club_of(st, st["club_id"]); tb = C.table(st); pos = next(r["pos"] for r in tb if r["club_id"] == me["id"])
    f = C.user_fixture(st)
    nxt = None
    if f and st["status"] == "active":
        opp = C.club_of(st, f["away"] if f["home"] == me["id"] else f["home"])
        nxt = {"fixture_id": f["id"], "week": f["week"], "home": f["home"] == me["id"], "opponent": _club_pub(opp), "played": f["played"]}
    wages = sum(st["players"][p]["wage"] for p in me["squad"])
    return {"club": _club_pub(me), "manager": st["manager"], "season": st["season"], "week": st["week"], "weeks": st.get("weeks", C.WEEKS_PER_SEASON), "league": st.get("league", "kv1"), "league_name": st.get("league_name", "Kickvana League"),
            "status": st["status"], "position": pos, "board": st["board"], "finances": {**{k: st["finances"][k] for k in ("balance", "transfer_budget", "wage_budget")}, "wage_bill": wages,
            "recent_income": st["finances"]["income"][-6:], "recent_expenses": st["finances"]["expenses"][-6:]},
            "window_open": C.window_open(st), "windows": st.get("windows", C.WINDOWS), "next_fixture": nxt, "news": st["news"][:12],
            "academy": {"intake_week": st.get("academy_week", C.ACADEMY_WEEK), "done": st["academy"]["intake_done"], "prospects": [_pub(st["players"][p]) for p in st["academy"]["prospects"]]},
            "seasons": st.get("seasons", []), "live_in_progress": bool(key and _live(key))}


# ---------------- models
class NewIn(BaseModel):
    club_id: str = "c4"; manager: str = Field("You", max_length=40); seed: Optional[int] = None
    league: str = "kv1"; pack_id: Optional[int] = None; pack: Optional[List[Dict]] = None


class SlotIn(BaseModel):
    x: float; y: float; player_id: str; role: Optional[str] = None


class TacticsIn(BaseModel):
    formation: str; slots: List[SlotIn]; sliders: Dict[str, int] = {}


class BidIn(BaseModel):
    player_id: str; fee: float = Field(..., ge=0, le=500); wage: int = Field(..., ge=0, le=1000)


class PidIn(BaseModel):
    player_id: str


class SellIn(BaseModel):
    player_id: str; accept: bool


class YouthIn(BaseModel):
    player_id: str; keep: bool = True


class TalkIn(BaseModel):
    tone: str


class AdvanceIn(BaseModel):
    to_minute: int = Field(90, ge=1, le=90)


class SubIn(BaseModel):
    out_id: str; in_id: str


class LiveTacticsIn(BaseModel):
    sliders: Optional[Dict[str, int]] = None; formation: Optional[str] = None


# ---------------- endpoints: career
@router.get("/clubs")
def clubs(league: str = "kv1"):
    if league not in C.LEAGUES: raise HTTPException(400, "unknown league")
    L = C.LEAGUES[league]; fake = {"league": league, "clubs": L["clubs"]}
    return {"league": league, "league_name": L["name"], "leagues": {k: v["name"] for k, v in C.LEAGUES.items()},
            "clubs": [{"id": f"c{i}", "name": n, "short": s, "colours": [a, b], "badge": sh, "reputation": r,
                       "expectation": C.expectation_text(min(len(L["clubs"]) - 2, i + 1), fake)} for i, (n, s, a, b, sh, r) in enumerate(L["clubs"])]}


class PackIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=60); league: str = "l2"; clubs: List[Dict]


@router.post("/packs")
def pack_new(b: PackIn, user=Depends(auth.optional_user)):
    """Community-editable league packs: club names/short codes/colours only (no crests or logos)."""
    if b.league not in C.LEAGUES: raise HTTPException(400, "unknown league")
    clean = C._clean_pack(b.clubs, C.LEAGUES[b.league]["clubs"])
    pid = db.q("INSERT INTO league_packs(name,league,clubs_json,user_id,created_at) VALUES (?,?,?,?,?)",
               (b.name, b.league, json.dumps([{"name": c[0], "short": c[1], "colours": [c[2], c[3]]} for c in clean]), user["id"] if user else None, time.time()))
    return {"id": pid}


@router.get("/packs")
def packs(league: str = "l2"):
    return {"packs": [{**{k: r[k] for k in ("id", "name", "league", "created_at")}, "clubs": json.loads(r["clubs_json"])}
                      for r in db.q("SELECT * FROM league_packs WHERE league=? ORDER BY id DESC LIMIT 30", (league,))]}


class ClubEditIn(BaseModel):
    name: Optional[str] = Field(None, max_length=40); short: Optional[str] = Field(None, max_length=4); colours: Optional[List[str]] = None


@router.patch("/clubs/{cid}")
def edit_club(cid: str, b: ClubEditIn, x_career_key: Optional[str] = Header(None)):
    """Rename/recolour any club in your own save (e.g. to match your real club). Text only - no crests."""
    st = _load(x_career_key)
    try: club = C.club_of(st, cid)
    except StopIteration: raise HTTPException(404, "unknown club")
    i = st["clubs"].index(club)
    cur = [(c["name"], c["short"], c["colours"][0], c["colours"][1], c["badge"], c["reputation"]) for c in st["clubs"]]
    new = C._clean_pack([{"name": b.name or club["name"], "short": b.short or club["short"], "colours": b.colours or club["colours"]}], cur[i:i + 1])[0]
    club.update(name=new[0], short=new[1], colours=[new[2], new[3]]); _save(x_career_key, st)
    return _club_pub(club)


@router.post("/new")
def new(b: NewIn, user=Depends(auth.optional_user)):
    if b.league not in C.LEAGUES: raise HTTPException(400, "unknown league")
    idx = int(b.club_id[1:]) if b.club_id[:1] == "c" and b.club_id[1:].isdigit() else -1
    if not 0 <= idx < len(C.LEAGUES[b.league]["clubs"]): raise HTTPException(400, "unknown club")
    pack = b.pack
    if b.pack_id:
        r = db.q("SELECT clubs_json, league FROM league_packs WHERE id=?", (b.pack_id,), one=True)
        if not r or r["league"] != b.league: raise HTTPException(404, "pack not found")
        pack = json.loads(r["clubs_json"])
    st = C.new_career(idx, b.seed, b.manager.strip() or "You", b.league, pack)
    key = secrets.token_urlsafe(24)
    db.q("INSERT INTO careers(key,user_id,club_id,season,week,status,state_json,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
         (key, user["id"] if user else None, st["club_id"], 1, 1, "active", json.dumps(st), time.time(), time.time()))
    return {"key": key, **_summary(st)}


@router.get("")
def summary(x_career_key: Optional[str] = Header(None)):
    return _summary(_load(x_career_key), x_career_key)


@router.get("/squad")
def squad(x_career_key: Optional[str] = Header(None)):
    st = _load(x_career_key); me = C.club_of(st, st["club_id"])
    t = st["tactics"][me["id"]]; xi = {s["player_id"] for s in t["slots"]}
    return {"players": [{**_pub(st["players"][p]), "starter": p in xi, "roles": C.ROLES[st["players"][p]["pos"]]} for p in me["squad"]],
            "attrs": C.ATTRS, "positions": C.POSITIONS, "season": st["season"]}


@router.get("/player/{pid}")
def player(pid: str, x_career_key: Optional[str] = Header(None)):
    st = _load(x_career_key); p = st["players"].get(pid)
    if not p: raise HTTPException(404, "unknown player")
    out = _pub(p, st)
    if p["club_id"] != st["club_id"]:
        out["asking_price"] = C.asking_price(st, pid); out["wage_demand"] = C.wage_demand(p) + (2 if p["club_id"] else 0)
        out["negotiation"] = st["transfers"]["negotiations"].get(pid)
    out["best_positions"] = sorted(C.POSITIONS, key=lambda pos: -C.slot_rating(p, pos))[:3]
    return out


@router.post("/player/{pid}/list")
def toggle_list(pid: str, x_career_key: Optional[str] = Header(None)):
    st = _load(x_career_key); p = st["players"].get(pid)
    if not p or p["club_id"] != st["club_id"]: raise HTTPException(404, "not your player")
    p["listed"] = not p["listed"]; _save(x_career_key, st); return {"listed": p["listed"]}


# ---------------- tactics
@router.get("/tactics")
def get_tactics(x_career_key: Optional[str] = Header(None)):
    st = _load(x_career_key)
    return {"tactics": st["tactics"][st["club_id"]], "presets": {k: [{"pos": p, "x": x, "y": y} for p, x, y in v] for k, v in C.FORMATIONS.items()},
            "roles": C.ROLES, "sliders": C.SLIDERS}


@router.put("/tactics")
def put_tactics(t: TacticsIn, x_career_key: Optional[str] = Header(None)):
    st = _load(x_career_key)
    clean = _err(C.validate_tactics, st, st["club_id"], t.model_dump())
    st["tactics"][st["club_id"]] = clean; _save(x_career_key, st)
    return {"tactics": clean, "progress": _rec(x_career_key, "tactics")}


@router.post("/tactics/auto")
def auto_pick(formation: str = "4-3-3", x_career_key: Optional[str] = Header(None)):
    st = _load(x_career_key)
    if formation not in C.FORMATIONS: raise HTTPException(400, "unknown formation")
    old = st["tactics"][st["club_id"]]
    t = C.auto_tactics(st, st["club_id"], formation); t["sliders"] = old["sliders"]
    st["tactics"][st["club_id"]] = t; _save(x_career_key, st); return {"tactics": t}


@router.post("/tactics/preview")
def preview(t: TacticsIn, runs: int = 60, x_career_key: Optional[str] = Header(None)):
    """Quick projection vs the next opponent with these tactics (does not save)."""
    st = _load(x_career_key); clean = _err(C.validate_tactics, st, st["club_id"], t.model_dump())
    f = C.user_fixture(st)
    if not f: raise HTTPException(409, "no fixture")
    st["tactics"][st["club_id"]] = clean; rng = random.Random(7); w = d = 0; gf = ga = sh = 0
    side = 0 if f["home"] == st["club_id"] else 1
    runs = max(20, min(runs, 120))
    for _ in range(runs):
        lv = C.quick_match(st, f, rng)
        a, b = lv["score"][side], lv["score"][1 - side]; w += a > b; d += a == b; gf += a; ga += b; sh += lv["stats"]["shots"][side]
    return {"win": round(w / runs, 3), "draw": round(d / runs, 3), "loss": round(1 - (w + d) / runs, 3),
            "goals_for": round(gf / runs, 2), "goals_against": round(ga / runs, 2), "shots": round(sh / runs, 1), "runs": runs}


# ---------------- transfers
@router.get("/transfers/search")
def search(pos: Optional[str] = None, max_age: Optional[int] = None, min_ovr: Optional[int] = None,
           max_value: Optional[float] = None, q: Optional[str] = None, x_career_key: Optional[str] = Header(None)):
    st = _load(x_career_key)
    res = C.scout(st, pos, max_age, min_ovr, max_value, q)
    return {"players": [{**_pub(p, st), "asking_price": C.asking_price(st, p["id"]), "negotiation": st["transfers"]["negotiations"].get(p["id"])} for p in res],
            "window_open": C.window_open(st), "windows": st.get("windows", C.WINDOWS), "budget": st["finances"]["transfer_budget"], "wage_budget": st["finances"]["wage_budget"],
            "wage_bill": sum(st["players"][p]["wage"] for p in C.club_of(st, st["club_id"])["squad"])}


@router.get("/transfers")
def transfers(x_career_key: Optional[str] = Header(None)):
    st = _load(x_career_key)
    offers = [{**o, "player": _pub(st["players"][o["player_id"]]), "club": _club_pub(C.club_of(st, o["club_id"]))}
              for o in st["transfers"].get("incoming_offers", []) if o["player_id"] in st["players"]]
    return {"log": st["transfers"]["log"][:30], "negotiations": st["transfers"]["negotiations"], "offers": offers,
            "window_open": C.window_open(st), "windows": st.get("windows", C.WINDOWS), "budget": st["finances"]["transfer_budget"]}


@router.post("/transfers/bid")
def bid(b: BidIn, x_career_key: Optional[str] = Header(None)):
    st = _load(x_career_key); _active(st)
    r = _err(C.bid, st, b.player_id, b.fee, b.wage, _rng(st)); _save(x_career_key, st)
    if r.get("completed"):
        r["progress"] = _rec(x_career_key, "signing")
        db.q("INSERT INTO career_transfers(career_key,season,week,player_id,direction,fee,wage,created_at) VALUES (?,?,?,?,?,?,?,?)",
             (x_career_key, st["season"], st["week"], b.player_id, "in", b.fee, b.wage, time.time()))
    return {**r, "budget": st["finances"]["transfer_budget"]}


@router.post("/transfers/walk")
def walk(b: PidIn, x_career_key: Optional[str] = Header(None)):
    st = _load(x_career_key); r = C.walk_away(st, b.player_id); _save(x_career_key, st); return r


@router.post("/transfers/offer")
def answer_offer(b: SellIn, x_career_key: Optional[str] = Header(None)):
    st = _load(x_career_key); _active(st)
    r = _err(C.sell, st, b.player_id, b.accept, _rng(st)); _save(x_career_key, st)
    if r.get("status") == "sold":
        db.q("INSERT INTO career_transfers(career_key,season,week,player_id,direction,fee,wage,created_at) VALUES (?,?,?,?,?,?,?,?)",
             (x_career_key, st["season"], st["week"], b.player_id, "out", r["fee"], 0, time.time()))
    return r


# ---------------- season
@router.get("/table")
def league_table(x_career_key: Optional[str] = Header(None)):
    st = _load(x_career_key); return {"table": C.table(st), "club_id": st["club_id"], "target_pos": st["board"]["target_pos"]}


@router.get("/fixtures")
def fixtures(x_career_key: Optional[str] = Header(None)):
    st = _load(x_career_key); names = {c["id"]: _club_pub(c) for c in st["clubs"]}
    return {"fixtures": [{**{k: f.get(k) for k in ("id", "week", "played", "score", "xg")}, "home": names[f["home"]], "away": names[f["away"]],
                          "mine": st["club_id"] in (f["home"], f["away"])} for f in st["fixtures"]], "week": st["week"], "season": st["season"]}


@router.post("/academy")
def academy(b: YouthIn, x_career_key: Optional[str] = Header(None)):
    st = _load(x_career_key); p = _err(C.promote_youth, st, b.player_id, b.keep); _save(x_career_key, st)
    if p: _rec(x_career_key, "academy")
    return {"player": _pub(p) if p else None}


def _finish(key, st, lv=None, live=False):
    f = C.user_fixture(st); rng = _rng(st); season = st["season"]
    if lv: C.apply_result(st, f, lv, rng)
    s = C.finish_week(st, rng); _save(key, st); _save_live(key, None)
    if live: _rec(key, "career_match")
    if s.get("result") and s["result"][0] > s["result"][1]: _rec(key, "career_win")
    if st["season"] > season and st.get("seasons") and st["seasons"][-1]["pos"] == 1: _rec(key, "title")
    return {"week_summary": s, **_summary(st, key)}


@router.post("/advance")
def advance(x_career_key: Optional[str] = Header(None)):
    """Quick-sim this week's match (if not played live) and move to the next week."""
    st = _load(x_career_key); _active(st)
    if _live(x_career_key): raise HTTPException(409, "finish your live match first")
    f = C.user_fixture(st); lv = None
    if f and not f["played"]:
        lv = C.quick_match(st, f, _rng(st), user_side="home" if f["home"] == st["club_id"] else "away")
    return _finish(x_career_key, st, lv)


@router.post("/new-job")
def new_job(x_career_key: Optional[str] = Header(None)):
    """After a sacking: take over the lowest-reputation club, keep the save."""
    st = _load(x_career_key)
    if st["status"] != "sacked": raise HTTPException(409, "you are still employed")
    club = min((c for c in st["clubs"] if c["id"] != st["club_id"]), key=lambda c: c["reputation"])
    st["club_id"] = club["id"]; st["status"] = "active"; st["board"].update(confidence=55, target_pos=len(st["clubs"]) - 1, expectation=C.expectation_text(len(st["clubs"]) - 1, st))
    C.news(st, f"Fresh start: you are the new manager of {club['name']}."); _save(x_career_key, st)
    return _summary(st, x_career_key)


# ---------------- live matchday
def _live_pub(st, lv):
    P = st["players"]; side = lv["user_side"]
    def team(s):
        t = lv["tactics"][s]; on = {x["player_id"] for x in t["slots"]}
        cid = lv[s]
        bench = [p for p in C.club_of(st, cid)["squad"] if p not in on and p not in lv["subbed_off"]]
        return {"club": _club_pub(C.club_of(st, cid)), "formation": t["formation"], "sliders": t["sliders"],
                "slots": [{**sl, "name": P[sl["player_id"]]["name"], "ovr": P[sl["player_id"]]["ovr"], "fitness": round(lv["fitness"].get(sl["player_id"], 100))} for sl in t["slots"]],
                "bench": [{"id": b, "name": P[b]["name"], "pos": P[b]["pos"], "ovr": P[b]["ovr"], "fitness": round(lv["fitness"].get(b, P[b]["fitness"]))} for b in bench][:12] if s == side else [],
                "subs_used": lv["subs"][s]}
    m = max(1, lv["minute"])
    return {"minute": lv["minute"], "phase": lv["phase"], "score": lv["score"], "user_side": side,
            "home": team("home"), "away": team("away"), "events": lv["events"][-40:], "talks": lv["talks"],
            "stats": {"shots": lv["stats"]["shots"], "xg": lv["stats"]["xg"],
                      "possession": [round(100 * lv["stats"]["poss"][0] / m), round(100 * lv["stats"]["poss"][1] / m)] if lv["minute"] else [50, 50]},
            "ball": lv["ball"]}


@router.post("/match/start")
def match_start(x_career_key: Optional[str] = Header(None)):
    st = _load(x_career_key); _active(st)
    lv = _live(x_career_key)
    if lv: return _live_pub(st, lv)
    f = C.user_fixture(st)
    if not f or f["played"]: raise HTTPException(409, "no match to play this week - advance the week")
    lv = C.new_live(st, f); lv["user_side"] = "home" if f["home"] == st["club_id"] else "away"
    _save_live(x_career_key, lv); return _live_pub(st, lv)


@router.get("/match")
def match_get(x_career_key: Optional[str] = Header(None)):
    st = _load(x_career_key); lv = _live(x_career_key)
    if not lv: raise HTTPException(404, "no live match")
    return _live_pub(st, lv)


def _need(key):
    st = _load(key); lv = _live(key)
    if not lv: raise HTTPException(404, "no live match - start one first")
    return st, lv


@router.post("/match/talk")
def match_talk(b: TalkIn, x_career_key: Optional[str] = Header(None)):
    st, lv = _need(x_career_key)
    when = "pre" if lv["phase"] == "pre" else "half" if lv["phase"] == "half" else None
    if not when: raise HTTPException(409, "team talks happen before kick-off or at half-time")
    if when in lv["talks"]: raise HTTPException(409, "you have already given this team talk")
    r = _err(C.team_talk, st, lv, lv["user_side"], b.tone, when)
    _save(x_career_key, st); _save_live(x_career_key, lv); return {"talk": r, **_live_pub(st, lv)}


@router.post("/match/advance")
def match_advance(b: AdvanceIn, x_career_key: Optional[str] = Header(None)):
    st, lv = _need(x_career_key)
    if lv["phase"] == "full": return _live_pub(st, lv)
    if lv["phase"] == "pre": lv["phase"] = "first"
    elif lv["phase"] == "half": lv["phase"] = "second"
    C.step(st, lv, _rng(st), b.to_minute)
    _save(x_career_key, st); _save_live(x_career_key, lv); return _live_pub(st, lv)


@router.post("/match/sub")
def match_sub(b: SubIn, x_career_key: Optional[str] = Header(None)):
    st, lv = _need(x_career_key)
    if lv["phase"] == "full": raise HTTPException(409, "the match is over")
    _err(C.do_sub, st, lv, lv["user_side"], b.out_id, b.in_id); _save_live(x_career_key, lv); return _live_pub(st, lv)


@router.post("/match/tactics")
def match_tactics(b: LiveTacticsIn, x_career_key: Optional[str] = Header(None)):
    st, lv = _need(x_career_key)
    if lv["phase"] == "full": raise HTTPException(409, "the match is over")
    t = lv["tactics"][lv["user_side"]]
    if b.sliders:
        for k, v in b.sliders.items():
            if k in C.SLIDERS: t["sliders"][k] = int(max(0, min(100, v)))
    if b.formation:
        if b.formation not in C.FORMATIONS: raise HTTPException(400, "unknown formation")
        ids = [s["player_id"] for s in t["slots"]]
        gk = next(s["player_id"] for s in t["slots"] if s["pos"] == "GK"); outfield = [i for i in ids if i != gk]
        new = []
        for pos, x, y in C.FORMATIONS[b.formation]:
            if pos == "GK": pid = gk
            else:
                pid = max(outfield, key=lambda i: C.slot_rating(st["players"][i], pos)); outfield.remove(pid)
            new.append({"pos": pos, "x": x, "y": y, "player_id": pid, "role": C.ROLES[pos][0]})
        t["formation"] = b.formation; t["slots"] = new
    lv["events"].append({"minute": lv["minute"], "type": "tactic", "side": lv["user_side"], "text": "Tactical change: " +
                         ", ".join([f"{k} {v}" for k, v in (b.sliders or {}).items()] + ([b.formation] if b.formation else []))})
    _save_live(x_career_key, lv); return _live_pub(st, lv)


@router.post("/match/finish")
def match_finish(x_career_key: Optional[str] = Header(None)):
    st, lv = _need(x_career_key)
    if lv["phase"] != "full": raise HTTPException(409, "the match is not over yet")
    return {"final": _live_pub(st, lv), **_finish(x_career_key, st, lv, live=True)}


# ---------------- contracts
class RenewIn(BaseModel):
    player_id: str; years: int = Field(..., ge=1, le=5); wage: int = Field(..., ge=0, le=1000)


@router.get("/contract/{pid}")
def contract_quote(pid: str, x_career_key: Optional[str] = Header(None)):
    st = _load(x_career_key); p = st["players"].get(pid)
    if not p or p["club_id"] != st["club_id"]: raise HTTPException(404, "not your player")
    return {"player_id": pid, "current_wage": p["wage"], "contract_until": p["contract_until"], "season": st["season"],
            "demands": {y: C.renewal_demand(st, pid, y) for y in range(1, 6)}}


@router.post("/contract/renew")
def contract_renew(b: RenewIn, x_career_key: Optional[str] = Header(None)):
    st = _load(x_career_key); _active(st); r = _err(C.renew, st, b.player_id, b.years, b.wage); _save(x_career_key, st); return r


# ---------------- real line-ups -> career shape
@router.get("/real-shapes")
def real_shapes():
    """Confirmed real XIs the lineup watcher has cached (no feed calls) - their shapes can be copied into your plan."""
    from .live import lineups
    out = []
    for fid, hit in list(lineups.STORE.items())[-20:]:
        out.append({"fixture_id": int(fid), "teams": [{"id": l["team"].get("id"), "name": l["team"].get("name"), "formation": l.get("formation"),
                                                       "maps_to": C.shape_from_lineup(l.get("formation"))} for l in hit.get("lineups", [])]})
    return {"fixtures": out[::-1]}


@router.post("/tactics/from-fixture/{fid}")
def tactics_from_fixture(fid: int, team_id: Optional[int] = None, x_career_key: Optional[str] = Header(None)):
    """Copy the shape of a real confirmed XI (from the lineup watcher cache - never calls the data feed) into your plan."""
    from .live import lineups
    hit = lineups.get(fid)
    if not hit: raise HTTPException(404, "no confirmed line-up cached for that fixture yet")
    lus = hit["lineups"]; lu = next((l for l in lus if not team_id or l["team"]["id"] == team_id), lus[0])
    form = C.shape_from_lineup(lu.get("formation"))
    st = _load(x_career_key); old = st["tactics"][st["club_id"]]
    t = C.auto_tactics(st, st["club_id"], form); t["sliders"] = old["sliders"]; st["tactics"][st["club_id"]] = t; _save(x_career_key, st)
    return {"tactics": t, "copied_from": {"team": lu["team"].get("name"), "formation": lu.get("formation"), "mapped_to": form}}


# ---------------- account linkage
@router.post("/claim")
def claim(x_career_key: Optional[str] = Header(None), user=Depends(auth.require_user)):
    """Attach this browser's career to the logged-in account (migrates the anonymous key)."""
    st = _load(x_career_key); cur = _owner(x_career_key)
    if cur and cur != user["id"]: raise HTTPException(409, "this career belongs to another account")
    db.q("UPDATE careers SET user_id=? WHERE key=?", (user["id"], x_career_key))
    return {"linked": True, "club": st["club_id"]}


@router.get("/mine")
def mine(user=Depends(auth.require_user)):
    rows = db.q("SELECT key,club_id,season,week,status,updated_at FROM careers WHERE user_id=? ORDER BY updated_at DESC", (user["id"],))
    out = []
    for r in rows:
        st = json.loads(db.q("SELECT state_json FROM careers WHERE key=?", (r["key"],), one=True)["state_json"])
        out.append({**r, "club": C.club_of(st, r["club_id"])["name"], "league": st.get("league_name", "Kickvana League")})
    return {"careers": out}
