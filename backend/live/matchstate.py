"""Fixture state machine: upcoming -> prematch -> live -> halftime -> live -> fulltime (+ postponed/stale).
Pure functions, so they are unit-testable without the network."""
from __future__ import annotations
import datetime as dt
from typing import Optional, Dict

LIVE = {"1H", "2H", "ET", "P", "LIVE", "BT", "INT", "SUSP"}
HALF = {"HT"}
FINISHED = {"FT", "AET", "PEN", "AWD", "WO"}
OFF = {"PST", "CANC", "ABD"}
MAX_MATCH_MIN = 150  # KO + 150 min: a match cannot still be "not started"/live after this without news


def _ko(kickoff_utc: str) -> dt.datetime:
    k = dt.datetime.fromisoformat(kickoff_utc.replace("Z", "+00:00"))
    return k if k.tzinfo else k.replace(tzinfo=dt.timezone.utc)


def phase(status_short: Optional[str], kickoff_utc: str, now: Optional[dt.datetime] = None) -> Dict:
    """Return {phase, label, playable_live, can_branch, can_replay, seconds_to_ko}.
    phase in: upcoming | prematch | live | halftime | fulltime | postponed | stale_finished"""
    now = now or dt.datetime.now(dt.timezone.utc)
    s = (status_short or "NS").upper()
    secs = int((_ko(kickoff_utc) - now).total_seconds())
    since_ko_min = -secs / 60
    if s in FINISHED: p = "fulltime"
    elif s in OFF: p = "postponed"
    elif s in HALF: p = "halftime"
    elif s in LIVE: p = "fulltime" if since_ko_min > MAX_MATCH_MIN + 60 else "live"
    else:  # NS / TBD
        if since_ko_min > MAX_MATCH_MIN: p = "stale_finished"   # feed never updated but the game is over
        elif secs <= 0: p = "live"                               # kicked off, feed lagging
        elif secs <= 75 * 60: p = "prematch"
        else: p = "upcoming"
    labels = {"upcoming": "Upcoming", "prematch": "Line-ups soon" if secs > 0 else "Kick-off", "live": "Live", "halftime": "Half-time",
              "fulltime": "Full time", "postponed": "Postponed", "stale_finished": "Full time (result pending)"}
    ended = p in ("fulltime", "stale_finished")
    return {"phase": p, "label": labels[p], "seconds_to_ko": secs,
            "playable_live": p in ("live", "halftime", "prematch"),
            "can_branch": p in ("live", "halftime") or ended,
            "can_replay": ended, "ended": ended}


def poll_seconds(p: str, seconds_to_ko: int, remaining_quota: Optional[int]) -> int:
    """Server-side refresh cadence per phase (free plan: 100/day)."""
    if p in ("fulltime", "postponed", "stale_finished"): return 6 * 3600
    if p == "upcoming": return int(min(6 * 3600, max(900, (seconds_to_ko - 70 * 60))))
    if p == "prematch": return 600
    if p == "halftime": return 300
    rem = remaining_quota if remaining_quota is not None else 50
    return int(max(90, 150 * 60 / max(1, rem - 8)))
