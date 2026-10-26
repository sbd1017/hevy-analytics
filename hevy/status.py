"""The system status shown on /hevy/system and by `python -m hevy.cli status`."""
from __future__ import annotations

import json
import os
from datetime import timedelta

from . import VERSION
from .config import Settings
from .db import Database, get_meta
from .analytics.rules import ANALYTICS_VERSION
from .sync import get_state
from .timeutil import parse_ts, utcnow

WORKER_STALE = timedelta(minutes=3)


def _db_bytes(settings: Settings) -> int:
    total = 0
    for suffix in ("", "-wal", "-shm"):
        try:
            total += os.path.getsize(str(settings.db_path) + suffix)
        except OSError:
            pass
    return total


def build(db: Database, settings: Settings) -> dict:
    now = utcnow()
    with db.read() as c:
        st = {r[0]: r[1] for r in c.execute("SELECT key, value FROM sync_state")}
        one = lambda sql, *a: c.execute(sql, a).fetchone()[0]
        runs = [dict(r) for r in c.execute(
            "SELECT id, kind, trigger, started_at, finished_at, status, api_requests, workouts_seen, workouts_changed, "
            "workouts_deleted, other_changed, error_kind, error FROM sync_runs ORDER BY id DESC LIMIT 12")]
        pending = c.execute("SELECT id, requested_at, picked_up_at FROM sync_requests WHERE run_id IS NULL "
                            "ORDER BY id LIMIT 1").fetchone()
        counts = {
            "workouts": one("SELECT count(*) FROM workouts"),
            "deleted_workouts": one("SELECT count(*) FROM raw_current WHERE kind = 'workout' AND deleted = 1"),
            "workout_versions": one("SELECT count(*) FROM raw_objects WHERE kind = 'workout'"),
            "sets": one("SELECT count(*) FROM sets"),
            "exercises_used": one("SELECT count(DISTINCT template_id) FROM workout_exercises"),
            "templates": one("SELECT count(*) FROM exercise_templates"),
            "routines": one("SELECT count(*) FROM routines"),
            "body_measurements": one("SELECT count(*) FROM body_measurements"),
        }
        issues = [dict(r) for r in c.execute("SELECT severity, code, count(*) AS n FROM data_issues GROUP BY 1, 2 "
                                             "ORDER BY CASE severity WHEN 'error' THEN 0 WHEN 'warning' THEN 1 ELSE 2 END, n DESC")]
        first, last = c.execute("SELECT min(start_utc), max(start_utc) FROM workouts").fetchone()
        origin = get_meta(c, "data_origin")
        analytics = {"version": get_meta(c, "analytics_version"), "built_at": get_meta(c, "analytics_built_at"),
                     "build_ms": get_meta(c, "analytics_build_ms"), "error": get_meta(c, "analytics_error"),
                     "current_rules": ANALYTICS_VERSION}
        suspects = [dict(r) for r in c.execute(
            "SELECT a.local_date, a.workout_id, t.title, a.weight_kg, a.reps, a.suspect FROM an_sets a "
            "JOIN exercise_templates t ON t.id = a.template_id WHERE a.suspect IS NOT NULL ORDER BY a.start_ms DESC LIMIT 50")]
        schema = one("SELECT max(version) FROM schema_migrations")
    offsite = None
    try:
        offsite = json.loads((settings.data_dir / "offsite.json").read_text())
        offsite["stale"] = bool(parse_ts(offsite.get("at")) and now - parse_ts(offsite["at"]) > timedelta(days=3))
    except (OSError, ValueError):
        pass
    beat = parse_ts(st.get("worker_heartbeat_at"))
    worker_alive = bool(beat and now - beat < WORKER_STALE)
    running = next((r for r in runs if r["status"] == "running"), None)
    failures = int(st.get("consecutive_failures") or 0)
    if running:
        state = "running"
    elif not runs:
        state = "never"
    elif failures and st.get("last_error_kind") == "auth":
        state = "auth_failed"
    elif failures:
        state = "failing"
    else:
        state = "ok"
    return {
        "version": VERSION, "env": settings.env, "data_origin": origin, "schema_version": schema,
        "now": now.isoformat().replace("+00:00", "Z"),
        "sync": {
            "state": state, "worker_alive": worker_alive, "worker_heartbeat_at": st.get("worker_heartbeat_at"),
            "last_success_at": st.get("last_success_at"), "last_failure_at": st.get("last_failure_at"),
            "consecutive_failures": failures, "last_error_kind": st.get("last_error_kind"),
            "last_error": st.get("last_error"), "cursor": st.get("cursor"),
            "next_incremental_at": st.get("next_incremental_at"), "last_full_sync_at": st.get("last_full_sync_at"),
            "last_reference_at": st.get("last_reference_at"), "reconcile_needed": st.get("reconcile_needed") == "1",
            "hevy_workout_count": int(st["hevy_workout_count"]) if st.get("hevy_workout_count") else None,
            "pending_request": dict(pending) if pending else None, "interval_s": settings.sync_interval_s,
        },
        "counts": counts, "range": {"first": first, "last": last}, "db_bytes": _db_bytes(settings),
        "issues": issues, "runs": runs, "analytics": analytics, "suspect_sets": suspects, "offsite": offsite,
    }
