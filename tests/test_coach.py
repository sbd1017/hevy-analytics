"""Next-workout suggestions: the rules engine, checking OpenAI replies, the worker's timing and the API."""
import json
from datetime import date, datetime, timedelta, timezone

import pytest

from hevy import coach
from hevy.coach_openai import CoachError, OpenAICoach, pick_model
from hevy.worker import Worker

from .builder import History
from .test_web import BASE, CSRF, HEADERS, client_for

KEY = "sk-test-" + "k" * 40
NOW = datetime(2026, 10, 17, 9, 0, tzinfo=timezone.utc)


def five(kg, n=3, reps=5):
    return [("normal", kg, reps)] * n


@pytest.fixture
def h(db):
    """Upper done often and yesterday; Lower less often and due; Cardio never done."""
    h = History(db)
    h.routine("U", "Upper v1", ("BENCH", five(100)), ("ROW", [("normal", 30, 10)] * 3))
    h.routine("L", "Lower v2", ("SQUAT", five(140)), ("CURL", [("normal", 15, 10)] * 2))
    h.routine("C", "Cardio v1", ("WALK", [("normal", None, None)]))
    h.routine("OLD", "Lower v1", ("SQUAT", five(120)))
    up = lambda d, kg=100: h.day(d, ("BENCH", five(kg)), ("ROW", [("normal", 30, 10)] * 3), routine_id="U")
    up(date(2026, 10, 1))
    h.day(date(2026, 10, 3), ("SQUAT", five(140)), routine_id="L")
    up(date(2026, 10, 6))
    h.day(date(2026, 10, 8), ("SQUAT", [("normal", 140, 5), ("normal", 140, 4), ("normal", 140, 3)]), routine_id="OLD")
    up(date(2026, 10, 13))
    up(date(2026, 10, 16))
    h.build()
    with db.tx() as c:
        coach.set_programme(c, ["U", "L", "C"])
    return h


def ctx_for(db, day):
    with db.read() as c:
        return coach.context(c, day)


def test_family_strips_versions():
    assert coach.family("Push Day v2.1") == coach.family("Push Day v2 ") == "push day"
    assert coach.family("Upper Strength v2.5") == "upper strength" and coach.family("Push") == "push"


def test_rhythm_ranking_and_fatigue(h, db):
    ctx = ctx_for(db, date(2026, 10, 17))
    lower = next(r for r in ctx["programme"] if r["routine_id"] == "L")
    assert lower["times_done"] == 2 and lower["gym_sessions_since"] == 2      # the older version counts too
    assert lower["usual_every_n_sessions"] == 3.0
    picks = coach.rules_suggest(ctx)["suggestions"]
    assert [p["routine_id"] for p in picks] == ["L", "C"]                     # Upper: chest trained yesterday
    assert "Usually every 3 gym sessions; 2 since" in picks[0]["why"]
    later = coach.rules_suggest(ctx_for(db, date(2026, 10, 30)))["suggestions"]
    assert [p["routine_id"] for p in later] == ["L", "U"]                     # recovered: Upper is next


def test_targets_progress_repeat_and_ease_back(h, db):
    ctx = ctx_for(db, date(2026, 10, 17))
    rid = {r["routine_id"]: r for r in ctx["programme"]}
    bench = coach.targets(rid["U"]["exercises"][0], ctx["performance"]["BENCH"])
    assert [s["weight_kg"] for s in bench["sets"]] == [102.5] * 3 and bench["change"].startswith("+2.5 kg")
    assert bench["last_time"] == "100×5, 100×5, 100×5 (2026-10-16)"
    squat = coach.targets(rid["L"]["exercises"][0], ctx["performance"]["SQUAT"])
    assert [s["weight_kg"] for s in squat["sets"]] == [140] * 3 and squat["change"].startswith("same weight")
    curl = coach.targets(rid["L"]["exercises"][1], ctx["performance"]["CURL"])
    assert curl["change"].startswith("first time") and [s["weight_kg"] for s in curl["sets"]] == [15, 15]
    late = ctx_for(db, date(2026, 10, 30))
    eased = coach.targets(rid["L"]["exercises"][0], late["performance"]["SQUAT"])
    assert [s["weight_kg"] for s in eased["sets"]] == [125] * 3 and "eased back" in eased["change"]


