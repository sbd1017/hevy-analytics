import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from hevy import normalise
from hevy.client import HevyClient
from hevy.sync import Busy, get_state

D = lambda *a: datetime(*a, tzinfo=timezone.utc)


def q(db, sql, *args):
    with db.read() as c:
        return [tuple(r) for r in c.execute(sql, args).fetchall()]


def one(db, sql, *args):
    return q(db, sql, *args)[0][0]


def snapshot(db):
    """The normalised layer as plain data (ids of autoincrement rows left out)."""
    with db.read() as c:
        return {
            "workouts": [tuple(r) for r in c.execute("SELECT id, title, start_utc, end_utc, duration_s, local_date FROM workouts ORDER BY id")],
            "exercises": [tuple(r) for r in c.execute("SELECT workout_id, position, template_id, title FROM workout_exercises ORDER BY workout_id, position")],
            "sets": [tuple(r) for r in c.execute("SELECT s.workout_id, e.position, s.position, set_type, weight_kg, reps FROM sets s "
                                                  "JOIN workout_exercises e ON e.id = s.workout_exercise_id ORDER BY 1, 2, 3")],
            "templates": [tuple(r) for r in c.execute("SELECT id, title, type, primary_muscle, known_to_hevy FROM exercise_templates ORDER BY id")],
            "muscles": [tuple(r) for r in c.execute("SELECT * FROM template_muscles ORDER BY 1, 2")],
            "issues": [tuple(r) for r in c.execute("SELECT kind, hevy_id, code FROM data_issues ORDER BY 1, 2, 3")],
        }


def seed(fake, n=3):
    ids = []
    for i in range(n):
        ids.append(fake.add_workout(D(2026, 9, 1 + i, 17, 0)))
        fake.tick(5)
    return ids


# ---------------------------------------------------------------- backfill

def test_backfill_stores_raw_and_normalised(engine, db, fake):
    seed(fake, 23)                                  # three pages of workouts
    res = engine.run("incremental", "test")         # no cursor yet: becomes a backfill
    assert res["status"] == "ok" and res["workouts_seen"] == 23 and res["workouts_changed"] == 23
    assert one(db, "SELECT kind FROM sync_runs WHERE id = ?", res["run_id"]) == "backfill"
    assert one(db, "SELECT count(*) FROM raw_objects WHERE kind = 'workout'") == 23
    assert one(db, "SELECT count(*) FROM workouts") == 23
    assert one(db, "SELECT count(*) FROM sets") == 46
    assert one(db, "SELECT count(*) FROM exercise_templates WHERE known_to_hevy = 1") == len(fake.templates)
    assert ("T-BENCH", "triceps", "secondary") in snapshot(db)["muscles"]
    with db.read() as c:
        assert get_state(c, "cursor") == "2026-10-01T13:55:00Z"
        assert get_state(c, "hevy_workout_count") == "23"


def test_sync_twice_changes_nothing(engine, db, fake):
    seed(fake)
    engine.run("backfill", "test")
    before = one(db, "SELECT count(*) FROM raw_objects")
    fake.tick(10)
    res = engine.run("incremental", "test")
    assert res["status"] == "ok" and res["workouts_changed"] == 0
    assert one(db, "SELECT count(*) FROM raw_objects") == before


# ---------------------------------------------------------------- incremental: new, edited, deleted

def test_new_workout_appears(engine, db, fake):
    seed(fake)
    engine.run("backfill", "test")
    fake.tick(30)
    wid = fake.add_workout(D(2026, 10, 1, 18, 0), [("T-SQUAT", [("warmup", 40, 10), ("normal", 100, 5)])])
    res = engine.run("incremental", "test")
    assert res["workouts_changed"] == 1
    assert q(db, "SELECT set_type, weight_kg, reps FROM sets WHERE workout_id = ? ORDER BY position", wid) == \
        [("warmup", 40.0, 10.0), ("normal", 100.0, 5.0)]


def test_edit_keeps_old_version_and_updates_normalised(engine, db, fake):
    wid = seed(fake)[0]
    engine.run("backfill", "test")
    fake.tick(30)
    def heavier(w):
        w["exercises"][0]["sets"][0]["weight_kg"] = 65
        w["title"] = "Push day"
    fake.edit_workout(wid, heavier)
    res = engine.run("incremental", "test")
    assert res["workouts_changed"] == 1
    assert one(db, "SELECT count(*) FROM raw_objects WHERE kind = 'workout' AND hevy_id = ?", wid) == 2
    assert one(db, "SELECT title FROM workouts WHERE id = ?", wid) == "Push day"
    assert one(db, "SELECT weight_kg FROM sets WHERE workout_id = ? AND position = 0", wid) == 65


