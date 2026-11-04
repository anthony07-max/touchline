"""football-data.org API v4 client + translator into the API-Football-shaped fixture objects the rest of the app
already understands (adapter.parse_fixture, centre._summ, squads.squad). Key: env FOOTBALL_DATA_KEY (header X-Auth-Token),
never logged. Free tier: 10 requests/min, competitions below only. Raw responses are disk-cached."""
from __future__ import annotations
import datetime as dt, hashlib, json, os, re, threading, time, urllib.error, urllib.request
from typing import Optional

BASE = "https://api.football-data.org/v4/"
CACHE = os.path.join(os.path.dirname(__file__), "..", "..", "data", "live_cache")
os.makedirs(CACHE, exist_ok=True)
MIN_GAP = 6.5  # 10 req/min with headroom
# football-data competition code -> API-Football league id (so LEAGUES/BIG ranking keeps working)
COMP_TO_LEAGUE = {"PL": 39, "CL": 2, "ELC": 40, "PD": 140, "BL1": 78, "SA": 135, "FL1": 61, "DED": 88, "PPL": 94,
                  "EC": 4, "WC": 1, "BSA": 71}
LEAGUE_TO_COMP = {v: k for k, v in COMP_TO_LEAGUE.items()}
FREE_COMPETITIONS = set(COMP_TO_LEAGUE)
STAGE = {"Final": "FINAL", "Semi-finals": "SEMI_FINALS", "Quarter-finals": "QUARTER_FINALS", "Round of 16": "LAST_16"}
NAME_FIX = {"fc internazionale milano": "Inter", "paris saint-germain fc": "Paris Saint Germain", "fc barcelona": "Barcelona",
            "fc bayern münchen": "Bayern München", "real madrid cf": "Real Madrid", "manchester united fc": "Manchester United",
            "as roma": "AS Roma", "club atlético de madrid": "Atletico Madrid"}
quota = {"provider": "football-data.org", "limit_minute": 10, "available_minute": None, "remaining": None,
         "calls_this_process": 0, "last_error": None}
_lock = threading.Lock()
_last = [0.0]


class APIError(Exception):
    pass


def key() -> Optional[str]:
    return os.environ.get("FOOTBALL_DATA_KEY") or None


def _path(ep): return os.path.join(CACHE, "fd_" + hashlib.md5(ep.encode()).hexdigest() + ".json")


def _cached(p):
    with open(p) as f: return json.load(f)


def raw(ep: str, max_age: float = 3600, force=False, opener=None) -> dict:
    """GET a v4 path (e.g. 'matches/123'). Disk cache first; rate-limited; on any upstream error serve stale cache."""
    p = _path(ep)
    if not force and os.path.exists(p) and time.time() - os.path.getmtime(p) < max_age: return _cached(p)
    k = key()
    if not k:
        quota["last_error"] = "FOOTBALL_DATA_KEY not set"
        if os.path.exists(p): return _cached(p)
        raise APIError("FOOTBALL_DATA_KEY not set")
    with _lock:
        gap = time.time() - _last[0]
        if gap < MIN_GAP: time.sleep(MIN_GAP - gap)
        _last[0] = time.time()
        req = urllib.request.Request(BASE + ep, headers={"X-Auth-Token": k, "Accept": "application/json"})
        try:
            with (opener or urllib.request.urlopen)(req, timeout=20) as r:
                data = json.load(r); h = r.headers
                quota["calls_this_process"] += 1
                if h.get("X-Requests-Available-Minute"): quota["available_minute"] = int(h["X-Requests-Available-Minute"])
        except urllib.error.HTTPError as e:
            msg = {400: "bad request", 403: "competition not in your football-data.org plan", 404: "not found",
                   429: "rate limit hit; serving cache"}.get(e.code, f"HTTP {e.code}")
            if e.code in (401,) or (e.code == 403 and "token" in (e.read() or b"").decode("utf8", "ignore").lower()):
                msg = "football-data.org rejected the key"; quota["last_error"] = msg
            elif e.code == 429: quota["last_error"] = msg; _last[0] = time.time() + 30
            if os.path.exists(p): return _cached(p)
            raise APIError(msg)
        except (urllib.error.URLError, TimeoutError, ValueError) as e:
            if os.path.exists(p): return _cached(p)
            raise APIError(f"network: {e}")
    quota["last_error"] = None
    with open(p, "w") as f: json.dump(data, f)
    return data


