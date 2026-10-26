"""Analytics on hand-built histories with answers worked out by hand (docs/METRICS.md)."""
from datetime import date, timedelta

import pytest

from hevy.analytics import periods as P
from hevy.analytics import queries as Q
from hevy.analytics.rules import brzycki, epley
from hevy.analytics.stats import theil_sen

from .builder import History

D = date


@pytest.fixture
def h(db):
    return History(db)


def approx(x, rel=1e-6):
    return pytest.approx(x, rel=rel)


# ---------------------------------------------------------------- volume, sets, e1RM

def test_volume_counts_working_load_sets_only(h):
    wid = h.day(D(2026, 9, 1),
                ("BENCH", [("warmup", 40, 10), ("normal", 100, 5), ("failure", 100, 4), ("dropset", 80, 6)]),
                ("PUSHUP", [{"reps": 20}]),
                ("FARMER", [{"weight_kg": 40, "distance_meters": 50}]),
                ("HYPER", [("normal", 20, 10)]),
                ("PLANK", [{"duration_seconds": 60}]),
                ("WALK", [{"distance_meters": 1000, "duration_seconds": 600}]))
    h.build()
    # bench 500 + 400 + 480 (warm-up excluded) + weighted hyperextension 200; push-ups, carries, planks, walks add none
    assert h.q("SELECT kind, working_sets, warmup_sets, reps, volume_kg, distance_m, cardio_duration_s FROM an_workouts") == \
        [("gym", 8, 1, 45.0, 1580.0, 1000.0, 600.0)]
    assert h.q("SELECT measure, count(*) FROM an_sets WHERE workout_id = ? GROUP BY measure ORDER BY 1", wid) == \
        [("added_load", 1), ("cardio", 1), ("load_distance", 1), ("load_reps", 4), ("reps", 1), ("time", 1)]


def test_e1rm_epley_and_limits(h):
    h.day(D(2026, 9, 1), ("BENCH", [("normal", 100, 5), ("normal", 120, 1), ("normal", 60, 12), ("normal", 50, 13),
                                    ("warmup", 140, 1)]))
    h.build()
    rows = h.q("SELECT weight_kg, reps, e1rm_kg, e1rm_note FROM an_sets ORDER BY set_position")
    assert rows[0][2] == approx(116.6666667) and rows[1][2] == 120 and rows[2][2] == approx(84.0)
    assert rows[3][2:] == (None, "high_reps")
    assert rows[4][2:] == (None, None)                  # warm-ups are never estimated
    assert h.q("SELECT best_e1rm_kg FROM an_exercise_sessions")[0][0] == 120
    assert epley(100, 5) == approx(116.6666667) and brzycki(100, 5) == approx(112.5)


def test_suspect_sets_kept_but_excluded(h):
    for i in range(6):
        h.day(D(2026, 9, 1) + timedelta(days=i), ("CURL", [("normal", 12, 10)]), ("ROW", [("normal", 20, 10)]))
    h.day(D(2026, 9, 10), ("CURL", [("normal", 12, 129)]), ("ROW", [("normal", 80, 4)]))
    h.build()
    assert h.q("SELECT count(*) FROM an_sets WHERE suspect IS NOT NULL") == [(2,)]
    assert h.q("SELECT working_sets, volume_kg FROM an_workouts WHERE local_date = '2026-09-10'") == [(2, 0.0)]
    assert h.q("SELECT count(*) FROM an_prs WHERE local_date = '2026-09-10'") == [(0,)]     # a typo is never a record
    assert "4x" in h.q("SELECT suspect FROM an_sets WHERE reps = 129")[0][0]


# ---------------------------------------------------------------- records

