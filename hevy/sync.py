"""Keeps the local database in step with Hevy.

Every run fetches first and writes afterwards, in ONE transaction: a failure part-way through (network,
Hevy errors, a crash) leaves the database exactly as it was, and the cursor only moves when everything
committed. Runs are serialised with a file lock, so the worker, the CLI and a manual "Sync now" never overlap.

Kinds of run:
* backfill     first time: list everything (workouts, templates, routines, body measurements)
* incremental  `GET /v1/workouts/events?since=` (updates and deletions), every ~10 minutes
* reconcile    a full re-list compared with what we hold (weekly, or when the workout count disagrees)
* reference    templates, routines, body measurements and the workout-count check (daily)

Something we hold but Hevy no longer lists is only treated as deleted after fetching it by id returns
"not found": paging can shift while new workouts are being saved, and that must never delete history.
"""
from __future__ import annotations

import fcntl
import logging
import os
from contextlib import contextmanager
from dataclasses import asdict, dataclass

from . import normalise, rawstore
from .client import BadRequest, HevyClient, HevyError, NotFound
from .config import Settings
from .db import Database
from .timeutil import iso, iso_s, minus, parse_ts, utcnow

log = logging.getLogger("hevy.sync")

OVERLAP_S = 24 * 3600     # each incremental asks for 24 h before the cursor: cheap, and covers late uploads
REFERENCE_KINDS = ("exercise_template", "routine", "body_measurement")


class Busy(Exception):
    """Another sync is running."""


@dataclass
class Stats:
    workouts_seen: int = 0
    workouts_changed: int = 0
    workouts_deleted: int = 0
    other_changed: int = 0
    count_mismatch: bool = False


def get_state(conn, key, default=None):
    row = conn.execute("SELECT value FROM sync_state WHERE key = ?", (key,)).fetchone()
    return row[0] if row else default


