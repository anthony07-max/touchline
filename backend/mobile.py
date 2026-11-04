"""Additive mobile API: CORS, live proxy, dashboard, push tokens/preferences with quiet hours,
RevenueCat-ready entitlements (server-verified), GDPR export/delete, user preferences."""
from __future__ import annotations
import datetime as dt, json, os, time
from typing import Dict, List, Optional
from zoneinfo import ZoneInfo
import httpx
from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel, Field
from . import db, auth, billing, account

router = APIRouter()
LIVE_UPSTREAM = os.getenv("TOUCHLINE_LIVE_UPSTREAM", "http://127.0.0.1:8766")

MOBILE_SCHEMA = """
CREATE TABLE IF NOT EXISTS push_tokens (
  token TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id), platform TEXT NOT NULL, created_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS user_prefs (user_id INTEGER PRIMARY KEY REFERENCES users(id), prefs_json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS entitlements (
  user_id INTEGER NOT NULL REFERENCES users(id), source TEXT NOT NULL, product TEXT, active INTEGER NOT NULL,
  expires_at REAL, updated_at REAL NOT NULL, PRIMARY KEY (user_id, source));
CREATE TABLE IF NOT EXISTS push_outbox (
  id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, type TEXT NOT NULL, title TEXT NOT NULL,
  body TEXT NOT NULL, status TEXT NOT NULL, created_at REAL NOT NULL);
"""


def _init():
    with db.conn() as c:
        c.executescript(MOBILE_SCHEMA)


NOTIF_TYPES = ["kickoff", "fantasy_goal", "opponent_formation", "challenge_available", "league_position",
               "scouting_report", "simulation_complete"]
DEFAULT_PREFS = {"favourite_team": None, "onboarded": False,
                 "notifications": {"enabled": True, "types": {t: t in ("kickoff", "challenge_available", "league_position") for t in NOTIF_TYPES},
                                   "quiet_start": "22:30", "quiet_end": "08:00", "tz": "Europe/London", "max_per_day": 4}}


def get_prefs(uid: int) -> Dict:
    _init()
    r = db.q("SELECT prefs_json FROM user_prefs WHERE user_id=?", (uid,), one=True)
    p = json.loads(json.dumps(DEFAULT_PREFS))
    if r:
        stored = json.loads(r["prefs_json"])
        n = {**p["notifications"], **stored.get("notifications", {})}
        n["types"] = {**p["notifications"]["types"], **stored.get("notifications", {}).get("types", {})}
        p.update(stored); p["notifications"] = n
    return p


def _hm(s):
    h, m = s.split(":"); return int(h) * 60 + int(m)


def in_quiet_hours(n: Dict, now_utc: dt.datetime) -> bool:
    local = now_utc.astimezone(ZoneInfo(n.get("tz", "Europe/London")))
    t = local.hour * 60 + local.minute
    a, b = _hm(n["quiet_start"]), _hm(n["quiet_end"])
    return (a <= t < b) if a < b else (t >= a or t < b)


def should_send(uid: int, ntype: str, now_utc: Optional[dt.datetime] = None) -> (bool, str):
    n = get_prefs(uid)["notifications"]
    now_utc = now_utc or dt.datetime.now(dt.timezone.utc)
    if not n["enabled"]: return False, "notifications disabled"
    if not n["types"].get(ntype): return False, f"{ntype} turned off"
    if in_quiet_hours(n, now_utc): return False, "quiet hours"
    start = now_utc - dt.timedelta(hours=24)
    sent = db.q("SELECT COUNT(*) n FROM push_outbox WHERE user_id=? AND status IN ('sent','dry-run') AND created_at>?",
                (uid, start.timestamp()), one=True)["n"]
    if sent >= n["max_per_day"]: return False, "daily cap reached"
    return True, "ok"