BENCH_HISTORY = [
    (D(2026, 9, 1), [("normal", 100, 5)]),                          # 1: baseline, no records
    (D(2026, 9, 3), [("normal", 100, 5), ("normal", 100, 6)]),      # 2: e1RM, set vol, session vol, reps@100 (early)
    (D(2026, 9, 5), [("normal", 100, 6)]),                          # 3: ties only: nothing
    (D(2026, 9, 8), [("normal", 102.5, 5)]),                        # 4: weight +2.5% (meaningful)
    (D(2026, 9, 10), [("normal", 100, 7)]),                         # 5: e1RM, reps@100 (6 -> 7), set volume
    (D(2026, 9, 12), [("normal", 103, 5)]),                         # 6: weight +0.5%: a record, not meaningful
    (D(2026, 9, 14), [("normal", 90, 8)]),                          # 7: 8 reps beats 7 at >= 90 kg; set volume 720
]


def bench(h):
    for d, s in BENCH_HISTORY:
        h.day(d, ("BENCH", s))
    h.build()


def test_records_follow_the_rules(h):
    bench(h)
    got = h.q("SELECT local_date, category, value, previous, early, meaningful FROM an_prs WHERE template_id = 'BENCH' "
              "ORDER BY start_ms, category")
    assert got == [
        ("2026-09-03", "e1rm", approx(120.0), approx(116.6666667), 1, 0),
        ("2026-09-03", "reps_at_weight", 6.0, 5.0, 1, 0),
        ("2026-09-03", "session_volume", 1100.0, 500.0, 1, 0),
        ("2026-09-03", "set_volume", 600.0, 500.0, 1, 0),
        ("2026-09-08", "weight", 102.5, 100.0, 0, 1),
        ("2026-09-10", "e1rm", approx(123.3333333), approx(120.0), 0, 1),
        ("2026-09-10", "reps_at_weight", 7.0, 6.0, 0, 1),
        ("2026-09-10", "set_volume", 700.0, 600.0, 0, 1),
        ("2026-09-12", "weight", 103.0, 102.5, 0, 0),
        ("2026-09-14", "reps_at_weight", 8.0, 7.0, 0, 1),
        ("2026-09-14", "set_volume", 720.0, 700.0, 0, 1),            # 90 x 8 = 720 kg beats 700
    ]
    assert h.q("SELECT detail FROM an_prs WHERE category = 'reps_at_weight' ORDER BY start_ms DESC LIMIT 1") == \
        [("90 kg × 8 (was × 7)",)]
    # whole-workout record: 1,100 kg on 3 Sep beat 500 kg (second gym workout, so early)
    assert h.q("SELECT local_date, value, early FROM an_prs WHERE category = 'workout_volume'") == [("2026-09-03", 1100.0, 1)]
    assert h.q("SELECT session_index FROM an_exercise_sessions ORDER BY start_ms") == [(i,) for i in range(1, 8)]


def test_editing_an_old_workout_changes_later_records(h):
    bench(h)
    h.n = 0
    h.day(D(2026, 9, 1), ("BENCH", [("normal", 100, 8)]), wid="W001")      # session 1 was really 100 x 8
    h.build()
    cats = h.q("SELECT category FROM an_prs WHERE local_date = '2026-09-03' ORDER BY 1")
    assert ("e1rm",) not in cats and ("reps_at_weight",) not in cats       # 100 x 6 no longer beats anything


def test_non_load_records(h):
    for i, (reps, hold, dist) in enumerate([(20, 60, 1000), (25, 55, 2500), (25, 90, 2400)]):
        h.day(D(2026, 9, 1) + timedelta(days=i), ("PUSHUP", [{"reps": reps}]), ("PLANK", [{"duration_seconds": hold}]),
              ("WALK", [{"distance_meters": dist, "duration_seconds": 900}]))
    h.build()
    assert h.q("SELECT local_date, category, value FROM an_prs WHERE template_id IS NOT NULL ORDER BY start_ms, category") == [
        ("2026-09-02", "longest_distance", 2500.0), ("2026-09-02", "max_reps", 25.0), ("2026-09-03", "longest_hold", 90.0)]


# ---------------------------------------------------------------- muscles, kinds, days

