"""Read-side analytics: everything the API serves, computed from the an_* tables (cheap indexed queries).

Every comparison carries its sample sizes and an `available` flag; below the minimum it says why instead of
showing a number (docs/METRICS.md).
"""
from __future__ import annotations

import sqlite3
from datetime import date, timedelta

from . import rules
from .periods import Period
from .stats import median, theil_sen

MIN_GYM_SESSIONS_TO_COMPARE = 2
MIN_STRENGTH_EXERCISES = 3
TREND_SESSIONS, TREND_MIN_SESSIONS, TREND_MIN_SPAN_DAYS = 8, 4, 21
BASELINE_WEEKS = 12
USUAL_MIN_WEEKLY_SETS = 2.0
DAY_MS = 86_400_000


def r1(x):
    return None if x is None else round(x, 1)


def r2(x):
    return None if x is None else round(x, 2)


def pct(cur, prev):
    if cur is None or prev in (None, 0):
        return None
    return round((cur - prev) / prev * 100, 1)


def first_date(conn) -> date | None:
    v = conn.execute("SELECT min(local_date) FROM an_days").fetchone()[0]
    return date.fromisoformat(v) if v else None


def titles(conn) -> dict:
    return {r[0]: r[1] for r in conn.execute("SELECT id, title FROM exercise_templates")}


# ---------------------------------------------------------------- totals and comparisons

TOTAL_FIELDS = ("workouts", "gym_sessions", "cardio_sessions", "duration_s", "gym_duration_s", "working_sets", "reps",
                "volume_kg", "distance_m", "prs", "meaningful_prs")


def totals(conn, start: date, end: date) -> dict:
    row = conn.execute("SELECT " + ", ".join(f"coalesce(sum({f}), 0)" for f in TOTAL_FIELDS) + ", count(*) "
                       "FROM an_days WHERE local_date BETWEEN ? AND ?", (start.isoformat(), end.isoformat())).fetchone()
    out = dict(zip(TOTAL_FIELDS, row[:-1]))
    out["active_days"] = row[-1]
    days = (end - start).days + 1
    out["days"] = days
    out["workouts_per_week"] = r2(out["workouts"] / days * 7)
    out["gym_sessions_per_week"] = r2(out["gym_sessions"] / days * 7)
    out["volume_kg"], out["reps"], out["distance_m"] = r1(out["volume_kg"]), r1(out["reps"]), r1(out["distance_m"])
    return out


def compare(cur: dict, prev: dict | None) -> dict:
    if prev is None:
        return {"available": False, "reason": "no previous period"}
    if cur["gym_sessions"] < MIN_GYM_SESSIONS_TO_COMPARE or prev["gym_sessions"] < MIN_GYM_SESSIONS_TO_COMPARE:
        return {"available": False, "reason": f"needs at least {MIN_GYM_SESSIONS_TO_COMPARE} gym sessions in both periods",
                "gym_sessions": [cur["gym_sessions"], prev["gym_sessions"]]}
    return {"available": True, **{f: pct(cur[f], prev[f]) for f in
            ("workouts", "gym_sessions", "duration_s", "gym_duration_s", "working_sets", "reps", "volume_kg", "distance_m")}}


def _best_e1rm_by_exercise(conn, start: date, end: date) -> dict:
    return {r[0]: {"best": r[1], "sessions": r[2]} for r in conn.execute(
        "SELECT template_id, max(best_e1rm_kg), count(*) FROM an_exercise_sessions WHERE best_e1rm_kg IS NOT NULL "
        "AND local_date BETWEEN ? AND ? GROUP BY template_id", (start.isoformat(), end.isoformat()))}


