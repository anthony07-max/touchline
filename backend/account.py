"""Account lifecycle: email verification, password reset, age gate record, full data export/delete.

Email goes through SMTP only when these env vars are set on the server:
  SMTP_HOST, SMTP_PORT (default 587), SMTP_USER, SMTP_PASSWORD, SMTP_FROM, optional SMTP_TLS (default "starttls"; "ssl" or "none").
Without SMTP_HOST every message is stored in the email_outbox table and nothing is sent.
"""
import hashlib, json, logging, os, secrets, smtplib, ssl, time
from email.message import EmailMessage
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from . import auth, db

log = logging.getLogger("kickvana.account")
SCHEMA = """
CREATE TABLE IF NOT EXISTS account_flags (
  user_id INTEGER PRIMARY KEY, verified_at REAL, age_ok_at REAL);
CREATE TABLE IF NOT EXISTS account_tokens (
  token_hash TEXT PRIMARY KEY, user_id INTEGER NOT NULL, kind TEXT NOT NULL, expires_at REAL NOT NULL, used_at REAL);
CREATE TABLE IF NOT EXISTS email_outbox (
  id INTEGER PRIMARY KEY AUTOINCREMENT, to_addr TEXT NOT NULL, subject TEXT NOT NULL, body TEXT NOT NULL,
  sent INTEGER NOT NULL DEFAULT 0, error TEXT, created_at REAL NOT NULL);
"""
if SCHEMA not in db.SCHEMA: db.SCHEMA += SCHEMA
router = APIRouter()
SMTP_VARS = ("SMTP_HOST", "SMTP_PORT", "SMTP_USER", "SMTP_PASSWORD", "SMTP_FROM", "SMTP_TLS")
SUPPORT = "anthony00.aduseipoku@gmail.com"


def base_url():
    return os.getenv("PUBLIC_BASE_URL", "https://touchline-live.onrender.com").rstrip("/")


def send_mail(to, subject, body):
    oid = db.q("INSERT INTO email_outbox(to_addr,subject,body,created_at) VALUES (?,?,?,?)", (to, subject, body, time.time()))
    host = os.getenv("SMTP_HOST")
    if not host:
        log.info("SMTP not configured; queued email %s to outbox only", oid); return {"sent": False, "queued": oid}
    try:
        m = EmailMessage(); m["From"] = os.getenv("SMTP_FROM") or os.getenv("SMTP_USER") or SUPPORT; m["To"] = to; m["Subject"] = subject
        m.set_content(body)
        port = int(os.getenv("SMTP_PORT", "587")); mode = os.getenv("SMTP_TLS", "starttls").lower()
        S = smtplib.SMTP_SSL(host, port, context=ssl.create_default_context(), timeout=15) if mode == "ssl" else smtplib.SMTP(host, port, timeout=15)
        with S as s:
            if mode == "starttls": s.starttls(context=ssl.create_default_context())
            if os.getenv("SMTP_USER"): s.login(os.getenv("SMTP_USER"), os.getenv("SMTP_PASSWORD", ""))
            s.send_message(m)
        db.q("UPDATE email_outbox SET sent=1 WHERE id=?", (oid,)); return {"sent": True}
    except Exception as e:
        db.q("UPDATE email_outbox SET error=? WHERE id=?", (str(e)[:300], oid)); log.warning("smtp failed: %s", e)
        return {"sent": False, "error": "mail delivery failed"}


def _h(t): return hashlib.sha256(t.encode()).hexdigest()


def make_token(uid, kind, ttl):
    t = secrets.token_urlsafe(32)
    db.q("INSERT INTO account_tokens(token_hash,user_id,kind,expires_at) VALUES (?,?,?,?)", (_h(t), uid, kind, time.time() + ttl))
    return t


def use_token(t, kind):
    r = db.q("SELECT * FROM account_tokens WHERE token_hash=? AND kind=?", (_h(t or ""), kind), one=True)
    if not r or r["used_at"] or r["expires_at"] < time.time(): raise HTTPException(400, "link is invalid or has expired")
    db.q("UPDATE account_tokens SET used_at=? WHERE token_hash=?", (time.time(), r["token_hash"]))
    return r["user_id"]


def flags(uid):
    return db.q("SELECT * FROM account_flags WHERE user_id=?", (uid,), one=True) or {"verified_at": None, "age_ok_at": None}