def test_muscle_credit_primary_one_secondary_half(h):
    h.day(D(2026, 9, 1), ("BENCH", [("normal", 100, 5)] * 3), ("ROW", [("normal", 30, 10)] * 2), ("CURL", [("normal", 15, 10)]))
    h.build()
    got = {m: (s, v, p, w) for m, s, v, p, w in h.q("SELECT muscle, sets, volume_kg, primary_sets, workouts FROM an_muscle_days")}
    assert got["chest"] == (3.0, 1500.0, 3, 1)
    assert got["triceps"] == (1.5, 750.0, 0, 1) and got["shoulders"] == (1.5, 750.0, 0, 1)
    assert got["upper_back"] == (2.0, 600.0, 2, 1) and got["lats"] == (1.0, 300.0, 0, 1)
    assert got["biceps"] == (2.0, 450.0, 1, 1)            # 1 curl set (150 kg) + half of 2 row sets (half of 600 kg)


def test_cardio_only_workouts_and_days(h):
    h.day(D(2026, 9, 1), ("WALK", [{"distance_meters": 5000, "duration_seconds": 3000}]), minutes=300, hour=8)
    h.day(D(2026, 9, 1), ("WALK", [{"distance_meters": 500, "duration_seconds": 300}]), ("PLANK", [{"duration_seconds": 60}]),
          minutes=30, hour=18)
    h.build()
    assert h.q("SELECT kind FROM an_workouts ORDER BY start_ms") == [("cardio",), ("gym",)]
    assert h.q("SELECT workouts, gym_sessions, cardio_sessions, duration_s, gym_duration_s, distance_m FROM an_days") == \
        [(2, 1, 1, 19800, 1800, 5500.0)]


def test_local_dates_in_london(h):
    h.day(D(2026, 7, 1), ("CURL", [("normal", 10, 10)]), hour=23)       # 23:00 UTC = 00:00 BST on 2 July
    h.build()
    assert h.q("SELECT local_date FROM an_days") == [("2026-07-02",)]


def test_rebuild_is_deterministic(h):
    bench(h)
    first = h.q("SELECT * FROM an_prs ORDER BY id"), h.q("SELECT * FROM an_days"), h.q("SELECT * FROM an_muscle_days ORDER BY 1, 2")
    h.build()
    assert (h.q("SELECT * FROM an_prs ORDER BY id"), h.q("SELECT * FROM an_days"), h.q("SELECT * FROM an_muscle_days ORDER BY 1, 2")) == first


# ---------------------------------------------------------------- date ranges

@pytest.mark.parametrize("key,today,start,prev_start,prev_end", [
    ("7d", D(2026, 10, 19), D(2026, 10, 13), D(2026, 10, 6), D(2026, 10, 12)),
    ("28d", D(2026, 10, 19), D(2026, 9, 22), D(2026, 8, 25), D(2026, 9, 21)),
    ("3m", D(2026, 5, 31), D(2026, 3, 1), D(2025, 11, 29), D(2026, 2, 28)),      # Feb 31 clamps to Feb 28
    ("1y", D(2024, 2, 29), D(2023, 3, 1), D(2022, 2, 28), D(2023, 2, 28)),      # 366 days each
    ("week", D(2026, 10, 21), D(2026, 10, 19), D(2026, 10, 12), D(2026, 10, 14)),  # Mon-Wed vs Mon-Wed
    ("month", D(2026, 3, 31), D(2026, 3, 1), D(2026, 2, 1), D(2026, 2, 28)),      # clamped to February
    ("year", D(2024, 12, 31), D(2024, 1, 1), D(2023, 1, 1), D(2023, 12, 31)),     # leap year
    ("5y", D(2026, 10, 20), D(2021, 10, 21), D(2016, 10, 21), D(2021, 10, 20)),
    ("10y", D(2026, 10, 20), D(2016, 10, 21), D(2006, 10, 22), D(2016, 10, 20)),     # same number of days, so one day later
])
def test_periods(key, today, start, prev_start, prev_end):
    p = P.resolve(key, today)
    assert (p.start, p.end, p.prev_start, p.prev_end) == (start, today, prev_start, prev_end)
    if key not in ("week", "month", "year"):
        assert (p.prev_end - p.prev_start).days == (p.end - p.start).days     # same length