def strength_change(conn, period: Period) -> dict:
    if period.prev_start is None:
        return {"available": False, "reason": "no previous period", "exercises": [], "n": 0}
    gym = [totals(conn, period.start, period.end)["gym_sessions"], totals(conn, period.prev_start, period.prev_end)["gym_sessions"]]
    if min(gym) < MIN_GYM_SESSIONS_TO_COMPARE:
        return {"available": False, "reason": f"needs at least {MIN_GYM_SESSIONS_TO_COMPARE} gym sessions in both periods",
                "exercises": [], "n": 0, "gym_sessions": gym}
    cur = _best_e1rm_by_exercise(conn, period.start, period.end)
    prev = _best_e1rm_by_exercise(conn, period.prev_start, period.prev_end)
    names = titles(conn)
    rows = [{"template_id": t, "title": names.get(t, t), "current": r1(cur[t]["best"]), "previous": r1(prev[t]["best"]),
             "change_pct": pct(cur[t]["best"], prev[t]["best"]), "sessions": [cur[t]["sessions"], prev[t]["sessions"]]}
            for t in sorted(set(cur) & set(prev))]
    rows.sort(key=lambda x: -x["change_pct"])
    out = {"exercises": rows, "n": len(rows)}
    if len(rows) < MIN_STRENGTH_EXERCISES:
        return {**out, "available": False,
                "reason": f"needs at least {MIN_STRENGTH_EXERCISES} exercises with an e1RM in both periods (has {len(rows)})"}
    return {**out, "available": True, "median_pct": r1(median([x["change_pct"] for x in rows]))}


def most_improved(strength: dict) -> dict | None:
    eligible = [x for x in strength.get("exercises", []) if min(x["sessions"]) >= 2 and x["change_pct"] > 0]
    return max(eligible, key=lambda x: x["change_pct"]) if eligible else None


def muscles(conn, start: date, end: date, prev_start: date | None = None, prev_end: date | None = None) -> list[dict]:
    q = ("SELECT muscle, sum(sets), sum(volume_kg), sum(primary_sets), sum(workouts) FROM an_muscle_days "
         "WHERE local_date BETWEEN ? AND ? GROUP BY muscle")
    cur = {r[0]: r[1:] for r in conn.execute(q, (start.isoformat(), end.isoformat()))}
    prev = {r[0]: r[1:] for r in conn.execute(q, (prev_start.isoformat(), prev_end.isoformat()))} if prev_start else {}
    on_map_total = sum(v[0] for m, v in cur.items() if m not in rules.NOT_ON_BODY_MAP) or 0
    out = []
    for m in sorted(set(cur) | set(prev)):
        c = cur.get(m, (0, 0, 0, 0))
        p = prev.get(m)
        on_map = m not in rules.NOT_ON_BODY_MAP
        out.append({"muscle": m, "on_map": on_map, "sets": r1(c[0]), "volume_kg": r1(c[1]), "primary_sets": c[2],
                    "workouts": c[3], "share_pct": r1(c[0] / on_map_total * 100) if on_map and on_map_total else None,
                    "previous_sets": r1(p[0]) if p else (0 if prev_start else None),
                    "change_pct": pct(c[0], p[0]) if p else None})
    out.sort(key=lambda x: -(x["sets"] or 0))
    return out


def less_than_usual(conn, period: Period) -> list[dict]:
    """Muscles trained at under half their usual weekly rate (usual = the 12 weeks before the period)."""
    if period.days < 7:
        return []
    b_end = period.start - timedelta(days=1)
    b_start = period.start - timedelta(weeks=BASELINE_WEEKS)
    q = "SELECT muscle, sum(sets) FROM an_muscle_days WHERE local_date BETWEEN ? AND ? GROUP BY muscle"
    base = {r[0]: r[1] / BASELINE_WEEKS for r in conn.execute(q, (b_start.isoformat(), b_end.isoformat()))}
    cur = {r[0]: r[1] / (period.days / 7) for r in conn.execute(q, (period.start.isoformat(), period.end.isoformat()))}
    out = []
    for m, usual in base.items():
        if m in rules.NOT_ON_BODY_MAP or usual < USUAL_MIN_WEEKLY_SETS:
            continue
        now = cur.get(m, 0.0)
        if now < usual * 0.5:
            out.append({"muscle": m, "usual_sets_per_week": r1(usual), "sets_per_week": r1(now), "ratio": r2(now / usual)})
    return sorted(out, key=lambda x: x["ratio"])


# ---------------------------------------------------------------- streaks

def _monday(d: date) -> date:
    return d - timedelta(days=d.weekday())


