"""The normalised layer, rebuilt from the current raw versions.

Nothing here changes raw data. Odd values are kept exactly as Hevy sent them and reported in data_issues;
deciding what counts (warm-ups, implausible durations, high-rep sets) is the analytics layer's job.
"""
from __future__ import annotations

import json
import sqlite3

from . import rawstore
from .timeutil import iso_s, local_date, parse_ts, to_ms

KNOWN_SET_TYPES = {"normal", "warmup", "dropset", "failure"}
BODY_MAIN = ("weight_kg", "fat_percent", "lean_mass_kg")
BODY_SKIP = ("date", "id", "created_at", "updated_at")


def _num(value):
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _issue(conn, kind, hevy_id, severity, code, detail=None):
    conn.execute("INSERT INTO data_issues (kind, hevy_id, severity, code, detail) VALUES (?, ?, ?, ?, ?)",
                 (kind, hevy_id, severity, code, detail))


# ---------------------------------------------------------------- templates

def rebuild_template(conn: sqlite3.Connection, template_id: str) -> None:
    payload, deleted, raw_id = rawstore.current_payload(conn, "exercise_template", template_id)
    if payload is None:
        return                                  # never received: a placeholder (if any) keeps its issue
    conn.execute("DELETE FROM data_issues WHERE kind = 'exercise_template' AND hevy_id = ?", (template_id,))
    title = payload.get("title") if isinstance(payload.get("title"), str) else template_id
    ttype = payload.get("type") if isinstance(payload.get("type"), str) else "unknown"
    primary = payload.get("primary_muscle_group") if isinstance(payload.get("primary_muscle_group"), str) else None
    secondary = [m for m in (payload.get("secondary_muscle_groups") or []) if isinstance(m, str)]
    # A template Hevy no longer lists stays here (workouts still refer to it), marked as unknown to Hevy.
    conn.execute("INSERT INTO exercise_templates (id, title, type, primary_muscle, equipment, is_custom, known_to_hevy, "
                 "raw_object_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(id) DO UPDATE SET title = excluded.title, "
                 "type = excluded.type, primary_muscle = excluded.primary_muscle, equipment = excluded.equipment, "
                 "is_custom = excluded.is_custom, known_to_hevy = excluded.known_to_hevy, "
                 "raw_object_id = excluded.raw_object_id",
                 (template_id, title, ttype, primary, payload.get("equipment"), 1 if payload.get("is_custom") else 0,
                  0 if deleted else 1, raw_id))
    conn.execute("DELETE FROM template_muscles WHERE template_id = ?", (template_id,))
    if primary:
        conn.execute("INSERT INTO template_muscles (template_id, muscle, role) VALUES (?, ?, 'primary')",
                     (template_id, primary))
    for muscle in dict.fromkeys(secondary):
        if muscle != primary:
            conn.execute("INSERT INTO template_muscles (template_id, muscle, role) VALUES (?, ?, 'secondary')",
                         (template_id, muscle))
    if deleted:
        _issue(conn, "exercise_template", template_id, "info", "template_removed_from_hevy", title)


def ensure_placeholder_template(conn: sqlite3.Connection, template_id: str, title: str) -> None:
    """A workout refers to a template Hevy doesn't list and never sent: keep the workout, invent nothing."""
    if conn.execute("SELECT 1 FROM exercise_templates WHERE id = ?", (template_id,)).fetchone():
        return
    conn.execute("INSERT INTO exercise_templates (id, title, type, known_to_hevy) VALUES (?, ?, 'unknown', 0)",
                 (template_id, title or template_id))
    _issue(conn, "exercise_template", template_id, "warning", "missing_template",
           f"Referenced by workouts but not available from Hevy; shown as '{title}'")


# ---------------------------------------------------------------- workouts

