"""The analytics API: shapes, range handling and auth (numbers themselves are covered in test_analytics.py)."""
from datetime import date, timedelta

from hevy import worker as worker_mod
from hevy.analytics import build

from .builder import History
from .test_web import BASE, HEADERS, client_for


def seeded(db):
    h = History(db)
    base = date.today() - timedelta(days=40)
    for i in range(8):
        h.day(base + timedelta(days=i * 5), ("BENCH", [("normal", 100 + i, 5)]), ("SQUAT", [("normal", 140, 5)]),
              ("CURL", [("normal", 15, 10)]), ("WALK", [{"distance_meters": 2000, "duration_seconds": 1200}]))
    h.build()
    return h


def test_endpoints(settings, monkeypatch, db):
    seeded(db)
    with client_for(settings, monkeypatch) as c:
        g = lambda path: c.get(BASE + path, headers=HEADERS)
        s = g("/api/summary?range=28d").json()
        assert s["period"]["key"] == "28d" and s["totals"]["gym_sessions"] >= 4 and "change" in s and "strength" in s
        assert g("/api/summary?range=custom&start=2026-01-01&end=2026-01-31").json()["period"]["days"] == 31
        assert g("/api/summary?range=forever").status_code == 400
        assert g("/api/summary?range=custom&start=2026-02-01&end=2026-01-01").status_code == 400
        assert g("/api/summary?range=custom&start=nope&end=2026-01-01").status_code == 400
        ex = g("/api/exercises").json()["exercises"]
        assert {e["template_id"] for e in ex} == {"BENCH", "SQUAT", "CURL", "WALK"}
        d = g("/api/exercises/BENCH").json()
        assert d["lifetime"]["sessions"] == 8 and len(d["series"]) == 8 and d["trend"]["available"]
        assert g("/api/exercises/WALK").json()["trend"]["available"] is False
        assert g("/api/exercises/NOPE").status_code == 404
        m = g("/api/muscles?range=3m").json()["muscles"]
        assert next(x for x in m if x["muscle"] == "chest")["sets"] == 8.0
        assert next(x for x in m if x["muscle"] == "cardio")["on_map"] is False
        r = g("/api/records?meaningful=1&limit=5").json()["records"]
        assert len(r) <= 5 and all(x["meaningful"] for x in r)
        assert len(g("/api/calendar").json()["days"]) == 8
        ws = g("/api/workouts?range=3m").json()["workouts"]
        assert len(ws) == 8
        assert g(f"/api/workouts/{ws[0]['workout_id']}").json()["exercises"][0]["template_id"] == "BENCH"
        assert g("/api/workouts/NOPE").status_code == 404


def test_analytics_api_needs_login(settings, monkeypatch, db):
    seeded(db)
    with client_for(settings, monkeypatch) as c:
        for path in ("/api/summary", "/api/exercises/BENCH", "/api/workouts", "/api/records"):
            assert c.get(BASE + path).status_code == 401


def test_status_reports_analytics_and_suspects(settings, monkeypatch, db):
    h = History(db)
    for i in range(6):
        h.day(date(2026, 9, 1) + timedelta(days=i), ("CURL", [("normal", 12, 10)]))
    h.day(date(2026, 9, 10), ("CURL", [("normal", 12, 129)]))
    h.build()
    with client_for(settings, monkeypatch) as c:
        s = c.get(BASE + "/api/status", headers=HEADERS).json()
    assert s["analytics"]["version"] == "1" and s["analytics"]["error"] is None
    assert s["suspect_sets"][0]["reps"] == 129


def test_worker_rebuilds_analytics_after_changes(engine, db, fake, settings):
    from datetime import datetime, timezone
    w = worker_mod.Worker(engine, db, settings, now=lambda: fake.now)
    fake.add_workout(datetime(2026, 9, 1, 17, 0, tzinfo=timezone.utc))
    w.tick()                                                   # backfill -> analytics built
    with db.read() as c:
        assert c.execute("SELECT count(*) FROM an_workouts").fetchone()[0] == 1
    fake.tick(20)
    fake.add_workout(datetime(2026, 10, 1, 7, 0, tzinfo=timezone.utc))
    with db.tx() as c:
        c.execute("INSERT INTO sync_requests (requested_at, source) VALUES ('x', 'web')")
    w.tick()
    with db.read() as c:
        assert c.execute("SELECT count(*) FROM an_workouts").fetchone()[0] == 2


def test_refresh_only_when_rules_change(db):
    assert build.refresh(db) is not None                       # never built
    assert build.refresh(db) is None                           # same rules: nothing to do
    with db.tx() as c:
        c.execute("UPDATE app_meta SET value = '0' WHERE key = 'analytics_version'")
    assert build.refresh(db) is not None                       # older rules: rebuilt


def test_phase4_endpoints_and_pages(settings, monkeypatch, db):
    seeded(db)
    with client_for(settings, monkeypatch) as c:
        g = lambda path: c.get(BASE + path, headers=HEADERS)
        for page in ("/exercises", "/exercises/BENCH", "/muscles/chest", "/sessions", "/sessions/W001", "/compare?a=W001&b=W002", "/insights"):
            r = g(page)
            assert r.status_code == 200 and 'name="csrf"' in r.text, page
        ex = g("/api/exercises?status=1").json()["exercises"]
        assert next(e for e in ex if e["template_id"] == "BENCH")["status"] in ("improving", "stable", "insufficient")
        d = g("/api/exercises/BENCH").json()
        assert d["plateau"]["status"] and d["order_effect"]["available"] is False and "momentum" in d
        assert g("/api/exercises/WALK").json()["plateau"] is None
        m = g("/api/muscles/biceps?range=3m").json()
        assert m["summary"]["muscle"] == "biceps" and m["contributors"][0]["template_id"] == "CURL"
        assert m["comparison"]["available"] or m["summary"]["change_pct"] is None
        assert g("/api/muscles/nonsense").status_code == 400
        mo = g("/api/insights/momentum").json()
        assert set(mo["exercises"]) == {"accelerating", "improving", "maintaining", "plateauing", "declining", "new"}
        assert g("/api/insights/order").json() == {"exercises": []}
        dna = g("/api/dna?range=6m").json()
        assert dna["working_sets"] > 0 and len(dna["balance"]) == 6
        cmp = g("/api/compare?a=W001&b=W002").json()
        assert cmp["shared_exercises"] == 4
        assert g("/api/compare?a=W001&b=nope").status_code == 404
        assert "similar" in g("/api/workouts/W001").json()
