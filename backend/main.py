from __future__ import annotations
import datetime as dt, json, os, re, secrets, time
from typing import Dict, List, Optional
from fastapi import Depends, FastAPI, HTTPException, Request, Header
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from touchline_engine.models import Tactics, Substitution, OPTIONS, FORMATIONS, ROLES
from touchline_engine.simulate import compare, SimConfig
from touchline_engine.opponent_ai import PERSONALITIES
from touchline_engine.demo_data import demo_state_at, DEMO_TIMELINE, DEMO_NOTICE
from touchline_engine.ratings import assign_slots
from touchline_engine.providers import PROVIDERS, ProviderUnavailable
from touchline_engine.copilot import advise
from touchline_engine import game
from . import db, auth, billing, account

app = FastAPI(title="Kickvana API")
from fastapi.middleware.gzip import GZipMiddleware
app.add_middleware(GZipMiddleware, minimum_size=800, compresslevel=6)

# CORS for the Expo app (web build / dev). Auth is bearer-token only, so credentials are not allowed.
from fastapi.middleware.cors import CORSMiddleware
app.add_middleware(CORSMiddleware, allow_origins=os.getenv("TOUCHLINE_CORS_ORIGINS", "*").split(","),
                   allow_methods=["*"], allow_headers=["Authorization", "Content-Type"], allow_credentials=False)
FRONT = os.path.join(os.path.dirname(__file__), "..", "frontend")
today = lambda: dt.date.today().isoformat()


# ---------- schemas ----------
class TacticsIn(BaseModel):
    formation: str = "4-3-3"; pressing: str = "medium"; defensive_line: str = "balanced"
    width: str = "balanced"; tempo: str = "balanced"; build_up: str = "mixed"
    attacking_focus: str = "balanced"; transition: str = "balanced"
    defensive_approach: str = "zonal"; set_piece_attack: str = "standard"; set_piece_defence: str = "zonal"
    roles: Dict[str, str] = {}


class SubIn(BaseModel):
    minute: int; out_id: str; in_id: str


class SimIn(BaseModel):
    timeline_id: Optional[int] = None
    minute: int = Field(55, ge=0, le=89)
    tactics: TacticsIn = TacticsIn()
    subs: List[SubIn] = []
    opponent_personality: str = "counter"
    difficulty: str = "normal"
    opponent_ai: bool = True
    runs: int = Field(3000, ge=200, le=50000)
    seed: Optional[int] = 42


class CopilotIn(SimIn):
    question: Optional[str] = None


class BranchIn(BaseModel):
    minute: int = Field(55, ge=0, le=89)


class SaveIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=80); request: SimIn


class Creds(BaseModel):
    email: str = Field(..., max_length=200); password: str = Field(..., min_length=8, max_length=200)
    age_ok: Optional[bool] = None  # registration form sends true after the 13+ checkbox; false is refused


class ClubIn(BaseModel):
    name: str = Field(..., max_length=60); colours: List[str] = ["#3ddc97", "#0b0f14"]
    starters: List[str]


class ChallengeIn(BaseModel):
    tactics: TacticsIn; subs: List[SubIn] = []


class LeagueIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=60)


# ---------- helpers ----------
def _team_json(team):
    return {"name": team.name, "tactics": team.tactics.to_dict(),
            "starters": [{"id": p.id, "name": p.name, "group": p.group, "side": p.side, "energy": round(p.energy, 2),
                          "att": p.att, "defn": p.defn, "pas": p.pas, "pac": p.pac, "sta": p.sta, "aer": p.aer} for p in team.starters],
            "bench": [{"id": p.id, "name": p.name, "group": p.group} for p in team.bench],
            "slots": [{"line": l, "id": p.id, "penalty": pen} for l, p, pen in assign_slots(team)]}


def _apply_club(st, user):
    """Use the user's chosen fantasy XI (from the demo squad) for the home side."""
    if not user or not user.get("club_json"):
        return st
    club = json.loads(user["club_json"])
    allp = {p.id: p for p in st.home.starters + st.home.bench}
    xi = [allp[i] for i in club["starters"] if i in allp]
    if len(xi) == 11:
        st.home.starters = xi
        st.home.bench = [p for p in allp.values() if p.id not in club["starters"]]
        st.home.name = club["name"] + " (your club, demo squad)"
    return st


