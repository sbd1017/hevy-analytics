"""The public demo: made-up history, open to visitors, and nothing that writes settings or reaches the outside."""
import re
from datetime import date

import pytest
from fastapi.testclient import TestClient

from hevy import coach, demo, worker
from hevy.config import Settings
from hevy.db import Database, set_meta
from hevy.web import create_app

TODAY = date(2026, 10, 21)


@pytest.fixture
def demo_db(tmp_path):
    s = Settings(env="production", demo=True, data_dir=tmp_path / "var", public_origins=("https://demo.example",))
    db = Database(s.db_path)
    db.migrate()
    return s, db


def test_history_is_believable_and_ends_yesterday(demo_db):
    s, db = demo_db
    counts = demo.seed(db, TODAY)
    assert counts["workout"] > 800 and counts["body_measurement"] > 300
    with db.read() as c:
        first, last = c.execute("SELECT min(local_date), max(local_date) FROM an_workouts WHERE kind = 'gym'").fetchone()
        assert last == "2026-10-20" and first < "2023-06-01"
        e1rm = dict(c.execute("SELECT substr(s.local_date, 1, 4), max(s.best_e1rm_kg) FROM an_exercise_sessions s "
                              "JOIN exercise_templates t ON t.id = s.template_id WHERE t.title = 'Squat (Barbell)' GROUP BY 1"))
        assert e1rm["2023"] < e1rm["2024"] < e1rm["2026"]                       # progress that keeps coming, slower
        assert c.execute("SELECT count(*) FROM an_sets WHERE suspect IS NOT NULL").fetchone()[0] == 1
        from hevy.analytics.queries import streaks
        assert streaks(c, TODAY)["any"]["longest_to"] <= TODAY.isoformat()      # a streak running now ends today
        assert {r[0] for r in c.execute("SELECT title FROM routines WHERE id IN ({})".format(
            ",".join("?" * len(coach.default_programme(c)))), coach.default_programme(c))} == {
            "Upper Strength v2", "Lower Strength v2", "Hypertrophy Full Body v1", "Deadlift Day v1", "Cardio Day v1"}
    with pytest.raises(SystemExit):
        demo.seed(db, TODAY)                                                     # only ever into an empty database
    assert demo.generate(TODAY)["workout"][5] == demo.generate(TODAY)["workout"][5]   # the same every night


def test_demo_site_is_open_and_read_mostly(demo_db, monkeypatch):
    s, db = demo_db
    monkeypatch.delenv("SITE_AUTH", raising=False)
    demo.seed(db, TODAY)
    c = TestClient(create_app(s), base_url="https://demo.example", follow_redirects=False)
    page = c.get("/hevy/")
    assert page.status_code == 200 and "demo-bar" in page.text and "/logout" not in page.text
    csrf = re.search(r'name="csrf" content="([^"]+)"', page.text).group(1)
    post = {"x-csrf-token": csrf, "origin": "https://demo.example"}
    st = c.get("/hevy/api/status").json()
    assert st["demo"] and st["sync"]["state"] == "demo"
    assert c.get("/hevy/api/summary?range=1y").json()["totals"]["gym_sessions"] > 100
    assert c.post("/hevy/api/sync", headers=post).status_code == 403
    assert c.post("/hevy/api/coach/settings", headers=post, json={"instructions": "rude words"}).status_code == 403
    assert c.post("/hevy/api/figure", headers=post, json={"sex": "f"}).status_code == 403
    assert c.get("/hevy/api/figure").json()["demo"]
    assert c.post("/hevy/api/coach/refresh", headers=post).status_code == 202          # the free rules, straight away
    got = c.get("/hevy/api/coach").json()
    assert got["demo"] and got["suggestion"]["source"] == "rules" and got["suggestion"]["result"]["suggestions"]
    assert c.get("/hevy/gpt/openapi.json").status_code == 404
    assert c.post("/hevy/gpt/suggestion", json={}).status_code == 404
    assert c.post("/hevy/api/coach/refresh", headers={"origin": "https://evil.example"}).status_code == 403


def test_demo_and_real_data_never_mix(tmp_path, monkeypatch):
    real = Settings(env="production", data_dir=tmp_path / "real")
    db = Database(real.db_path); db.migrate()
    with db.tx() as c:
        set_meta(c, "data_origin", "hevy")
    with pytest.raises(SystemExit, match="open demo on a database holding 'hevy'"):
        create_app(Settings(env="production", demo=True, data_dir=tmp_path / "real"))     # no open demo on real data
    fake = Settings(env="production", demo=True, data_dir=tmp_path / "demo")
    db2 = Database(fake.db_path); db2.migrate()
    demo.seed(db2, TODAY)
    monkeypatch.setenv("SITE_AUTH", "1")
    (tmp_path / "pk").write_text("k" * 40)
    monkeypatch.setenv("SITE_PROXY_KEY_FILE", str(tmp_path / "pk"))
    with pytest.raises(SystemExit, match="holding 'demo' data in production"):
        create_app(Settings(env="production", data_dir=tmp_path / "demo"))                # nor the real site on demo data
    monkeypatch.setenv("HEVY_DEMO", "1")
    with pytest.raises(SystemExit, match="never runs in the demo"):
        worker.main()                                                                       # nothing ever syncs in a demo


def test_figure_settings(settings, monkeypatch, db):
    from .builder import History
    from .test_web import BASE, CSRF, HEADERS, client_for
    History(db)
    post = {**HEADERS, "x-csrf-token": CSRF, "origin": "https://example.com"}
    with client_for(settings, monkeypatch) as c:
        f = c.get(BASE + "/api/figure", headers=HEADERS).json()
        assert f["sex"] == "m" and f["width"] == 1 and f["bmi"] is None
        assert c.post(BASE + "/api/figure", headers=post, json={"sex": "x"}).status_code == 400
        assert c.post(BASE + "/api/figure", headers=post, json={"height_cm": 50}).status_code == 400
        assert c.post(BASE + "/api/figure", headers=HEADERS, json={"sex": "f"}).status_code == 403          # CSRF
        f = c.post(BASE + "/api/figure", headers=post, json={"sex": "f", "height_cm": 180}).json()
        assert f["sex"] == "f" and f["height_cm"] == 180 and f["bmi"] is None                         # no weight logged yet
        from hevy import normalise, rawstore
        with db.tx() as conn:
            for d, kg in (("2026-10-01", 81), ("2026-10-20", 97.2), ("2026-10-21", None)):
                rawstore.apply_object(conn, "body_measurement", {"date": d, "weight_kg": kg}, None)
                normalise.rebuild_body(conn, d)
        f = c.get(BASE + "/api/figure", headers=HEADERS).json()
        assert f["weight_kg"] == 97.2 and f["bmi"] == 30.0 and f["width"] == round((30 / 22.5) ** 0.5, 3)
        f = c.post(BASE + "/api/figure", headers=post, json={"height_cm": None}).json()
        assert f["sex"] == "f" and f["height_cm"] is None and f["width"] == 1


def test_worker_never_syncs_into_demo_data(tmp_path, monkeypatch):
    s = Settings(env="development", data_dir=tmp_path / "d")
    db = Database(s.db_path); db.migrate()
    demo.seed(db, TODAY)
    monkeypatch.setenv("HEVY_ENV", "development")
    monkeypatch.setenv("HEVY_DATA_DIR", str(tmp_path / "d"))
    monkeypatch.delenv("HEVY_DEMO", raising=False)
    with pytest.raises(SystemExit, match="holding 'demo' data"):
        worker.main()
