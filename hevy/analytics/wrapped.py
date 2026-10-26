"""Year in Training ("Wrapped") for any period, and the playful landmarks (docs/METRICS.md, "Wrapped").

Every card is a plain fact from the an_* tables. Landmarks divide your totals by published approximate masses and
distances; the maths is exact, the reference figures are rounded and labelled approximate.
"""
from __future__ import annotations

import math
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from . import rules
from .insights import dna
from .queries import pct, r1, titles

# (name, kg, note): rounded public figures. Ordered small to absurd; the picker chooses whatever gives a readable count.
MASSES = [
    ("tigers", 220, "a large adult, about 220 kg"),
    ("grizzly bears", 300, "about 300 kg"),
    ("grand pianos", 480, "about 480 kg"),
    ("horses", 500, "about 500 kg"),
    ("dairy cows", 700, "about 700 kg"),
    ("giraffes", 1_200, "about 1.2 t"),
    ("family cars", 1_500, "about 1.5 t"),
    ("hippos", 1_500, "about 1.5 t"),
    ("white rhinos", 2_300, "about 2.3 t"),
    ("African elephants", 6_000, "an adult, about 6 t"),
    ("Tyrannosaurus rexes", 8_000, "estimated about 8 t"),
    ("London double-decker buses", 12_500, "about 12.5 t"),
    ("humpback whales", 30_000, "about 30 t"),
    ("Boeing 737s", 41_000, "about 41 t empty"),
    ("Challenger 2 tanks", 62_500, "about 62.5 t"),
    ("Space Shuttle orbiters", 78_000, "about 78 t empty"),
    ("blue whales", 150_000, "a large adult, about 150 t"),
    ("Boeing 747s", 180_000, "about 180 t empty"),
    ("Statues of Liberty", 204_000, "about 204 t"),
    ("Airbus A380s", 277_000, "about 277 t empty"),
    ("International Space Stations", 420_000, "about 420 t"),
    ("Saturn V rockets", 2_970_000, "fuelled for launch, about 2,970 t"),
    ("Eiffel Towers", 7_300_000, "the iron structure, about 7,300 t"),
    ("Titanics", 52_300_000, "about 52,300 t displacement"),
    ("Empire State Buildings", 331_000_000, "about 331,000 t"),
    ("Great Pyramids of Giza", 5_900_000_000, "about 5.9 million t"),
]
DISTANCES = [
    ("parkruns", 5.0, "5 km"),
    ("half marathons", 21.0975, "21.1 km"),
    ("crossings of the Strait of Dover", 33.3, "about 33 km"),
    ("marathons", 42.195, "42.195 km"),
    ("trips from London to Brighton", 76, "about 76 km as the crow flies"),
    ("trips from London to Paris", 344, "about 344 km as the crow flies"),
    ("Land's End to John o' Groats journeys", 1_407, "about 1,407 km by road"),
    ("trips from London to New York", 5_570, "about 5,570 km as the crow flies"),
    ("trips around the Earth", 40_075, "40,075 km at the equator"),
    ("trips to the Moon", 384_400, "about 384,400 km"),
]
# Things no lifetime of training comes near: shown as "at your pace, this would take N years"
IMPOSSIBLE_MASS = [("the Great Pyramid of Giza", 5.9e9), ("all the water in Lake Windermere", 3.1e11), ("the Moon", 7.342e22)]
IMPOSSIBLE_DIST = [("the Moon", 384_400), ("the Sun", 149_600_000)]
SWEET = (1.5, 60.0)          # readable counts: "about 7 elephants", not "0.1 of a 747" or "41,000 tigers"
TARGET = 6.0