def _state(req: SimIn, user):
    minute = req.minute
    if req.timeline_id:
        t = db.get_timeline(req.timeline_id)
        if not t: raise HTTPException(404, "timeline not found")
        minute = t["branch_minute"]
    return _apply_club(demo_state_at(minute), user)


def _cfg(req: SimIn, runs=None):
    return SimConfig(runs=runs or req.runs, seed=req.seed, opponent_personality=req.opponent_personality,
                     difficulty=req.difficulty, opponent_ai=req.opponent_ai,
                     user_subs=[Substitution(**s.model_dump()) for s in req.subs])


def run_sim(req: SimIn, user):
    billing.check_sim(user, req.runs, req.difficulty)
    st = _state(req, user)
    try:
        tac = Tactics(**req.tactics.model_dump()).validate()
        return compare(st, tac, _cfg(req))
    except (ValueError, StopIteration, KeyError) as e:
        raise HTTPException(422, str(e))


def _summary(res):
    a = res["after"]
    return {"probabilities": a["probabilities"], "xg": a["xg"], "score_distribution": a["score_distribution"][:5],
            "before_probabilities": res["before"]["probabilities"], "explanation": res["explanation"],
            "opponent": {k: v for k, v in a["opponent"].items() if k != "in_game_variants"}, "label": res["label"],
            "shots": a["shots"], "possession_user": a["possession_user"]}


# ---------- meta / match ----------
@app.get("/api/meta")
def meta(user=Depends(auth.optional_user)):
    return {"options": OPTIONS, "formations": list(FORMATIONS), "personalities": list(PERSONALITIES),
            "roles": ROLES, "difficulties": ["easy", "normal", "hard"],
            "providers": [P().status() for P in PROVIDERS.values()], "notice": DEMO_NOTICE,
            "plans": billing.PLANS, "user": user and {k: user[k] for k in ("id", "email", "plan")},
            "llm": "none configured - copilot uses engine-derived templated analysis"}


@app.get("/api/match/demo")
def demo_match(minute: int = 90, user=Depends(auth.optional_user)):
    st = _apply_club(demo_state_at(min(max(minute, 0), 90)), user)
    return {"match_id": "demo-1", "notice": DEMO_NOTICE, "minute": st.minute,
            "score": [st.home_goals, st.away_goals], "home": _team_json(st.home), "away": _team_json(st.away),
            "timeline": [e for e in DEMO_TIMELINE if e["minute"] <= minute], "full_timeline": DEMO_TIMELINE}


@app.post("/api/branch")
def branch(b: BranchIn, user=Depends(auth.optional_user)):
    tid = db.create_timeline("demo-1", b.minute, user and user["id"])
    st = _apply_club(demo_state_at(b.minute), user)
    return {"timeline_id": tid, "minute": b.minute, "score": [st.home_goals, st.away_goals],
            "home": _team_json(st.home), "away": _team_json(st.away),
            "label": "Alternate timeline branched from the demo match. The real match is untouched."}


@app.post("/api/simulate")
def simulate_ep(req: SimIn, user=Depends(auth.optional_user)):
    return run_sim(req, user)


# ---------- copilot ----------
@app.post("/api/copilot")
def copilot(req: CopilotIn, user=Depends(auth.optional_user), x_forwarded_for: Optional[str] = Header(None)):
    billing.check_sim(user, min(req.runs, 1500), req.difficulty)
    key = f"user:{user['id']}" if user else "anon"
    if db.get_usage(key, today(), "copilot") >= billing.plan_of(user)["copilot_per_day"]:
        raise HTTPException(402, "Daily copilot limit reached on the Free plan (Pro: unlimited)")
    db.bump_usage(key, today(), "copilot")
    st = _state(req, user)
    try:
        tac = Tactics(**req.tactics.model_dump()).validate()
        return advise(st, tac, _cfg(req), req.question)
    except (ValueError, StopIteration) as e:
        raise HTTPException(422, str(e))


