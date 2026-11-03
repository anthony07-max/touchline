"""Converts players + formation + tactics into team unit ratings, and
computes per-minute event-rate modifiers from the tactical interaction model."""
from __future__ import annotations
from typing import List, Tuple, Dict
from .models import Team, Tactics, FORMATIONS, Player

OUT_OF_POSITION = 0.86


def assign_slots(team: Team) -> List[Tuple[str, Player, float]]:
    """Fill formation lines with starters; players out of their line get a penalty."""
    lines = dict(FORMATIONS[team.tactics.formation])
    gk = [p for p in team.starters if p.group == "GK"]
    outfield = [p for p in team.starters if p.group != "GK"]
    slots: List[Tuple[str, Player, float]] = [("GK", g, 1.0) for g in gk[:1]]
    remaining = list(outfield)
    for line in ("DEF", "MID", "FWD"):
        native = [p for p in remaining if p.group == line][: lines[line]]
        for p in native:
            slots.append((line, p, 1.0)); remaining.remove(p)
        lines[line] -= len(native)
    order = {"DEF": ["MID", "FWD"], "MID": ["DEF", "FWD"], "FWD": ["MID", "DEF"]}
    for line in ("DEF", "MID", "FWD"):
        while lines[line] > 0 and remaining:
            pick = next((p for g in order[line] for p in remaining if p.group == g), remaining[0])
            slots.append((line, pick, OUT_OF_POSITION)); remaining.remove(pick); lines[line] -= 1
    return slots


def unit_ratings(team: Team) -> Dict[str, float]:
    s = assign_slots(team)
    def tot(line, attr, w=1.0):
        return sum(p.eff(attr) * pen * w for l, p, pen in s if l == line)
    attack = tot("FWD", "att") + 0.45 * tot("MID", "att") + 0.12 * tot("DEF", "att")
    control = tot("MID", "pas") + 0.35 * tot("DEF", "pas") + 0.3 * tot("FWD", "pas")
    defence = tot("DEF", "defn") + 0.4 * tot("MID", "defn") + 0.1 * tot("FWD", "defn")
    gk = sum(p.eff("defn") for l, p, _ in s if l == "GK")
    pace_fwd = [p.eff("pac") for l, p, _ in s if l == "FWD"] or [50]
    pace_def = [p.eff("pac") for l, p, _ in s if l == "DEF"] or [50]
    side_def = {}
    for side in ("L", "R"):
        v = [p.eff("defn") * pen for l, p, pen in s if l == "DEF" and p.side == side]
        side_def[side] = sum(v) / len(v) if v else 50.0
    aer = sorted((p.eff("aer") for l, p, _ in s if l != "GK"), reverse=True)[:4] or [50]
    k = team.strength
    # normalise against a reference XI of 70-rated players in 4-3-3
    return {
        "attack": k * attack / (70 * (3 + 0.45 * 3 + 0.12 * 4)),
        "control": k * control / (70 * (3 + 0.35 * 4 + 0.3 * 3)),
        "defence": k * defence / (70 * (4 + 0.4 * 3 + 0.1 * 3)),
        "gk": gk / 70,
        "fwd_pace": max(pace_fwd), "def_pace": sum(pace_def) / len(pace_def),
        "def_L": side_def["L"], "def_R": side_def["R"],
        "aerial": sum(aer) / len(aer),
    }


