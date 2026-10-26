from datetime import datetime, timedelta, timezone

from hevy import normalise, rawstore
from hevy.client import HevyClient, RateLimited
from hevy.sync import set_state
from hevy.worker import Worker, backoff_s, plan

from .fake_hevy import KEY

D = lambda *a: datetime(*a, tzinfo=timezone.utc)


def put(db, settings, payload):
    with db.tx() as c:
        rawstore.apply_object(c, "workout", payload, None)
        normalise.rebuild_workout(c, payload["id"], settings.timezone)


def rows(db, sql, *args):
    with db.read() as c:
        return [tuple(r) for r in c.execute(sql, args)]


def workout(**kw):
    base = {"id": "W1", "title": "t", "start_time": "2026-10-01T09:00:00Z", "end_time": "2026-10-01T10:00:00Z",
            "updated_at": "2026-10-01T10:00:00Z", "exercises": []}
    return {**base, **kw}


# ---------------------------------------------------------------- normaliser

def test_local_date_follows_london_time(db, settings):
    # 23:30 UTC on 30 Mar 2026 is 00:30 BST on 31 Mar; in winter 23:30 UTC is still the same day.
    put(db, settings, workout(id="S", start_time="2026-03-30T23:30:00Z", end_time="2026-03-31T00:30:00Z"))
    put(db, settings, workout(id="W", start_time="2026-01-10T23:30:00Z", end_time="2026-01-11T00:30:00Z"))
    assert rows(db, "SELECT id, local_date FROM workouts ORDER BY id") == [("S", "2026-03-31"), ("W", "2026-01-10")]


def test_accepts_hevy_timestamp_formats(db, settings):
    put(db, settings, workout(start_time="2026-09-23T16:00:36+00:00", end_time="2026-09-23T17:01:53.250Z"))
    assert rows(db, "SELECT start_utc, end_utc, duration_s FROM workouts") == [("2026-09-23T16:00:36Z", "2026-09-23T17:01:53Z", 3677)]


def test_bad_times_are_kept_and_reported(db, settings):
    put(db, settings, workout(end_time="2026-10-01T08:00:00Z"))
    assert rows(db, "SELECT duration_s FROM workouts") == [(None,)]
    assert rows(db, "SELECT code FROM data_issues") == [("end_before_start",)]
    put(db, settings, workout(id="W2", start_time="not a time"))
    assert rows(db, "SELECT count(*) FROM workouts WHERE id = 'W2'") == [(0,)]
    assert ("unparseable",) in rows(db, "SELECT code FROM data_issues WHERE hevy_id = 'W2'")


def test_logged_title_and_order_preserved(db, settings):
    with db.tx() as c:
        rawstore.apply_object(c, "exercise_template", {"id": "T1", "title": "Bulgarian Split Squat (Dumbbell)",
                                                       "type": "weight_reps", "primary_muscle_group": "quadriceps",
                                                       "secondary_muscle_groups": ["glutes", "quadriceps"]}, None)
        normalise.rebuild_template(c, "T1")
    ex = lambda i, sets: {"index": i, "title": "Bulgarian Split Squat", "exercise_template_id": "T1", "sets": sets}
    s = lambda i, t="normal": {"index": i, "type": t, "weight_kg": 20, "reps": 8}
    put(db, settings, workout(exercises=[ex(3, [s(1), s(0, "warmup")]), ex(1, [s(0, "myo-rep")])]))
    assert rows(db, "SELECT position, title FROM workout_exercises ORDER BY position") == \
        [(0, "Bulgarian Split Squat"), (1, "Bulgarian Split Squat")]
    assert rows(db, "SELECT e.position, s.position, s.set_type FROM sets s JOIN workout_exercises e ON e.id = s.workout_exercise_id "
                    "ORDER BY 1, 2") == [(0, 0, "myo-rep"), (1, 0, "warmup"), (1, 1, "normal")]
    assert {r[0] for r in rows(db, "SELECT code FROM data_issues")} == {"exercise_index_gaps", "unknown_set_type"}
    # a muscle listed as both primary and secondary counts once, as primary
    assert rows(db, "SELECT muscle, role FROM template_muscles ORDER BY muscle") == [("glutes", "secondary"), ("quadriceps", "primary")]


