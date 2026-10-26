from datetime import date, timedelta

import pytest

from hevy.analytics.strength_index import strength_index

from .builder import History

D0 = date(2026, 1, 5)          # a Monday


def week(i):
    return D0 + timedelta(weeks=i)


def build(db, weeks, plan):
    """plan(i) -> list of (template, load) done with 1-rep sets (so e1RM = load) in week i."""
    h = History(db)
    for i in range(weeks):
        ex = plan(i)
        if ex:
            h.day(week(i), *[(t, [("normal", w, 1)]) for t, w in ex])
    h.build()


def test_uniform_gain_moves_the_index_by_that_much(db):
    # three exercises, flat for 10 weeks, then all exactly 10% heavier for 10 weeks
    build(db, 20, lambda i: [(t, base * (1.1 if i >= 10 else 1.0)) for t, base in (("BENCH", 100), ("SQUAT", 140), ("ROW", 60))])
    with db.read() as c:
        s = strength_index(c)
    assert s["available"] and s["series"][0]["index"] == 100.0
    assert s["current"] == pytest.approx(110.0, abs=0.1)


def test_a_new_exercise_joining_does_not_jump_the_index(db):
    # three flat exercises; a fourth (much heavier) starts in week 8 and stays flat
    build(db, 16, lambda i: [("BENCH", 100), ("SQUAT", 140), ("ROW", 60)] + ([("CURL", 300)] if i >= 8 else []))
    with db.read() as c:
        s = strength_index(c)
    assert {p["index"] for p in s["series"]} == {100.0}
    assert {x["template_id"] for x in s["components"]} == {"BENCH", "SQUAT", "ROW", "CURL"}


def test_inactive_components_drop_out_and_index_holds(db):
    # ROW stops after week 8 (9 sessions); 90+ days later only two components are active, and the index holds
    build(db, 28, lambda i: [("BENCH", 100 + i), ("SQUAT", 140 + i)] + ([("ROW", 60)] if i <= 8 else []))
    with db.read() as c:
        s = strength_index(c)
    row = next(x for x in s["components"] if x["template_id"] == "ROW")
    assert row["active"] is False
    late = [p for p in s["series"] if p["week"] >= week(22).isoformat()]
    assert all(p["active"] == 2 for p in late) and len({p["index"] for p in late}) == 1


def test_needs_three_qualifying_exercises(db):
    build(db, 10, lambda i: [("BENCH", 100), ("SQUAT", 140)])
    with db.read() as c:
        s = strength_index(c)
    assert s["available"] is False and "has 2" in s["reason"]


def test_at_most_two_components_per_muscle(db):
    # BENCH and PUSHUP-like weighted chest work: give chest three exercises; only two may enter
    h = History(db)
    from hevy import normalise, rawstore
    with db.tx() as c:
        for tid in ("CHEST2", "CHEST3"):
            rawstore.apply_object(c, "exercise_template", {"id": tid, "title": tid, "type": "weight_reps", "primary_muscle_group": "chest",
                                                           "secondary_muscle_groups": []}, None)
            normalise.rebuild_template(c, tid)
    for i in range(10):
        h.day(week(i), ("BENCH", [("normal", 100, 1)]), ("CHEST2", [("normal", 50, 1)]), ("CHEST3", [("normal", 40, 1)]),
              ("SQUAT", [("normal", 140, 1)]), ("ROW", [("normal", 60, 1)]))
    h.build()
    with db.read() as c:
        s = strength_index(c)
    assert sum(1 for x in s["components"] if x["muscle"] == "chest") == 2