def _pick(total, table, unit, seed, count):
    """Comparisons whose count lands in the readable range, closest to ~6 first; `seed` rotates through them."""
    if not total or total <= 0:
        return []
    rows = [(n, size, note, total / size) for n, size, note in table]
    good = [r for r in rows if SWEET[0] <= r[3] <= SWEET[1]] or \
        sorted(rows, key=lambda r: abs(math.log10(r[3]) - math.log10(TARGET)))[:1]
    good.sort(key=lambda r: abs(math.log10(r[3]) - math.log10(TARGET)))
    k = seed % len(good)
    chosen = (good[k:] + good[:k])[:count]
    return [{"thing": n, "count": round(c, 2 if c < 10 else 1 if c < 100 else 0), "each": size, "unit": unit, "note": note}
            for n, size, note, c in chosen]


def _years(x: float) -> str:
    if x < 10:
        return f"{x:.1f} years"
    if x < 1e6:
        return f"{x:,.0f} years"
    if x < 1e9:
        return f"{x / 1e6:,.1f} million years"
    if x < 1e12:
        return f"{x / 1e9:,.1f} billion years"
    e = int(math.floor(math.log10(x)))
    sup = str(e).translate(str.maketrans("0123456789", "⁰¹²³⁴⁵⁶⁷⁸⁹"))
    return f"{x / 10 ** e:.1f} × 10{sup} years (the universe is 13.8 billion years old)"


def landmarks(volume_kg: float, distance_m: float, seed: int = 0, count: int = 3, span_days: float | None = None) -> dict:
    """Readable comparisons for the totals, plus (for a year or more) how long the impossible would take at this pace."""
    out = {"volume": _pick(volume_kg, MASSES, "kg", seed, count), "distance": _pick(distance_m / 1000, DISTANCES, "km", seed, count),
           "perspective": []}
    if span_days and span_days >= 300:
        years = span_days / 365.25
        if volume_kg:
            rate = volume_kg / years
            out["perspective"] += [{"thing": n, "years": _years(m / rate), "kind": "lift"} for n, m in IMPOSSIBLE_MASS]
        if distance_m:
            rate = distance_m / 1000 / years
            out["perspective"] += [{"thing": n, "years": _years(d / rate), "kind": "walk"} for n, d in IMPOSSIBLE_DIST]
    return out


def _streak_weeks(days: list[str]) -> tuple[int, str | None]:
    weeks = sorted({(date.fromisoformat(d) - timedelta(days=date.fromisoformat(d).weekday())) for d in days})
    best, run, end = 0, 0, None
    for i, w in enumerate(weeks):
        run = run + 1 if i and weeks[i - 1] == w - timedelta(weeks=1) else 1
        if run > best:
            best, end = run, w
    return best, (end - timedelta(weeks=best - 1)).isoformat() if end else None