def test_last_month_and_last_year():
    p = P.resolve("lastmonth", D(2026, 3, 15))
    assert (p.start, p.end, p.prev_start, p.prev_end, p.label) == (D(2026, 2, 1), D(2026, 2, 28), D(2026, 1, 1), D(2026, 1, 31), "February 2026")
    p = P.resolve("lastmonth", D(2026, 1, 3))
    assert (p.start, p.end, p.prev_start, p.prev_end) == (D(2025, 12, 1), D(2025, 12, 31), D(2025, 11, 1), D(2025, 11, 30))
    p = P.resolve("lastyear", D(2026, 10, 20))
    assert (p.start, p.end, p.prev_start, p.prev_end, p.label) == (D(2025, 1, 1), D(2025, 12, 31), D(2024, 1, 1), D(2024, 12, 31), "2025")


def test_all_time_and_custom():
    p = P.resolve("all", D(2026, 10, 19), first=D(2023, 11, 23))
    assert (p.start, p.prev_start) == (D(2023, 11, 23), None)
    p = P.resolve("custom", D(2026, 10, 19), start=D(2026, 1, 1), end=D(2026, 1, 31))
    assert (p.prev_start, p.prev_end) == (D(2025, 12, 1), D(2025, 12, 31))
    for bad in [dict(start=D(2026, 2, 1), end=D(2026, 1, 1)), dict(start=None, end=D(2026, 1, 1))]:
        with pytest.raises(ValueError):
            P.resolve("custom", D(2026, 10, 19), **bad)
    with pytest.raises(ValueError):
        P.resolve("forever", D(2026, 10, 19))


# ---------------------------------------------------------------- comparisons and strength change

def strength_history(h, curl_sets=(("normal", 20, 8),), with_squat=True):
    for d in (D(2026, 9, 5), D(2026, 9, 12)):
        h.day(d, ("BENCH", [("normal", 100, 5)]), *([("SQUAT", [("normal", 140, 5)])] if with_squat else []),
              ("CURL", [("normal", 20, 10)]))
    for d in (D(2026, 10, 5), D(2026, 10, 12)):
        h.day(d, ("BENCH", [("normal", 105, 5)]), *([("SQUAT", [("normal", 140, 5)])] if with_squat else []),
              ("CURL", list(curl_sets)))
    h.build()


def test_strength_change_is_median_of_exercises(h, db):
    strength_history(h)
    period = P.resolve("28d", D(2026, 10, 28))
    with db.read() as c:
        s = Q.strength_change(c, period)
        assert s["available"] and s["n"] == 3
        assert {x["template_id"]: x["change_pct"] for x in s["exercises"]} == {"BENCH": 5.0, "SQUAT": 0.0, "CURL": -5.0}
        assert s["median_pct"] == 0.0
        assert Q.most_improved(s)["template_id"] == "BENCH"
        summ = Q.summary(c, period, D(2026, 10, 28))
    assert summ["change"]["available"] and summ["change"]["volume_kg"] == pytest.approx(
        (2 * (525 + 700 + 160) - 2 * (500 + 700 + 200)) / (2 * (500 + 700 + 200)) * 100, abs=0.05)


def test_strength_change_needs_three_exercises(h, db):
    strength_history(h, with_squat=False)
    with db.read() as c:
        s = Q.strength_change(c, P.resolve("28d", D(2026, 10, 28)))
    assert not s["available"] and "at least 3 exercises" in s["reason"]