# ---------- auth ----------
EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


@app.post("/api/auth/register")
def register(c: Creds):
    if not EMAIL.match(c.email): raise HTTPException(422, "invalid email")
    if c.age_ok is False: raise HTTPException(403, "Kickvana is for players aged 13 and over")
    if db.q("SELECT id FROM users WHERE email=?", (c.email.lower(),), one=True):
        raise HTTPException(409, "email already registered")
    uid = db.q("INSERT INTO users(email,pw_hash,plan,created_at) VALUES (?,?,?,?)",
               (c.email.lower(), auth.hash_password(c.password), "free", time.time()))
    account.on_register(uid, c.email.lower(), c.age_ok)
    return {"token": auth.issue_token(uid), "user": {"id": uid, "email": c.email.lower(), "plan": "free"}}


@app.post("/api/auth/login")
def login(c: Creds):
    u = db.q("SELECT * FROM users WHERE email=?", (c.email.lower(),), one=True)
    if not u or not auth.verify_password(c.password, u["pw_hash"]):
        raise HTTPException(401, "invalid email or password")
    return {"token": auth.issue_token(u["id"]), "user": {"id": u["id"], "email": u["email"], "plan": u["plan"]}}


@app.post("/api/auth/logout")
def logout(authorization: Optional[str] = Header(None)):
    if authorization: auth.revoke(authorization[7:].strip())
    return {"ok": True}


@app.get("/api/me")
def me(user=Depends(auth.require_user)):
    return {"id": user["id"], "email": user["email"], "plan": user["plan"],
            "entitlements": billing.plan_of(user), "club": json.loads(user["club_json"]) if user["club_json"] else None,
            "copilot_used_today": db.get_usage(f"user:{user['id']}", today(), "copilot")}


@app.post("/api/club")
def set_club(c: ClubIn, user=Depends(auth.require_user)):
    st = demo_state_at(0)
    allp = {p.id: p for p in st.home.starters + st.home.bench}
    if len(set(c.starters)) != 11 or any(i not in allp for i in c.starters):
        raise HTTPException(422, "pick exactly 11 distinct players from the demo squad")
    if sum(allp[i].group == "GK" for i in c.starters) != 1:
        raise HTTPException(422, "starting XI needs exactly one goalkeeper")
    db.q("UPDATE users SET club_json=? WHERE id=?", (json.dumps(c.model_dump()), user["id"]))
    return {"ok": True, "club": c.model_dump()}


# ---------- scenarios / lab ----------
@app.post("/api/scenarios")
def save(s: SaveIn, user=Depends(auth.require_user)):
    n = len(db.list_scenarios(user["id"]))
    if n >= billing.plan_of(user)["max_saved_scenarios"]:
        raise HTTPException(402, f"Free plan stores {n} scenarios max - upgrade to Pro for more")
    res = run_sim(s.request, user)
    sid = db.save_scenario(user["id"], s.request.timeline_id, s.name, s.request.model_dump(), _summary(res))
    return {"id": sid, "name": s.name, "result": _summary(res)}


@app.get("/api/scenarios")
def scenarios(user=Depends(auth.require_user)):
    return db.list_scenarios(user["id"])


@app.get("/api/scenarios/compare")
def compare_scenarios(ids: str, user=Depends(auth.require_user)):
    idl = [int(i) for i in ids.split(",") if i.strip()]
    rows = db.list_scenarios(user["id"], idl)
    out = [{"id": r["id"], "name": r["name"], "win": r["result"]["probabilities"]["user_win"],
            "draw": r["result"]["probabilities"]["draw"], "loss": r["result"]["probabilities"]["user_loss"],
            "exp_points": 3 * r["result"]["probabilities"]["user_win"] + r["result"]["probabilities"]["draw"],
            "xg_for": r["result"]["xg"]["user_mean"], "xg_against": r["result"]["xg"]["opp_mean"],
            "tactics": r["request"]["tactics"], "minute": r["request"]["minute"]} for r in rows]
    best = max(out, key=lambda x: x["exp_points"]) if out else None
    return {"scenarios": out, "best": best and best["id"],
            "note": "Differences under ~2 percentage points are within Monte Carlo noise."}