# ---------------- translation (pure; unit-tested with recorded JSON) ----------------
def clean_name(n: Optional[str]) -> str:
    if not n: return ""
    if n.lower() in NAME_FIX: return NAME_FIX[n.lower()]
    n = re.sub(r"^(FC|AFC|SSC|AC|SL|SC|CF|RC|VfL|VfB|TSG|RCD)\s+", "", n)
    n = re.sub(r"\s+(FC|AFC|CF|SC|SAD|FK)$", "", n)
    return n.replace("-", " ")


def _status(m: dict):
    s = m.get("status"); dur = (m.get("score") or {}).get("duration")
    minute = m.get("minute")
    try: minute = int(str(minute).split("+")[0]) if minute is not None else None
    except ValueError: minute = None
    if s in ("SCHEDULED", "TIMED"): return "NS", "Not Started", None
    if s == "PAUSED": return "HT", "Halftime", 45
    if s in ("IN_PLAY", "LIVE"):
        if minute is None:
            try:
                ko = dt.datetime.fromisoformat(m["utcDate"].replace("Z", "+00:00"))
                el = int((dt.datetime.now(dt.timezone.utc) - ko).total_seconds() // 60)
                minute = el if el <= 45 else max(46, min(90, el - 15))
            except (KeyError, ValueError): minute = None
        if dur == "PENALTY_SHOOTOUT": return "P", "Penalty In Progress", minute
        if dur == "EXTRA_TIME" or (minute or 0) > 90: return "ET", "Extra Time", minute
        return ("2H" if (minute or 0) > 45 else "1H"), ("Second Half" if (minute or 0) > 45 else "First Half"), minute
    if s == "FINISHED":
        return {"EXTRA_TIME": ("AET", "Match Finished After Extra Time"), "PENALTY_SHOOTOUT": ("PEN", "Match Finished After Penalty")}.get(
            dur, ("FT", "Match Finished")) + (120 if dur in ("EXTRA_TIME", "PENALTY_SHOOTOUT") else 90,)
    return {"SUSPENDED": ("SUSP", "Match Suspended"), "POSTPONED": ("PST", "Match Postponed"), "CANCELLED": ("CANC", "Match Cancelled"),
            "AWARDED": ("AWD", "Technical Loss")}.get(s, ("TBD", "Time To Be Defined")) + (None,)


def _team(t: dict) -> dict:
    return {"id": t.get("id"), "name": clean_name(t.get("name") or t.get("shortName")), "logo": t.get("crest")}


def _score(m):
    sc = m.get("score") or {}
    ft = sc.get("fullTime") or {}
    if sc.get("duration") == "PENALTY_SHOOTOUT" and sc.get("regularTime"):
        rt, et = sc.get("regularTime") or {}, sc.get("extraTime") or {}
        return {"home": (rt.get("home") or 0) + (et.get("home") or 0), "away": (rt.get("away") or 0) + (et.get("away") or 0)}
    return {"home": ft.get("home"), "away": ft.get("away")}


def _pos(p):
    s = (p or "").lower()
    if "keeper" in s: return "G"
    if "back" in s or "defen" in s: return "D"
    if "midfield" in s: return "M"
    if any(w in s for w in ("forward", "offence", "winger", "striker", "attack")): return "F"
    return None


def _p(x): return {"id": (x or {}).get("id"), "name": (x or {}).get("name")}


def to_fixture(m: dict) -> dict:
    """One football-data v4 match -> API-Football fixture object."""
    home, away = _team(m.get("homeTeam") or {}), _team(m.get("awayTeam") or {})
    tm = {home["id"]: home, away["id"]: away}
    other = {home["id"]: away, away["id"]: home}
    short, long_, el = _status(m)
    comp = m.get("competition") or {}
    sc = _score(m); ht = (m.get("score") or {}).get("halfTime") or {}
    ref = next((r.get("name") for r in m.get("referees") or [] if r.get("type") in (None, "REFEREE")), None)
    evs = []
    for g in m.get("goals") or []:
        t = tm.get((g.get("team") or {}).get("id")) or _team(g.get("team") or {})
        typ = g.get("type")
        if typ == "OWN":  # football-data credits the team that benefits; API-Football uses the scorer's own team
            t = other.get(t["id"], t)
        evs.append({"time": {"elapsed": g.get("minute"), "extra": g.get("injuryTime")}, "team": t, "type": "Goal",
                    "detail": {"OWN": "Own Goal", "PENALTY": "Penalty"}.get(typ, "Normal Goal"),
                    "player": _p(g.get("scorer")), "assist": _p(g.get("assist"))})
    for b in m.get("bookings") or []:
        t = tm.get((b.get("team") or {}).get("id")) or _team(b.get("team") or {})
        evs.append({"time": {"elapsed": b.get("minute"), "extra": None}, "team": t, "type": "Card",
                    "detail": {"RED": "Red Card", "YELLOW_RED": "Second Yellow card"}.get(b.get("card"), "Yellow Card"),
                    "player": _p(b.get("player")), "assist": _p(None)})
    for s in m.get("substitutions") or []:
        t = tm.get((s.get("team") or {}).get("id")) or _team(s.get("team") or {})
        evs.append({"time": {"elapsed": s.get("minute"), "extra": None}, "team": t, "type": "subst", "detail": "Substitution",
                    "player": _p(s.get("playerOut")), "assist": _p(s.get("playerIn"))})
    evs.sort(key=lambda e: ((e["time"]["elapsed"] or 0), (e["time"]["extra"] or 0)))
    lineups, stats = [], []
    for side, t in (("homeTeam", home), ("awayTeam", away)):
        src = m.get(side) or {}
        if src.get("lineup"):
            lineups.append({"team": t, "formation": src.get("formation"), "coach": {"name": (src.get("coach") or {}).get("name")},
                            "startXI": [{"player": {"id": p.get("id"), "name": p.get("name"), "number": p.get("shirtNumber"), "pos": _pos(p.get("position")), "grid": None}} for p in src["lineup"]],
                            "substitutes": [{"player": {"id": p.get("id"), "name": p.get("name"), "number": p.get("shirtNumber"), "pos": _pos(p.get("position"))}} for p in src.get("bench") or []]})
        st = src.get("statistics")
        if st:
            pos = st.get("ball_possession")
            stats.append({"team": t, "statistics": [
                {"type": "Total Shots", "value": st.get("shots")}, {"type": "Shots on Goal", "value": st.get("shots_on_goal")},
                {"type": "Ball Possession", "value": f"{pos}%" if pos is not None else None}, {"type": "Corner Kicks", "value": st.get("corner_kicks")},
                {"type": "Fouls", "value": st.get("fouls")}, {"type": "Yellow Cards", "value": st.get("yellow_cards")},
                {"type": "Red Cards", "value": st.get("red_cards")}]})
    season = (m.get("season") or {}).get("startDate", "")[:4]
    return {"fixture": {"id": m["id"], "referee": ref, "timezone": "UTC", "date": m.get("utcDate"),
                        "venue": {"id": None, "name": m.get("venue"), "city": None},
                        "status": {"short": short, "long": long_, "elapsed": el, "extra": None}},
            "league": {"id": COMP_TO_LEAGUE.get(comp.get("code"), comp.get("id")), "name": comp.get("name"), "code": comp.get("code"),
                       "logo": comp.get("emblem"), "season": int(season) if season.isdigit() else None,
                       "round": (m.get("stage") or "").replace("_", " ").title() if m.get("stage") not in (None, "REGULAR_SEASON") else (f"Matchday {m['matchday']}" if m.get("matchday") else None)},
            "teams": {"home": home, "away": away}, "goals": sc,
            "score": {"halftime": {"home": ht.get("home"), "away": ht.get("away")}, "fulltime": sc},
            "events": evs, "lineups": lineups, "statistics": stats, "source": "football-data.org"}


def to_squad(team: dict) -> dict:
    """football-data /teams/{id} -> API-Football players/squads response."""
    today = dt.date.today(); players = []
    for p in team.get("squad") or []:
        age = None
        try:
            b = dt.date.fromisoformat((p.get("dateOfBirth") or "")[:10]); age = today.year - b.year - ((today.month, today.day) < (b.month, b.day))
        except ValueError: pass
        g = {"G": "Goalkeeper", "D": "Defender", "M": "Midfielder", "F": "Attacker"}.get(_pos(p.get("position")), "Midfielder")
        players.append({"id": p.get("id"), "name": p.get("name"), "age": age, "number": p.get("shirtNumber"), "position": g, "photo": None})
    return {"response": [{"team": _team(team), "players": players}] if players else []}