def streaks(conn, today: date) -> dict:
    out = {}
    for name, cond in (("any", "workouts > 0"), ("gym", "gym_sessions > 0")):
        weeks = sorted({_monday(date.fromisoformat(r[0])) for r in conn.execute(f"SELECT local_date FROM an_days WHERE {cond}")})
        wk = set(weeks)
        this_week = _monday(today)
        cursor = this_week if this_week in wk else this_week - timedelta(weeks=1)   # the current week isn't over
        current = 0
        while cursor in wk:
            current += 1
            cursor -= timedelta(weeks=1)
        longest, run, best_end = 0, 0, None
        for i, w in enumerate(weeks):
            run = run + 1 if i and weeks[i - 1] == w - timedelta(weeks=1) else 1
            if run > longest:
                longest, best_end = run, w
        out[name] = {"current_weeks": current, "longest_weeks": longest,
                     "longest_from": (best_end - timedelta(weeks=longest - 1)).isoformat() if best_end else None,
                     "longest_to": min(best_end + timedelta(days=6), today).isoformat() if best_end else None,   # never in the future
                     "this_week_counts": this_week in wk}
    return out


# ---------------------------------------------------------------- records

def prs(conn, start: date | None = None, end: date | None = None, template_id: str | None = None,
        meaningful_only: bool = False, limit: int = 50, workout_id: str | None = None) -> list[dict]:
    where, args = [], []
    if workout_id:
        where.append("p.workout_id = ?"); args.append(workout_id)
    if start:
        where.append("p.local_date >= ?"); args.append(start.isoformat())
    if end:
        where.append("p.local_date <= ?"); args.append(end.isoformat())
    if template_id:
        where.append("p.template_id = ?"); args.append(template_id)
    if meaningful_only:
        where.append("p.meaningful = 1")
    sql = ("SELECT p.*, t.title FROM an_prs p LEFT JOIN exercise_templates t ON t.id = p.template_id"
           + (" WHERE " + " AND ".join(where) if where else "") + " ORDER BY p.start_ms DESC, p.id DESC LIMIT ?")
    out = []
    for r in conn.execute(sql, (*args, max(1, min(limit, 5000)))):
        d = dict(r)
        d["value"], d["previous"] = r2(d["value"]), r2(d["previous"])
        d["improvement_pct"] = pct(r["value"], r["previous"])
        d["title"] = d["title"] or "Whole workout"
        out.append(d)
    return out


# ---------------------------------------------------------------- exercises

def trend(conn, template_id: str, until_ms: int | None = None) -> dict:
    rows = conn.execute("SELECT start_ms, best_e1rm_kg FROM an_exercise_sessions WHERE template_id = ? AND best_e1rm_kg IS NOT NULL "
                        "AND start_ms <= ? ORDER BY start_ms DESC LIMIT ?",
                        (template_id, until_ms if until_ms is not None else 2**62, TREND_SESSIONS)).fetchall()[::-1]
    n = len(rows)
    if n < TREND_MIN_SESSIONS:
        return {"available": False, "reason": f"needs {TREND_MIN_SESSIONS} sessions with an e1RM (has {n})", "sessions": n}
    span = (rows[-1][0] - rows[0][0]) / DAY_MS
    if span < TREND_MIN_SPAN_DAYS:
        return {"available": False, "reason": f"sessions need to span {TREND_MIN_SPAN_DAYS} days (span {span:.0f})", "sessions": n}
    pts = [((ms - rows[0][0]) / DAY_MS, v) for ms, v in rows]
    slope = theil_sen(pts)
    mid = median([v for _, v in pts])
    return {"available": True, "sessions": n, "span_days": round(span), "pct_per_month": r1(slope * 30.44 / mid * 100),
            "kg_per_month": r2(slope * 30.44)}


def exercises(conn, start: date, end: date) -> list[dict]:
    life = {r[0]: dict(r) for r in conn.execute(
        "SELECT template_id, count(*) AS sessions, max(best_e1rm_kg) AS best_e1rm, min(local_date) AS first, "
        "max(local_date) AS last FROM an_exercise_sessions GROUP BY template_id")}
    rows = conn.execute(
        "SELECT s.template_id, t.title, t.primary_muscle, t.equipment, max(s.measure) AS measure, count(*) AS sessions, "
        "sum(s.working_sets) AS working_sets, sum(s.volume_kg) AS volume_kg, max(s.best_e1rm_kg) AS best_e1rm, "
        "max(s.top_weight_kg) AS top_weight, sum(s.reps) AS reps, sum(s.total_distance_m) AS distance_m, "
        "sum(s.total_duration_s) AS duration_s, max(s.local_date) AS last FROM an_exercise_sessions s "
        "JOIN exercise_templates t ON t.id = s.template_id WHERE s.local_date BETWEEN ? AND ? "
        "GROUP BY s.template_id ORDER BY sessions DESC, working_sets DESC", (start.isoformat(), end.isoformat())).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        lt = life.get(r["template_id"], {})
        d.update(volume_kg=r1(d["volume_kg"]), best_e1rm=r1(d["best_e1rm"]), reps=r1(d["reps"]),
                 distance_m=r1(d["distance_m"]), lifetime_sessions=lt.get("sessions"), lifetime_best_e1rm=r1(lt.get("best_e1rm")),
                 first=lt.get("first"))
        out.append(d)
    return out