# ---------- game modes ----------
@app.get("/api/challenge/today")
def challenge_today():
    ch = game.daily_challenge(today())
    return {k: v for k, v in ch.items() if not k.startswith("_")}


@app.post("/api/challenge/submit")
def challenge_submit(c: ChallengeIn, user=Depends(auth.require_user)):
    day = today()
    if db.q("SELECT 1 FROM challenge_entries WHERE user_id=? AND day=?", (user["id"], day), one=True):
        raise HTTPException(409, "already submitted today's challenge")
    try:
        tac = Tactics(**c.tactics.model_dump()).validate()
        res = game.score_decision(day, tac, [Substitution(**s.model_dump()) for s in c.subs])
    except (ValueError, StopIteration) as e:
        raise HTTPException(422, str(e))
    db.q("INSERT INTO challenge_entries(user_id,day,tactics_json,score,created_at) VALUES (?,?,?,?,?)",
         (user["id"], day, json.dumps(c.tactics.model_dump()), res["decision_score"], time.time()))
    return res


@app.get("/api/challenge/leaderboard")
def leaderboard(day: Optional[str] = None):
    rows = db.q("SELECT u.email, c.score FROM challenge_entries c JOIN users u ON u.id=c.user_id "
                "WHERE c.day=? ORDER BY c.score DESC, c.created_at ASC LIMIT 50", (day or today(),))
    return [{"rank": i + 1, "manager": r["email"].split("@")[0], "score": r["score"]} for i, r in enumerate(rows)]


@app.post("/api/leagues")
def create_league(l: LeagueIn, user=Depends(auth.require_user)):
    owned = db.q("SELECT COUNT(*) n FROM leagues WHERE owner_id=?", (user["id"],), one=True)["n"]
    if owned >= billing.plan_of(user)["leagues_owned"]:
        raise HTTPException(402, "Free plan can own 1 league - upgrade to Pro for more")
    code = secrets.token_hex(3).upper()
    lid = db.q("INSERT INTO leagues(name,code,owner_id,created_at) VALUES (?,?,?,?)", (l.name, code, user["id"], time.time()))
    db.q("INSERT INTO league_members(league_id,user_id) VALUES (?,?)", (lid, user["id"]))
    return {"id": lid, "name": l.name, "code": code}


@app.post("/api/leagues/join/{code}")
def join_league(code: str, user=Depends(auth.require_user)):
    lg = db.q("SELECT * FROM leagues WHERE code=?", (code.upper(),), one=True)
    if not lg: raise HTTPException(404, "league not found")
    db.q("INSERT OR IGNORE INTO league_members(league_id,user_id) VALUES (?,?)", (lg["id"], user["id"]))
    return {"id": lg["id"], "name": lg["name"]}


@app.get("/api/leagues")
def my_leagues(user=Depends(auth.require_user)):
    lgs = db.q("SELECT l.* FROM leagues l JOIN league_members m ON m.league_id=l.id WHERE m.user_id=?", (user["id"],))
    out = []
    for lg in lgs:
        table = db.q("SELECT u.email, COALESCE(SUM(c.score),0) pts, COUNT(c.day) played FROM league_members m "
                     "JOIN users u ON u.id=m.user_id LEFT JOIN challenge_entries c ON c.user_id=m.user_id "
                     "WHERE m.league_id=? GROUP BY u.id ORDER BY pts DESC", (lg["id"],))
        out.append({"id": lg["id"], "name": lg["name"], "code": lg["code"],
                    "table": [{"manager": t["email"].split("@")[0], "points": round(t["pts"], 1), "played": t["played"]} for t in table]})
    return out