def notify(uid: int, ntype: str, title: str, body: str, now_utc=None, url: Optional[str] = None) -> Dict:
    """Queue a push. Sends via Expo Push API only if EXPO_PUSH_ENABLED=1 (optionally EXPO_ACCESS_TOKEN)."""
    _init()
    ok, why = should_send(uid, ntype, now_utc)
    if not ok:
        db.q("INSERT INTO push_outbox(user_id,type,title,body,status,created_at) VALUES (?,?,?,?,?,?)",
             (uid, ntype, title, body, "suppressed:" + why, time.time()))
        return {"sent": False, "reason": why}
    tokens = [r["token"] for r in db.q("SELECT token FROM push_tokens WHERE user_id=?", (uid,))]
    status = "dry-run"
    if os.getenv("EXPO_PUSH_ENABLED") == "1" and tokens:
        hdr = {"Authorization": "Bearer " + os.environ["EXPO_ACCESS_TOKEN"]} if os.getenv("EXPO_ACCESS_TOKEN") else {}
        httpx.post("https://exp.host/--/api/v2/push/send", json=[{"to": t, "title": title, "body": body, "data": {"type": ntype, "url": url or "/"}} for t in tokens],
                   headers=hdr, timeout=10)
        status = "sent"
    db.q("INSERT INTO push_outbox(user_id,type,title,body,status,created_at) VALUES (?,?,?,?,?,?)",
         (uid, ntype, title, body, status, time.time()))
    return {"sent": status == "sent", "status": status, "tokens": len(tokens)}


def notify_app_users(ntype: str, title: str, body: str, url: Optional[str] = None) -> int:
    """Fan a match alert out to every user with a registered app push token (prefs, quiet hours and caps apply)."""
    _init(); n = 0
    for r in db.q("SELECT DISTINCT user_id FROM push_tokens"):
        try: n += bool(notify(r["user_id"], ntype, title, body, url=url).get("sent"))
        except Exception: pass
    return n


# ---------- entitlements (Stripe web + RevenueCat mobile, no duplicates) ----------
def set_entitlement(uid, source, active, product=None, expires_at=None):
    _init()
    db.q("INSERT INTO entitlements(user_id,source,product,active,expires_at,updated_at) VALUES (?,?,?,?,?,?) "
         "ON CONFLICT(user_id,source) DO UPDATE SET product=excluded.product, active=excluded.active, "
         "expires_at=excluded.expires_at, updated_at=excluded.updated_at",
         (uid, source, product, 1 if active else 0, expires_at, time.time()))
    recompute_plan(uid)


def recompute_plan(uid):
    rows = db.q("SELECT * FROM entitlements WHERE user_id=?", (uid,))
    now = time.time()
    active = [r for r in rows if r["active"] and (r["expires_at"] is None or r["expires_at"] > now)]
    if rows:  # only manage plan once an entitlement record exists (legacy/dev upgrades untouched)
        db.q("UPDATE users SET plan=? WHERE id=?", ("pro" if active else "free", uid))
    return active


class PushReg(BaseModel):
    token: str = Field(..., min_length=10, max_length=300); platform: str = Field(..., pattern="^(ios|android|web)$")


class PrefsIn(BaseModel):
    favourite_team: Optional[str] = None
    onboarded: Optional[bool] = None
    notifications: Optional[Dict] = None


@router.post("/api/push/register")
def push_register(p: PushReg, user=Depends(auth.require_user)):
    _init()
    if not (p.token.startswith("ExponentPushToken[") or p.token.startswith("ExpoPushToken[") or p.platform == "web"):
        raise HTTPException(422, "expected an Expo push token")
    db.q("INSERT OR REPLACE INTO push_tokens(token,user_id,platform,created_at) VALUES (?,?,?,?)",
         (p.token, user["id"], p.platform, time.time()))
    return {"ok": True}


@router.delete("/api/push/register")
def push_unregister(token: str, user=Depends(auth.require_user)):
    db.q("DELETE FROM push_tokens WHERE token=? AND user_id=?", (token, user["id"]))
    return {"ok": True}


@router.get("/api/me/prefs")
def prefs_get(user=Depends(auth.require_user)):
    return {**get_prefs(user["id"]), "notification_types": NOTIF_TYPES}