def test_default_programme_is_newest_of_each_family_in_the_current_folder(db):
    h = History(db)
    h.routine("A1", "Push v1", ("BENCH", five(80)))
    h.routine("A2", "Push v2", ("BENCH", five(80)))
    h.routine("B", "Pull v1", ("ROW", five(30)))
    h.routine("X", "Other folder", ("CURL", five(10)), folder_id=2)
    with db.tx() as c:                                   # make A2 the newer version
        c.execute("UPDATE routines SET updated_utc = '2026-09-01' WHERE id = 'A2'")
    h.day(date(2026, 10, 1), ("ROW", five(30)), routine_id="B")
    with db.read() as c:
        assert sorted(coach.default_programme(c)) == ["A2", "B"]
        assert sorted(coach.programme_ids(c)) == ["A2", "B"]
    with db.tx() as c:
        assert coach.set_programme(c, ["X", "nope", "X"]) == ["X"]
        assert coach.programme_ids(c) == ["X"]


def reply(*sugs, summary="ok"):
    return {"summary": summary, "suggestions": list(sugs)}


def sug(rid, exercises, why="because"):
    return {"routine_id": rid, "why": why, "exercises": exercises}


def ex(tid, *sets, change="same"):
    return {"template_id": tid, "change": change,
            "sets": [{"type": "normal", "weight_kg": kg, "reps": reps, "duration_s": None, "distance_m": None} for kg, reps in sets]}


def test_validate_accepts_good_replies_and_repairs_or_rejects_bad_ones(h, db):
    ctx = ctx_for(db, date(2026, 10, 17))
    rules = coach.rules_suggest(ctx)
    good = coach.validate(reply(sug("L", [ex("SQUAT", (142.5, 5)), ex("CURL", (15, 10))]), sug("C", [ex("WALK", (None, None))])), ctx, rules)
    assert [s["routine_id"] for s in good["suggestions"]] == ["L", "C"]
    squat = good["suggestions"][0]["exercises"][0]
    assert squat["sets"][0]["weight_kg"] == 142.5 and squat["title"] == "Squat (Barbell)" and squat["last_time"]
    # silly weight on one exercise: that exercise falls back to the rules targets, the rest is kept
    fixed = coach.validate(reply(sug("L", [ex("SQUAT", (900, 5)), ex("CURL", (16, 10))])), ctx, rules)
    assert fixed["suggestions"][0]["exercises"][0]["sets"][0]["weight_kg"] == 140
    assert fixed["suggestions"][0]["exercises"][1]["sets"][0]["weight_kg"] == 16
    with pytest.raises(CoachError):                     # not in the programme (an old version isn't either)
        coach.validate(reply(sug("OLD", [ex("SQUAT", (120, 5))])), ctx, rules)
    with pytest.raises(CoachError):                     # drops most of the routine
        coach.validate(reply(sug("U", [])), ctx, rules)
    with pytest.raises(CoachError):
        coach.validate({"nope": 1}, ctx, rules)
    many = coach.validate(reply(sug("L", [ex("SQUAT", (140, 5))]), sug("L", [ex("SQUAT", (1, 1))]),
                                sug("C", [ex("WALK", (None, None))]), sug("U", [ex("BENCH", (100, 5))])), ctx, rules)
    assert [s["routine_id"] for s in many["suggestions"]] == ["L", "C"]


class FakeOpenAI:
    def __init__(self, answer, status=200, models=("gpt-4o", "gpt-5", "gpt-5.1", "gpt-5.1-mini", "gpt-5.1-chat-latest")):
        self.answer, self.status, self.models, self.calls = answer, status, models, []

    def __call__(self, url, headers, body):
        self.calls.append((url, headers, json.loads(body) if body else None))
        if url.endswith("/v1/models"):
            return 200, json.dumps({"data": [{"id": m} for m in self.models]}).encode()
        if self.status != 200:
            return self.status, b'{"error": {"message": "nope"}}'
        return 200, json.dumps({"model": "gpt-5.1-2026", "usage": {"prompt_tokens": 900, "completion_tokens": 300},
                                "choices": [{"finish_reason": "stop", "message": {"content": json.dumps(self.answer)}}]}).encode()


def test_pick_model():
    assert pick_model(["gpt-4o", "gpt-5", "gpt-5.1", "gpt-5.1-mini", "gpt-10"]) == "gpt-10"
    assert pick_model(["gpt-4o", "gpt-5-mini", "o3"]) == "gpt-5-mini"
    assert pick_model(["gpt-4o", "whisper-1"]) == "gpt-4o" and pick_model(["dall-e-3"]) is None