@app.get("/api/autopsy")
def autopsy(scenario_id: Optional[int] = None, user=Depends(auth.optional_user)):
    alt = None
    if scenario_id:
        if not user: raise HTTPException(401, "login required to attach a scenario")
        rows = db.list_scenarios(user["id"], [scenario_id])
        alt = rows[0] if rows else None
    rep = game.autopsy(alt)
    if not billing.plan_of(user)["autopsy_model_checkpoints"]:
        rep["model_checkpoints"] = []
        rep["upsell"] = "Model win-probability checkpoints are a Pro feature."
    return rep


# ---------- billing ----------
@app.post("/api/billing/checkout")
def checkout(user=Depends(auth.require_user)):
    return billing.checkout(user)


@app.post("/api/billing/webhook")
async def webhook(request: Request, stripe_signature: Optional[str] = Header(None)):
    return billing.handle_webhook(await request.body(), stripe_signature)


@app.post("/api/billing/dev-upgrade")
def dev_upgrade(plan: str = "pro", user=Depends(auth.require_user)):
    if os.getenv("TOUCHLINE_DEV_BILLING") != "1":
        raise HTTPException(403, "dev billing disabled")
    if plan not in billing.PLANS: raise HTTPException(422, "unknown plan")
    db.q("UPDATE users SET plan=? WHERE id=?", (plan, user["id"]))
    return {"plan": plan, "note": "DEV ONLY - no payment taken"}


# ---------- providers ----------
@app.get("/api/providers/{name}/fixtures")
def provider_fixtures(name: str, date: str = "today"):
    if name not in PROVIDERS: raise HTTPException(404, "unknown provider")
    try:
        return PROVIDERS[name]().fixtures(date)
    except ProviderUnavailable as e:
        raise HTTPException(503, {"state": "data unavailable", "reason": str(e)})


from .mobile import router as mobile_router
app.include_router(mobile_router)
from .career import router as career_router
app.include_router(career_router)
from .progress import router as progress_router
app.include_router(progress_router)
from .whatif import router as whatif_router
app.include_router(whatif_router)
from .share import router as share_router
app.include_router(share_router)
from .social import router as social_router
app.include_router(social_router)
from .webpush import router as webpush_router
from .account import router as account_router
app.include_router(account_router)
app.include_router(webpush_router)


@app.get("/")
def index():
    return FileResponse(os.path.join(FRONT, "welcome.html"))


@app.get("/career")
def career_page():
    return FileResponse(os.path.join(FRONT, "career.html"))


@app.get("/social")
def social_page():
    return FileResponse(os.path.join(FRONT, "social.html"))


@app.get("/live/manage")
def manage_page():
    return FileResponse(os.path.join(FRONT, "manage.html"))


@app.get("/sw.js")
def service_worker():
    return FileResponse(os.path.join(FRONT, "sw.js"), media_type="application/javascript", headers={"Service-Worker-Allowed": "/", "Cache-Control": "no-cache"})


@app.get("/manager")
def manager():
    return FileResponse(os.path.join(FRONT, "index.html"))


PAGES = {"/privacy": "privacy.html", "/terms": "terms.html", "/support": "support.html", "/account": "account.html",
         "/account/verify": "account.html", "/account/reset": "account.html", "/tutorial": "tutorial.html", "/offline": "offline.html"}
for _path, _file in PAGES.items():
    app.add_api_route(_path, (lambda f: (lambda: FileResponse(os.path.join(FRONT, f), headers={"Cache-Control": "public, max-age=300"})))(_file),
                      methods=["GET"], include_in_schema=False)


class CachedStatic(StaticFiles):
    """Long cache for fonts/images, short revalidating cache for html/js/css (no build hashing in this app)."""
    async def get_response(self, path, scope):
        r = await super().get_response(path, scope)
        if r.status_code == 200:
            long = path.rsplit(".", 1)[-1].lower() in ("ttf", "woff", "woff2", "png", "jpg", "jpeg", "webp", "svg", "ico")
            r.headers["Cache-Control"] = "public, max-age=604800, stale-while-revalidate=86400" if long else "public, max-age=300, stale-while-revalidate=3600"
        return r


app.mount("/static", CachedStatic(directory=FRONT), name="static")