def test_delete_removes_from_analytics_but_raw_keeps_it(engine, db, fake):
    wid = seed(fake)[1]
    engine.run("backfill", "test")
    fake.tick(30)
    fake.delete_workout(wid)
    res = engine.run("incremental", "test")
    assert res["workouts_deleted"] == 1
    assert one(db, "SELECT count(*) FROM workouts WHERE id = ?", wid) == 0
    assert one(db, "SELECT count(*) FROM sets WHERE workout_id = ?", wid) == 0
    assert one(db, "SELECT count(*) FROM raw_objects WHERE hevy_id = ?", wid) == 1
    assert q(db, "SELECT deleted, tombstone_id IS NOT NULL FROM raw_current WHERE hevy_id = ?", wid) == [(1, 1)]
    assert one(db, "SELECT source FROM raw_tombstones WHERE hevy_id = ?", wid) == "event"


def test_stale_update_after_delete_is_ignored(engine, db, fake):
    wid = seed(fake)[0]
    engine.run("backfill", "test")
    old = dict(fake.workouts[wid])
    fake.tick(30)
    fake.delete_workout(wid)
    engine.run("incremental", "test")
    # The same old payload arrives again (e.g. from an overlapping events page): it must not resurrect the workout.
    from hevy import rawstore
    with db.tx() as c:
        assert rawstore.apply_object(c, "workout", old, None)["changed"] is False
    assert one(db, "SELECT deleted FROM raw_current WHERE hevy_id = ?", wid) == 1


def test_edit_after_delete_brings_it_back(db):
    from hevy import rawstore
    w = {"id": "W1", "updated_at": "2026-10-01T10:00:00Z", "start_time": "2026-10-01T09:00:00Z", "exercises": []}
    with db.tx() as c:
        rawstore.apply_object(c, "workout", w, None)
        assert rawstore.apply_deletion(c, "workout", "W1", "2026-10-01T11:00:00.000Z", "event", None)
        assert rawstore.apply_object(c, "workout", {**w, "updated_at": "2026-10-01T12:00:00Z", "title": "back"}, None)["changed"]
    assert one(db, "SELECT deleted FROM raw_current WHERE hevy_id = 'W1'") == 0


def test_deletion_older_than_an_edit_is_ignored(db):
    from hevy import rawstore
    w = {"id": "W1", "updated_at": "2026-10-01T12:00:00Z", "start_time": "2026-10-01T09:00:00Z", "exercises": []}
    with db.tx() as c:
        rawstore.apply_object(c, "workout", w, None)
        assert not rawstore.apply_deletion(c, "workout", "W1", "2026-10-01T11:00:00Z", "event", None)
    assert one(db, "SELECT deleted FROM raw_current WHERE hevy_id = 'W1'") == 0


def test_older_payload_does_not_replace_newer(db):
    from hevy import rawstore
    base = {"id": "W1", "start_time": "2026-10-01T09:00:00Z", "exercises": []}
    with db.tx() as c:
        rawstore.apply_object(c, "workout", {**base, "updated_at": "2026-10-01T12:00:00Z", "title": "new"}, None)
        res = rawstore.apply_object(c, "workout", {**base, "updated_at": "2026-10-01T10:00:00Z", "title": "old"}, None)
    assert res == {"id": "W1", "new_version": True, "changed": False}
    assert '"title":"new"' in one(db, "SELECT o.payload FROM raw_current c JOIN raw_objects o ON o.id = c.raw_object_id")


def test_event_overlap_uses_24h_before_cursor(engine, db, fake):
    seed(fake)
    engine.run("backfill", "test")
    fake.tick(15)
    engine.run("incremental", "test")
    # backfill at 12:15 sets the cursor; the next run asks from 24 h before it
    assert any("since=2026-09-30T12%3A15%3A00Z" in c for c in fake.calls if "events" in c)


# ---------------------------------------------------------------- failures never corrupt anything

def test_failure_midway_changes_nothing_then_recovers(engine, db, fake):
    seed(fake, 5)
    engine.run("backfill", "test")
    with db.read() as c:
        cursor = get_state(c, "cursor")
    before = snapshot(db)
    fake.tick(30)
    for i in range(12):                                     # 12 new workouts: two pages of events
        fake.add_workout(D(2026, 10, 1, 6, i))
    fake.fail("&page=2&", 503, times=10)                    # events page 2 keeps failing past the retries
    res = engine.run("incremental", "test")
    assert res["status"] == "failed" and res["error_kind"] == "server"
    assert snapshot(db) == before                           # nothing half-applied
    with db.read() as c:
        assert get_state(c, "cursor") == cursor and get_state(c, "consecutive_failures") == "1"
    fake.failures.clear()
    res = engine.run("incremental", "test")
    assert res["status"] == "ok" and res["workouts_changed"] == 12
    with db.read() as c:
        assert get_state(c, "consecutive_failures") == "0" and get_state(c, "last_error") is None


