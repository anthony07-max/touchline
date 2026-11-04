"""Standards Web Push (VAPID + RFC 8291 aes128gcm) for line-ups confirmed, kick-off and full-time alerts.

Keys: VAPID_PUBLIC_KEY / VAPID_PRIVATE_KEY (base64url, raw P-256) and VAPID_SUBJECT (mailto:) env vars if set;
otherwise a key pair is generated once and stored in the database (stable until the DB is reset).
"""
from __future__ import annotations
import base64, hashlib, hmac, json, os, struct, time, urllib.parse
from typing import Dict, List, Optional
import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from . import db, auth

router = APIRouter(prefix="/api/webpush", tags=["webpush"])
SCHEMA = """
CREATE TABLE IF NOT EXISTS kv (k TEXT PRIMARY KEY, v TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS web_push_subs (
  endpoint TEXT PRIMARY KEY, p256dh TEXT NOT NULL, auth TEXT NOT NULL, user_id INTEGER, follow_json TEXT NOT NULL DEFAULT '[]',
  all_elite INTEGER NOT NULL DEFAULT 1, created_at REAL NOT NULL, last_status TEXT);
CREATE TABLE IF NOT EXISTS web_push_log (fixture_id INTEGER NOT NULL, kind TEXT NOT NULL, sent INTEGER NOT NULL, created_at REAL NOT NULL,
  PRIMARY KEY (fixture_id, kind));
"""
if SCHEMA not in db.SCHEMA: db.SCHEMA += SCHEMA
KINDS = {"lineups": "Line-ups confirmed", "kickoff": "Kick-off", "fulltime": "Full time"}


def b64u(b: bytes) -> str: return base64.urlsafe_b64encode(b).rstrip(b"=").decode()
def ub64(s: str) -> bytes: return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _ec():
    from cryptography.hazmat.primitives.asymmetric import ec
    return ec


def keys() -> Dict[str, str]:
    if os.getenv("VAPID_PUBLIC_KEY") and os.getenv("VAPID_PRIVATE_KEY"):
        return {"public": os.environ["VAPID_PUBLIC_KEY"], "private": os.environ["VAPID_PRIVATE_KEY"], "source": "env"}
    r = db.q("SELECT v FROM kv WHERE k='vapid'", one=True)
    if r: return {**json.loads(r["v"]), "source": "db"}
    ec = _ec(); from cryptography.hazmat.primitives import serialization
    priv = ec.generate_private_key(ec.SECP256R1())
    pub = priv.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    k = {"public": b64u(pub), "private": b64u(priv.private_numbers().private_value.to_bytes(32, "big"))}
    db.q("INSERT OR IGNORE INTO kv(k,v) VALUES ('vapid',?)", (json.dumps(k),))
    return {**json.loads(db.q("SELECT v FROM kv WHERE k='vapid'", one=True)["v"]), "source": "db"}


def _priv(raw_b64):
    ec = _ec(); return ec.derive_private_key(int.from_bytes(ub64(raw_b64), "big"), ec.SECP256R1())


def vapid_header(endpoint: str) -> str:
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
    k = keys(); u = urllib.parse.urlparse(endpoint)
    head = b64u(json.dumps({"typ": "JWT", "alg": "ES256"}).encode())
    claims = b64u(json.dumps({"aud": f"{u.scheme}://{u.netloc}", "exp": int(time.time()) + 12 * 3600,
                              "sub": os.getenv("VAPID_SUBJECT", "mailto:hello@kickvana.app")}).encode())
    sig = _priv(k["private"]).sign(f"{head}.{claims}".encode(), _ec().ECDSA(hashes.SHA256()))
    r, s = decode_dss_signature(sig)
    return f"vapid t={head}.{claims}.{b64u(r.to_bytes(32, 'big') + s.to_bytes(32, 'big'))}, k={k['public']}"


def _hkdf(salt, ikm, info, n):
    prk = hmac.new(salt, ikm, hashlib.sha256).digest()
    return hmac.new(prk, info + b"\x01", hashlib.sha256).digest()[:n]


def encrypt(payload: bytes, p256dh: str, auth_secret: str, salt: Optional[bytes] = None, as_priv=None) -> bytes:
    """RFC 8291 / RFC 8188 aes128gcm single-record encryption."""
    ec = _ec(); from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    ua_pub_b = ub64(p256dh); ua_pub = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), ua_pub_b)
    as_priv = as_priv or ec.generate_private_key(ec.SECP256R1())
    as_pub_b = as_priv.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    shared = as_priv.exchange(ec.ECDH(), ua_pub)
    ikm = _hkdf(ub64(auth_secret), shared, b"WebPush: info\x00" + ua_pub_b + as_pub_b, 32)
    salt = salt or os.urandom(16)
    cek = _hkdf(salt, ikm, b"Content-Encoding: aes128gcm\x00", 16); nonce = _hkdf(salt, ikm, b"Content-Encoding: nonce\x00", 12)
    ct = AESGCM(cek).encrypt(nonce, payload + b"\x02", None)
    return salt + struct.pack(">I", 4096) + bytes([len(as_pub_b)]) + as_pub_b + ct


