"""Daily objectives, streaks and achievements. Pure engagement: no betting mechanics, no currency, no paid loot."""
from __future__ import annotations
import datetime as dt, hashlib, time
from typing import Dict, List, Optional
from fastapi import APIRouter, Depends
from . import db, auth

router = APIRouter(prefix="/api/progress", tags=["progress"])
SCHEMA = """
CREATE TABLE IF NOT EXISTS user_events (user_id INTEGER NOT NULL, day TEXT NOT NULL, kind TEXT NOT NULL, count INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (user_id, day, kind));
CREATE TABLE IF NOT EXISTS user_counters (user_id INTEGER NOT NULL, kind TEXT NOT NULL, total INTEGER NOT NULL DEFAULT 0, PRIMARY KEY (user_id, kind));
CREATE TABLE IF NOT EXISTS user_achievements (user_id INTEGER NOT NULL, code TEXT NOT NULL, unlocked_at REAL NOT NULL, PRIMARY KEY (user_id, code));
CREATE TABLE IF NOT EXISTS user_streaks (user_id INTEGER PRIMARY KEY, current INTEGER NOT NULL, best INTEGER NOT NULL, last_day TEXT NOT NULL);
"""
if SCHEMA not in db.SCHEMA: db.SCHEMA += SCHEMA

OBJECTIVES = {
    "career_win": "Win a career match", "career_match": "Manage a career match live from the dugout",
    "live_change": "Make a sub or tactical change in a real-match timeline", "challenge": "Enter this week's community challenge",
    "duel": "Play a manager duel", "share": "Create a what-if share card", "signing": "Complete a transfer",
    "tactics": "Save a tactical plan", "whatif": "Finish a real-match alternate timeline",
}
ACHIEVEMENTS = [  # code, title, description, (counter, threshold)
    ("first_win", "First Blood", "Win your first career match", ("career_win", 1)),
    ("ten_wins", "Serial Winner", "Win 10 career matches", ("career_win", 10)),
    ("first_signing", "Deal Maker", "Complete your first transfer", ("signing", 1)),
    ("five_signings", "Super Agent", "Complete 5 transfers", ("signing", 5)),
    ("academy", "Grown Our Own", "Promote an academy prospect", ("academy", 1)),
    ("champion", "Champions", "Win the Kickvana League", ("title", 1)),
    ("comeback", "Rewrite History", "Turn a real defeat into a win in your alternate timeline", ("comeback", 1)),
    ("challenger", "Challenger", "Enter 3 weekly challenges", ("challenge", 3)),
    ("duelist", "Duelist", "Win 3 manager duels", ("duel_win", 3)),
    ("tactician", "Tactician", "Save 10 tactical plans", ("tactics", 10)),
    ("storyteller", "Storyteller", "Create 3 share cards", ("share", 3)),
    ("streak3", "On a Roll", "Hit a 3-day objective streak", ("streak", 3)),
    ("streak7", "Week of Graft", "Hit a 7-day objective streak", ("streak", 7)),
]


def today(): return dt.date.today().isoformat()


def daily_objectives(day: str) -> List[str]:
    keys = sorted(OBJECTIVES); h = int(hashlib.sha256(day.encode()).hexdigest(), 16); out = []
    while len(out) < 3:
        k = keys[h % len(keys)]; h //= 7
        if k not in out: out.append(k)
    return out


def _counter(uid, kind) -> int:
    r = db.q("SELECT total FROM user_counters WHERE user_id=? AND kind=?", (uid, kind), one=True); return r["total"] if r else 0


def _unlock(uid, newly):
    for code, title, desc, (kind, n) in ACHIEVEMENTS:
        if _counter(uid, kind) >= n and not db.q("SELECT 1 FROM user_achievements WHERE user_id=? AND code=?", (uid, code), one=True):
            db.q("INSERT INTO user_achievements(user_id,code,unlocked_at) VALUES (?,?,?)", (uid, code, time.time())); newly.append({"code": code, "title": title, "desc": desc})


def record(uid: Optional[int], kind: str, n: int = 1, day: Optional[str] = None) -> Dict:
    """Record an action. Returns objectives completed and achievements unlocked by it."""
    if not uid: return {}
    day = day or today(); newly, done = [], []
    with db.conn() as c:
        before = c.execute("SELECT count FROM user_events WHERE user_id=? AND day=? AND kind=?", (uid, day, kind)).fetchone()
        c.execute("INSERT INTO user_events(user_id,day,kind,count) VALUES (?,?,?,?) ON CONFLICT(user_id,day,kind) DO UPDATE SET count=count+?", (uid, day, kind, n, n))
        c.execute("INSERT INTO user_counters(user_id,kind,total) VALUES (?,?,?) ON CONFLICT(user_id,kind) DO UPDATE SET total=total+?", (uid, kind, n, n))
    if kind in daily_objectives(day) and not before:
        done.append(kind); _streak(uid, day)
    _unlock(uid, newly)
    return {"objectives_completed": done, "achievements_unlocked": newly}


def _streak(uid, day):
    r = db.q("SELECT * FROM user_streaks WHERE user_id=?", (uid,), one=True)
    y = (dt.date.fromisoformat(day) - dt.timedelta(days=1)).isoformat()
    if not r: cur, best = 1, 1
    elif r["last_day"] == day: return
    else:
        cur = r["current"] + 1 if r["last_day"] == y else 1; best = max(r["best"], cur)
    db.q("INSERT OR REPLACE INTO user_streaks(user_id,current,best,last_day) VALUES (?,?,?,?)", (uid, cur, best, day))
    with db.conn() as c:  # mirror the streak into a counter so streak achievements unlock
        c.execute("INSERT INTO user_counters(user_id,kind,total) VALUES (?,?,?) ON CONFLICT(user_id,kind) DO UPDATE SET total=max(total,?)", (uid, "streak", cur, cur))


def summary(uid: int, day: Optional[str] = None) -> Dict:
    day = day or today()
    ev = {r["kind"]: r["count"] for r in db.q("SELECT kind,count FROM user_events WHERE user_id=? AND day=?", (uid, day))}
    st = db.q("SELECT * FROM user_streaks WHERE user_id=?", (uid,), one=True) or {"current": 0, "best": 0, "last_day": None}
    y = (dt.date.fromisoformat(day) - dt.timedelta(days=1)).isoformat()
    current = st["current"] if st["last_day"] in (day, y) else 0
    got = {r["code"]: r["unlocked_at"] for r in db.q("SELECT code,unlocked_at FROM user_achievements WHERE user_id=?", (uid,))}
    return {"day": day, "objectives": [{"kind": k, "title": OBJECTIVES[k], "done": ev.get(k, 0) > 0} for k in daily_objectives(day)],
            "streak": {"current": current, "best": st["best"], "alive_today": st["last_day"] == day},
            "achievements": [{"code": c, "title": t, "desc": d, "progress": min(_counter(uid, k), n), "target": n, "unlocked": c in got,
                              "unlocked_at": got.get(c)} for c, t, d, (k, n) in ACHIEVEMENTS]}


@router.get("")
def me(user=Depends(auth.require_user)):
    return summary(user["id"])
