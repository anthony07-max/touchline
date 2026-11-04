"""Parse API-Football fixtures?id=X (includes events, lineups, statistics) into a normalized live match dict."""
from __future__ import annotations
from typing import Dict, Optional


def _stat(stats_list, name):
    for s in stats_list:
        if s.get("type") == name: return s.get("value")
    return None


def parse_fixture(fx: dict, seen_event_ids: Optional[set] = None) -> Dict:
    f, t, g = fx["fixture"], fx["teams"], fx["goals"]
    st = f["status"]
    out = {
        "fixture_id": f["id"], "kickoff_utc": f["date"], "venue": (f.get("venue") or {}).get("name"),
        "referee": f.get("referee"), "league": fx["league"]["name"], "round": fx["league"].get("round"),
        "status": {"short": st["short"], "long": st["long"], "elapsed": st.get("elapsed"), "extra": st.get("extra")},
        "home": {"id": t["home"]["id"], "name": t["home"]["name"], "logo": t["home"]["logo"]},
        "away": {"id": t["away"]["id"], "name": t["away"]["name"], "logo": t["away"]["logo"]},
        "score": {"home": g["home"], "away": g["away"]},
    }
    # events: API has no event id -> stable synthetic id for dedupe
    evs, ids = [], set()
    for e in fx.get("events") or []:
        tm = e.get("time") or {}
        eid = f'{tm.get("elapsed")}+{tm.get("extra") or 0}|{e["team"]["id"]}|{e.get("type")}|{e.get("detail")}|{(e.get("player") or {}).get("id")}|{(e.get("assist") or {}).get("id")}'
        if eid in ids: continue
        ids.add(eid)
        evs.append({"id": eid, "minute": tm.get("elapsed"), "extra": tm.get("extra"), "team_id": e["team"]["id"],
                    "team": e["team"]["name"], "type": e.get("type"), "detail": e.get("detail"),
                    "player": (e.get("player") or {}).get("name"), "player_id": (e.get("player") or {}).get("id"),
                    "assist": (e.get("assist") or {}).get("name"), "assist_id": (e.get("assist") or {}).get("id"),
                    "new": seen_event_ids is not None and eid not in seen_event_ids})
    out["events"] = evs
    # lineups
    lus = {}
    for lu in fx.get("lineups") or []:
        lus[lu["team"]["id"]] = {"formation": lu.get("formation"), "coach": (lu.get("coach") or {}).get("name"),
            "startXI": [{"id": str(p["player"]["id"]), "name": p["player"]["name"], "number": p["player"].get("number"), "pos": p["player"].get("pos"), "grid": p["player"].get("grid")} for p in lu.get("startXI") or []],
            "substitutes": [{"id": str(p["player"]["id"]), "name": p["player"]["name"], "number": p["player"].get("number"), "pos": p["player"].get("pos")} for p in lu.get("substitutes") or []]}
    out["lineups"] = {"available": bool(lus), "by_team": {str(k): v for k, v in lus.items()},
                      "note": None if lus else ("Line-ups expected ~1h before kick-off; until then the XI shown is modelled from the squad" if fx.get("source") == "football-data.org" else "unavailable: official line-ups usually published ~60 min before kick-off")}
    # statistics
    stats = {}
    for s in fx.get("statistics") or []:
        L = s.get("statistics") or []
        stats[str(s["team"]["id"])] = {
            "shots": _stat(L, "Total Shots"), "shots_on_target": _stat(L, "Shots on Goal"), "possession": _stat(L, "Ball Possession"),
            "corners": _stat(L, "Corner Kicks"), "fouls": _stat(L, "Fouls"), "yellow": _stat(L, "Yellow Cards"), "red": _stat(L, "Red Cards"),
            "passes_pct": _stat(L, "Passes %"), "xg": _stat(L, "expected_goals")}
    out["statistics"] = {"available": bool(stats), "by_team": stats,
                         "xg_available": any(v.get("xg") is not None for v in stats.values()),
                         "note": None if stats else ("not in the free data feed; possession and shots are modelled" if fx.get("source") == "football-data.org" else "unavailable (not started or not provided for this fixture)")}
    out["source"] = fx.get("source") or "API-Football"
    return out


def on_pitch(live: Dict, team_id: int):
    """Current on-pitch ids after applying subs and red cards to the official XI."""
    lu = live["lineups"]["by_team"].get(str(team_id))
    if not lu: return None
    on = [p["id"] for p in lu["startXI"]]
    bench = [p["id"] for p in lu["substitutes"]]
    sent_off = []
    for e in live["events"]:
        if e["team_id"] != team_id: continue
        if e["type"] == "subst":
            # API-Football: player = player OFF, assist = player ON (usually); handle reversed feeds
            a, b = str(e["player_id"]), str(e["assist_id"])
            if a in on and b not in on: on[on.index(a)] = b; bench = [x for x in bench if x != b]
            elif b in on and a not in on: on[on.index(b)] = a; bench = [x for x in bench if x != a]
        elif e["type"] == "Card" and e["detail"] in ("Red Card", "Second Yellow card") and str(e["player_id"]) in on:
            on.remove(str(e["player_id"])); sent_off.append(str(e["player_id"]))
    return {"on": on, "bench": bench, "sent_off": sent_off, "formation": lu["formation"]}