def send(sub: Dict, data: Dict) -> str:
    if os.getenv("WEBPUSH_DRY_RUN") == "1": return "dry-run"
    try:
        body = encrypt(json.dumps(data).encode(), sub["p256dh"], sub["auth"])
        r = httpx.post(sub["endpoint"], content=body, timeout=10, headers={
            "Authorization": vapid_header(sub["endpoint"]), "Content-Encoding": "aes128gcm", "TTL": "3600", "Urgency": "high",
            "Content-Type": "application/octet-stream"})
        if r.status_code in (404, 410): db.q("DELETE FROM web_push_subs WHERE endpoint=?", (sub["endpoint"],)); return "gone"
        st = f"http {r.status_code}"
    except Exception as e:
        st = f"error {str(e)[:60]}"
    db.q("UPDATE web_push_subs SET last_status=? WHERE endpoint=?", (st, sub["endpoint"])); return st


def notify_fixture(fid: int, kind: str, title: str, body: str, elite: bool = True, url: Optional[str] = None) -> int:
    """Send once per (fixture, kind) to subscribers following that fixture (or all elite fixtures)."""
    if db.q("SELECT 1 FROM web_push_log WHERE fixture_id=? AND kind=?", (fid, kind), one=True): return 0
    subs = [s for s in db.q("SELECT * FROM web_push_subs") if fid in json.loads(s["follow_json"]) or (elite and s["all_elite"])]
    for s in subs: send(s, {"title": title, "body": body, "url": url or f"/match/{fid}", "tag": f"{kind}-{fid}"})
    if elite:  # native app users (Expo); dry-run unless EXPO_PUSH_ENABLED=1
        try:
            from . import mobile
            mobile.notify_app_users("opponent_formation" if kind == "lineups" else "kickoff", title, body, url or f"/match/{fid}")
        except Exception: pass
    db.q("INSERT OR IGNORE INTO web_push_log(fixture_id,kind,sent,created_at) VALUES (?,?,?,?)", (fid, kind, len(subs), time.time()))
    return len(subs)


def tick(items: List[Dict]) -> List:
    """Called by the live poller with the already-cached discovery list: detect kick-off / full-time transitions."""
    out = []
    for x in items:
        if x.get("classic"): continue
        ph = x["state"]["phase"]; fid = x["fixture_id"]; name = f"{x['home']['name']} v {x['away']['name']}"
        if ph in ("live", "halftime"):
            out.append((fid, "kickoff", notify_fixture(fid, "kickoff", f"Kick-off: {name}", "Take charge live or start a what-if.", x.get("elite", False))))
        if x["state"].get("ended") and ph == "fulltime" and None not in x["score"]:
            s = x["score"]
            out.append((fid, "fulltime", notify_fixture(fid, "fulltime", f"Full time: {x['home']['name']} {s[0]}–{s[1]} {x['away']['name']}",
                                                      "Could you have changed it? Rewrite the result.", x.get("elite", False))))
    return out


class SubIn(BaseModel):
    subscription: Dict; fixtures: List[int] = []; all_elite: bool = True


class FollowIn(BaseModel):
    endpoint: str; fixture_id: int; on: bool = True


@router.get("/key")
def public_key():
    try: k = keys()
    except ImportError: raise HTTPException(503, "web push unavailable on this server")
    return {"public_key": k["public"], "source": k["source"], "kinds": KINDS}


@router.post("/subscribe")
def subscribe(b: SubIn, user=Depends(auth.optional_user)):
    s = b.subscription; ep = s.get("endpoint", ""); k = s.get("keys") or {}
    if not ep.startswith("https://") or not k.get("p256dh") or not k.get("auth"): raise HTTPException(400, "invalid push subscription")
    old = db.q("SELECT follow_json FROM web_push_subs WHERE endpoint=?", (ep,), one=True)
    follow = sorted(set((json.loads(old["follow_json"]) if old else []) + b.fixtures))
    db.q("INSERT OR REPLACE INTO web_push_subs(endpoint,p256dh,auth,user_id,follow_json,all_elite,created_at) VALUES (?,?,?,?,?,?,?)",
         (ep, k["p256dh"], k["auth"], user["id"] if user else None, json.dumps(follow), 1 if b.all_elite else 0, time.time()))
    return {"ok": True, "following": follow, "all_elite": b.all_elite}


@router.post("/follow")
def follow(b: FollowIn):
    r = db.q("SELECT follow_json FROM web_push_subs WHERE endpoint=?", (b.endpoint,), one=True)
    if not r: raise HTTPException(404, "subscribe first")
    f = set(json.loads(r["follow_json"])); (f.add if b.on else f.discard)(b.fixture_id)
    db.q("UPDATE web_push_subs SET follow_json=? WHERE endpoint=?", (json.dumps(sorted(f)), b.endpoint))
    return {"following": sorted(f)}


@router.post("/unsubscribe")
def unsubscribe(b: Dict):
    db.q("DELETE FROM web_push_subs WHERE endpoint=?", (b.get("endpoint", ""),)); return {"ok": True}


@router.post("/test")
def test_push(b: Dict):
    s = db.q("SELECT * FROM web_push_subs WHERE endpoint=?", (b.get("endpoint", ""),), one=True)
    if not s: raise HTTPException(404, "subscribe first")
    return {"status": send(s, {"title": "Kickvana alerts are on", "body": "You'll hear about line-ups, kick-off and full time.", "url": "/"})}
