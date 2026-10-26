"""Builds every derived table from the normalised layer, in one transaction (rules: rules.py, docs/METRICS.md).

A full rebuild is cheap at personal scale (tens of thousands of sets take well under a second or two on a Raspberry Pi), and it
is the simplest way to stay correct: an edit to an old workout can change every later record of that exercise.
"""
from __future__ import annotations

import sqlite3
import time
from collections import defaultdict

from ..db import set_meta
from ..timeutil import iso, utcnow
from . import rules
from .stats import median

TABLES = ("an_sets", "an_exercise_sessions", "an_workouts", "an_prs", "an_muscle_days", "an_days")


def _fmt(x: float | None) -> str:
    if x is None:
        return "–"
    return f"{x:,.1f}".rstrip("0").rstrip(".") if abs(x - round(x)) > 1e-9 else f"{round(x):,}"


def rebuild(conn: sqlite3.Connection) -> dict:
    """Recompute all an_* tables. Call inside a transaction (db.tx())."""
    t0 = time.monotonic()
    templates = {r["id"]: dict(r) for r in conn.execute("SELECT id, title, type, primary_muscle FROM exercise_templates")}
    muscles = defaultdict(list)                          # template -> [(muscle, credit, is_primary)]
    for r in conn.execute("SELECT template_id, muscle, role FROM template_muscles"):
        muscles[r[0]].append((r[1], 1.0 if r[2] == "primary" else rules.SECONDARY_CREDIT, r[2] == "primary"))

    rows = conn.execute(
        "SELECT s.id AS set_id, s.workout_id, s.workout_exercise_id AS we_id, e.template_id, w.local_date, w.start_ms, "
        "e.position AS ex_position, s.position AS set_position, s.set_type, s.weight_kg, s.reps, s.distance_m, "
        "s.duration_s, s.rpe FROM sets s JOIN workout_exercises e ON e.id = s.workout_exercise_id "
        "JOIN workouts w ON w.id = s.workout_id ORDER BY w.start_ms, w.id, e.position, s.position").fetchall()
    sets = [dict(r) for r in rows]

    # ---------------------------------------------------------------- per set
    for s in sets:
        t = templates.get(s["template_id"], {})
        s["measure"] = rules.measure(t.get("type", "unknown"), t.get("primary_muscle"), s)
        s["working"] = 1 if s["set_type"] in rules.WORKING_TYPES else 0
    by_template = defaultdict(list)
    for s in sets:
        if s["working"]:
            by_template[s["template_id"]].append(s)
    typical = {tid: (median([x["reps"] for x in ss if x["reps"]]), median([x["weight_kg"] for x in ss if x["weight_kg"]]), len(ss))
               for tid, ss in by_template.items()}
    for s in sets:
        m, w, r = s["measure"], s["weight_kg"], s["reps"]
        med_r, med_w, n = typical.get(s["template_id"], (None, None, 0))
        s["suspect"] = rules.suspect_reason(m, w, r, med_r, med_w, n) if s["working"] else None
        s["volume_kg"] = w * r if (s["working"] and not s["suspect"] and m in rules.VOLUME_MEASURES and w and r and w > 0 and r > 0) else None
        s["e1rm_kg"], s["e1rm_note"] = None, None
        if m == "load_reps" and s["working"]:
            if s["suspect"]:
                s["e1rm_note"] = "suspect"
            elif not w or w <= 0 or not r or r < 1:
                s["e1rm_note"] = "no_load"
            elif r > rules.E1RM_MAX_REPS:
                s["e1rm_note"] = "high_reps"
            else:
                s["e1rm_kg"] = rules.epley(w, r)

    # ---------------------------------------------------------------- per exercise session
    sessions: dict[int, dict] = {}
    exercises_in_workout = {r[0]: r[1] for r in conn.execute("SELECT workout_id, count(*) FROM workout_exercises GROUP BY workout_id")}
    for r in conn.execute("SELECT e.id, e.workout_id, e.template_id, e.position, w.local_date, w.start_ms FROM workout_exercises e "
                          "JOIN workouts w ON w.id = e.workout_id ORDER BY w.start_ms, w.id, e.position"):
        t = templates.get(r["template_id"], {})
        sessions[r["id"]] = {
            "we_id": r["id"], "workout_id": r["workout_id"], "template_id": r["template_id"], "local_date": r["local_date"],
            "start_ms": r["start_ms"], "position": r["position"], "exercises_in_workout": exercises_in_workout[r["workout_id"]],
            "measure": rules.measure(t.get("type", "unknown"), t.get("primary_muscle"), {}), "sets": [],
        }
    for s in sets:
        sessions[s["we_id"]]["sets"].append(s)
        sessions[s["we_id"]]["measure"] = s["measure"]
    counter = defaultdict(int)
    for ses in sessions.values():                         # already in chronological order
        counter[ses["template_id"]] += 1
        ses["session_index"] = counter[ses["template_id"]]
        work = [s for s in ses["sets"] if s["working"]]
        ok = [s for s in work if not s["suspect"]]
        ses["working_sets"] = len(work)
        ses["warmup_sets"] = sum(1 for s in ses["sets"] if not s["working"])
        ses["failure_sets"] = sum(1 for s in work if s["set_type"] == "failure")
        ses["reps"] = sum(s["reps"] or 0 for s in work)
        ses["volume_kg"] = sum(s["volume_kg"] or 0 for s in work)
        best = max((s for s in ok if s["e1rm_kg"]), key=lambda s: s["e1rm_kg"], default=None)
        ses["best_e1rm_kg"], ses["best_e1rm_set_id"] = (best["e1rm_kg"], best["set_id"]) if best else (None, None)
        top = max((s for s in ok if s["weight_kg"]), key=lambda s: (s["weight_kg"], s["reps"] or 0), default=None)
        ses["top_weight_kg"], ses["top_weight_reps"] = (top["weight_kg"], top["reps"]) if top else (None, None)
        ses["max_reps"] = max((s["reps"] for s in ok if s["reps"]), default=None)
        ses["max_duration_s"] = max((s["duration_s"] for s in ok if s["duration_s"]), default=None)
        ses["total_duration_s"] = sum(s["duration_s"] or 0 for s in work)
        ses["total_distance_m"] = sum(s["distance_m"] or 0 for s in work)
        rpes = [s["rpe"] for s in work if s["rpe"] is not None]
        ses["avg_rpe"], ses["rpe_sets"] = (sum(rpes) / len(rpes), len(rpes)) if rpes else (None, 0)

    # ---------------------------------------------------------------- records, per exercise in time order
    prs: list[dict] = []
    bests: dict[str, dict] = defaultdict(dict)            # template -> category -> best value
    reps_by_weight: dict[str, dict] = defaultdict(dict)   # template -> weight -> most reps

    def record(ses, category, value, unit, detail, set_id=None, at_weight=None):
        prev = bests[ses["template_id"]].get(category)
        if prev is None or value <= prev:
            return
        early = ses["session_index"] <= rules.EARLY_SESSIONS
        prs.append({"template_id": ses["template_id"], "workout_id": ses["workout_id"], "we_id": ses["we_id"], "set_id": set_id,
                    "local_date": ses["local_date"], "start_ms": ses["start_ms"], "category": category, "value": value,
                    "previous": prev, "unit": unit, "at_weight_kg": at_weight, "detail": detail,
                    "session_index": ses["session_index"], "early": 1 if early else 0,
                    "meaningful": 1 if rules.is_meaningful(category, value, prev, early) else 0})

    for ses in sessions.values():
        m, tid = ses["measure"], ses["template_id"]
        ok = [s for s in ses["sets"] if s["working"] and not s["suspect"]]
        cands: dict[str, tuple] = {}                      # category -> (value, unit, detail, set_id, at_weight)
        if m == "load_reps":
            b = max((s for s in ok if s["e1rm_kg"]), key=lambda s: s["e1rm_kg"], default=None)
            if b:
                cands["e1rm"] = (b["e1rm_kg"], "kg", f"{_fmt(b['weight_kg'])} kg × {_fmt(b['reps'])}", b["set_id"], None)
            b = max((s for s in ok if s["volume_kg"]), key=lambda s: s["volume_kg"], default=None)
            if b:
                cands["set_volume"] = (b["volume_kg"], "kg", f"{_fmt(b['weight_kg'])} kg × {_fmt(b['reps'])}", b["set_id"], None)
            if ses["volume_kg"] > 0:
                cands["session_volume"] = (ses["volume_kg"], "kg", f"{_fmt(ses['volume_kg'])} kg in {ses['working_sets']} sets", None, None)
        if m in rules.LOAD_MEASURES:
            b = max((s for s in ok if s["weight_kg"] and s["weight_kg"] > 0), key=lambda s: (s["weight_kg"], s["reps"] or 0), default=None)
            if b:
                tail = f" × {_fmt(b['reps'])}" if b["reps"] else (f" for {_fmt(b['distance_m'])} m" if b["distance_m"] else "")
                cands["weight"] = (b["weight_kg"], "kg", f"{_fmt(b['weight_kg'])} kg{tail}", b["set_id"], None)
        if m in ("load_reps", "added_load"):
            # more reps than ever at this load or heavier; one per session, the heaviest qualifying load
            table = reps_by_weight[tid]
            best_rep = None
            for s in ok:
                w, r = s["weight_kg"], s["reps"]
                if not w or not r or w <= 0:
                    continue
                before = [v for k, v in table.items() if k >= w]
                if before and r > max(before) and (best_rep is None or (w, r) > (best_rep[0], best_rep[1])):
                    best_rep = (w, r, s["set_id"], max(before))
            if best_rep:
                w, r, sid, prev = best_rep
                if not (ses["session_index"] == 1):
                    early = ses["session_index"] <= rules.EARLY_SESSIONS
                    prs.append({"template_id": tid, "workout_id": ses["workout_id"], "we_id": ses["we_id"], "set_id": sid,
                                "local_date": ses["local_date"], "start_ms": ses["start_ms"], "category": "reps_at_weight",
                                "value": r, "previous": prev, "unit": "reps", "at_weight_kg": w,
                                "detail": f"{_fmt(w)} kg × {_fmt(r)} (was × {_fmt(prev)})", "session_index": ses["session_index"],
                                "early": 1 if early else 0, "meaningful": 1 if rules.is_meaningful("reps_at_weight", r, prev, early) else 0})
            for s in ok:
                w, r = s["weight_kg"], s["reps"]
                if w and r and w > 0:
                    table[w] = max(table.get(w, 0), r)
        if m in ("reps", "assisted"):
            b = max((s for s in ok if s["reps"]), key=lambda s: s["reps"], default=None)
            if b:
                cands["max_reps"] = (b["reps"], "reps", f"{_fmt(b['reps'])} reps", b["set_id"], None)
        if m == "time":
            b = max((s for s in ok if s["duration_s"]), key=lambda s: s["duration_s"], default=None)
            if b:
                cands["longest_hold"] = (b["duration_s"], "s", f"{_fmt(b['duration_s'])} s", b["set_id"], None)
        if m == "cardio":
            if ses["total_distance_m"] > 0:
                cands["longest_distance"] = (ses["total_distance_m"], "m", f"{_fmt(ses['total_distance_m'])} m", None, None)
            if ses["total_duration_s"] > 0:
                cands["longest_session"] = (ses["total_duration_s"], "s", f"{_fmt(ses['total_duration_s'] / 60)} min", None, None)
        for category, (value, unit, detail, sid, at_w) in cands.items():
            if ses["session_index"] > 1:
                record(ses, category, value, unit, detail, sid, at_w)
            bests[tid][category] = max(bests[tid].get(category, value), value)

    # ---------------------------------------------------------------- workouts
    workouts = {r["id"]: dict(r) for r in conn.execute("SELECT id, local_date, start_ms, duration_s FROM workouts ORDER BY start_ms, id")}
    per_workout = defaultdict(list)
    for ses in sessions.values():
        per_workout[ses["workout_id"]].append(ses)
    pr_count = defaultdict(lambda: [0, 0])
    an_workouts = []
    best_workout_volume, gym_index = None, 0
    for wid, w in workouts.items():
        ss = per_workout.get(wid, [])
        cardio = bool(ss) and all(rules.is_cardio_exercise(x["measure"], templates.get(x["template_id"], {}).get("primary_muscle")) for x in ss)
        work_sets = [s for x in ss for s in x["sets"] if s["working"]]
        rpes = [s["rpe"] for s in work_sets if s["rpe"] is not None]
        row = {"workout_id": wid, "local_date": w["local_date"], "start_ms": w["start_ms"], "duration_s": w["duration_s"],
               "kind": "cardio" if cardio else "gym", "exercises": len(ss), "working_sets": len(work_sets),
               "warmup_sets": sum(x["warmup_sets"] for x in ss), "reps": sum(x["reps"] for x in ss),
               "volume_kg": sum(x["volume_kg"] for x in ss),
               "distance_m": sum(x["total_distance_m"] for x in ss if x["measure"] == "cardio"),
               "cardio_duration_s": sum(x["total_duration_s"] for x in ss if x["measure"] == "cardio"),
               "avg_rpe": sum(rpes) / len(rpes) if rpes else None}
        if not cardio and row["volume_kg"] > 0:
            gym_index += 1
            if best_workout_volume is not None and row["volume_kg"] > best_workout_volume:
                early = gym_index <= rules.EARLY_SESSIONS
                prs.append({"template_id": None, "workout_id": wid, "we_id": None, "set_id": None, "local_date": w["local_date"],
                            "start_ms": w["start_ms"], "category": "workout_volume", "value": row["volume_kg"],
                            "previous": best_workout_volume, "unit": "kg", "at_weight_kg": None,
                            "detail": f"{_fmt(row['volume_kg'])} kg in one workout", "session_index": gym_index,
                            "early": 1 if early else 0,
                            "meaningful": 1 if rules.is_meaningful("workout_volume", row["volume_kg"], best_workout_volume, early) else 0})
            best_workout_volume = max(best_workout_volume or 0, row["volume_kg"])
        an_workouts.append(row)
    for p in prs:
        pr_count[p["workout_id"]][0] += 1
        pr_count[p["workout_id"]][1] += p["meaningful"]
    for row in an_workouts:
        row["prs"], row["meaningful_prs"] = pr_count[row["workout_id"]]

    # ---------------------------------------------------------------- muscles and days
    muscle_days: dict[tuple, dict] = {}
    for ses in sessions.values():
        work = [s for s in ses["sets"] if s["working"]]
        if not work:
            continue
        for muscle, credit, is_primary in muscles.get(ses["template_id"], []):
            key = (ses["local_date"], muscle)
            md = muscle_days.setdefault(key, {"local_date": key[0], "muscle": muscle, "sets": 0.0, "volume_kg": 0.0,
                                              "primary_sets": 0, "workouts": set()})
            md["sets"] += credit * len(work)
            md["volume_kg"] += credit * ses["volume_kg"]
            md["primary_sets"] += len(work) if is_primary else 0
            md["workouts"].add(ses["workout_id"])
    days: dict[str, dict] = {}
    for row in an_workouts:
        d = days.setdefault(row["local_date"], {"local_date": row["local_date"], "workouts": 0, "gym_sessions": 0, "cardio_sessions": 0,
                                                "duration_s": 0, "gym_duration_s": 0, "working_sets": 0, "reps": 0.0,
                                                "volume_kg": 0.0, "distance_m": 0.0, "prs": 0, "meaningful_prs": 0})
        d["workouts"] += 1
        d["gym_sessions" if row["kind"] == "gym" else "cardio_sessions"] += 1
        d["duration_s"] += row["duration_s"] or 0
        d["gym_duration_s"] += (row["duration_s"] or 0) if row["kind"] == "gym" else 0
        for k in ("working_sets", "reps", "volume_kg", "distance_m", "prs", "meaningful_prs"):
            d[k] += row[k]

    # ---------------------------------------------------------------- write
    for table in TABLES:
        conn.execute(f"DELETE FROM {table}")
    conn.executemany(
        "INSERT INTO an_sets VALUES (:set_id, :workout_id, :we_id, :template_id, :local_date, :start_ms, :ex_position, :set_position, "
        ":set_type, :working, :measure, :weight_kg, :reps, :distance_m, :duration_s, :rpe, :volume_kg, :e1rm_kg, :e1rm_note, :suspect)", sets)
    conn.executemany(
        "INSERT INTO an_exercise_sessions VALUES (:we_id, :workout_id, :template_id, :local_date, :start_ms, :position, "
        ":exercises_in_workout, :session_index, :measure, :working_sets, :warmup_sets, :failure_sets, :reps, :volume_kg, "
        ":best_e1rm_kg, :best_e1rm_set_id, :top_weight_kg, :top_weight_reps, :max_reps, :max_duration_s, :total_duration_s, "
        ":total_distance_m, :avg_rpe, :rpe_sets)", list(sessions.values()))
    conn.executemany(
        "INSERT INTO an_workouts VALUES (:workout_id, :local_date, :start_ms, :duration_s, :kind, :exercises, :working_sets, "
        ":warmup_sets, :reps, :volume_kg, :distance_m, :cardio_duration_s, :avg_rpe, :prs, :meaningful_prs)", an_workouts)
    conn.executemany(
        "INSERT INTO an_prs (template_id, workout_id, we_id, set_id, local_date, start_ms, category, value, previous, unit, "
        "at_weight_kg, detail, session_index, early, meaningful) VALUES (:template_id, :workout_id, :we_id, :set_id, :local_date, "
        ":start_ms, :category, :value, :previous, :unit, :at_weight_kg, :detail, :session_index, :early, :meaningful)",
        sorted(prs, key=lambda p: (p["start_ms"], p["template_id"] or "", p["category"])))
    conn.executemany(
        "INSERT INTO an_muscle_days VALUES (:local_date, :muscle, :sets, :volume_kg, :primary_sets, :workouts)",
        [{**m, "workouts": len(m["workouts"])} for m in muscle_days.values()])
    conn.executemany(
        "INSERT INTO an_days VALUES (:local_date, :workouts, :gym_sessions, :cardio_sessions, :duration_s, :gym_duration_s, "
        ":working_sets, :reps, :volume_kg, :distance_m, :prs, :meaningful_prs)", list(days.values()))
    elapsed_ms = int((time.monotonic() - t0) * 1000)
    set_meta(conn, "analytics_version", str(rules.ANALYTICS_VERSION))
    set_meta(conn, "analytics_built_at", iso(utcnow()))
    set_meta(conn, "analytics_build_ms", str(elapsed_ms))
    return {"sets": len(sets), "sessions": len(sessions), "workouts": len(an_workouts), "prs": len(prs), "ms": elapsed_ms}


def refresh(db, force: bool = False) -> dict | None:
    """Rebuild in its own transaction if forced, never built, or built by older rules. Records failures in app_meta
    (the raw and normalised data are untouched either way, so a failed build only means stale analytics)."""
    from ..db import get_meta
    with db.read() as conn:
        version = get_meta(conn, "analytics_version")
    if not force and version == str(rules.ANALYTICS_VERSION):
        return None
    try:
        with db.tx() as conn:
            result = rebuild(conn)
            set_meta(conn, "analytics_error", None)
        return result
    except Exception as exc:
        with db.tx() as conn:
            set_meta(conn, "analytics_error", f"{type(exc).__name__} at {iso(utcnow())}")
        raise