def test_small_samples_are_not_compared(h, db):
    h.day(D(2026, 9, 20), ("BENCH", [("normal", 100, 5)]))
    h.day(D(2026, 10, 10), ("BENCH", [("normal", 100, 5)]))
    h.day(D(2026, 10, 12), ("BENCH", [("normal", 100, 5)]))
    h.build()
    with db.read() as c:
        summ = Q.summary(c, P.resolve("28d", D(2026, 10, 28)), D(2026, 10, 28))
    assert summ["change"] == {"available": False, "reason": "needs at least 2 gym sessions in both periods", "gym_sessions": [2, 1]}
    assert not summ["strength"]["available"]
    assert summ["totals"]["gym_sessions"] == 2 and summ["totals"]["volume_kg"] == 1000.0


def test_trained_less_than_usual(h, db):
    start = D(2026, 10, 1)
    for w in range(1, 13):                                  # 12 weeks before: 3 curl + 3 bench sets a week
        h.day(start - timedelta(weeks=w), ("CURL", [("normal", 15, 10)] * 3), ("BENCH", [("normal", 80, 8)] * 3))
    for w in range(4):                                      # this period: bench as usual, curls only once
        h.day(start + timedelta(weeks=w), ("BENCH", [("normal", 80, 8)] * 3), *([("CURL", [("normal", 15, 10)] * 3)] if w == 0 else []))
    h.build()
    with db.read() as c:
        less = Q.less_than_usual(c, P.resolve("28d", D(2026, 10, 28)))
    assert [(x["muscle"], x["usual_sets_per_week"], x["sets_per_week"]) for x in less] == [("biceps", 3.0, 0.8)]


def test_streaks(h, db):
    for d in (D(2026, 9, 8), D(2026, 9, 15), D(2026, 9, 22), D(2026, 8, 3)):
        h.day(d, ("CURL", [("normal", 10, 10)]))
    h.build()
    with db.read() as c:
        assert Q.streaks(c, D(2026, 10, 1))["any"]["current_weeks"] == 3      # this week isn't over yet
        assert Q.streaks(c, D(2026, 10, 6))["any"]["current_weeks"] == 0      # a whole week was missed
        s = Q.streaks(c, D(2026, 10, 1))["gym"]
    assert (s["longest_weeks"], s["longest_from"], s["longest_to"]) == (3, "2026-09-07", "2026-09-27")


# ---------------------------------------------------------------- trends

def test_theil_sen_ignores_one_outlier():
    pts = [(i * 7.0, 100 + i) for i in range(8)]
    assert theil_sen(pts) == approx(1 / 7)
    pts[4] = (28.0, 150.0)
    assert theil_sen(pts) == approx(1 / 7)


def test_exercise_trend_and_minimums(h, db):
    for i in range(8):
        e = 100 + i                                          # e1RM rises 1 kg a week (1 rep sets: e1RM = load)
        h.day(D(2026, 7, 1) + timedelta(weeks=i), ("BENCH", [("normal", e if i != 4 else 150, 1)]))
    h.build()
    with db.read() as c:
        t = Q.trend(c, "BENCH")
        assert t["available"] and t["kg_per_month"] == pytest.approx(30.44 / 7, abs=0.01)
        cutoff = h.q("SELECT start_ms FROM an_exercise_sessions ORDER BY start_ms")[2][0]
        assert Q.trend(c, "BENCH", until_ms=cutoff)["reason"].startswith("needs 4 sessions")


def test_trend_needs_three_weeks(h, db):
    for i in range(5):
        h.day(D(2026, 9, 1) + timedelta(days=i * 3), ("BENCH", [("normal", 100 + i, 1)]))
    h.build()
    with db.read() as c:
        assert "span 21 days" in Q.trend(c, "BENCH")["reason"].replace("need to span", "span")


# ---------------------------------------------------------------- read models

