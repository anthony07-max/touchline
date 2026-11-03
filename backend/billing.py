"""Free vs Pro entitlements (enforced server-side) and a payment-ready Stripe stub.
No live keys are used. With STRIPE_SECRET_KEY unset, checkout returns a stub session.
Webhooks are verified with Stripe's signature scheme (t=..,v1=HMAC-SHA256(secret, f"{t}.{body}"))."""
import hashlib, hmac, json, os, time
from fastapi import HTTPException
from . import db

PLANS = {
    "free": {"max_runs": 3000, "max_saved_scenarios": 5, "difficulties": ["easy", "normal"],
             "copilot_per_day": 5, "leagues_owned": 1, "autopsy_model_checkpoints": False, "price_gbp": 0},
    "pro": {"max_runs": 50000, "max_saved_scenarios": 1000, "difficulties": ["easy", "normal", "hard"],
            "copilot_per_day": 1000, "leagues_owned": 20, "autopsy_model_checkpoints": True, "price_gbp": 6.99},
}


def plan_of(user):
    return PLANS[(user or {}).get("plan", "free")]


def check_sim(user, runs, difficulty):
    p = plan_of(user)
    if difficulty not in p["difficulties"]:
        raise HTTPException(402, f"'{difficulty}' opponent AI is a Pro feature")
    if runs > p["max_runs"]:
        raise HTTPException(402, f"{runs} Monte Carlo runs exceeds your plan limit of {p['max_runs']} (Pro: 50000)")


def checkout(user):
    key = os.getenv("STRIPE_SECRET_KEY")
    if not key:
        return {"mode": "stub", "session_id": f"cs_stub_{user['id']}_{int(time.time())}", "checkout_url": None,
                "message": "Stripe not configured (STRIPE_SECRET_KEY unset). In production this creates a Checkout "
                           "Session for STRIPE_PRICE_PRO with client_reference_id=user id."}
    raise HTTPException(501, "Live Stripe integration not enabled in Phase 1 (install `stripe` and implement Checkout).")


def verify_signature(payload: bytes, header: str, secret: str, tolerance=300) -> bool:
    try:
        parts = dict(kv.split("=", 1) for kv in header.split(","))
        t = int(parts["t"]); sig = parts["v1"]
    except Exception:
        return False
    if abs(time.time() - t) > tolerance:
        return False
    expected = hmac.new(secret.encode(), f"{t}.".encode() + payload, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, sig)


def handle_webhook(payload: bytes, sig_header: str):
    secret = os.getenv("STRIPE_WEBHOOK_SECRET")
    if not secret:
        raise HTTPException(503, "STRIPE_WEBHOOK_SECRET not configured")
    if not verify_signature(payload, sig_header or "", secret):
        raise HTTPException(400, "invalid signature")
    ev = json.loads(payload)
    if db.q("SELECT id FROM billing_events WHERE id=?", (ev["id"],), one=True):
        return {"status": "duplicate"}
    db.q("INSERT INTO billing_events(id,type,payload_json,created_at) VALUES (?,?,?,?)",
         (ev["id"], ev["type"], payload.decode(), time.time()))
    obj = ev.get("data", {}).get("object", {})
    if ev["type"] == "checkout.session.completed":
        db.q("UPDATE users SET plan='pro', stripe_customer=? WHERE id=?", (obj.get("customer"), int(obj["client_reference_id"])))
    elif ev["type"] == "customer.subscription.deleted":
        db.q("UPDATE users SET plan='free' WHERE stripe_customer=?", (obj.get("customer"),))
    return {"status": "ok"}