def test_transient_errors_are_retried(engine, db, fake):
    seed(fake, 2)
    fake.fail("/v1/workouts?page=1", 502, times=2)
    fake.fail("/v1/exercise_templates?", 429, times=1)
    res = engine.run("backfill", "test")
    assert res["status"] == "ok" and one(db, "SELECT count(*) FROM workouts") == 2


def test_wrong_key_is_an_auth_failure(db, settings, fake):
    from hevy.sync import SyncEngine
    bad = HevyClient("wrong-key-123", "https://api.test", transport=fake, gap_s=0, sleep=lambda s: None)
    res = SyncEngine(db, bad, settings, now=lambda: fake.now).run("backfill", "test")
    assert res["status"] == "failed" and res["error_kind"] == "auth"
    assert len(fake.calls) == 1                             # no retries against a refused key
    assert "wrong-key" not in (res["error"] or "") and "wrong-key" not in repr(bad)


def test_network_down(engine, db, fake):
    fake.network_down = True
    res = engine.run("backfill", "test")
    assert res["status"] == "failed" and res["error_kind"] == "network"
    assert one(db, "SELECT count(*) FROM raw_objects") == 0


def test_malformed_page_is_bad_data(engine, db, fake):
    seed(fake)
    real = fake.route
    fake.route = lambda path, q: (200, {"page": 1, "workouts": "nope"}) if path == "/v1/workouts" else real(path, q)
    res = engine.run("backfill", "test")
    assert res["status"] == "failed" and res["error_kind"] == "bad_data"


def test_concurrent_runs_are_refused(engine, settings):
    with engine._lock():
        with pytest.raises(Busy):
            engine.run("incremental", "test")


def test_interrupted_run_is_marked_failed(engine, db, fake):
    with db.tx() as c:
        c.execute("INSERT INTO sync_runs (kind, trigger, started_at) VALUES ('incremental', 'test', '2026-09-01T00:00:00Z')")
    engine.run("backfill", "test")
    assert q(db, "SELECT status, error FROM sync_runs ORDER BY id LIMIT 1") == [("failed", "interrupted")]


# ---------------------------------------------------------------- reconcile and the paging-shift guard

def test_reconcile_finds_deletion_without_event(engine, db, fake):
    ids = seed(fake, 4)
    engine.run("backfill", "test")
    fake.tick(60)
    fake.delete_workout(ids[2], record_event=False)
    res = engine.run("reconcile", "test")
    assert res["workouts_deleted"] == 1
    assert one(db, "SELECT source FROM raw_tombstones WHERE hevy_id = ?", ids[2]) == "missing_from_full_list"
    assert one(db, "SELECT count(*) FROM workouts") == 3


def test_paging_shift_never_deletes_history(engine, db, fake):
    """A workout deleted while we page pulls the next one back onto a page already served, so it is never
    listed. Only the deleted one may be tombstoned; the skipped one is checked by id and kept."""
    ids = [fake.add_workout(D(2026, 9, 1 + i, 17, 0)) for i in range(20)]
    engine.run("backfill", "test")
    fake.tick(60)
    newest_first = ids[::-1]
    removed = []
    def shift(path, page):
        if path == "/v1/workouts" and page == 2 and not removed:
            removed.append(newest_first[0])
            fake.delete_workout(newest_first[0])         # everything moves up one: newest_first[10] is skipped
    fake.on_page = shift
    res = engine.run("reconcile", "test")
    assert res["status"] == "ok" and res["workouts_deleted"] == 0   # it was listed on page 1 before it went
    assert one(db, "SELECT count(*) FROM workouts WHERE id = ?", newest_first[10]) == 1
    assert f"/v1/workouts/{newest_first[10]}" in fake.calls     # the skipped one was checked by id
    fake.on_page = None
    res = engine.run("incremental", "test")                       # its deletion event arrives next time
    assert res["workouts_deleted"] == 1 and one(db, "SELECT count(*) FROM workouts") == 19


def test_reference_run_flags_count_mismatch(engine, db, fake):
    ids = seed(fake, 3)
    engine.run("backfill", "test")
    fake.delete_workout(ids[0], record_event=False)
    res = engine.run("reference", "test")
    assert res["count_mismatch"] is True
    with db.read() as c:
        assert get_state(c, "reconcile_needed") == "1"
    engine.run("reconcile", "test")
    with db.read() as c:
        assert get_state(c, "reconcile_needed") is None


