"""The data explorer: any slice of your sets, with totals, distributions and a page of rows.

Filters arrive from the URL; every value is passed as an SQL parameter and the sort column comes from a whitelist.
"""
from __future__ import annotations

from datetime import date, timedelta

from .queries import r1, r2

SORTS = {"date": "a.start_ms DESC, a.ex_position, a.set_position", "weight": "a.weight_kg DESC", "reps": "a.reps DESC",
         "e1rm": "a.e1rm_kg DESC", "rpe": "a.rpe DESC", "volume": "a.volume_kg DESC"}
SET_TYPES = {"working": "a.working = 1", "warmup": "a.set_type = 'warmup'", "normal": "a.set_type = 'normal'",
             "failure": "a.set_type = 'failure'", "dropset": "a.set_type = 'dropset'", "all": "1 = 1"}
NUMERIC = {"weight": "a.weight_kg", "reps": "a.reps", "rpe": "a.rpe", "minutes": "(w.duration_s / 60.0)"}
MAX_PAGE = 500


def _filters(f: dict) -> tuple[str, list]:
    where, args = ["a.local_date BETWEEN ? AND ?"], [f["start"], f["end"]]
    where.append(SET_TYPES.get(f.get("set_type") or "working", SET_TYPES["working"]))
    if f.get("exercises"):
        ids = [x for x in f["exercises"] if x][:50]
        where.append(f"a.template_id IN ({','.join('?' * len(ids))})"); args += ids
    if f.get("muscle"):
        role = " AND role = 'primary'" if f.get("primary_only") else ""
        where.append(f"a.template_id IN (SELECT template_id FROM template_muscles WHERE muscle = ?{role})"); args.append(f["muscle"])
    if f.get("kind") in ("gym", "cardio"):
        where.append("w.kind = ?"); args.append(f["kind"])
    for key, expr in NUMERIC.items():
        for bound, op in (("min", ">="), ("max", "<=")):
            v = f.get(f"{key}_{bound}")
            if v is not None:
                where.append(f"{expr} {op} ?"); args.append(float(v))
    if not f.get("include_suspect"):
        where.append("a.suspect IS NULL")
    return " WHERE " + " AND ".join(where), args


FROM = " FROM an_sets a JOIN an_workouts w ON w.workout_id = a.workout_id JOIN exercise_templates t ON t.id = a.template_id"


def explore(conn, f: dict) -> dict:
    where, args = _filters(f)
    agg = conn.execute("SELECT count(*), count(DISTINCT a.workout_id), count(DISTINCT a.template_id), sum(a.reps), sum(a.volume_kg), "
                       "avg(a.weight_kg), avg(a.reps), avg(a.rpe), count(a.rpe), max(a.e1rm_kg), max(a.weight_kg)" + FROM + where, args).fetchone()
    totals = {"sets": agg[0], "workouts": agg[1], "exercises": agg[2], "reps": r1(agg[3]), "volume_kg": r1(agg[4]), "avg_weight_kg": r1(agg[5]),
              "avg_reps": r1(agg[6]), "avg_rpe": r1(agg[7]), "rpe_sets": agg[8], "best_e1rm_kg": r1(agg[9]), "max_weight_kg": agg[10]}
    reps_hist = {int(r[0]): r[1] for r in conn.execute("SELECT min(CAST(a.reps AS INTEGER), 30), count(*)" + FROM + where +
                                                      " AND a.reps IS NOT NULL GROUP BY 1", args)}
    wmax = agg[10] or 0
    step = next((s for s in (1, 2.5, 5, 10, 20, 25, 50) if wmax / s <= 40), 100)
    weight_hist = [{"from": r[0] * step, "to": (r[0] + 1) * step, "sets": r[1]} for r in conn.execute(
        "SELECT CAST(a.weight_kg / ? AS INTEGER), count(*)" + FROM + where + " AND a.weight_kg > 0 GROUP BY 1 ORDER BY 1", [step] + args)]
    weekly: dict[str, list] = {}
    for d, n, v in conn.execute("SELECT a.local_date, count(*), sum(a.volume_kg)" + FROM + where + " GROUP BY a.local_date", args):
        dd = date.fromisoformat(d)
        k = (dd - timedelta(days=dd.weekday())).isoformat()
        cur = weekly.setdefault(k, [0, 0.0])
        cur[0] += n; cur[1] += v or 0
    sort = SORTS.get(f.get("sort") or "date", SORTS["date"])
    limit = max(1, min(int(f.get("limit") or 100), MAX_PAGE))
    offset = max(0, int(f.get("offset") or 0))
    cols = ("local_date", "workout_id", "workout", "template_id", "exercise", "set_type", "weight_kg", "reps", "distance_m", "duration_s",
            "rpe", "volume_kg", "e1rm_kg", "position", "suspect")
    page = [dict(zip(cols, r)) for r in conn.execute(
        "SELECT a.local_date, a.workout_id, (SELECT title FROM workouts WHERE id = a.workout_id), a.template_id, t.title, a.set_type, a.weight_kg, "
        "a.reps, a.distance_m, a.duration_s, a.rpe, a.volume_kg, round(a.e1rm_kg, 1), a.ex_position + 1, a.suspect" + FROM + where +
        f" ORDER BY {sort} NULLS LAST LIMIT ? OFFSET ?", args + [limit, offset])]
    # weight vs reps, evenly thinned to at most 2,000 points
    pts = conn.execute("SELECT a.weight_kg, a.reps, a.local_date" + FROM + where + " AND a.weight_kg > 0 AND a.reps > 0 ORDER BY a.start_ms", args).fetchall()
    stride = max(1, len(pts) // 2000)
    scatter = [{"w": p[0], "r": p[1], "d": p[2]} for p in pts[::stride]]
    return {"totals": totals, "reps_histogram": [{"reps": k, "sets": reps_hist[k]} for k in sorted(reps_hist)], "weight_histogram": weight_hist,
            "weight_step": step, "weekly": [{"start": k, "sets": v[0], "volume_kg": r1(v[1])} for k, v in sorted(weekly.items())],
            "rows": page, "offset": offset, "limit": limit, "scatter": scatter, "scatter_thinned": stride > 1}