def exercise_detail(conn, template_id: str) -> dict | None:
    t = conn.execute("SELECT * FROM exercise_templates WHERE id = ?", (template_id,)).fetchone()
    if t is None:
        return None
    sessions = [dict(r) for r in conn.execute(
        "SELECT s.*, w.title AS workout_title FROM an_exercise_sessions s JOIN workouts w ON w.id = s.workout_id "
        "WHERE s.template_id = ? ORDER BY s.start_ms", (template_id,))]
    if not sessions:
        return {"template": dict(t), "sessions": [], "lifetime": None}
    pr_rows = prs(conn, template_id=template_id, limit=5000)
    pr_by_we = {}
    for p in pr_rows:
        pr_by_we.setdefault(p["we_id"], []).append(p["category"])
    rpe = conn.execute("SELECT avg(rpe), count(rpe) FROM an_sets WHERE template_id = ? AND working = 1 AND rpe IS NOT NULL",
                       (template_id,)).fetchone()
    best_set = conn.execute("SELECT * FROM an_sets WHERE template_id = ? AND e1rm_kg IS NOT NULL ORDER BY e1rm_kg DESC, start_ms LIMIT 1",
                            (template_id,)).fetchone()
    heaviest = conn.execute("SELECT * FROM an_sets WHERE template_id = ? AND working = 1 AND suspect IS NULL AND weight_kg > 0 "
                            "ORDER BY weight_kg DESC, reps DESC, start_ms LIMIT 1", (template_id,)).fetchone()
    first, last = sessions[0], sessions[-1]
    weeks = max(1.0, (last["start_ms"] - first["start_ms"]) / DAY_MS / 7)
    positions = {"1st": 0, "2nd": 0, "3rd": 0, "4th or later": 0}
    for s in sessions:
        positions[["1st", "2nd", "3rd"][s["position"]] if s["position"] < 3 else "4th or later"] += 1
    best_session = max(sessions, key=lambda s: s["volume_kg"])
    lifetime = {
        "sessions": len(sessions), "first": first["local_date"], "last": last["local_date"],
        "working_sets": sum(s["working_sets"] for s in sessions), "reps": r1(sum(s["reps"] for s in sessions)),
        "volume_kg": r1(sum(s["volume_kg"] for s in sessions)),
        "distance_m": r1(sum(s["total_distance_m"] for s in sessions)), "duration_s": r1(sum(s["total_duration_s"] for s in sessions)),
        "best_e1rm": {"kg": r1(best_set["e1rm_kg"]), "weight_kg": best_set["weight_kg"], "reps": best_set["reps"],
                      "date": best_set["local_date"], "workout_id": best_set["workout_id"]} if best_set else None,
        "heaviest": {"weight_kg": heaviest["weight_kg"], "reps": heaviest["reps"], "date": heaviest["local_date"],
                     "workout_id": heaviest["workout_id"]} if heaviest else None,
        "best_session_volume": {"kg": r1(best_session["volume_kg"]), "date": best_session["local_date"],
                                "workout_id": best_session["workout_id"]} if best_session["volume_kg"] else None,
        "max_reps": max((s["max_reps"] or 0 for s in sessions), default=None) or None,
        "longest_hold_s": max((s["max_duration_s"] or 0 for s in sessions), default=None) or None,
        "records": len(pr_rows), "meaningful_records": sum(p["meaningful"] for p in pr_rows),
        "avg_rpe": r1(rpe[0]), "rpe_sets": rpe[1], "sessions_per_week": r2(len(sessions) / weeks),
        "positions": positions, "typical_position": int(median([s["position"] for s in sessions])) + 1,
    }
    series = [{"date": s["local_date"], "start_ms": s["start_ms"], "workout_id": s["workout_id"], "workout_title": s["workout_title"],
               "position": s["position"] + 1, "of": s["exercises_in_workout"], "best_e1rm": r1(s["best_e1rm_kg"]),
               "top_weight": s["top_weight_kg"], "top_reps": s["top_weight_reps"], "volume_kg": r1(s["volume_kg"]),
               "working_sets": s["working_sets"], "reps": r1(s["reps"]), "max_reps": s["max_reps"],
               "distance_m": r1(s["total_distance_m"]), "duration_s": r1(s["total_duration_s"]), "avg_rpe": r1(s["avg_rpe"]),
               "records": sorted(pr_by_we.get(s["we_id"], []))} for s in sessions]
    secondary = [r[0] for r in conn.execute("SELECT muscle FROM template_muscles WHERE template_id = ? AND role = 'secondary' "
                                            "ORDER BY muscle", (template_id,))]
    tpl = dict(t)
    tpl["secondary_muscles"] = secondary
    tpl["measure"] = last["measure"]
    tr = trend(conn, template_id) if tpl["measure"] == "load_reps" else \
        {"available": False, "reason": "trends use estimated 1RM, which only load × reps exercises have"}
    return {"template": tpl, "lifetime": lifetime, "trend": tr, "series": series, "records": pr_rows[:100]}