@router.put("/api/me/prefs")
def prefs_put(p: PrefsIn, user=Depends(auth.require_user)):
    cur = get_prefs(user["id"])
    if p.favourite_team is not None: cur["favourite_team"] = p.favourite_team[:60]
    if p.onboarded is not None: cur["onboarded"] = p.onboarded
    if p.notifications:
        n = p.notifications
        for k in ("quiet_start", "quiet_end"):
            if k in n:
                try: _hm(n[k]); assert 0 <= _hm(n[k]) < 1440
                except Exception: raise HTTPException(422, f"{k} must be HH:MM")
        if "tz" in n:
            try: ZoneInfo(n["tz"])
            except Exception: raise HTTPException(422, "unknown timezone")
        if "max_per_day" in n and not (0 <= int(n["max_per_day"]) <= 20): raise HTTPException(422, "max_per_day 0-20")
        types = {k: bool(v) for k, v in n.get("types", {}).items() if k in NOTIF_TYPES}
        cur["notifications"] = {**cur["notifications"], **{k: v for k, v in n.items() if k != "types"},
                                "types": {**cur["notifications"]["types"], **types}}
    db.q("INSERT OR REPLACE INTO user_prefs(user_id,prefs_json) VALUES (?,?)", (user["id"], json.dumps(cur)))
    return cur


@router.post("/api/push/test")
def push_test(user=Depends(auth.require_user)):
    return notify(user["id"], "challenge_available", "Today's tactical challenge is live", "Make your decision before midnight.")


@router.get("/api/push/outbox")
def push_outbox(user=Depends(auth.require_user)):
    _init()
    return db.q("SELECT type,title,body,status,created_at FROM push_outbox WHERE user_id=? ORDER BY id DESC LIMIT 30", (user["id"],))


# ---------- billing: RevenueCat ----------
PRO_ENTITLEMENT = os.getenv("REVENUECAT_ENTITLEMENT_ID", "pro")


@router.get("/api/billing/entitlements")
def entitlements(user=Depends(auth.require_user)):
    _init()
    u = db.q("SELECT plan FROM users WHERE id=?", (user["id"],), one=True)
    return {"plan": u["plan"], "entitlements": billing.plan_of(u),
            "sources": db.q("SELECT source,product,active,expires_at FROM entitlements WHERE user_id=?", (user["id"],)),
            "revenuecat_configured": bool(os.getenv("REVENUECAT_SECRET_KEY")),
            "app_user_id": str(user["id"])}


@router.post("/api/billing/revenuecat/webhook")
async def revenuecat_webhook(request: Request, authorization: Optional[str] = Header(None)):
    secret = os.getenv("REVENUECAT_WEBHOOK_AUTH")
    if not secret: raise HTTPException(503, "REVENUECAT_WEBHOOK_AUTH not configured")
    import hmac
    if not authorization or not hmac.compare_digest(authorization, secret):
        raise HTTPException(401, "bad webhook authorization")
    body = await request.json()
    ev = body.get("event", {})
    eid = ev.get("id")
    _init()
    if eid and db.q("SELECT id FROM billing_events WHERE id=?", ("rc_" + eid,), one=True):
        return {"status": "duplicate"}
    if eid:
        db.q("INSERT INTO billing_events(id,type,payload_json,created_at) VALUES (?,?,?,?)",
             ("rc_" + eid, ev.get("type", "?"), json.dumps(body), time.time()))
    try:
        uid = int(ev.get("app_user_id"))
    except (TypeError, ValueError):
        return {"status": "ignored", "reason": "app_user_id not a Kickvana user id"}
    if PRO_ENTITLEMENT not in (ev.get("entitlement_ids") or [PRO_ENTITLEMENT]):
        return {"status": "ignored"}
    exp = ev.get("expiration_at_ms"); exp = exp / 1000 if exp else None
    t = ev.get("type")
    if t in ("INITIAL_PURCHASE", "RENEWAL", "UNCANCELLATION", "PRODUCT_CHANGE", "NON_RENEWING_PURCHASE"):
        set_entitlement(uid, "revenuecat", True, ev.get("product_id"), exp)
    elif t == "CANCELLATION":  # auto-renew off; stays active until expiry
        set_entitlement(uid, "revenuecat", True, ev.get("product_id"), exp)
    elif t in ("EXPIRATION", "BILLING_ISSUE"):
        set_entitlement(uid, "revenuecat", False, ev.get("product_id"), exp)
    return {"status": "ok", "plan": db.q("SELECT plan FROM users WHERE id=?", (uid,), one=True)["plan"]}


