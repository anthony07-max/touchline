"""First-class football-data.org (free tier) browsing: competitions, standings, fixture lists, team pages.
Everything is server-side cached (standings 30 min, fixtures 10 min, teams 1 day) so viewers never spend the 10 req/min budget."""
from __future__ import annotations
import datetime as dt, os
from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from . import football_data as fd, provider, centre

router = APIRouter()
FRONT = os.path.join(os.path.dirname(__file__), "..", "..", "frontend")
COMPETITIONS = [  # free-tier codes, elite order
    ("CL", "Champions League", "Europe"), ("PL", "Premier League", "England"), ("PD", "La Liga", "Spain"),
    ("BL1", "Bundesliga", "Germany"), ("SA", "Serie A", "Italy"), ("FL1", "Ligue 1", "France"),
    ("ELC", "Championship", "England"), ("DED", "Eredivisie", "Netherlands"), ("PPL", "Primeira Liga", "Portugal"),
    ("EC", "European Championship", "Europe"), ("WC", "World Cup", "World"), ("BSA", "Brasileirão", "Brazil")]
CODES = {c[0] for c in COMPETITIONS}
NOT_COVERED = "League One, League Two (incl. Leyton Orient), cups and other leagues aren't in football-data.org's free tier."


def _need(code):
    code = code.upper()
    if code not in CODES: raise HTTPException(404, f"{code} isn't covered by the free football-data.org feed. {NOT_COVERED}")
    if not fd.key(): raise HTTPException(503, "Live feed paused: no live data key is set yet.")
    return code


def _err(e): return HTTPException(503, f"data unavailable: {e}")


def table_rows(r: dict) -> list:
    """football-data standings -> flat rows (TOTAL tables only; CL league phase is one table, groups give several)."""
    out = []
    for s in r.get("standings") or []:
        if s.get("type", "TOTAL") != "TOTAL": continue
        rows = [{"pos": t.get("position"), "team": fd._team(t.get("team") or {}), "played": t.get("playedGames"),
                 "won": t.get("won"), "draw": t.get("draw"), "lost": t.get("lost"), "gf": t.get("goalsFor"),
                 "ga": t.get("goalsAgainst"), "gd": t.get("goalDifference"), "pts": t.get("points"),
                 "form": t.get("form"), "elite": centre.is_elite(fd.clean_name((t.get("team") or {}).get("name")))}
                for t in s.get("table") or []]
        out.append({"group": (s.get("group") or "").replace("_", " ").title() or None, "rows": rows})
    return out


@router.get("/api/competitions")
def competitions():
    return {"competitions": [{"code": c, "name": n, "area": a, "emblem": f"https://crests.football-data.org/{c}.png"} for c, n, a in COMPETITIONS],
            "not_covered": NOT_COVERED, "provider": provider.active(), "paused": not fd.key()}


@router.get("/api/competitions/{code}/standings")
def standings(code: str):
    code = _need(code)
    try: r = fd.raw(f"competitions/{code}/standings", max_age=1800)
    except fd.APIError as e: raise _err(e)
    return {"code": code, "competition": (r.get("competition") or {}).get("name"), "season": r.get("season"),
            "matchday": (r.get("season") or {}).get("currentMatchday"), "tables": table_rows(r)}


@router.get("/api/competitions/{code}/matches")
def comp_matches(code: str, back: int = 7, ahead: int = 14):
    code = _need(code); t = dt.date.today()
    try: r = fd.raw(f"competitions/{code}/matches?dateFrom={t - dt.timedelta(days=min(back, 10))}&dateTo={t + dt.timedelta(days=min(ahead, 21))}", max_age=600)
    except fd.APIError as e: raise _err(e)
    items = [centre._summ(fd.to_fixture(m)) for m in r.get("matches") or []]
    return {"code": code, "matches": sorted(items, key=lambda x: x["kickoff_utc"])}


@router.get("/api/teams/{tid}")
def team(tid: int):
    if not fd.key(): raise HTTPException(503, "Live feed paused: no live data key is set yet.")
    try: t = fd.raw(f"teams/{tid}", max_age=86400)
    except fd.APIError as e: raise _err(e)
    try: ms = fd.raw(f"teams/{tid}/matches?limit=12&dateFrom={dt.date.today() - dt.timedelta(days=60)}&dateTo={dt.date.today() + dt.timedelta(days=60)}", max_age=1800)
    except fd.APIError: ms = {}
    sq = fd.to_squad(t)["response"]
    return {"team": {**fd._team(t), "venue": t.get("venue"), "founded": t.get("founded"), "colors": t.get("clubColors"),
                     "coach": (t.get("coach") or {}).get("name"), "competitions": [c.get("code") for c in t.get("runningCompetitions") or [] if c.get("code") in CODES]},
            "squad": sq[0]["players"] if sq else [],
            "squad_note": "Squad list is real; player ratings in Touchline are modelled from position and age (the free feed has no player stats).",
            "matches": [centre._summ(fd.to_fixture(m)) for m in ms.get("matches") or []]}


def _page(): return FileResponse(os.path.join(FRONT, "browse.html"))


@router.get("/competitions", include_in_schema=False)
def p_comps(): return _page()


@router.get("/competitions/{code}", include_in_schema=False)
def p_comp(code: str): return _page()


@router.get("/team/{tid}", include_in_schema=False)
def p_team(tid: int): return _page()