def test_numbers_only(db, settings):
    put(db, settings, workout(exercises=[{"index": 0, "title": "x", "exercise_template_id": "TX",
                                         "sets": [{"index": 0, "type": "normal", "weight_kg": "60", "reps": True, "rpe": 9.5}]}]))
    assert rows(db, "SELECT weight_kg, reps, rpe FROM sets") == [(None, None, 9.5)]


# ---------------------------------------------------------------- client

def test_client_honours_retry_after_and_gives_up(fake):
    waits = []
    c = HevyClient(KEY, "https://api.test", transport=fake, gap_s=0, sleep=waits.append, max_attempts=3)
    fake.failures.append({"match": "count", "status": 429, "times": 5})
    real = fake.__call__
    fake_with_header = lambda m, u, h: (lambda r: (r[0], {"Retry-After": "7"} if r[0] == 429 else r[1], r[2]))(real(m, u, h))
    c.transport = fake_with_header
    try:
        c.workout_count()
        assert False
    except RateLimited:
        pass
    assert len(waits) == 2 and all(w >= 7 for w in waits)


# ---------------------------------------------------------------- worker schedule

def test_backoff():
    assert [backoff_s(n, "server") for n in range(0, 9)] == [0, 60, 120, 240, 480, 960, 1920, 3600, 3600]
    assert backoff_s(1, "auth") == 6 * 3600


def test_plan_sequence(db, settings):
    now = D(2026, 10, 19, 12, 0)
    with db.tx() as c:
        assert plan(c, now, settings) == ("backfill", "startup", None)
        for k, v in {"cursor": "2026-10-19T11:00:00Z", "last_full_sync_at": "2026-10-19T00:00:00Z",
                     "last_reference_at": "2026-10-19T03:30:00Z", "next_incremental_at": "2026-10-19T12:05:00Z"}.items():
            set_state(c, k, v)
        assert plan(c, now, settings) is None
        assert plan(c, now + timedelta(minutes=6), settings) == ("incremental", "schedule", None)
        c.execute("INSERT INTO sync_requests (requested_at, source) VALUES ('2026-10-19T12:00:00Z', 'web')")
        assert plan(c, now, settings)[:2] == ("incremental", "manual")
        c.execute("UPDATE sync_requests SET picked_up_at = 'x'")
        set_state(c, "consecutive_failures", 3)
        set_state(c, "last_error_kind", "network")
        set_state(c, "last_failure_at", "2026-10-19T11:58:00Z")
        set_state(c, "next_incremental_at", "2026-10-19T11:59:00Z")                   # overdue, but ...
        assert plan(c, now + timedelta(minutes=1), settings) is None                 # ... backing off 4 min from 11:58
        assert plan(c, now + timedelta(minutes=3), settings) == ("incremental", "schedule", None)
        set_state(c, "consecutive_failures", 0)
        set_state(c, "reconcile_needed", "1")
        assert plan(c, now, settings) == ("reconcile", "schedule", None)
        set_state(c, "reconcile_needed", None)
        set_state(c, "last_full_sync_at", "2026-10-11T00:00:00Z")
        assert plan(c, now, settings) == ("reconcile", "schedule", None)              # weekly
        set_state(c, "last_full_sync_at", "2026-10-19T00:00:00Z")
        set_state(c, "last_reference_at", "2026-10-18T03:30:00Z")
        assert plan(c, D(2026, 10, 19, 3, 40), settings) == ("reference", "schedule", None)   # early hours, London


def test_manual_request_answered_by_one_run(engine, db, fake, settings):
    fake.add_workout(D(2026, 9, 1, 17, 0))
    w = Worker(engine, db, settings, now=lambda: fake.now)
    with db.tx() as c:
        for _ in range(2):
            c.execute("INSERT INTO sync_requests (requested_at, source) VALUES ('2026-10-01T12:00:00Z', 'web')")
    res = w.tick()
    assert res["status"] == "ok"
    assert rows(db, "SELECT count(*), count(DISTINCT run_id) FROM sync_requests WHERE run_id IS NOT NULL") == [(2, 1)]
    with db.read() as c:
        assert c.execute("SELECT value FROM sync_state WHERE key = 'next_incremental_at'").fetchone()[0] > "2026-10-01T12:10"
