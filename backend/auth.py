"""Password auth: PBKDF2-HMAC-SHA256 (salted, 240k iterations), opaque bearer tokens stored only as SHA-256 hashes."""
import hashlib, hmac, os, secrets, time
from typing import Optional
from fastapi import Header, HTTPException
from . import db

ITER = int(os.getenv("TOUCHLINE_PBKDF2_ITER", "240000"))
TOKEN_TTL = 14 * 24 * 3600


def hash_password(pw: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", pw.encode(), salt, ITER)
    return f"pbkdf2_sha256${ITER}${salt.hex()}${dk.hex()}"


def verify_password(pw: str, stored: str) -> bool:
    try:
        _, it, salt, dk = stored.split("$")
    except ValueError:
        return False
    cand = hashlib.pbkdf2_hmac("sha256", pw.encode(), bytes.fromhex(salt), int(it))
    return hmac.compare_digest(cand.hex(), dk)


def issue_token(user_id: int) -> str:
    tok = secrets.token_urlsafe(32)
    db.q("INSERT INTO sessions(token_hash,user_id,expires_at) VALUES (?,?,?)",
         (hashlib.sha256(tok.encode()).hexdigest(), user_id, time.time() + TOKEN_TTL))
    return tok


def revoke(tok: str):
    db.q("DELETE FROM sessions WHERE token_hash=?", (hashlib.sha256(tok.encode()).hexdigest(),))


def user_from_token(tok: Optional[str]):
    if not tok:
        return None
    s = db.q("SELECT * FROM sessions WHERE token_hash=?", (hashlib.sha256(tok.encode()).hexdigest(),), one=True)
    if not s or s["expires_at"] < time.time():
        return None
    return db.q("SELECT id,email,plan,club_json FROM users WHERE id=?", (s["user_id"],), one=True)


def _tok(authorization):
    if authorization and authorization.lower().startswith("bearer "):
        return authorization[7:].strip()
    return None


def optional_user(authorization: Optional[str] = Header(None)):
    return user_from_token(_tok(authorization))


def require_user(authorization: Optional[str] = Header(None)):
    u = user_from_token(_tok(authorization))
    if not u:
        raise HTTPException(401, "login required")
    return u