def set_state(conn, key, value):
    conn.execute("INSERT INTO sync_state (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                 (key, None if value is None else str(value)))


class SyncEngine:
    def __init__(self, db: Database, client: HevyClient, settings: Settings, now=utcnow):
        self.db, self.client, self.settings, self.now = db, client, settings, now

    # ------------------------------------------------------------ public

    def run(self, kind: str = "incremental", trigger: str = "schedule") -> dict:
        """Run one sync. Never raises for Hevy/network problems: the outcome is recorded and returned."""
        with self._lock():
            with self.db.read() as conn:
                has_cursor = get_state(conn, "cursor") is not None
            if kind == "incremental" and not has_cursor:
                kind = "backfill"
            run_id = self._start(kind, trigger)
            stats, before = Stats(), self.client.requests
            try:
                if kind in ("backfill", "reconcile"):
                    self._full(run_id, stats, kind)
                elif kind == "incremental":
                    self._incremental(run_id, stats)
                elif kind == "reference":
                    self._reference(run_id, stats)
                else:
                    raise ValueError(kind)
            except HevyError as exc:
                return self._finish(run_id, stats, self.client.requests - before, exc.kind, str(exc))
            except Exception as exc:          # a bug or bad data: record it, keep the database as it was
                log.exception("sync run %s failed", run_id)
                return self._finish(run_id, stats, self.client.requests - before, "internal", type(exc).__name__)
            return self._finish(run_id, stats, self.client.requests - before)

    # ------------------------------------------------------------ the kinds of run

    def _full(self, run_id: int, stats: Stats, kind: str) -> None:
        started = self.now()
        refs = self._fetch_reference_lists()
        workouts = list(self.client.workouts())
        stats.workouts_seen = len(workouts)
        with self.db.read() as conn:
            live = {k: rawstore.live_ids(conn, k) for k in ("workout",) + REFERENCE_KINDS}
            known_templates = self._known_templates(conn)
        listed_ids = {w.get("id") for w in workouts}
        gone_workouts = []
        for wid in sorted(live["workout"] - listed_ids):
            try:
                workouts.append(self.client.workout(wid))      # still exists: the list shifted while paging
            except NotFound:
                gone_workouts.append(wid)
        gone_refs = self._verify_missing_refs(refs, live)
        known_templates |= {t["id"] for t in refs["exercise_template"]}
        refs["exercise_template"] += self._fetch_missing_templates(workouts, known_templates)
        with self.db.tx() as conn:
            touched = self._apply_refs(conn, run_id, refs, gone_refs, stats)
            deleted_at = iso_s(started)
            for w in workouts:
                if rawstore.apply_object(conn, "workout", w, run_id)["changed"]:
                    stats.workouts_changed += 1
                    touched.add(("workout", w["id"]))
            for wid in gone_workouts:
                if rawstore.apply_deletion(conn, "workout", wid, deleted_at, "missing_from_full_list", run_id):
                    stats.workouts_deleted += 1
                    touched.add(("workout", wid))
            if kind == "backfill":
                normalise.rebuild_all(conn, self.settings.timezone)
            else:
                self._rebuild(conn, touched)
            set_state(conn, "hevy_workout_count", len({w["id"] for w in workouts} - set(gone_workouts)))
            set_state(conn, "cursor", iso_s(started))
            set_state(conn, "last_full_sync_at", iso(started))
            set_state(conn, "last_reference_at", iso(started))
            set_state(conn, "reconcile_needed", None)

    def _incremental(self, run_id: int, stats: Stats) -> None:
        started = self.now()
        with self.db.read() as conn:
            cursor = get_state(conn, "cursor")
            known_templates = self._known_templates(conn)
        since = minus(cursor, OVERLAP_S)
        updated: dict[str, dict] = {}
        deleted: dict[str, str] = {}
        for event in self.client.events(since):
            etype = event.get("type")
            if etype == "updated" and isinstance(event.get("workout"), dict) and event["workout"].get("id"):
                w = event["workout"]
                prev = updated.get(w["id"])
                if prev is None or (parse_ts(w.get("updated_at")) or started) >= (parse_ts(prev.get("updated_at")) or started):
                    updated[w["id"]] = w
            elif etype == "deleted" and isinstance(event.get("id"), str) and isinstance(event.get("deleted_at"), str):
                if event["deleted_at"] > deleted.get(event["id"], ""):
                    deleted[event["id"]] = event["deleted_at"]
            else:
                log.warning("ignored a workout event of type %r", etype)
        stats.workouts_seen = len(updated)
        new_templates = self._fetch_missing_templates(list(updated.values()), known_templates)
        with self.db.tx() as conn:
            touched: set[tuple[str, str]] = set()
            for t in new_templates:
                if rawstore.apply_object(conn, "exercise_template", t, run_id)["changed"]:
                    stats.other_changed += 1
                    touched.add(("exercise_template", t["id"]))
            for w in sorted(updated.values(), key=lambda w: w.get("updated_at") or ""):
                if rawstore.apply_object(conn, "workout", w, run_id)["changed"]:
                    stats.workouts_changed += 1
                    touched.add(("workout", w["id"]))
            for wid, deleted_at in deleted.items():
                if rawstore.apply_deletion(conn, "workout", wid, deleted_at, "event", run_id):
                    stats.workouts_deleted += 1
                    touched.add(("workout", wid))
            self._rebuild(conn, touched)
            conn.execute("UPDATE sync_runs SET since_cursor = ? WHERE id = ?", (since, run_id))
            set_state(conn, "cursor", iso_s(started))

    def _reference(self, run_id: int, stats: Stats) -> None:
        started = self.now()
        refs = self._fetch_reference_lists()
        with self.db.read() as conn:
            live = {k: rawstore.live_ids(conn, k) for k in ("workout",) + REFERENCE_KINDS}
        gone_refs = self._verify_missing_refs(refs, live)
        hevy_count = self.client.workout_count()
        with self.db.tx() as conn:
            touched = self._apply_refs(conn, run_id, refs, gone_refs, stats)
            self._rebuild(conn, touched)
            local = conn.execute("SELECT count(*) FROM raw_current WHERE kind = 'workout' AND deleted = 0").fetchone()[0]
            stats.count_mismatch = hevy_count != local
            set_state(conn, "reconcile_needed", "1" if stats.count_mismatch else None)
            set_state(conn, "hevy_workout_count", hevy_count)
            set_state(conn, "last_reference_at", iso(started))

    # ------------------------------------------------------------ helpers

    def _fetch_reference_lists(self) -> dict:
        refs = {"exercise_template": list(self.client.templates()), "routine": list(self.client.routines()),
                "body_measurement": list(self.client.body_measurements())}
        try:
            refs["user_info"] = [self.client.user_info()]
        except NotFound:
            refs["user_info"] = []
        return refs

    def _verify_missing_refs(self, refs: dict, live: dict) -> dict:
        """Objects we hold that a list left out: fetch each; only 'not found' counts as deleted."""
        fetchers = {"exercise_template": self.client.template, "routine": self.client.routine,
                    "body_measurement": self.client.body_measurement}
        gone = {}
        for kind in REFERENCE_KINDS:
            listed = {rawstore.object_key(kind, o) for o in refs[kind]}
            gone[kind] = []
            for hevy_id in sorted(live[kind] - listed):
                try:
                    refs[kind].append(fetchers[kind](hevy_id))
                except NotFound:
                    gone[kind].append(hevy_id)
                except BadRequest:
                    if kind != "routine":       # the routine endpoint answers 400 for an unknown id
                        raise
                    gone[kind].append(hevy_id)
        return gone

    def _known_templates(self, conn) -> set[str]:
        return {r[0] for r in conn.execute("SELECT hevy_id FROM raw_current WHERE kind = 'exercise_template'")}

    def _fetch_missing_templates(self, workouts: list[dict], known: set[str]) -> list[dict]:
        needed = sorted({e.get("exercise_template_id") for w in workouts for e in (w.get("exercises") or [])
                         if isinstance(e, dict) and isinstance(e.get("exercise_template_id"), str)} - known)
        found = []
        for template_id in needed:
            try:
                found.append(self.client.template(template_id))
            except NotFound:
                pass                            # the normaliser keeps the workout with a placeholder template
        return found

    def _apply_refs(self, conn, run_id: int, refs: dict, gone: dict, stats: Stats) -> set:
        touched = set()
        for kind in ("user_info",) + REFERENCE_KINDS:
            for obj in refs.get(kind, []):
                try:
                    if rawstore.apply_object(conn, kind, obj, run_id)["changed"]:
                        touched.add((kind, rawstore.object_key(kind, obj)))
                except ValueError:
                    log.warning("skipped a %s without an id", kind)
        now = iso_s(self.now())
        for kind, ids in gone.items():
            for hevy_id in ids:
                if rawstore.apply_deletion(conn, kind, hevy_id, now, "missing_from_full_list", run_id):
                    touched.add((kind, hevy_id))
        stats.other_changed += sum(1 for k, _ in touched if k != "user_info")
        return touched

    def _rebuild(self, conn, touched: set) -> None:
        tz = self.settings.timezone
        for kind, hevy_id in sorted(touched, key=lambda t: (t[0] != "exercise_template", t)):
            normalise.rebuild(conn, kind, hevy_id, tz)

    # ------------------------------------------------------------ bookkeeping

    @contextmanager
    def _lock(self):
        self.settings.data_dir.mkdir(mode=0o770, parents=True, exist_ok=True)
        fd = os.open(self.settings.data_dir / "sync.lock", os.O_RDWR | os.O_CREAT, 0o660)
        try:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise Busy("another sync is running") from None
            yield
        finally:
            os.close(fd)

    def _start(self, kind: str, trigger: str) -> int:
        with self.db.tx() as conn:
            # A run left 'running' by a crash or kill can't still be running: we hold the lock.
            conn.execute("UPDATE sync_runs SET status = 'failed', error_kind = 'internal', error = 'interrupted', "
                         "finished_at = ? WHERE status = 'running'", (iso(self.now()),))
            cur = conn.execute("INSERT INTO sync_runs (kind, trigger, started_at) VALUES (?, ?, ?)",
                               (kind, trigger, iso(self.now())))
            return cur.lastrowid

    def _finish(self, run_id: int, stats: Stats, requests: int, error_kind: str | None = None,
                error: str | None = None) -> dict:
        status = "failed" if error_kind else "ok"
        with self.db.tx() as conn:
            conn.execute("UPDATE sync_runs SET finished_at = ?, status = ?, api_requests = ?, workouts_seen = ?, "
                         "workouts_changed = ?, workouts_deleted = ?, other_changed = ?, error_kind = ?, error = ?, "
                         "new_cursor = (SELECT value FROM sync_state WHERE key = 'cursor') WHERE id = ?",
                         (iso(self.now()), status, requests, stats.workouts_seen, stats.workouts_changed,
                          stats.workouts_deleted, stats.other_changed, error_kind, (error or "")[:300] or None, run_id))
            if error_kind:
                failures = int(get_state(conn, "consecutive_failures", "0")) + 1
                set_state(conn, "consecutive_failures", failures)
                set_state(conn, "last_error_kind", error_kind)
                set_state(conn, "last_error", (error or "")[:300])
                set_state(conn, "last_failure_at", iso(self.now()))
            else:
                set_state(conn, "consecutive_failures", 0)
                set_state(conn, "last_error_kind", None)
                set_state(conn, "last_error", None)
                set_state(conn, "last_success_at", iso(self.now()))
        if error_kind:
            log.warning("sync run %s (%s) failed: %s", run_id, error_kind, error)
        else:
            log.info("sync run %s ok: %s", run_id, stats)
        return {"run_id": run_id, "status": status, "error_kind": error_kind, "error": error, "api_requests": requests,
                **asdict(stats)}
