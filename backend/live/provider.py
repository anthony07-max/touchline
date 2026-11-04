"""Live data provider facade. Speaks the API-Football endpoint dialect the app already uses (fixtures?id=, fixtures?date=,
players/squads?team=, ...) and routes it:
  * football-data.org v4 (env FOOTBALL_DATA_KEY) is the primary source;
  * API-Football (env API_FOOTBALL_KEY) is an optional fallback, used only while its key works (auto-disabled for 6h after an
    account/suspension error) and for legacy API-Football fixture ids (>= 1,000,000) and its recorded cache.
No key at all -> APIError, last_error set, and the UI shows the 'feed paused' notice."""
from __future__ import annotations
import datetime as dt, os, time, urllib.parse
from . import football_data as fd, api_football as afb


AF_ID_MIN = 1_000_000
_af_dead_until = [0.0]
quota = {"provider": None, "remaining": None, "last_error": None, "calls_this_process": 0}



APIError = fd.APIError  # api_football re-exports the same class, so `except af.APIError` catches both


def af_usable() -> bool:
    return bool(os.environ.get("API_FOOTBALL_KEY")) and time.time() > _af_dead_until[0]


def active() -> str:
    return "football-data.org" if fd.key() else "API-Football" if af_usable() else "none"


def _sync():
    quota.update(provider=active(), calls_this_process=fd.quota["calls_this_process"] + afb.quota["calls_this_process"])
    quota["remaining"] = afb.quota["remaining"] if active() == "API-Football" else None
    if active() == "none": quota["last_error"] = "No live data key set (FOOTBALL_DATA_KEY)"
    elif active() == "football-data.org": quota["last_error"] = fd.quota["last_error"]
    else: quota["last_error"] = afb.quota["last_error"]


def paused_reason():
    _sync(); return quota["last_error"]


def _af(ep, max_age, force):
    try:
        return afb.get(ep, max_age=max_age, force=force)
    except afb.APIError as e:
        if any(w in str(e).lower() for w in ("suspend", "access", "account", "token", "key")):
            _af_dead_until[0] = time.time() + 6 * 3600
        raise


def _parse(ep):
    path, _, qs = ep.partition("?")
    return path, {k: v[0] for k, v in urllib.parse.parse_qs(qs).items()}


def _fd(ep, max_age, force) -> dict:
    path, q = _parse(ep)
    if path == "fixtures" and "id" in q:
        return {"response": [fd.to_fixture(fd.raw(f"matches/{int(q['id'])}", max_age, force))]}
    if path == "fixtures" and "ids" in q:
        return {"response": [fd.to_fixture(fd.raw(f"matches/{int(i)}", max_age, force)) for i in q["ids"].split("-") if int(i) < AF_ID_MIN]}
    if path == "fixtures" and "date" in q:
        d0 = dt.date.fromisoformat(q["date"]); d1 = d0 + dt.timedelta(days=1)
        r = fd.raw(f"matches?dateFrom={d0}&dateTo={d1}", max_age, force)
        out = [fd.to_fixture(m) for m in r.get("matches") or []]
        return {"response": [f for f in out if (f["fixture"]["date"] or "")[:10] == q["date"]]}
    if path == "fixtures" and "league" in q:
        comp = fd.LEAGUE_TO_COMP.get(int(q["league"]))
        if not comp: raise fd.APIError("competition not covered by football-data.org free tier")
        qs = f"season={q['season']}" + (f"&stage={fd.STAGE[q['round']]}" if q.get("round") in fd.STAGE else "")
        r = fd.raw(f"competitions/{comp}/matches?{qs}", max_age, force)
        return {"response": [fd.to_fixture(m) for m in r.get("matches") or []]}
    if path == "players/squads":
        return fd.to_squad(fd.raw(f"teams/{int(q['team'])}", max_age, force))
    raise fd.APIError(f"not available from football-data.org: {path}")


def get(ep: str, max_age: float = 3600 * 24 * 7, force=False) -> dict:
    path, q = _parse(ep)
    legacy = path == "fixtures" and "id" in q and int(q["id"]) >= AF_ID_MIN
    try:
        if legacy or not fd.key():
            if not fd.key() and not legacy and not af_usable():
                fd.quota["last_error"] = "FOOTBALL_DATA_KEY not set"
                raise fd.APIError("No live data key set (FOOTBALL_DATA_KEY)")
            return _af(ep, max_age, force)  # also serves API-Football's recorded disk cache without a key
        return _fd(ep, max_age, force)  # ids differ between providers, so no per-call cross-provider fallback
    finally:
        _sync()


def status() -> dict:
    a = active()
    if a == "football-data.org":
        r = fd.raw("competitions", max_age=3600)
        comps = sorted(c.get("code") for c in r.get("competitions") or [] if c.get("code"))
        return {"provider": a, "plan": "football-data.org free tier", "active": True, "competitions": comps, "limit_minute": 10}
    if a == "API-Football":
        return {"provider": a, **afb.status()}
    raise fd.APIError("No live data key set (FOOTBALL_DATA_KEY)")