def test_removed_template_kept_for_history(engine, db, fake):
    seed(fake)
    engine.run("backfill", "test")
    del fake.templates["T-BENCH"]
    engine.run("reference", "test")
    assert q(db, "SELECT title, known_to_hevy FROM exercise_templates WHERE id = 'T-BENCH'") == [("Bench Press (Barbell)", 0)]
    assert one(db, "SELECT count(*) FROM workout_exercises WHERE template_id = 'T-BENCH'") == 3


def test_routine_400_counts_as_gone(engine, db, fake):
    fake.routines["R1"] = {"id": "R1", "title": "Push", "folder_id": None, "updated_at": "2026-09-01T00:00:00Z",
                           "created_at": "2026-09-01T00:00:00Z", "exercises": []}
    engine.run("backfill", "test")
    assert one(db, "SELECT count(*) FROM routines") == 1
    del fake.routines["R1"]
    engine.run("reference", "test")
    assert one(db, "SELECT count(*) FROM routines") == 0


# ---------------------------------------------------------------- templates referenced but missing

def test_custom_template_fetched_by_id(engine, db, fake):
    seed(fake)
    engine.run("backfill", "test")
    fake.templates["T-CUSTOM"] = {"id": "T-CUSTOM", "title": "Lu Raises (Plate)", "type": "weight_reps",
                                  "primary_muscle_group": "shoulders", "secondary_muscle_groups": [], "equipment": "plate",
                                  "is_custom": True}
    fake.tick(10)
    fake.add_workout(D(2026, 10, 1, 18, 0), [("T-CUSTOM", [("normal", 10, 12)])])
    engine.run("incremental", "test")
    assert q(db, "SELECT title, is_custom, known_to_hevy FROM exercise_templates WHERE id = 'T-CUSTOM'") == [("Lu Raises (Plate)", 1, 1)]


def test_unavailable_template_gets_placeholder_and_issue(engine, db, fake):
    fake.add_workout(D(2026, 9, 1, 17, 0), [("T-GONE", [("normal", 20, 10)])])
    fake.workouts["W0001"]["exercises"][0]["title"] = "Old Custom Move"
    fake.events[-1]["workout"]["exercises"][0]["title"] = "Old Custom Move"
    res = engine.run("backfill", "test")
    assert res["status"] == "ok"
    assert q(db, "SELECT title, type, known_to_hevy FROM exercise_templates WHERE id = 'T-GONE'") == [("Old Custom Move", "unknown", 0)]
    assert ("exercise_template", "T-GONE", "missing_template") in snapshot(db)["issues"]
    assert one(db, "SELECT count(*) FROM sets") == 1


# ---------------------------------------------------------------- rebuilding from raw

def test_rebuild_from_raw_matches_incremental_state(engine, db, fake, settings):
    ids = seed(fake, 6)
    engine.run("backfill", "test")
    fake.tick(10)
    fake.edit_workout(ids[0], lambda w: w["exercises"][0]["sets"].append(
        {"index": 2, "type": "failure", "weight_kg": 60, "reps": 6, "distance_meters": None, "duration_seconds": None,
         "rpe": 10, "custom_metric": None}))
    fake.delete_workout(ids[3])
    fake.add_workout(D(2026, 10, 1, 8, 0), [("T-PULLDOWN", [("normal", 50, 10)]), ("T-PLANK", [])])
    engine.run("incremental", "test")
    incremental = snapshot(db)
    with db.tx() as c:
        normalise.rebuild_all(c, settings.timezone)
    assert snapshot(db) == incremental


def test_raw_tables_are_append_only(engine, db, fake):
    seed(fake, 1)
    engine.run("backfill", "test")
    with db.tx() as c, pytest.raises(sqlite3.IntegrityError):
        c.execute("UPDATE raw_objects SET payload = '{}'")
    with db.tx() as c, pytest.raises(sqlite3.IntegrityError):
        c.execute("DELETE FROM raw_objects")


def test_hevy_empty_events_reply_quirk(engine, db, fake):
    """With nothing to report, Hevy's events endpoint replies with an empty list called "workouts" (seen Oct 2026)."""
    seed(fake)
    engine.run("backfill", "test")
    real = fake.route
    fake.route = lambda path, q: (200, {"page": 1, "page_count": 1, "workouts": []}) if path == "/v1/workouts/events" else real(path, q)
    fake.tick(30)
    res = engine.run("incremental", "test")
    assert res["status"] == "ok" and res["workouts_changed"] == 0
    # but a non-empty list under an unexpected name is still refused rather than guessed at
    fake.route = lambda path, q: (200, {"page": 1, "page_count": 1, "things": [{"id": "x"}]}) if path == "/v1/workouts/events" else real(path, q)
    assert engine.run("incremental", "test")["error_kind"] == "bad_data"