def test_refresh_prefers_openai_and_keeps_rules_as_fallback(h, db, settings):
    fake = FakeOpenAI(reply(sug("L", [ex("SQUAT", (142.5, 5)), ex("CURL", (15, 10))], why="Legs are due.")))
    ai = OpenAICoach(KEY, "https://openai.test", post=fake)
    assert KEY not in repr(ai)
    sid = coach.refresh(db, settings, "manual", NOW, NOW.date(), ai)
    with db.read() as c:
        s = coach.latest(c)
    assert s["id"] == sid and s["source"] == "openai" and s["model"] == "gpt-5.1-2026" and s["tokens_in"] == 900
    assert s["result"]["suggestions"][0]["why"] == "Legs are due." and s["openai_error"] is None
    sent = fake.calls[-1]
    assert sent[2]["model"] == "gpt-5.1" and sent[2]["response_format"]["json_schema"]["strict"] is True
    assert sent[1]["authorization"] == f"Bearer {KEY}" and KEY not in json.dumps(sent[2])
    assert "PROGRAMME" in sent[2]["messages"][1]["content"] and coach.DEFAULT_INSTRUCTIONS in sent[2]["messages"][0]["content"]
    # an unusable reply: the rules answer is shown, with the reason kept
    coach.refresh(db, settings, "manual", NOW, NOW.date(), OpenAICoach(KEY, "https://openai.test", post=FakeOpenAI(reply(sug("ZZ", [])))))
    # a refused key, and no key at all
    coach.refresh(db, settings, "manual", NOW, NOW.date(), OpenAICoach(KEY, "https://openai.test", post=FakeOpenAI(None, status=401)))
    coach.refresh(db, settings, "manual", NOW, NOW.date(), None)
    with db.read() as c:
        rows = [tuple(r) for r in c.execute("SELECT source, openai_error FROM coach_suggestions ORDER BY id")]
    assert rows[1] == ("rules", "the answer didn't match the programme (routine_id 'ZZ' is not in the programme)")
    assert rows[2] == ("rules", "OpenAI refused the key (HTTP 401)")
    assert rows[3] == ("rules", "no OpenAI key installed")
    with db.read() as c:
        assert [s["routine_id"] for s in coach.latest(c)["result"]["suggestions"]] == ["L", "C"]


def test_daily_openai_budget(h, db, settings):
    settings.coach_daily_calls = 2
    fake = FakeOpenAI(reply(sug("L", [ex("SQUAT", (140, 5)), ex("CURL", (15, 10))])))
    ai = OpenAICoach(KEY, "https://openai.test", post=fake)
    for i in range(3):
        coach.refresh(db, settings, "manual", NOW + timedelta(minutes=i), NOW.date(), ai)
    with db.read() as c:
        assert [r[0] for r in c.execute("SELECT source FROM coach_suggestions ORDER BY id")] == ["openai", "openai", "rules"]
    coach.refresh(db, settings, "manual", NOW + timedelta(days=1, minutes=1), NOW.date(), ai)
    with db.read() as c:
        assert coach.latest(c)["source"] == "openai"


def test_worker_timing(h, db, settings):
    clock = {"now": NOW}
    w = Worker(None, db, settings, now=lambda: clock["now"])
    assert w.coach_tick() is not None                                  # nothing yet
    assert w.coach_tick() is None                                      # fresh
    with db.tx() as c:
        c.execute("INSERT INTO coach_requests (requested_at) VALUES ('x')")
    sid = w.coach_tick()                                               # asked from the page: straight away
    with db.read() as c:
        assert c.execute("SELECT suggestion_id FROM coach_requests").fetchone()[0] == sid
        assert coach.latest(c)["trigger"] == "manual"
    h.day(date(2026, 10, 17), ("SQUAT", five(140)), routine_id="L")    # a new workout arrives
    h.build()
    assert w.coach_tick() is None                                      # within 10 minutes of the last attempt
    clock["now"] += timedelta(minutes=11)
    assert w.coach_tick() is not None
    with db.read() as c:
        s = coach.latest(c)
    assert s["trigger"] == "sync" and s["result"]["suggestions"][0]["routine_id"] != "L"
    clock["now"] += timedelta(days=3, minutes=1)
    w.coach_tick()
    with db.read() as c:
        assert coach.latest(c)["trigger"] == "stale"


def test_worker_retries_openai_after_a_transient_failure(h, db, settings):
    clock = {"now": NOW}
    fake = FakeOpenAI(None, status=503)
    w = Worker(None, db, settings, now=lambda: clock["now"], ai=OpenAICoach(KEY, "https://openai.test", post=fake))
    w.coach_tick()
    clock["now"] += timedelta(minutes=30)
    assert w.coach_tick() is None
    fake.status, fake.answer = 200, reply(sug("L", [ex("SQUAT", (140, 5)), ex("CURL", (15, 10))]))
    clock["now"] += timedelta(minutes=31)
    w.coach_tick()
    with db.read() as c:
        s = coach.latest(c)
    assert s["trigger"] == "retry" and s["source"] == "openai"


