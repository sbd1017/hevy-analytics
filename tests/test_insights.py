"""Plateaus, momentum, order effect, Training DNA, balance, comparisons: hand-built data with known answers."""
from datetime import date, datetime, timedelta, timezone

import pytest

from hevy.analytics import insights as I

from .builder import History

DAY = 86_400_000
T0 = int(datetime(2026, 1, 5, 18, tzinfo=timezone.utc).timestamp() * 1000)


def sess(rows, step_days=7, position=0):
    """rows: list of (e1rm, volume, reps, top_weight[, position]) -> session dicts like an_exercise_sessions."""
    out = []
    for i, r in enumerate(rows):
        e1, vol, reps, top = r[:4]
        ms = T0 + i * step_days * DAY
        out.append({"start_ms": ms, "local_date": date.fromtimestamp(ms / 1000).isoformat(), "best_e1rm_kg": e1, "volume_kg": vol,
                    "reps": reps, "top_weight_kg": top, "working_sets": 3, "position": r[4] if len(r) > 4 else position})
    return out


def ref(ss, days=3):
    return ss[-1]["start_ms"] + days * DAY


# ---------------------------------------------------------------- plateaus

def test_insufficient_below_six_sessions():
    ss = sess([(100, 1000, 30, 80)] * 5)
    p = I.plateau(ss, ref(ss))
    assert p["status"] == "insufficient" and "has 5" in p["reasons"][0]


def test_improving_and_declining():
    up = sess([(100 + 2 * i, 1000, 30, 80) for i in range(8)])            # +2 kg a week: ~8.5%/month
    p = I.plateau(up, ref(up))
    assert p["status"] == "improving" and p["trend_pct_per_month"] > 5 and p["sessions_since_best"] == 0
    down = sess([(110 - 2 * i, 1000, 30, 80) for i in range(8)])
    assert I.plateau(down, ref(down))["status"] == "declining"


def test_stalled_vs_productive_plateau_vs_stable():
    flat = [(120, 1000, 30, 90)] + [(118, 1000, 30, 90)] * 7               # best in session 1, then 7 sessions without
    p = I.plateau(sess(flat), ref(sess(flat)))
    assert p["status"] == "stalled" and p["sessions_since_best"] == 7
    assert any("No new e1RM high for 7 sessions" in r for r in p["reasons"])
    growing = [(120, 1000, 30, 90)] + [(118, 1000 + 40 * i, 30 + i, 90) for i in range(7)]   # flat e1RM, more work
    p = I.plateau(sess(growing), ref(sess(growing)))
    assert p["status"] == "productive_plateau" and p["changes"]["volume_pct"] >= 5
    recent_best = [(118, 1000, 30, 90)] * 6 + [(120, 1000, 30, 90), (119, 1000, 30, 90)]
    assert I.plateau(sess(recent_best), ref(sess(recent_best)))["status"] == "stable"


def test_older_best_is_explained():
    ss = sess([(140, 1000, 30, 100)] + [(100, 1000, 30, 80)] * 4 + [(100 + 2 * i, 1000, 30, 80) for i in range(8)])
    p = I.plateau(ss, ref(ss))
    assert p["status"] == "improving" and p["sessions_since_best"] == 12
    assert any("older than these sessions" in r and "is 1" in r for r in p["reasons"])     # latest 114 vs best 140: 19% below


def test_old_exercises_are_marked_not_recent():
    ss = sess([(100 + i, 1000, 30, 80) for i in range(8)])
    p = I.plateau(ss, ss[-1]["start_ms"] + 120 * DAY)
    assert p["recent"] is False and any("more than 90 days" in r for r in p["reasons"])


# ---------------------------------------------------------------- momentum

def test_momentum_classes():
    accel = sess([(100, 1000, 30, 80)] * 4 + [(102, 1000, 30, 80), (106, 1000, 30, 80), (111, 1000, 30, 80), (117, 1000, 30, 80)])
    assert I.momentum_of(accel, ref(accel))["momentum"] == "accelerating"
    steady_up = sess([(100 + 2 * i, 1000, 30, 80) for i in range(8)])
    assert I.momentum_of(steady_up, ref(steady_up))["momentum"] == "improving"
    new = sess([(50, 500, 30, 40)] * 3)
    assert I.momentum_of(new, ref(new))["momentum"] == "new"
    assert I.momentum_of(steady_up, steady_up[-1]["start_ms"] + 100 * DAY) is None          # not current


# ---------------------------------------------------------------- order effect

def test_order_effect_found_when_real():
    rows = []
    for i in range(24):                          # stable strength; late placement costs 10%
        late = i % 2 == 1
        rows.append((90.0 if late else 100.0, 1000, 30, 80, 4 if late else 0))
    o = I.order_effect(sess(rows, step_days=3))
    assert o["available"] and o["effect_pct"] == pytest.approx(-10, abs=1.5) and o["clear"]
    assert o["early_n"] == 12 and o["late_n"] == 12