def rebuild_workout(conn: sqlite3.Connection, workout_id: str, tz: str) -> None:
    conn.execute("DELETE FROM workouts WHERE id = ?", (workout_id,))      # cascades to exercises and sets
    conn.execute("DELETE FROM data_issues WHERE kind = 'workout' AND hevy_id = ?", (workout_id,))
    payload, deleted, raw_id = rawstore.current_payload(conn, "workout", workout_id)
    if payload is None or deleted:
        return
    start = parse_ts(payload.get("start_time"))
    if start is None:
        _issue(conn, "workout", workout_id, "error", "unparseable", "no valid start_time; workout left out")
        return
    end = parse_ts(payload.get("end_time"))
    duration = None
    if end is None:
        _issue(conn, "workout", workout_id, "warning", "no_end_time")
    elif end < start:
        _issue(conn, "workout", workout_id, "warning", "end_before_start")
    else:
        duration = int((end - start).total_seconds())
    created, updated = parse_ts(payload.get("created_at")), parse_ts(payload.get("updated_at"))
    conn.execute("INSERT INTO workouts (id, title, description, routine_id, start_utc, end_utc, start_ms, end_ms, "
                 "duration_s, local_date, created_utc, updated_utc, raw_object_id) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                 (workout_id, payload.get("title") if isinstance(payload.get("title"), str) else "",
                  payload.get("description") if isinstance(payload.get("description"), str) else None,
                  payload.get("routine_id") if isinstance(payload.get("routine_id"), str) else None,
                  iso_s(start), iso_s(end) if end else None, to_ms(start), to_ms(end) if end else None, duration,
                  local_date(start, tz), iso_s(created) if created else None, iso_s(updated) if updated else None,
                  raw_id))
    exercises = [e for e in (payload.get("exercises") or []) if isinstance(e, dict)]
    if len(exercises) != len(payload.get("exercises") or []):
        _issue(conn, "workout", workout_id, "warning", "malformed_exercise", "non-object exercise entries skipped")
    exercises.sort(key=lambda e: e.get("index") if isinstance(e.get("index"), (int, float)) else 1e9)
    if [e.get("index") for e in exercises] != list(range(len(exercises))):
        _issue(conn, "workout", workout_id, "info", "exercise_index_gaps", "positions renumbered in logged order")
    for position, ex in enumerate(exercises):
        template_id = ex.get("exercise_template_id")
        title = ex.get("title") if isinstance(ex.get("title"), str) else ""
        if not isinstance(template_id, str) or not template_id:
            _issue(conn, "workout", workout_id, "warning", "exercise_without_template", title)
            continue
        ensure_placeholder_template(conn, template_id, title)
        superset = ex.get("superset_id") if isinstance(ex.get("superset_id"), int) else None
        cur = conn.execute("INSERT INTO workout_exercises (workout_id, position, template_id, title, notes, superset_id) "
                           "VALUES (?, ?, ?, ?, ?, ?)",
                           (workout_id, position, template_id, title,
                            ex.get("notes") if isinstance(ex.get("notes"), str) else None, superset))
        we_id = cur.lastrowid
        sets = [s for s in (ex.get("sets") or []) if isinstance(s, dict)]
        sets.sort(key=lambda s: s.get("index") if isinstance(s.get("index"), (int, float)) else 1e9)
        for set_pos, s in enumerate(sets):
            set_type = s.get("type") if isinstance(s.get("type"), str) else "normal"
            if set_type not in KNOWN_SET_TYPES:
                _issue(conn, "workout", workout_id, "info", "unknown_set_type", set_type)
            conn.execute("INSERT INTO sets (workout_exercise_id, workout_id, position, set_type, weight_kg, reps, "
                         "distance_m, duration_s, rpe, custom_metric) VALUES (?,?,?,?,?,?,?,?,?,?)",
                         (we_id, workout_id, set_pos, set_type, _num(s.get("weight_kg")), _num(s.get("reps")),
                          _num(s.get("distance_meters")), _num(s.get("duration_seconds")), _num(s.get("rpe")),
                          _num(s.get("custom_metric"))))


# ---------------------------------------------------------------- routines and body measurements

def rebuild_routine(conn: sqlite3.Connection, routine_id: str) -> None:
    conn.execute("DELETE FROM routines WHERE id = ?", (routine_id,))
    payload, deleted, raw_id = rawstore.current_payload(conn, "routine", routine_id)
    if payload is None or deleted:
        return
    conn.execute("INSERT INTO routines (id, title, folder_id, updated_utc, raw_object_id) VALUES (?, ?, ?, ?, ?)",
                 (routine_id, payload.get("title") or "", str(payload["folder_id"]) if payload.get("folder_id") is not None
                  else None, payload.get("updated_at"), raw_id))


def rebuild_body(conn: sqlite3.Connection, date: str) -> None:
    conn.execute("DELETE FROM body_measurements WHERE date = ?", (date,))
    payload, deleted, raw_id = rawstore.current_payload(conn, "body_measurement", date)
    if payload is None or deleted:
        return
    other = {k: v for k, v in payload.items() if k not in BODY_MAIN + BODY_SKIP and v is not None}
    conn.execute("INSERT INTO body_measurements (date, weight_kg, fat_percent, lean_mass_kg, other_json, raw_object_id) "
                 "VALUES (?, ?, ?, ?, ?, ?)",
                 (date, _num(payload.get("weight_kg")), _num(payload.get("fat_percent")),
                  _num(payload.get("lean_mass_kg")), json.dumps(other, sort_keys=True) if other else None, raw_id))


REBUILDERS = {"exercise_template": rebuild_template, "routine": rebuild_routine, "body_measurement": rebuild_body}


def rebuild(conn: sqlite3.Connection, kind: str, hevy_id: str, tz: str) -> None:
    if kind == "workout":
        rebuild_workout(conn, hevy_id, tz)
    elif kind in REBUILDERS:
        REBUILDERS[kind](conn, hevy_id)


def rebuild_all(conn: sqlite3.Connection, tz: str) -> dict:
    """Recreate the whole normalised layer from raw (inside the caller's transaction)."""
    for table in ("sets", "workout_exercises", "workouts", "template_muscles", "exercise_templates", "routines",
                  "body_measurements", "data_issues"):
        conn.execute(f"DELETE FROM {table}")
    ids = {kind: [r[0] for r in conn.execute("SELECT hevy_id FROM raw_current WHERE kind = ? ORDER BY hevy_id", (kind,))]
           for kind in ("exercise_template", "workout", "routine", "body_measurement")}
    for kind in ("exercise_template", "workout", "routine", "body_measurement"):      # templates before workouts
        for hevy_id in ids[kind]:
            rebuild(conn, kind, hevy_id, tz)
    return {k: len(v) for k, v in ids.items()}