def test_coach_api(h, db, settings, monkeypatch):
    with client_for(settings, monkeypatch) as c:
        g = c.get(BASE + "/api/coach", headers=HEADERS).json()
        assert g["suggestion"] is None and g["programme_chosen"] and g["instructions_default"]
        assert [r["id"] for r in g["routines"] if r["selected"]] == ["C", "L", "U"]
        post = {**HEADERS, "x-csrf-token": CSRF, "origin": "https://example.com"}
        assert c.post(BASE + "/api/coach/refresh", headers=HEADERS).status_code == 403
        assert c.post(BASE + "/api/coach/refresh", headers=post).status_code == 202
        assert c.post(BASE + "/api/coach/refresh", headers=post).status_code == 202
        assert c.post(BASE + "/api/coach/settings", headers=post, json={"programme": "U"}).status_code == 400
        r = c.post(BASE + "/api/coach/settings", headers=post, json={"programme": ["L", "U"], "instructions": "Short answers."}).json()
        assert [x["id"] for x in r["routines"] if x["selected"]] == ["L", "U"] and r["instructions"] == "Short answers."
        assert r["pending"] and not r["instructions_default"]
        r = c.post(BASE + "/api/coach/settings", headers=post, json={"instructions": None}).json()
        assert r["instructions_default"] and [x["id"] for x in r["routines"] if x["selected"]] == ["L", "U"]
    with db.read() as conn:
        assert conn.execute("SELECT count(*) FROM coach_requests").fetchone()[0] == 1   # repeats don't queue more


def test_gpt_actions(h, db, settings, monkeypatch):
    with db.tx() as c:
        key = coach.new_gpt_key(c)
    assert key.startswith("hevy_") and len(key) > 40
    with db.read() as c:
        assert key not in json.dumps([tuple(r) for r in c.execute("SELECT * FROM app_meta")])   # only a hash is kept
    gk = {"x-hevy-gpt-key": f"Bearer {key}"}
    with client_for(settings, monkeypatch) as c:
        schema = c.get(BASE + "/gpt/openapi.json").json()                 # public: it holds no data
        assert schema["servers"][0]["url"] == "https://example.com/hevy/gpt"
        assert {"getTrainingContext", "saveSuggestion"} == {op["operationId"] for p in schema["paths"].values() for op in p.values()}
        assert c.get(BASE + "/gpt/context").status_code == 401
        assert c.get(BASE + "/gpt/context", headers={"x-hevy-gpt-key": "Bearer hevy_wrong"}).status_code == 401
        assert c.get(BASE + "/gpt/context", headers=HEADERS).status_code == 401              # the site login is no key
        ctx = c.get(BASE + "/gpt/context", headers=gk).json()
        assert [r["routine_id"] for r in ctx["programme"]] == ["U", "L", "C"] and ctx["owner_instructions"] and ctx["rules_proposal"]
        bad = c.post(BASE + "/gpt/suggestion", headers=gk, json=reply(sug("NOPE", [])))
        assert bad.status_code == 422 and "'NOPE' is not in the programme" in bad.json()["error"]
        assert c.post(BASE + "/gpt/suggestion", headers=gk, content=b"x" * (65 * 1024)).status_code == 413
        ok = c.post(BASE + "/gpt/suggestion", headers=gk, json=reply(sug("L", [ex("SQUAT", (142.5, 5)), ex("CURL", (15, 10))], why="Legs.")))
        assert ok.status_code == 200 and ok.json()["saved"] and ok.json()["picks"][0]["title"] == "Lower v2"
        st = c.get(BASE + "/api/coach", headers=HEADERS).json()
        assert st["suggestion"]["source"] == "chatgpt" and st["gpt"]["key_set"] and st["gpt"]["last_used_at"]
        with db.tx() as conn:
            coach.revoke_gpt_key(conn)
        assert c.get(BASE + "/gpt/context", headers=gk).status_code == 401


def test_chatgpt_suggestion_kept_until_a_new_workout(h, db, settings):
    coach.save_gpt_suggestion(db, reply(sug("L", [ex("SQUAT", (140, 5)), ex("CURL", (15, 10))])), NOW, NOW.date())
    clock = {"now": NOW + timedelta(days=5)}
    w = Worker(None, db, settings, now=lambda: clock["now"])
    assert w.coach_tick() is None                                      # not replaced just for being old
    h.day(date(2026, 10, 22), ("SQUAT", five(140)), routine_id="L")
    h.build()
    w.coach_tick()
    with db.read() as c:
        assert coach.latest(c)["source"] == "rules"                    # trained since: the rules take over again
