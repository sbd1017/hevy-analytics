"""Next-session estimates, Wrapped, landmarks, exports and the explorer."""
import io
import json
import zipfile
from datetime import date, timedelta

import pytest

from hevy import exports as EX
from hevy.analytics import explore as X
from hevy.analytics import forecast as F
from hevy.analytics import wrapped as WR

from .builder import History
from .test_web import BASE, HEADERS, client_for


@pytest.fixture
def h(db):
    return History(db)


def series_history(h, n=24):
    """Three exercises over n weekly sessions: bench creeps up with stalls, squat flat, curl rising steadily."""
    d0 = date(2026, 1, 5)
    for i in range(n):
        bench = 80 + 2.5 * (i // 3)                         # a new best every third week
        h.day(d0 + timedelta(weeks=i), ("BENCH", [("normal", bench, 5), ("warmup", 40, 10)]), ("SQUAT", [("normal", 120, 5)]),
              ("CURL", [("normal", 10 + 0.5 * i, 10)]))
    h.build()


# ---------------------------------------------------------------- next-session estimate

def test_situation_bands():
    s = lambda *e1: [{"best_e1rm_kg": v, "start_ms": i * 7 * 86_400_000} for i, v in enumerate(e1)]
    assert F.situation(s(100, 100, 100, 100, 100, 99))[0] == 0          # within 2% of best
    assert F.situation(s(100, 97))[0] == 1
    assert F.situation(s(100, 92))[0] == 2
    assert F.situation(s(100, 80))[0] == 3


def test_estimate_falls_back_to_band_when_cell_is_small():
    cell = {(0, True): (3, 10)}
    band = {0: (12, 40)}
    assert F._estimate(cell, band, 0, True) == (0.3, 40, False)
    cell = {(0, True): (9, 30)}
    assert F._estimate(cell, band, 0, True) == (0.3, 30, True)


def test_load_step():
    assert F._step([60, 62.5, 65, 67.5, 70]) == 2.5
    assert F._step([10, 12, 14, 16]) == 2
    assert F._step([50]) == 2.5


def test_next_session_is_a_checkable_frequency(h, db):
    series_history(h)
    with db.read() as c:
        cases = F._cases(c)
        n = F.next_session(c, "BENCH")
        bt = F.backtest(c)
        assert F.next_session(c, "PLANK")["available"] is False
    b, rising, _, best = None, None, None, None
    cell, band = F._tables(cases)
    with db.read() as c:
        e = [s for s in F._sessions(c, "BENCH")["BENCH"] if s["best_e1rm_kg"]]
    b, rising, gap, best = F.situation(e)
    p, cnt, _ = F._estimate(cell, band, b, rising)
    assert n["available"] and n["chance_new_best_pct"] == round(p * 100) and n["cases"] == cnt
    assert n["to_beat_best"]["weight_kg"] * (1 + n["to_beat_best"]["reps"] / 30) > n["best_e1rm"]
    assert n["experimental"] is True and "of the time" in n["reasons"][0]
    assert bt["available"] and bt["n"] == len(cases) and sum(x["n"] for x in bt["bins"]) == bt["n"]


# ---------------------------------------------------------------- Wrapped and landmarks

def test_landmarks_are_exact_and_readable():
    lm = WR.landmarks(3_000_000, 100_000, seed=1)
    for x in lm["volume"]:
        assert 1.5 <= x["count"] <= 60 and x["count"] == pytest.approx(3_000_000 / x["each"], rel=0.01)
    assert all(x["count"] == pytest.approx(100 / x["each"], rel=0.01) for x in lm["distance"])
    assert WR.landmarks(0, 0) == {"volume": [], "distance": [], "perspective": []}
    assert WR.landmarks(3_000_000, 0, seed=1) == WR.landmarks(3_000_000, 0, seed=1)     # deterministic
    assert WR.landmarks(3_000_000, 0, seed=0)["volume"][0] != WR.landmarks(3_000_000, 0, seed=1)["volume"][0]   # rotates


@pytest.mark.parametrize("kg,expect", [
    (45_000, "African elephants"),            # a month: animals, not a tenth of a jumbo jet
    (1_100_000, "Boeing 747s"),               # a year: aircraft
    (12_000_000, "Saturn V rockets"),          # a decade: rockets and towers
])
def test_landmarks_scale_with_the_total(kg, expect):
    things = [x["thing"] for x in WR.landmarks(kg, 0, seed=0, count=5)["volume"]]
    assert expect in things
    assert all(1.5 <= x["count"] <= 60 for x in WR.landmarks(kg, 0, seed=0, count=5)["volume"])


def test_impossible_things_only_for_long_periods():
    assert WR.landmarks(1_000_000, 300_000, span_days=30)["perspective"] == []
    p = {x["thing"]: x["years"] for x in WR.landmarks(1_000_000, 300_000, span_days=365.25)["perspective"] if x["kind"] == "lift"}
    assert p["the Great Pyramid of Giza"] == "5,900 years" and "10¹⁶" in p["the Moon"]


def test_wrapped_cards(h, db):
    series_history(h, 12)
    h.day(date(2026, 2, 1), ("WALK", [{"distance_meters": 5000, "duration_seconds": 3000}]))
    with db.read() as c:
        pass
    h.build()
    with db.read() as c:
        w = WR.wrapped(c, date(2026, 1, 1), date(2026, 12, 31), seed=2)
        empty = WR.wrapped(c, date(2020, 1, 1), date(2020, 12, 31))
    t = w["totals"]
    assert (t["workouts"], t["gym_sessions"], t["cardio_sessions"], t["distance_km"]) == (13, 12, 1, 5.0)
    assert w["strongest_lift"]["template_id"] == "SQUAT" and w["strongest_lift"]["e1rm"] == 140.0
    assert w["favourite_exercise"]["sessions"] == 12 and w["favourite_cardio"]["title"] == "Walking"
    assert w["habits"]["weekday"] == "Monday" and w["habits"]["time_of_day"] == "evening"
    assert w["new_exercises"]["count"] == 4 and w["longest_streak"]["weeks"] == 12
    assert w["biggest_progression"]["template_id"] == "CURL"
    assert w["most_trained_muscle"]["muscle"] in ("quadriceps", "chest", "biceps")
    assert empty == {"start": "2020-01-01", "end": "2020-12-31", "empty": True}
    assert w["busiest_week"]["workouts"] == 2 and w["years"] is None and w["coverage"]["partial"] is False   # Sun walk + Mon session


# ---------------------------------------------------------------- exports

F_ALL = {"start": "2020-01-01", "end": "2030-01-01"}


def test_csv_json_xlsx(h, db):
    series_history(h, 4)
    with db.read() as c:
        body, media, ext = EX.build(c, "csv", ["sets"], F_ALL)
        text = body.decode("utf-8-sig").splitlines()
        assert ext == "csv" and text[0].startswith("date,start_utc,workout_id") and len(text) == 1 + 4 * 4      # 4 sets a week incl. warm-up
        z, _, ext = EX.build(c, "csv", ["sets", "workouts", "raw"], F_ALL)
        names = zipfile.ZipFile(io.BytesIO(z)).namelist()
        assert ext == "zip" and set(names) == {"sets.csv", "workouts.csv", "raw_workouts.json", "about.json"}
        j = json.loads(EX.build(c, "json", ["workouts", "records", "raw"], F_ALL)[0])
        assert len(j["workouts"]) == 4 and j["about"]["rules_version"] == 1 and len(j["raw_workouts"]) == 4
        x, media, ext = EX.build(c, "xlsx", ["sets", "exercises"], F_ALL)
        sheets = zipfile.ZipFile(io.BytesIO(x)).read("xl/workbook.xml").decode()
        assert ext == "xlsx" and all(n in sheets for n in ("About", "Sets", "Exercises"))
        only_curl = EX.rows(c, "sets", {**F_ALL, "exercise": "CURL"})[1]
        assert len(only_curl) == 4 and {r[5] for r in only_curl} == {"Bicep Curl (Dumbbell)"}
        chest = EX.rows(c, "sets", {**F_ALL, "muscle": "chest"})[1]
        assert {r[6] for r in chest} == {"BENCH"}
        assert EX.filename(["sets"], F_ALL, "csv") == "hevy-sets-2020-01-01-to-2030-01-01.csv"


def test_csv_neutralises_formulas():
    out = EX._csv_bytes(["title"], [("=HYPERLINK(1)",), ("-5 reps",), ("ok",)]).decode("utf-8-sig").splitlines()
    assert out[1:] == ["'=HYPERLINK(1)", "'-5 reps", "ok"]


# ---------------------------------------------------------------- explorer

def test_explorer_filters(h, db):
    series_history(h, 6)
    base = {"start": "2020-01-01", "end": "2030-01-01"}
    with db.read() as c:
        allw = X.explore(c, base)
        warm = X.explore(c, {**base, "set_type": "warmup"})
        heavy = X.explore(c, {**base, "weight_min": 100})
        curl = X.explore(c, {**base, "exercises": ["CURL"], "sort": "weight", "limit": 2})
        bogus_sort = X.explore(c, {**base, "sort": "1; DROP TABLE sets"})
    assert allw["totals"]["sets"] == 18 and warm["totals"]["sets"] == 6          # warm-ups only when asked
    assert heavy["totals"]["sets"] == 6 and heavy["totals"]["exercises"] == 1     # squat at 120
    assert [r["weight_kg"] for r in curl["rows"]] == [12.5, 12.0] and curl["limit"] == 2
    assert sum(b["sets"] for b in allw["reps_histogram"]) == 18
    assert bogus_sort["rows"]                                                     # unknown sort falls back to date


def test_wrapped_adapts_to_the_period(h, db):
    for y, n in ((2024, 3), (2025, 5)):
        for i in range(n):
            h.day(date(y, 3, 2) + timedelta(weeks=i), ("BENCH", [("normal", 100, 5)]))
    h.build()
    with db.read() as c:
        month = WR.wrapped(c, date(2025, 3, 1), date(2025, 3, 31), prev_start=date(2025, 2, 1), prev_end=date(2025, 2, 28))
        decade = WR.wrapped(c, date(2016, 1, 1), date(2025, 12, 31), today=date(2026, 10, 20))
    assert month["most_active_month"] is None and month["busiest_week"]["workouts"] == 1
    assert month["vs_before"]["available"] is False                          # nothing in February to compare with
    assert [y["year"] for y in decade["years"]] == ["2024", "2025"] and decade["best_year"] == "2025"
    assert decade["coverage"]["partial"] is True and decade["coverage"]["history_starts"] == "2024-03-02"
    assert decade["span_days"] == (date(2025, 12, 31) - date(2024, 3, 2)).days + 1


def test_phase5_api(settings, monkeypatch, db):
    series_history(History(db), 12)
    with client_for(settings, monkeypatch) as c:
        g = lambda p: c.get(BASE + p, headers=HEADERS)
        assert g("/api/exercises/BENCH/next").json()["available"] is True
        assert g("/api/forecast/backtest").status_code == 200
        w = g("/api/wrapped?range=custom&start=2026-01-01&end=2026-12-31").json()
        assert w["totals"]["gym_sessions"] == 12 and w["landmarks"]["volume"]
        r = g("/api/export?format=csv&datasets=sets,workouts")
        assert r.status_code == 200 and r.headers["content-type"] == "application/zip" and "attachment" in r.headers["content-disposition"]
        assert g("/api/export?format=pdf").status_code == 400 and g("/api/export?datasets=passwords").status_code == 400
        assert c.get(BASE + "/api/export?format=json").status_code == 401                      # never without the login
        e = g("/api/explore?exercises=CURL&weight_min=11&sort=weight")
        assert e.status_code == 200 and all(x["weight_kg"] >= 11 for x in e.json()["rows"])
        assert g("/api/explore?weight_min=heavy").status_code == 400
        assert g("/wrapped").status_code == 200 and g("/data").status_code == 200