# ---------------------------------------------------------------- workouts and calendar

def workouts(conn, start: date, end: date, limit: int = 500) -> list[dict]:
    return [dict(r) for r in conn.execute(
        "SELECT a.*, w.title, w.start_utc FROM an_workouts a JOIN workouts w ON w.id = a.workout_id "
        "WHERE a.local_date BETWEEN ? AND ? ORDER BY a.start_ms DESC LIMIT ?", (start.isoformat(), end.isoformat(), limit))]


def workout_detail(conn, workout_id: str) -> dict | None:
    w = conn.execute("SELECT w.id, w.title, w.description, w.routine_id, w.start_utc, w.end_utc, a.* FROM workouts w "
                     "JOIN an_workouts a ON a.workout_id = w.id WHERE w.id = ?", (workout_id,)).fetchone()
    if w is None:
        return None
    exercises_out = []
    for e in conn.execute("SELECT e.id, e.position, e.template_id, e.title, e.notes, e.superset_id, s.working_sets, s.volume_kg, "
                          "s.best_e1rm_kg, s.session_index, s.measure, t.primary_muscle FROM workout_exercises e "
                          "JOIN an_exercise_sessions s ON s.we_id = e.id LEFT JOIN exercise_templates t ON t.id = e.template_id "
                          "WHERE e.workout_id = ? ORDER BY e.position", (workout_id,)):
        sets = [dict(r) for r in conn.execute("SELECT set_id, set_position, set_type, working, weight_kg, reps, distance_m, duration_s, "
                                              "rpe, volume_kg, e1rm_kg, e1rm_note, suspect FROM an_sets WHERE we_id = ? "
                                              "ORDER BY set_position", (e["id"],))]
        for s in sets:
            s["e1rm_kg"] = r1(s["e1rm_kg"])
        d = dict(e)
        d.update(volume_kg=r1(d["volume_kg"]), best_e1rm_kg=r1(d["best_e1rm_kg"]), sets=sets)
        exercises_out.append(d)
    muscle_rows = conn.execute(
        "SELECT m.muscle, sum(CASE m.role WHEN 'primary' THEN 1.0 ELSE ? END * s.working_sets) FROM an_exercise_sessions s "
        "JOIN template_muscles m ON m.template_id = s.template_id WHERE s.workout_id = ? GROUP BY m.muscle ORDER BY 2 DESC",
        (rules.SECONDARY_CREDIT, workout_id)).fetchall()
    kind = w["kind"]
    avg = conn.execute("SELECT avg(duration_s), avg(working_sets), avg(volume_kg), count(*) FROM an_workouts WHERE kind = ? "
                       "AND start_ms < ?", (kind, w["start_ms"])).fetchone()
    d = dict(w)
    d.update(volume_kg=r1(d["volume_kg"]), avg_rpe=r1(d["avg_rpe"]), exercises=exercises_out,
             muscles=[{"muscle": m, "sets": r1(v)} for m, v in muscle_rows if v],
             records=prs(conn, workout_id=workout_id, limit=500),
             compared_with_earlier={"kind": kind, "workouts": avg[3], "avg_duration_s": r1(avg[0]),
                                    "avg_working_sets": r1(avg[1]), "avg_volume_kg": r1(avg[2])} if avg[3] else None)
    return d