@router.post("/api/billing/restore")
def restore(user=Depends(auth.require_user)):
    """Server-side verification against RevenueCat REST API (GET /v1/subscribers/{app_user_id})."""
    key = os.getenv("REVENUECAT_SECRET_KEY")
    if not key:
        return {"mode": "stub", "plan": user["plan"], "message": "RevenueCat not configured (REVENUECAT_SECRET_KEY). "
                "Entitlements shown are the server's current record; no client claim is trusted."}
    r = httpx.get(f"https://api.revenuecat.com/v1/subscribers/{user['id']}", headers={"Authorization": f"Bearer {key}"}, timeout=10)
    r.raise_for_status()
    ent = r.json().get("subscriber", {}).get("entitlements", {}).get(PRO_ENTITLEMENT)
    if ent:
        exp = ent.get("expires_date")
        e = dt.datetime.fromisoformat(exp.replace("Z", "+00:00")).timestamp() if exp else None
        set_entitlement(user["id"], "revenuecat", e is None or e > time.time(), ent.get("product_identifier"), e)
    return entitlements(user)


# ---------- GDPR ----------
@router.get("/api/me/export")
def export(user=Depends(auth.require_user)):
    uid = user["id"]; _init()
    return {"user": db.q("SELECT id,email,plan,club_json,created_at FROM users WHERE id=?", (uid,), one=True),
            "prefs": get_prefs(uid), "scenarios": db.list_scenarios(uid),
            "challenge_entries": db.q("SELECT day,tactics_json,score FROM challenge_entries WHERE user_id=?", (uid,)),
            "leagues": db.q("SELECT league_id FROM league_members WHERE user_id=?", (uid,)),
            "entitlements": db.q("SELECT source,product,active,expires_at FROM entitlements WHERE user_id=?", (uid,)),
            **account.export_extra(uid), "exported_at": time.time()}


@router.delete("/api/me")
def delete_me(user=Depends(auth.require_user)):
    uid = user["id"]; _init()
    if db.q("SELECT 1 FROM leagues WHERE owner_id=?", (uid,), one=True):
        db.q("DELETE FROM league_members WHERE league_id IN (SELECT id FROM leagues WHERE owner_id=?)", (uid,))
        db.q("DELETE FROM leagues WHERE owner_id=?", (uid,))
    for t in ("league_members", "challenge_entries", "scenarios", "timelines", "push_tokens", "user_prefs", "entitlements", "sessions", "push_outbox"):
        db.q(f"DELETE FROM {t} WHERE user_id=?", (uid,))
    account.delete_extra(uid)
    db.q("DELETE FROM users WHERE id=?", (uid,))
    return {"deleted": True}


# ---------- live proxy (main API -> live worker on 8766; never fabricates data) ----------
async def _proxy(method, path, body=None):
    try:
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.request(method, LIVE_UPSTREAM + path, json=body)
    except httpx.HTTPError as e:
        raise HTTPException(503, {"state": "live data unavailable", "reason": f"live service unreachable: {type(e).__name__}"})
    if r.status_code >= 400:
        raise HTTPException(r.status_code, r.json() if r.headers.get("content-type", "").startswith("application/json") else r.text)
    return r.json()


@router.get("/api/m/live/match")
async def m_live_match():
    return await _proxy("GET", "/api/live/match")


@router.get("/api/m/live/squads")
async def m_live_squads():
    return await _proxy("GET", "/api/live/squads")


@router.post("/api/m/live/branch")
async def m_live_branch(request: Request):
    return await _proxy("POST", "/api/live/branch", await request.json())


@router.post("/api/m/live/simulate")
async def m_live_simulate(request: Request):
    return await _proxy("POST", "/api/live/simulate", await request.json())


@router.get("/api/m/dashboard")
def dashboard(user=Depends(auth.optional_user)):
    from touchline_engine import game
    from . import main as m
    ch = game.daily_challenge(dt.date.today().isoformat())
    out = {"challenge": {k: v for k, v in ch.items() if not k.startswith("_")},
           "leaderboard_top": m.leaderboard()[:5], "demo_match": {"home": "Real Madrid (demo)", "away": "Manchester City (demo)"}}
    if user:
        out["prefs"] = get_prefs(user["id"])
        out["saved_scenarios"] = len(db.list_scenarios(user["id"]))
        out["leagues"] = m.my_leagues(user)
        out["plan"] = user["plan"]
    return out