def test_exercise_detail(h, db):
    bench(h)
    with db.read() as c:
        d = Q.exercise_detail(c, "BENCH")
    lt = d["lifetime"]
    assert lt["sessions"] == 7 and lt["first"] == "2026-09-01" and lt["last"] == "2026-09-14"
    assert lt["best_e1rm"]["kg"] == 123.3 and lt["best_e1rm"]["reps"] == 7
    assert lt["heaviest"]["weight_kg"] == 103 and lt["records"] == 11          # the workout-volume record isn't the exercise's
    assert lt["positions"] == {"1st": 7, "2nd": 0, "3rd": 0, "4th or later": 0}
    assert [s["records"] for s in d["series"]][1] == ["e1rm", "reps_at_weight", "session_volume", "set_volume"]
    assert d["template"]["secondary_muscles"] == ["shoulders", "triceps"]


def test_workout_detail(h, db):
    bench(h)
    wid = h.q("SELECT workout_id FROM an_workouts WHERE local_date = '2026-09-10'")[0][0]
    with db.read() as c:
        w = Q.workout_detail(c, wid)
        assert Q.workout_detail(c, "nope") is None
    assert w["volume_kg"] == 700.0 and len(w["exercises"]) == 1 and w["exercises"][0]["sets"][0]["e1rm_kg"] == 123.3
    assert sorted(p["category"] for p in w["records"]) == ["e1rm", "reps_at_weight", "set_volume"]
    assert w["muscles"][0] == {"muscle": "chest", "sets": 1.0}
    assert w["compared_with_earlier"]["workouts"] == 4


def test_break_is_reported_once_not_per_muscle(h, db):
    for w in range(1, 13):
        h.day(D(2026, 10, 1) - timedelta(weeks=w), ("CURL", [("normal", 15, 10)] * 3), ("BENCH", [("normal", 80, 8)] * 3))
    h.build()
    with db.read() as c:
        s = Q.summary(c, P.resolve("28d", D(2026, 10, 28)), D(2026, 10, 28))
    assert s["no_gym_sessions"] is True and s["trained_less_than_usual"] == []
    assert s["days_since_last_gym_session"] == 34


def test_series_buckets_fill_gaps(h, db):
    h.day(D(2026, 9, 2), ("BENCH", [("normal", 100, 5)]))                    # Wed, week of 31 Aug
    h.day(D(2026, 9, 3), ("BENCH", [("normal", 100, 5)]))
    h.day(D(2026, 9, 17), ("WALK", [{"distance_meters": 3000, "duration_seconds": 1800}]))   # week of 14 Sep
    h.build()
    with db.read() as c:
        weeks = Q.series(c, D(2026, 9, 1), D(2026, 9, 20), "week")
        months = Q.series(c, D(2026, 8, 15), D(2026, 9, 30), "month")
        days = Q.series(c, D(2026, 9, 1), D(2026, 9, 3), "day")
        with pytest.raises(ValueError):
            Q.series(c, D(2026, 9, 1), D(2026, 9, 3), "hour")
    assert [(w["start"], w["gym_sessions"], w["cardio_sessions"], w["volume_kg"]) for w in weeks] == [
        ("2026-08-31", 2, 0, 1000.0), ("2026-09-07", 0, 0, 0.0), ("2026-09-14", 0, 1, 0.0)]
    assert [(m["start"], m["workouts"], m["distance_m"]) for m in months] == [("2026-08-01", 0, 0.0), ("2026-09-01", 3, 3000.0)]
    assert [d["workouts"] for d in days] == [0, 1, 1]


def test_muscle_changes_hidden_when_samples_are_too_small(h, db):
    h.day(D(2026, 9, 20), ("CURL", [("normal", 15, 10)]))                       # previous period: one session
    for d in (D(2026, 10, 10), D(2026, 10, 12)):
        h.day(d, ("CURL", [("normal", 15, 10)] * 3))
    h.build()
    with db.read() as c:
        s = Q.summary(c, P.resolve("28d", D(2026, 10, 28)), D(2026, 10, 28))
    biceps = next(m for m in s["muscles"] if m["muscle"] == "biceps")
    assert not s["change"]["available"] and biceps["change_pct"] is None and biceps["previous_sets"] == 1.0
