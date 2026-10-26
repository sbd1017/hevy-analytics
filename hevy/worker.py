"""The sync worker: ``python -m hevy.worker`` (the sync service). The only process that talks to Hevy.

Schedule (Hevy has no webhooks, so new workouts are found by polling, which is cheap):
* a "Sync now" request from the web page or CLI: within ~2 seconds
* incremental check: every ~10 minutes, at a random second (Hevy asks pollers to avoid xx:00)
* reference refresh + workout-count check: daily in the early hours
* full reconcile: weekly, or straight away when the workout count disagrees
* next-workout suggestion (coach.py): after a sync brings a new or changed workout, when asked from the page,
  and at least every 3 days
Failures back off (1, 2, 4 ... 60 minutes; 6 hours when Hevy refuses the key); a manual request always tries.
"""
from __future__ import annotations

import logging
import random
import signal
import time
from datetime import timedelta
from zoneinfo import ZoneInfo

from . import coach
from .analytics import build as analytics
from .client import AuthError, HevyClient, load_key
from .coach_openai import CoachError, OpenAICoach
from .coach_openai import load_key as load_openai_key
from .config import Settings, load_settings
from .db import Database, get_meta, set_meta
from .sync import Busy, SyncEngine, get_state, set_state
from .timeutil import iso, parse_ts, utcnow

log = logging.getLogger("hevy.worker")

HEARTBEAT_S = 30
WEEK = timedelta(days=7)


def backoff_s(failures: int, error_kind: str | None) -> int:
    if failures <= 0:
        return 0
    if error_kind == "auth":
        return 6 * 3600
    return min(3600, 60 * 2 ** (failures - 1))


def plan(conn, now, settings: Settings) -> tuple[str, str, int | None] | None:
    """What to run now, if anything: (kind, trigger, request_id)."""
    req = conn.execute("SELECT id FROM sync_requests WHERE picked_up_at IS NULL ORDER BY id LIMIT 1").fetchone()
    reconcile = get_state(conn, "reconcile_needed") == "1"
    if req:
        return ("reconcile" if reconcile else "incremental"), "manual", req[0]
    failures = int(get_state(conn, "consecutive_failures", "0"))
    last_failure = parse_ts(get_state(conn, "last_failure_at"))
    if failures and last_failure and now < last_failure + timedelta(seconds=backoff_s(failures, get_state(conn, "last_error_kind"))):
        return None
    if get_state(conn, "cursor") is None:
        return "backfill", "startup", None
    if reconcile:
        return "reconcile", "schedule", None
    last_full = parse_ts(get_state(conn, "last_full_sync_at"))
    if last_full is None or now - last_full >= WEEK:
        return "reconcile", "schedule", None
    last_ref = parse_ts(get_state(conn, "last_reference_at"))
    local_hour = now.astimezone(ZoneInfo(settings.timezone)).hour
    if last_ref is None or (now - last_ref >= timedelta(hours=20) and 3 <= local_hour < 6) \
            or now - last_ref >= timedelta(hours=36):
        return "reference", "schedule", None
    next_at = parse_ts(get_state(conn, "next_incremental_at"))
    if next_at is None or now >= next_at:
        return "incremental", "schedule", None
    return None