def on_register(uid, email, age_ok):
    db.q("INSERT OR REPLACE INTO account_flags(user_id,verified_at,age_ok_at) VALUES (?,?,?)", (uid, None, time.time() if age_ok else None))
    send_verify(uid, email)


def send_verify(uid, email):
    t = make_token(uid, "verify", 3 * 86400)
    return send_mail(email, "Confirm your Kickvana email",
                     f"Welcome to Kickvana!\n\nConfirm your email: {base_url()}/account/verify?token={t}\n\nThis link lasts 3 days. "
                     f"If you didn't sign up, ignore this email.\n\nHelp: {SUPPORT}")


@router.post("/api/auth/verify/resend")
def resend(user=Depends(auth.require_user)):
    if flags(user["id"])["verified_at"]: return {"ok": True, "already": True}
    r = send_verify(user["id"], user["email"]); return {"ok": True, "sent": r["sent"]}


class TokIn(BaseModel):
    token: str


@router.post("/api/auth/verify")
def verify(b: TokIn):
    uid = use_token(b.token, "verify")
    db.q("INSERT INTO account_flags(user_id,verified_at) VALUES (?,?) ON CONFLICT(user_id) DO UPDATE SET verified_at=excluded.verified_at", (uid, time.time()))
    return {"verified": True}


class ForgotIn(BaseModel):
    email: str = Field(..., max_length=200)


@router.post("/api/auth/forgot")
def forgot(b: ForgotIn):
    u = db.q("SELECT id,email FROM users WHERE email=?", (b.email.lower().strip(),), one=True)
    if u:
        t = make_token(u["id"], "reset", 3600)
        send_mail(u["email"], "Reset your Kickvana password",
                  f"Someone asked to reset your Kickvana password.\n\nChoose a new one: {base_url()}/account/reset?token={t}\n\n"
                  f"This link lasts 1 hour. If it wasn't you, ignore this email.\n\nHelp: {SUPPORT}")
    return {"ok": True, "message": "If that email has an account, a reset link is on its way."}


class ResetIn(BaseModel):
    token: str; password: str = Field(..., min_length=8, max_length=200)


@router.post("/api/auth/reset")
def reset(b: ResetIn):
    uid = use_token(b.token, "reset")
    db.q("UPDATE users SET pw_hash=? WHERE id=?", (auth.hash_password(b.password), uid))
    db.q("DELETE FROM sessions WHERE user_id=?", (uid,))
    return {"ok": True}


@router.get("/api/account")
def account(user=Depends(auth.require_user)):
    f = flags(user["id"])
    return {"email": user["email"], "verified": bool(f["verified_at"]), "age_confirmed": bool(f["age_ok_at"]),
            "email_delivery": "smtp" if os.getenv("SMTP_HOST") else "not configured (messages held on server)", "support": SUPPORT}


# Tables (beyond the original GDPR set in mobile.py) that hold per-user data.
EXTRA_TABLES = ("careers", "weekly_entries", "shadows", "share_cards", "web_push_subs", "user_events", "user_counters",
                "user_achievements", "user_streaks", "account_flags", "account_tokens")


def _safe(sql, args=()):
    try: return db.q(sql, args)
    except Exception: return []


def export_extra(uid):
    out = {}
    for t in EXTRA_TABLES:
        if t == "account_tokens": continue
        rows = _safe(f"SELECT * FROM {t} WHERE user_id=?", (uid,))
        if t == "careers": rows = [{k: (json.loads(v) if k == "state_json" else v) for k, v in r.items()} for r in rows]
        if t == "web_push_subs": rows = [{k: v for k, v in r.items() if k not in ("p256dh", "auth")} for r in rows]
        out[t] = rows
    out["duels"] = _safe("SELECT * FROM duels WHERE a_user=? OR b_user=?", (uid, uid))
    return out


def delete_extra(uid):
    for t in EXTRA_TABLES: _safe(f"DELETE FROM {t} WHERE user_id=?", (uid,))
    _safe("DELETE FROM duels WHERE a_user=? AND b_user IS NULL", (uid,))
    _safe("UPDATE duels SET a_user=0 WHERE a_user=?", (uid,)); _safe("UPDATE duels SET b_user=0 WHERE b_user=?", (uid,))