def tactic_modifiers(t: Tactics, opp_t: Tactics, own: Dict, opp: Dict, team: Team = None, opp_team: Team = None):
    """Returns (mods, factors). mods: multipliers for this team's
    shot rate (att), shot quality (q), concession rate (exp), conceded quality (expq),
    possession shift (pos, additive), fatigue rate (fat). factors: human-readable reasons."""
    m = {"att": 1.0, "q": 1.0, "exp": 1.0, "expq": 1.0, "pos": 0.0, "fat": 1.0,
         "sp_rate": 1.0, "sp_q": 1.0, "sp_exp": 1.0, "sp_expq": 1.0}
    f: List[Tuple[str, str, float]] = []

    def apply(key, val, why):
        if key == "pos":
            m[key] += val
        else:
            m[key] *= val
        f.append((key, why, val))

    p = t.pressing
    if p == "low":
        apply("att", 0.92, "a low block wins the ball back deeper, so fewer attacks start high up")
        apply("exp", 0.90, "sitting deeper denies space between the lines")
        apply("fat", 0.85, "low pressing conserves energy")
        apply("pos", -0.03, "conceding the ball to the opponent")
    elif p in ("high", "counter-press"):
        hi = p == "high"
        apply("att", 1.12 if hi else 1.15, "pressing high forces turnovers close to goal")
        apply("exp", 1.05 if hi else 1.03, "when the press is bypassed there is space behind it")
        apply("fat", 1.25 if hi else 1.35, "intense pressing drains stamina faster")
        apply("pos", 0.03 if hi else 0.04, "winning the ball back quickly")
        if opp_t.build_up == "short":
            apply("att", 1.06, "their short build-up plays straight into the press")
        elif opp_t.build_up == "direct":
            apply("att", 0.95, "their direct balls go over the press, blunting it")

    if t.defensive_line == "deep":
        apply("exp", 0.90, "a deep line removes the space in behind")
        apply("expq", 0.94, "chances conceded come from further out")
        apply("att", 0.92, "the team is further from goal when it wins the ball")
    elif t.defensive_line == "high":
        apply("att", 1.06, "a high line compresses the pitch and sustains pressure")
        pace_gap = max(0.0, (opp["fwd_pace"] - own["def_pace"]) / 100)
        apply("expq", 1.10 + 0.8 * pace_gap,
              "a high line leaves space behind" + (" for their quicker forwards" if pace_gap > 0.03 else ""))
        if opp_t.transition == "counterattack":
            apply("exp", 1.10, "their counterattacking set-up targets the space behind a high line")

    if t.width == "narrow":
        apply("q", 1.03, "narrow shape gets numbers into central shooting areas")
        apply("att", 0.97, "less stretching of the opponent's back line")
        apply("exp", 0.95, "a compact, narrow block protects the middle")
    elif t.width == "wide":
        apply("att", 1.05, "width stretches their defence and creates crossing chances")
        apply("q", 0.96, "chances from wide areas are lower quality on average")
        apply("exp", 1.04, "wide shape leaves gaps between centre-backs and full-backs")

    if t.tempo == "slow":
        apply("att", 0.91, "a slow tempo produces fewer attacks")
        apply("pos", 0.04, "patient circulation keeps the ball")
        apply("exp", 0.94, "controlling the ball limits their attacks")
        apply("fat", 0.92, "a slower tempo is less tiring")
    elif t.tempo == "fast":
        apply("att", 1.10, "a fast tempo increases the number of attacks")
        apply("pos", -0.03, "quicker, riskier passing loses the ball more")
        apply("exp", 1.05, "the game becomes more open at both ends")
        apply("fat", 1.10, "a fast tempo is more tiring")

    if t.build_up == "short":
        apply("pos", 0.05, "short build-up retains possession")
        apply("q", 1.04, "worked openings tend to be better chances")
        apply("att", 0.97, "build-up is slower to reach the final third")
        if opp_t.pressing in ("high", "counter-press"):
            apply("exp", 1.07, "playing short into a high press risks turnovers near your own box")
    elif t.build_up == "direct":
        apply("pos", -0.06, "direct balls give possession away more often")
        apply("att", 1.06, "going long reaches the final third quickly")
        apply("q", 0.91, "hopeful long-ball chances are lower quality")

    if t.attacking_focus in ("left", "right"):
        # attacking our left means facing their right-side defenders
        target = "def_R" if t.attacking_focus == "left" else "def_L"
        other = "def_L" if target == "def_R" else "def_R"
        edge = (opp[other] - opp[target]) / 100
        apply("q", 1.0 + 0.6 * edge, f"targeting their {'right' if target == 'def_R' else 'left'} side "
              + ("exploits their weaker full-back" if edge > 0 else "attacks their stronger full-back"))
        apply("att", 0.98, "a one-sided focus is easier to predict")

    if t.transition == "counterattack":
        apply("pos", -0.05, "the team is happy to give up the ball and break")
        bonus = 1.14 if opp_t.defensive_line == "high" else 1.06
        apply("q", bonus, "fast breaks create high-quality chances"
              + (" against their high line" if opp_t.defensive_line == "high" else ""))
        apply("exp", 0.96, "keeping a solid shape behind the ball")
    elif t.transition == "possession":
        apply("pos", 0.04, "regaining shape and recycling the ball after a turnover")
        apply("att", 0.97, "fewer quick breaks")
        apply("exp", 0.96, "fewer chaotic transitions against you")

    # defensive approach
    da = t.defensive_approach
    if da == "man-oriented":
        apply("exp", 0.96, "man-oriented marking tracks runners and disrupts their build-up")
        apply("fat", 1.05, "man-marking is physically demanding")
        gap = max(0.0, (opp["fwd_pace"] - own["def_pace"]) / 100)
        if gap > 0.03 or opp_t.roles and "false-nine" in opp_t.roles.values():
            apply("expq", 1.08, "man-markers can be dragged out of position, opening gaps")
    elif da == "compact":
        apply("exp", 0.92, "a compact block shuts the central lanes")
        apply("att", 0.95, "a compact team has fewer players forward when it wins the ball")
        apply("pos", -0.03, "a compact block concedes the flanks and the ball")
        if opp_t.width == "wide":
            apply("exp", 1.05, "their width pulls a compact block apart")

    # player roles (only valid when the player occupies a line where the role makes sense)
    if team is not None and t.roles:
        from .models import ROLES
        slots = {p.id: (line, p) for line, p, _ in assign_slots(team)}
        for pid, role in t.roles.items():
            if pid not in slots:
                continue
            line, p = slots[pid]
            if line not in ROLES[role]:
                f.append(("role", f"{p.name} cannot play {role} from the {line} line - role ignored", 1.0)); continue
            n = p.name
            if role == "target-man":
                apply("att", 1.02 + (0.04 if t.build_up == "direct" else 0.0), f"{n} as a target man gives a focal point for longer balls")
                apply("q", 1 + (p.eff("aer") - 65) / 400, f"{n}'s aerial ability {'helps' if p.aer > 65 else 'limits'} hold-up and headed chances")
            elif role == "false-nine":
                apply("pos", 0.02, f"{n} dropping deep as a false nine adds a midfield passer")
                apply("q", 1.03 if (team and sum(1 for l, *_ in assign_slots(team) if l == 'MID') >= 3) else 0.96,
                      f"{n} dropping deep creates space for runners" )
            elif role == "poacher":
                apply("q", 1 + (p.att - 70) / 300 + 0.03, f"{n} as a poacher gets on the end of high-quality chances")
                apply("pos", -0.01, f"{n} contributes little to build-up as a poacher")
            elif role == "runner-in-behind":
                edge = max(0.0, (p.eff("pac") - opp["def_pace"]) / 100)
                hi = 2.0 if opp_t.defensive_line == "high" else 0.8
                apply("q", 1 + edge * hi, f"{n} running in behind {'exploits their high line' if opp_t.defensive_line == 'high' else 'stretches their back line'}")
            elif role == "inverted-winger":
                apply("q", 1.04, f"{n} cutting inside as an inverted winger shoots from better angles")
                apply("exp", 1.02, f"{n} leaves the flank less protected when cutting inside")
            elif role == "ball-playing-defender":
                if opp_t.pressing in ("high", "counter-press"):
                    apply("pos", 0.02, f"{n} as a ball-playing defender breaks their press")
                    apply("exp", 1.03, f"{n} risks losing the ball to their press")
                else:
                    apply("att", 1.02, f"{n} progresses the ball from the back unopposed")
                    apply("pos", 0.02, f"{n} as a ball-playing defender keeps possession")
            elif role == "wing-back":
                apply("att", 1.04, f"{n} as a wing-back adds width in attack")
                apply("exp", 1.04, f"{n} pushing on leaves space behind on that side")
                apply("fat", 1.02, f"{n}'s wing-back role is tiring")
            elif role == "box-to-box":
                apply("att", 1.03, f"{n} arriving late in the box as a box-to-box midfielder")
                apply("fat", 1.03, f"{n}'s box-to-box running is tiring")
            elif role == "anchor":
                apply("exp", 0.96, f"{n} sitting as an anchor screens the back line")
                apply("att", 0.98, f"{n} as an anchor adds less going forward")
            elif role == "playmaker":
                apply("pos", 0.02, f"{n} as a playmaker dictates the tempo")
                apply("q", 1 + (p.eff("pas") - 70) / 400, f"{n}'s passing {'unlocks' if p.pas > 70 else 'struggles to unlock'} the defence")

    # set pieces: routine vs their marking scheme, scaled by aerial strength
    spa, spd = t.set_piece_attack, opp_t.set_piece_defence
    air = (own["aerial"] / max(opp["aerial"], 1)) ** 2
    m["sp_q"] *= air
    if spa == "near-post":
        apply("sp_q", 1.15 if spd == "zonal" else 1.03, "near-post corners " + ("attack the gap in their zonal set-up" if spd == "zonal" else "flick on at the near post"))
    elif spa == "aerial":
        apply("sp_q", air ** 0.5 * (1.05 if spd != "man" else 0.95),
              "aerial set-piece routines " + ("use your height advantage" if air > 1 else "struggle against taller defenders"))
    elif spa == "short":
        apply("sp_rate", 0.85, "short corners sometimes come to nothing")
        apply("sp_q", 1.12 if spd == "man" else 1.04, "short routines " + ("drag man-markers out of shape" if spd == "man" else "create a better crossing angle"))
    if t.set_piece_defence == "man":
        apply("sp_expq", 0.94 if opp["aerial"] <= own["aerial"] else 1.06, "man-marking at set pieces " + ("matches up well" if opp["aerial"] <= own["aerial"] else "loses aerial duels against their taller players"))
    elif t.set_piece_defence == "mixed":
        apply("sp_expq", 0.97, "mixed zonal/man set-piece defence covers both zones and runners")

    form = {"5-3-2": (0.90, 0.88), "4-5-1": (0.93, 0.93), "3-4-3": (1.05, 1.08),
            "3-5-2": (1.0, 1.02), "4-4-2": (1.0, 1.0), "4-3-3": (1.0, 1.0), "4-2-3-1": (1.0, 0.97)}
    a, e = form[t.formation]
    if a != 1.0: apply("att", a, f"the {t.formation} shape commits {'more' if a > 1 else 'fewer'} players forward")
    if e != 1.0: apply("exp", e, f"the {t.formation} shape is {'more' if e < 1 else 'less'} protective at the back")
    return m, f