def test_order_effect_not_fooled_by_progress_over_time():
    """Early in the history the exercise was done late in workouts, later it was done first, and strength rose
    steadily throughout. Raw averages differ a lot; relative to the level at the time there is no effect."""
    rows = [(80 + i, 1000, 30, 80, 4 if i < 15 else 0) for i in range(30)]
    o = I.order_effect(sess(rows, step_days=4))
    assert o["late_avg_e1rm"] < o["early_avg_e1rm"] - 10                 # the naive comparison would claim ~ -15%
    assert abs(o["effect_pct"]) < 2


def test_order_effect_needs_five_per_group():
    rows = [(100, 1000, 30, 80, 0)] * 10 + [(95, 1000, 30, 80, 5)] * 4
    o = I.order_effect(sess(rows, step_days=3))
    assert o["available"] is False and "has 10 early, 4 late" in o["reason"]


# ---------------------------------------------------------------- DNA, balance, muscles, comparisons (full pipeline)

@pytest.fixture
def h(db):
    return History(db)


def test_dna_and_balance(h, db):
    d = date(2026, 9, 1)
    h.day(d, ("BENCH", [("normal", 100, 3), ("failure", 80, 8), ("normal", 60, 15), ("warmup", 40, 10)]),
          ("ROW", [("normal", 30, 10)] * 2), ("SQUAT", [("dropset", 100, 10)]), ("PUSHUP", [{"reps": 20}]))
    h.day(d + timedelta(days=1), ("WALK", [{"distance_meters": 3000, "duration_seconds": 1800}]))
    h.build()
    with db.read() as c:
        x = I.dna(c, d, d + timedelta(days=1))
    t = {r["key"]: r["pct"] for r in x["traits"]}
    # weighted sets with reps: 3, 8, 15, 10, 10, 10 -> heavy 1/6, moderate 4/6, high 1/6
    assert (t["heavy"], t["moderate"], t["high_rep"]) == (16.7, 66.7, 16.7)
    assert t["failure"] == pytest.approx(100 / 8, abs=0.05) and t["dropset"] == pytest.approx(12.5, abs=0.05)   # 8 working sets incl. walk
    assert t["cardio"] == 50.0 and x["facts"]["new_exercises"] == 5
    bal = {(b["a"], b["b"]): b for b in x["balance"]}
    # push credit: bench 3 chest + 1.5 tri + 1.5 sh + push-up 1 chest + .5 tri = 7.5 ; pull: row 2 upper back + 1 lats + 1 biceps = 4
    assert (bal[("Push", "Pull")]["a_sets"], bal[("Push", "Pull")]["b_sets"]) == (7.5, 4.0)


def test_muscle_detail_credits_secondary_at_half(h, db):
    h.day(date(2026, 9, 1), ("ROW", [("normal", 30, 10)] * 2), ("CURL", [("normal", 15, 10)] * 3))
    h.build()
    with db.read() as c:
        m = I.muscle_detail(c, "biceps", date(2026, 9, 1), date(2026, 9, 30))
    got = {x["template_id"]: (x["role"], x["credited"]) for x in m["contributors"]}
    assert got == {"CURL": ("primary", 3.0), "ROW": ("secondary", 1.0)}
    assert m["weekly"] == [{"start": "2026-08-31", "sets": 4.0}]


def test_compare_and_similar_workouts(h, db):
    a = h.day(date(2026, 9, 1), ("BENCH", [("normal", 100, 5)] * 3), ("ROW", [("normal", 30, 10)] * 2))
    b = h.day(date(2026, 9, 8), ("BENCH", [("normal", 105, 5)] * 3), ("ROW", [("normal", 30, 10)] * 2), ("CURL", [("normal", 15, 10)]))
    h.day(date(2026, 9, 9), ("SQUAT", [("normal", 140, 5)]))
    h.build()
    with db.read() as c:
        cmp = I.compare_workouts(c, a, b)
        sim = I.similar_workouts(c, a)
        assert I.compare_workouts(c, a, "nope") is None
    ex = {e["template_id"]: e for e in cmp["exercises"]}
    assert ex["BENCH"]["e1rm_change_pct"] == 5.0 and ex["CURL"]["a"] is None and cmp["shared_exercises"] == 2
    assert cmp["change"]["volume_kg"] == pytest.approx((1575 + 600 + 150 - 2100) / 2100 * 100, abs=0.05)
    assert [s["workout_id"] for s in sim] == [b]                     # 2 of 3 exercises shared; the squat day isn't similar
