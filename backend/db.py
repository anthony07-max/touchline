"""SQLite persistence for the MVP. Schema is plain SQL and portable to Postgres
(INTEGER PRIMARY KEY AUTOINCREMENT -> BIGSERIAL, TEXT JSON -> JSONB, REAL epoch -> TIMESTAMPTZ)."""
import json, os, sqlite3, time

def db_path():
    return os.getenv("TOUCHLINE_DB", os.path.join(os.path.dirname(__file__), "..", "touchline.db"))

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
  id INTEGER PRIMARY KEY AUTOINCREMENT, email TEXT UNIQUE NOT NULL, pw_hash TEXT NOT NULL,
  plan TEXT NOT NULL DEFAULT 'free', club_json TEXT, stripe_customer TEXT, created_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS sessions (
  token_hash TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id), expires_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS timelines (
  id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER REFERENCES users(id),
  match_id TEXT NOT NULL, branch_minute INTEGER NOT NULL, created_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS scenarios (
  id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER REFERENCES users(id),
  timeline_id INTEGER REFERENCES timelines(id), name TEXT NOT NULL,
  request_json TEXT NOT NULL, result_json TEXT NOT NULL, created_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS usage (
  user_key TEXT NOT NULL, day TEXT NOT NULL, feature TEXT NOT NULL, count INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (user_key, day, feature));
CREATE TABLE IF NOT EXISTS leagues (
  id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, code TEXT UNIQUE NOT NULL,
  owner_id INTEGER NOT NULL REFERENCES users(id), created_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS league_members (
  league_id INTEGER NOT NULL REFERENCES leagues(id), user_id INTEGER NOT NULL REFERENCES users(id),
  PRIMARY KEY (league_id, user_id));
CREATE TABLE IF NOT EXISTS challenge_entries (
  user_id INTEGER NOT NULL REFERENCES users(id), day TEXT NOT NULL, tactics_json TEXT NOT NULL,
  score REAL NOT NULL, created_at REAL NOT NULL, PRIMARY KEY (user_id, day));
CREATE TABLE IF NOT EXISTS billing_events (
  id TEXT PRIMARY KEY, type TEXT NOT NULL, payload_json TEXT NOT NULL, created_at REAL NOT NULL);
"""


def conn():
    c = sqlite3.connect(db_path()); c.row_factory = sqlite3.Row
    c.executescript(SCHEMA); return c


def q(sql, args=(), one=False):
    with conn() as c:
        cur = c.execute(sql, args)
        if sql.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE")):
            return cur.lastrowid
        rows = [dict(r) for r in cur.fetchall()]
        return (rows[0] if rows else None) if one else rows


def create_timeline(match_id, minute, user_id=None):
    return q("INSERT INTO timelines(user_id, match_id, branch_minute, created_at) VALUES (?,?,?,?)",
             (user_id, match_id, minute, time.time()))


def get_timeline(tid):
    return q("SELECT * FROM timelines WHERE id=?", (tid,), one=True)


def save_scenario(user_id, tid, name, req, res):
    return q("INSERT INTO scenarios(user_id,timeline_id,name,request_json,result_json,created_at) VALUES (?,?,?,?,?,?)",
             (user_id, tid, name, json.dumps(req), json.dumps(res), time.time()))


def list_scenarios(user_id, ids=None):
    sql = "SELECT * FROM scenarios WHERE user_id IS ?"
    args = [user_id]
    if ids:
        sql += f" AND id IN ({','.join('?'*len(ids))})"; args += ids
    rows = q(sql + " ORDER BY id DESC", args)
    return [{**r, "request": json.loads(r["request_json"]), "result": json.loads(r["result_json"])} for r in rows]


def bump_usage(key, day, feature):
    with conn() as c:
        c.execute("INSERT INTO usage(user_key,day,feature,count) VALUES (?,?,?,1) "
                  "ON CONFLICT(user_key,day,feature) DO UPDATE SET count=count+1", (key, day, feature))
        return c.execute("SELECT count FROM usage WHERE user_key=? AND day=? AND feature=?", (key, day, feature)).fetchone()[0]


def get_usage(key, day, feature):
    r = q("SELECT count FROM usage WHERE user_key=? AND day=? AND feature=?", (key, day, feature), one=True)
    return r["count"] if r else 0
