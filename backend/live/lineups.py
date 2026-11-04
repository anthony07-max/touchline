"""Lineup watcher: for every upcoming fixture, from T-60 min before kick-off poll the provider for confirmed XIs every
POLL_S seconds until found (then stop), cache them (memory + disk) and inject them into fixture payloads so the match
centre, 2D view and What-if/simulation engine use the real XI instead of a squad-modelled one.

Sources, in order:
  1. football-data.org /v4/matches/{id} with X-Unfold-Lineups (only returns line-ups on the paid "Deep Data" plan;
     on the free tier the lineup arrays are empty, so the watcher keeps showing the placeholder);
  2. API-Football, only if API_FOOTBALL_KEY is set and not auto-disabled after a suspension.
Rate: at most MAX_PER_TICK upstream calls per minute, well inside football-data's 10/min (raw() also spaces calls 6.5s)."""
from __future__ import annotations
import asyncio, datetime as dt, json, os, re, threading, time
from typing import Optional
from . import provider as af, football_data as fd

WINDOW_S = 60 * 60          # start polling 60 min before kick-off
POLL_S = 5 * 60             # every ~5 min until found
GIVE_UP_AFTER_KO_S = 30 * 60
MAX_PER_TICK = 2
TICK_S = 60
PATH = os.path.join(fd.CACHE, "lineups_store.json")
STORE: dict = {}            # fid(str) -> {"lineups": [...API-Football shape...], "found_at", "source"}
CHECKS: dict = {}           # fid(str) -> {"last": ts, "n": int, "result": str}
_lock = threading.Lock()


def _load():
    try:
        with open(PATH) as f: STORE.update(json.load(f))
    except Exception: pass


def _save():
    try:
        with open(PATH, "w") as f: json.dump(STORE, f)
    except Exception: pass


_load()


def _ts(iso: Optional[str]) -> Optional[float]:
    if not iso: return None
    try: return dt.datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()
    except ValueError: return None


def get(fid) -> Optional[dict]:
    return STORE.get(str(fid))


def inject(raw_fx: dict) -> dict:
    """Return the raw (API-Football-shaped) fixture with cached confirmed line-ups filled in if the feed lacks them."""
    if raw_fx.get("lineups"): return raw_fx
    hit = get((raw_fx.get("fixture") or {}).get("id"))
    return {**raw_fx, "lineups": hit["lineups"]} if hit else raw_fx


def status(fid, kickoff_iso: Optional[str], available: bool, now: Optional[float] = None) -> dict:
    now = now or time.time(); ko = _ts(kickoff_iso); c = CHECKS.get(str(fid)) or {}
    hit = get(fid)
    if available or hit:
        return {"state": "confirmed", "label": "Confirmed XI", "source": (hit or {}).get("source"), "found_at": (hit or {}).get("found_at")}
    if ko is None: return {"state": "unknown", "label": "Line-ups expected ~1h before kick-off"}
    starts = ko - WINDOW_S
    if now < starts:
        return {"state": "scheduled", "label": "Line-ups expected ~1h before kick-off", "watch_starts_utc": dt.datetime.fromtimestamp(starts, dt.timezone.utc).isoformat()}
    if now > ko + GIVE_UP_AFTER_KO_S:
        return {"state": "unavailable", "label": "Line-ups not provided by the data feed", "checks": c.get("n", 0), "plan_note": PLAN_NOTE}
    return {"state": "watching", "label": "Checking for line-ups every 5 min", "checks": c.get("n", 0), "last_check": c.get("last"),
            "last_result": c.get("result"), "plan_note": PLAN_NOTE if c.get("result") == "empty" else None}


PLAN_NOTE = "football-data.org only sends line-ups on its Deep Data plan; the free plan has fixtures, scores and tables only."


def due(fid, kickoff_iso, ended: bool, now: Optional[float] = None) -> bool:
    now = now or time.time(); ko = _ts(kickoff_iso)
    if ended or ko is None or get(fid): return False
    if not (ko - WINDOW_S <= now <= ko + GIVE_UP_AFTER_KO_S): return False
    return now - (CHECKS.get(str(fid)) or {}).get("last", 0) >= POLL_S


def _norm(n): return set(w for w in re.sub(r"[^a-z ]", " ", (n or "").lower()).split() if len(w) > 2 and w not in ("fc", "cf", "pae", "afc", "club"))


def _from_api_football(fx_summary: dict) -> Optional[list]:
    if not af.af_usable(): return None
    try:
        day = fx_summary["kickoff_utc"][:10]
        d = af._af(f"fixtures?date={day}", 3600, False)
        h, a = _norm(fx_summary["home"]["name"]), _norm(fx_summary["away"]["name"])
        for f in d.get("response") or []:
            if _norm(f["teams"]["home"]["name"]) & h and _norm(f["teams"]["away"]["name"]) & a:
                r = af._af(f"fixtures?id={f['fixture']['id']}", 0, True).get("response") or []
                lus = (r[0].get("lineups") if r else None) or []
                if lus:  # remap team ids to the football-data ids the app uses
                    ids = {r[0]["teams"]["home"]["id"]: fx_summary["home"], r[0]["teams"]["away"]["id"]: fx_summary["away"]}
                    return [{**l, "team": {**l["team"], **{k: ids.get(l["team"]["id"], l["team"]).get(k) for k in ("id", "name")}}} for l in lus]
    except Exception: return None
    return None


def check(fx_summary: dict, fetch=None) -> str:
    """One poll for one fixture. Returns 'found' | 'empty' | 'error: ...'. fetch(fid) -> raw API-Football-shaped fixture (tests)."""
    fid = fx_summary["fixture_id"]; key = str(fid)
    c = CHECKS.setdefault(key, {"n": 0}); c["last"] = time.time(); c["n"] += 1
    try:
        if fetch: raw = fetch(fid)
        else: raw = fd.to_fixture(fd.raw(f"matches/{int(fid)}", max_age=0, force=True, headers={"X-Unfold-Lineups": "true"}))
        lus, src = raw.get("lineups") or [], "football-data.org"
        if not lus and not fetch:
            lus, src = _from_api_football(fx_summary) or [], "API-Football"
        if lus:
            with _lock:
                STORE[key] = {"lineups": lus, "found_at": time.time(), "source": src}; _save()
            try:
                from . import centre; centre._mem.pop(fid, None)  # next snapshot picks the XI up immediately
            except Exception: pass
            c["result"] = "found"
        else: c["result"] = "empty"
    except Exception as e:
        c["result"] = f"error: {str(e)[:80]}"
    return c["result"]


def tick(items=None, now=None) -> list:
    if items is None:
        from . import centre
        items = centre.discover().get("items") or []
    done = []
    for x in items:
        if len(done) >= MAX_PER_TICK: break
        if x.get("classic") or x["fixture_id"] >= af.AF_ID_MIN and not af.af_usable(): continue
        if due(x["fixture_id"], x.get("kickoff_utc"), (x.get("state") or {}).get("ended", False), now):
            done.append((x["fixture_id"], check(x)))
    return done


async def watcher():
    await asyncio.sleep(20)
    while True:
        try: await asyncio.to_thread(tick)
        except Exception: pass
        await asyncio.sleep(TICK_S)
