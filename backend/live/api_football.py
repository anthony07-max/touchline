"""API-Football (api-sports.io v3) client: quota-aware, disk-cached. Key read from env API_FOOTBALL_KEY, never logged."""
from __future__ import annotations
import json, os, time, threading, urllib.request, urllib.parse, hashlib
BASE = "https://v3.football.api-sports.io/"
CACHE = os.path.join(os.path.dirname(__file__), "..", "..", "data", "live_cache")
os.makedirs(CACHE, exist_ok=True)
_lock = threading.Lock()
_last = [0.0]
quota = {"limit_day": None, "remaining": None, "plan": None, "calls_this_process": 0, "last_error": None}


class APIError(Exception):
    pass


def _path(ep): return os.path.join(CACHE, hashlib.md5(ep.encode()).hexdigest() + ".json")


def get(ep: str, max_age: float = 3600 * 24 * 7, force=False):
    """GET endpoint (e.g. 'fixtures?id=1'). Returns cached JSON if younger than max_age."""
    p = _path(ep)
    if not force and os.path.exists(p) and time.time() - os.path.getmtime(p) < max_age:
        with open(p) as f: return json.load(f)
    key = os.environ.get("API_FOOTBALL_KEY")
    if not key: raise APIError("API_FOOTBALL_KEY not set")
    with _lock:
        if quota["remaining"] is not None and quota["remaining"] <= 3:
            if os.path.exists(p):
                with open(p) as f: return json.load(f)
            raise APIError("daily quota nearly exhausted; serving cache only")
        gap = time.time() - _last[0]
        if gap < 6.5: time.sleep(6.5 - gap)  # free plan: 10 req/min
        _last[0] = time.time()
        req = urllib.request.Request(BASE + ep, headers={"x-apisports-key": key})
        with urllib.request.urlopen(req, timeout=20) as r:
            data = json.load(r)
            h = r.headers
            quota["calls_this_process"] += 1
            if h.get("x-ratelimit-requests-remaining"): quota["remaining"] = int(h["x-ratelimit-requests-remaining"])
            if h.get("x-ratelimit-requests-limit"): quota["limit_day"] = int(h["x-ratelimit-requests-limit"])
    errs = data.get("errors")
    if errs:
        quota["last_error"] = errs
        if os.path.exists(p):
            with open(p) as f: return json.load(f)
        raise APIError(str(errs))
    with open(p, "w") as f: json.dump(data, f)
    return data


def status():
    d = get("status", max_age=600)
    r = d["response"]
    quota["plan"] = r["subscription"]["plan"]
    quota["limit_day"] = r["requests"]["limit_day"]
    if quota["remaining"] is None: quota["remaining"] = r["requests"]["limit_day"] - r["requests"]["current"]
    return {"plan": r["subscription"]["plan"], "active": r["subscription"]["active"],
            "used_today": r["requests"]["current"], "limit_day": r["requests"]["limit_day"]}