def wrapped(conn, start: date, end: date, tz: str = "Europe/London", seed: int = 0,
            prev_start: date | None = None, prev_end: date | None = None, today: date | None = None) -> dict:
    a, b = start.isoformat(), end.isoformat()
    names = titles(conn)
    W = [dict(r) for r in conn.execute("SELECT * FROM an_workouts WHERE local_date BETWEEN ? AND ? ORDER BY start_ms", (a, b))]
    if not W:
        return {"start": a, "end": b, "empty": True}
    first_ever = date.fromisoformat(conn.execute("SELECT min(local_date) FROM an_workouts").fetchone()[0])
    covered_from = max(start, first_ever)
    covered_to = min(end, today or end)
    span_days = (covered_to - covered_from).days + 1
    gym = [w for w in W if w["kind"] == "gym"]
    tot = lambda k, ws=W: sum(w[k] or 0 for w in ws)
    out = {"start": a, "end": b, "empty": False, "span_days": span_days,
           "coverage": {"history_starts": first_ever.isoformat(), "partial": (first_ever - start).days > 31,
                        "covered_from": covered_from.isoformat(), "covered_to": covered_to.isoformat()}}
    out["totals"] = {"workouts": len(W), "gym_sessions": len(gym), "cardio_sessions": len(W) - len(gym), "active_days": len({w["local_date"] for w in W}),
                     "hours": r1(tot("duration_s") / 3600), "gym_hours": r1(tot("duration_s", gym) / 3600), "working_sets": tot("working_sets"),
                     "reps": r1(tot("reps")), "volume_kg": r1(tot("volume_kg")), "distance_km": r1(tot("distance_m") / 1000),
                     "records": tot("meaningful_prs")}
    # strongest lift and biggest record
    s = conn.execute("SELECT template_id, e1rm_kg, weight_kg, reps, local_date, workout_id FROM an_sets WHERE e1rm_kg IS NOT NULL "
                     "AND local_date BETWEEN ? AND ? ORDER BY e1rm_kg DESC LIMIT 1", (a, b)).fetchone()
    out["strongest_lift"] = {"title": names.get(s[0]), "template_id": s[0], "e1rm": r1(s[1]), "weight_kg": s[2], "reps": s[3], "date": s[4],
                             "workout_id": s[5]} if s else None
    p = conn.execute("SELECT template_id, value, previous, detail, local_date, workout_id FROM an_prs WHERE category = 'e1rm' AND meaningful = 1 "
                     "AND local_date BETWEEN ? AND ? ORDER BY (value - previous) / previous DESC LIMIT 1", (a, b)).fetchone()
    out["biggest_record"] = {"title": names.get(p[0]), "template_id": p[0], "improvement_pct": pct(p[1], p[2]), "detail": p[3], "date": p[4],
                             "workout_id": p[5]} if p else None
    # favourites
    fav = conn.execute("SELECT template_id, count(*) n, max(measure) m FROM an_exercise_sessions WHERE local_date BETWEEN ? AND ? "
                       "GROUP BY template_id ORDER BY n DESC", (a, b)).fetchall()
    gym_fav = next((r for r in fav if r["m"] != "cardio"), None)
    cardio_fav = next((r for r in fav if r["m"] == "cardio"), None)
    out["favourite_exercise"] = {"title": names.get(gym_fav[0]), "template_id": gym_fav[0], "sessions": gym_fav[1]} if gym_fav else None
    out["favourite_cardio"] = {"title": names.get(cardio_fav[0]), "template_id": cardio_fav[0], "sessions": cardio_fav[1]} if cardio_fav else None
    mus = conn.execute("SELECT muscle, sum(sets) FROM an_muscle_days WHERE local_date BETWEEN ? AND ? GROUP BY muscle ORDER BY 2 DESC", (a, b)).fetchall()
    top = next((m for m in mus if m[0] not in rules.NOT_ON_BODY_MAP), None)
    out["most_trained_muscle"] = {"muscle": top[0], "sets": r1(top[1])} if top else None
    months = Counter(w["local_date"][:7] for w in W)
    m, n = max(months.items(), key=lambda kv: (kv[1], sum(w["volume_kg"] for w in W if w["local_date"].startswith(kv[0]))))
    out["most_active_month"] = {"month": m, "workouts": n} if span_days > 62 else None
    weeks = Counter((date.fromisoformat(w["local_date"]) - timedelta(days=date.fromisoformat(w["local_date"]).weekday())).isoformat() for w in W)
    wk, wn = max(weeks.items(), key=lambda kv: (kv[1], kv[0]))
    out["busiest_week"] = {"week": wk, "workouts": wn, "volume_kg": r1(sum(w["volume_kg"] for w in W
                           if wk <= w["local_date"] <= (date.fromisoformat(wk) + timedelta(days=6)).isoformat()))}
    if (end - start).days + 1 >= 700:                  # the period asked for, even if your history covers less of it
        per = {}
        for w in W:
            y = per.setdefault(w["local_date"][:4], {"year": w["local_date"][:4], "workouts": 0, "gym_sessions": 0, "volume_kg": 0.0,
                                                     "hours": 0.0, "distance_km": 0.0, "records": 0})
            y["workouts"] += 1; y["gym_sessions"] += w["kind"] == "gym"; y["volume_kg"] += w["volume_kg"]
            y["hours"] += (w["duration_s"] or 0) / 3600; y["distance_km"] += w["distance_m"] / 1000; y["records"] += w["meaningful_prs"]
        years = sorted(per.values(), key=lambda y: y["year"])
        for y in years:
            y["volume_kg"], y["hours"], y["distance_km"] = r1(y["volume_kg"]), r1(y["hours"]), r1(y["distance_km"])
        out["years"] = years
        out["best_year"] = max(years, key=lambda y: y["volume_kg"])["year"] if years else None
    else:
        out["years"], out["best_year"] = None, None
    if prev_start and prev_end:
        from .queries import compare, totals
        out["vs_before"] = {**compare(totals(conn, start, end), totals(conn, prev_start, prev_end)),
                            "prev_start": prev_start.isoformat(), "prev_end": prev_end.isoformat()}
    else:
        out["vs_before"] = None
    zone = ZoneInfo(tz)
    local = [datetime.fromtimestamp(w["start_ms"] / 1000, timezone.utc).astimezone(zone) for w in gym or W]
    wd = Counter(t.strftime("%A") for t in local).most_common(1)[0]
    part = lambda hr: "morning" if 5 <= hr < 12 else "afternoon" if hr < 17 else "evening" if hr < 22 else "night"
    tod = Counter(part(t.hour) for t in local).most_common(1)[0]
    out["habits"] = {"weekday": wd[0], "weekday_sessions": wd[1], "time_of_day": tod[0], "time_of_day_sessions": tod[1], "of": len(local)}
    new = conn.execute("SELECT template_id FROM an_exercise_sessions WHERE session_index = 1 AND local_date BETWEEN ? AND ? ORDER BY start_ms", (a, b)).fetchall()
    out["new_exercises"] = {"count": len(new), "examples": [names.get(r[0]) for r in new[:6]]}
    weeks, from_ = _streak_weeks([w["local_date"] for w in W])
    out["longest_streak"] = {"weeks": weeks, "from": from_}
    lw = max(gym, key=lambda w: w["duration_s"] or 0, default=None)
    bw = max(gym, key=lambda w: w["volume_kg"], default=None)
    out["longest_workout"] = {"workout_id": lw["workout_id"], "date": lw["local_date"], "minutes": round((lw["duration_s"] or 0) / 60)} if lw else None
    out["biggest_workout"] = {"workout_id": bw["workout_id"], "date": bw["local_date"], "volume_kg": r1(bw["volume_kg"])} if bw and bw["volume_kg"] else None
    # biggest progression: median of the last 3 sessions' best e1RM vs the first 3, exercises with 6+ sessions in the period
    prog = []
    by = {}
    for r in conn.execute("SELECT template_id, best_e1rm_kg FROM an_exercise_sessions WHERE best_e1rm_kg IS NOT NULL AND local_date BETWEEN ? AND ? "
                          "ORDER BY start_ms", (a, b)):
        by.setdefault(r[0], []).append(r[1])
    for tid, xs in by.items():
        if len(xs) >= 6:
            first, last = sorted(xs[:3])[1], sorted(xs[-3:])[1]
            prog.append((pct(last, first), tid, first, last, len(xs)))
    if prog:
        g, tid, first, last, n = max(prog)
        out["biggest_progression"] = {"title": names.get(tid), "template_id": tid, "change_pct": g, "from_e1rm": r1(first), "to_e1rm": r1(last), "sessions": n}
    else:
        out["biggest_progression"] = None
    d = dna(conn, start, end)
    out["dna"] = sorted([t for t in d["traits"] if t["pct"] is not None], key=lambda t: -t["pct"])[:6]
    out["landmarks"] = landmarks(out["totals"]["volume_kg"], out["totals"]["distance_km"] * 1000, seed=seed, span_days=span_days)
    return out