def calendar_days(conn, start: date, end: date) -> list[dict]:
    return [dict(r) for r in conn.execute("SELECT * FROM an_days WHERE local_date BETWEEN ? AND ? ORDER BY local_date",
                                          (start.isoformat(), end.isoformat()))]


# ---------------------------------------------------------------- the summary behind the homepage

def summary(conn, period: Period, today: date) -> dict:
    cur = totals(conn, period.start, period.end)
    prev = totals(conn, period.prev_start, period.prev_end) if period.prev_start else None
    strength = strength_change(conn, period)
    change = compare(cur, prev)
    muscle_rows = muscles(conn, period.start, period.end, period.prev_start, period.prev_end)
    if not change["available"]:                       # same minimum-sample rule as every other comparison
        for m in muscle_rows:
            m["change_pct"] = None
    last = conn.execute("SELECT a.workout_id, a.local_date, a.kind, w.title FROM an_workouts a JOIN workouts w ON w.id = a.workout_id "
                        "ORDER BY a.start_ms DESC LIMIT 1").fetchone()
    last_gym = conn.execute("SELECT local_date FROM an_workouts WHERE kind = 'gym' ORDER BY start_ms DESC LIMIT 1").fetchone()
    return {
        "period": period.as_dict(), "today": today.isoformat(),
        "totals": cur, "previous_totals": prev, "change": change,
        "strength": strength, "most_improved": most_improved(strength),
        "muscles": muscle_rows,
        # With no gym sessions at all, every muscle is "less than usual": say that once instead of listing them all.
        "trained_less_than_usual": less_than_usual(conn, period) if cur["gym_sessions"] else [],
        "no_gym_sessions": cur["gym_sessions"] == 0,
        "records": prs(conn, period.start, period.end, limit=20),
        "streaks": streaks(conn, today),
        "last_workout": dict(last) if last else None,
        "days_since_last_workout": (today - date.fromisoformat(last["local_date"])).days if last else None,
        "days_since_last_gym_session": (today - date.fromisoformat(last_gym[0])).days if last_gym else None,
    }


# ---------------------------------------------------------------- time series for charts

SERIES_FIELDS = ("workouts", "gym_sessions", "cardio_sessions", "duration_s", "gym_duration_s", "working_sets", "reps",
                 "volume_kg", "distance_m", "prs", "meaningful_prs")


def _bucket_start(d: date, bucket: str) -> date:
    if bucket == "week":
        return d - timedelta(days=d.weekday())
    if bucket == "month":
        return d.replace(day=1)
    return d


def _next_bucket(d: date, bucket: str) -> date:
    if bucket == "week":
        return d + timedelta(weeks=1)
    if bucket == "month":
        return (d.replace(day=28) + timedelta(days=4)).replace(day=1)
    return d + timedelta(days=1)


def series(conn, start: date, end: date, bucket: str = "week") -> list[dict]:
    """Totals per day / week (Monday) / month between start and end, with empty buckets filled with zeros."""
    if bucket not in ("day", "week", "month"):
        raise ValueError("bucket must be day, week or month")
    acc: dict[date, dict] = {}
    cursor = _bucket_start(start, bucket)
    while cursor <= end:
        acc[cursor] = {"start": cursor.isoformat(), **{f: 0 for f in SERIES_FIELDS}}
        cursor = _next_bucket(cursor, bucket)
    for r in conn.execute("SELECT local_date, " + ", ".join(SERIES_FIELDS) + " FROM an_days WHERE local_date BETWEEN ? AND ?",
                          (start.isoformat(), end.isoformat())):
        b = acc[_bucket_start(date.fromisoformat(r[0]), bucket)]
        for i, f in enumerate(SERIES_FIELDS, start=1):
            b[f] += r[i]
    out = list(acc.values())
    for b in out:
        b["volume_kg"], b["reps"], b["distance_m"] = r1(b["volume_kg"]), r1(b["reps"]), r1(b["distance_m"])
    return out