class Worker:
    def __init__(self, engine: SyncEngine, db: Database, settings: Settings, now=utcnow, ai: OpenAICoach | None = None):
        self.engine, self.db, self.settings, self.now, self.ai = engine, db, settings, now, ai
        self.stopping = False
        self._last_beat = 0.0

    def tick(self) -> dict | None:
        result = self.sync_tick()
        self.coach_tick(force=bool(result and result["status"] == "ok" and (result["workouts_changed"] or result["workouts_deleted"])))
        return result

    def coach_tick(self, force: bool = False) -> int | None:
        """A new suggestion when a sync changed workouts (force), or when coach.due() says so."""
        now = self.now()
        with self.db.read() as conn:
            todo = coach.due(conn, now)
        if force and (todo is None or todo[0] != "manual"):
            todo = ("sync", [])
        if todo is None:
            return None
        trigger, request_ids = todo
        try:
            sid = coach.refresh(self.db, self.settings, trigger, now, now.astimezone(ZoneInfo(self.settings.timezone)).date(), self.ai)
        except Exception:
            log.exception("coach suggestion failed (sync is unaffected)")
            with self.db.tx() as conn:
                set_state(conn, "coach_last_attempt_at", iso(now))
            sid = None
        if request_ids:
            with self.db.tx() as conn:
                conn.executemany("UPDATE coach_requests SET picked_up_at = ?, suggestion_id = ? WHERE id = ?",
                                 [(iso(now), sid, r) for r in request_ids])
        return sid

    def sync_tick(self) -> dict | None:
        now = self.now()
        with self.db.read() as conn:
            todo = plan(conn, now, self.settings)
        if todo is None:
            return None
        kind, trigger, request_id = todo
        if request_id is not None:
            with self.db.tx() as conn:          # one run answers every request waiting now
                conn.execute("UPDATE sync_requests SET picked_up_at = ? WHERE picked_up_at IS NULL", (iso(now),))
        try:
            result = self.engine.run(kind, trigger)
        except Busy:                             # e.g. a CLI sync is running: ask again next tick
            if request_id is not None:
                with self.db.tx() as conn:
                    conn.execute("UPDATE sync_requests SET picked_up_at = NULL WHERE run_id IS NULL AND picked_up_at = ?",
                                 (iso(now),))
            return None
        if result["status"] == "ok" and (result["workouts_changed"] or result["workouts_deleted"] or result["other_changed"]):
            self.rebuild_analytics()
        with self.db.tx() as conn:
            if request_id is not None:
                conn.execute("UPDATE sync_requests SET run_id = ? WHERE run_id IS NULL AND picked_up_at = ?",
                             (result["run_id"], iso(now)))
            if kind in ("incremental", "backfill", "reconcile"):
                jitter = random.randint(0, 59)
                set_state(conn, "next_incremental_at", iso(self.now() + timedelta(seconds=self.settings.sync_interval_s + jitter)))
        return result

    def rebuild_analytics(self, force: bool = True) -> None:
        try:
            res = analytics.refresh(self.db, force=force)
            if res:
                log.info("analytics rebuilt: %s", res)
        except Exception:
            log.exception("analytics rebuild failed (sync data is unaffected)")

    def heartbeat(self) -> None:
        if time.monotonic() - self._last_beat >= HEARTBEAT_S:
            with self.db.tx() as conn:
                set_state(conn, "worker_heartbeat_at", iso(self.now()))
            self._last_beat = time.monotonic()

    def loop(self) -> None:
        while not self.stopping:
            try:
                self.heartbeat()
                self.tick()
            except Exception:                    # never die on a bad tick; systemd restarts only for real crashes
                log.exception("worker tick failed")
                time.sleep(30)
            for _ in range(4):
                if self.stopping:
                    break
                time.sleep(0.5)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    settings = load_settings()
    if settings.demo:
        raise SystemExit("hevy worker: never runs in the demo (HEVY_DEMO=1)")
    db = Database(settings.db_path)
    db.migrate()
    with db.tx() as conn:
        origin = get_meta(conn, "data_origin")
        if origin == "demo" or (settings.is_production and origin not in (None, "hevy")):
            raise SystemExit(f"Refusing to sync real Hevy data into a database holding {origin!r} data")
        if origin is None:
            set_meta(conn, "data_origin", "hevy")
    try:
        key = load_key(settings.api_key_path())
    except AuthError as exc:
        with db.tx() as conn:
            set_state(conn, "last_error_kind", "auth")
            set_state(conn, "last_error", str(exc))
        raise SystemExit(f"hevy worker: {exc}")
    client = HevyClient(key, settings.api_base, gap_s=settings.request_gap_s)
    ai = None
    try:
        openai_key = load_openai_key(settings.openai_key_path())
        ai = OpenAICoach(openai_key, settings.openai_base, settings.coach_model) if openai_key else None
        coach_note = "on" if ai else "off: no OpenAI key installed"
    except CoachError as exc:
        coach_note = f"off: {exc}"
    with db.tx() as conn:
        set_state(conn, "coach_openai", coach_note)
    worker = Worker(SyncEngine(db, client, settings), db, settings, ai=ai)

    def stop(*_):
        worker.stopping = True
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    log.info("hevy worker started (interval %ss, OpenAI coach %s)", settings.sync_interval_s, coach_note)
    worker.rebuild_analytics(force=False)          # first start, or new rules deployed
    worker.loop()
    log.info("hevy worker stopped")


if __name__ == "__main__":
    main()
