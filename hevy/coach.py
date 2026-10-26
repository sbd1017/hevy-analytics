"""Next-workout suggestions (docs/METRICS.md, "Next workout").

Three sources produce the same shape of answer:
* the rules engine here: always computed, free and private, and the fallback;
* the owner's own GPT in ChatGPT: reads gpt_context() and posts its answer back through web.py's /gpt/ routes
  (no API billing; it runs on the owner's ChatGPT plan, only when asked in ChatGPT);
* the OpenAI API coach (coach_openai.py): used by the worker when an API key is installed.
Answers from ChatGPT or the API are checked by validate() against the programme before they're shown.

The programme is a list of Hevy routines (by id) chosen on the System page; routines keep their ids when edited in
Hevy, so edits flow through automatically.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import math
import re
import secrets
import sqlite3
from datetime import date, datetime, timedelta

from . import rawstore
from .analytics.forecast import _step
from .coach_openai import CoachError
from .db import get_meta, set_meta
from .sync import get_state, set_state
from .timeutil import iso, parse_ts, utcnow

log = logging.getLogger("hevy.coach")

DEFAULT_INSTRUCTIONS = (
    "You are my strength coach. Pick my next workout from my programme only, rotating sensibly: don't repeat what "
    "I trained hard in the last 48 hours, keep upper, lower and deadlift days balanced, and count my walking as "
    "cardio. Use my recent sets to set realistic targets: add a small step when I hit all my reps, repeat the "
    "weight when I didn't, and ease back after time off. Be brief and practical.")
RECENT_DAYS = 21
EASE_BACK_DAYS = 14
FATIGUE_HOURS = 48
CARDIO_ENOUGH_MIN = 90
STALE_AFTER = timedelta(days=3)          # a suggestion is redone at least this often, so "last done" stays current
RETRY_AFTER = timedelta(hours=1)         # after OpenAI was briefly unavailable
MIN_GAP = timedelta(minutes=10)          # between automatic attempts, whatever happens


def family(title: str) -> str:
    """Routine family: the title without a trailing version (\"Push Day v2.1\" -> \"push day\")."""
    return re.sub(r"\s*v\d+(\.\d+)*\s*$", "", (title or "").strip(), flags=re.I).strip().lower()


# ---------------------------------------------------------------- settings

def default_programme(conn) -> list[str]:
    """Until one is chosen: the newest version of each routine in the folder of the routine done most recently."""
    last = conn.execute("SELECT r.folder_id FROM workouts w JOIN routines r ON r.id = w.routine_id "
                        "ORDER BY w.start_utc DESC LIMIT 1").fetchone()
    if not last:
        return []
    newest: dict[str, tuple] = {}
    for rid, title, updated in conn.execute("SELECT id, title, updated_utc FROM routines WHERE folder_id IS ?", (last[0],)):
        fam = family(title)
        if fam not in newest or (updated or "") > newest[fam][1]:
            newest[fam] = (rid, updated or "")
    return [v[0] for v in newest.values()]


def programme_ids(conn) -> list[str]:
    raw = get_meta(conn, "coach_programme")
    if raw is None:
        return default_programme(conn)
    try:
        return json.loads(raw)
    except ValueError:
        return []


def set_programme(conn, ids: list[str]) -> list[str]:
    known = {r[0] for r in conn.execute("SELECT id FROM routines")}
    ids = [i for i in dict.fromkeys(ids) if i in known][:20]
    set_meta(conn, "coach_programme", json.dumps(ids))
    return ids


def instructions(conn) -> str:
    return get_meta(conn, "coach_instructions") or DEFAULT_INSTRUCTIONS


def set_instructions(conn, text: str | None) -> None:
    text = (text or "").strip()[:4000]
    set_meta(conn, "coach_instructions", text or None)


# ---------------------------------------------------------------- the owner's GPT in ChatGPT (web.py /gpt/)

GPT_INSTRUCTIONS = """You are my training coach. My training data lives on my own site, which you reach with two actions.

When I ask what to do next (or anything like "next workout?", "what should I train?"):
1. Call getTrainingContext. Follow its owner_instructions, and the rules in reply_rules.
2. Pick one or two workouts from programme only, best first, and set targets for each exercise from my recent sets.
3. Tell me in the chat: the workout, why (refer to my actual recent sessions), and targets per exercise like
   "3 x 8 @ 72.5 kg", with what I did last time.
4. Then call saveSuggestion with exactly the same picks, so they appear on my site. Use routine_id and template_id
   from the context. If it returns an error, fix what it says and try once more. Finish with a one-line note that
   it's on the site (or why it isn't).

For other questions about my training, call getTrainingContext and answer from it. Never invent sets I didn't do."""


def _key_hash(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


def new_gpt_key(conn) -> str:
    """A new key for the GPT's Action. Only its hash is kept; the old key stops working."""
    key = "hevy_" + secrets.token_urlsafe(32)
    set_meta(conn, "gpt_key_sha256", _key_hash(key))
    set_meta(conn, "gpt_key_created_at", iso(utcnow()))
    set_meta(conn, "gpt_last_used_at", None)
    return key


def revoke_gpt_key(conn) -> None:
    for k in ("gpt_key_sha256", "gpt_key_created_at", "gpt_last_used_at"):
        set_meta(conn, k, None)


def gpt_key_ok(conn, presented: str | None) -> bool:
    want = get_meta(conn, "gpt_key_sha256")
    if not want or not presented:
        return False
    key = presented[7:].strip() if presented[:7].lower() == "bearer " else presented.strip()
    return hmac.compare_digest(_key_hash(key), want)


def gpt_context(conn, today: date) -> dict:
    """What the GPT reads: everything the OpenAI coach would be sent, plus how to answer."""
    ctx = context(conn, today)
    rules = rules_suggest(ctx)
    return {
        "today": ctx["today"],
        "owner_instructions": instructions(conn),
        "reply_rules": [
            "Choose one or two different workouts from programme only (by routine_id), best first.",
            "Give targets for the routine's exercises by template_id, in the routine's order; leave one out only for a "
            "clear reason and say why.",
            "Weights in kg. Use each exercise's step (performance) and never exceed heaviest plus one step.",
            "Keep warm-up sets as type warmup; timed or distance work uses duration_s / distance_m.",
            "rules_proposal is a simple baseline from the site; agree or differ, using judgement.",
        ],
        "programme": ctx["programme"],
        "recent_sessions_21_days": ctx["recent"],
        "muscle_sets_last_48h": ctx["muscles_last_48h"],
        "cardio_minutes_last_7_days": ctx["cardio_minutes_7d"],
        "days_since_last_gym_session": ctx["days_since_gym"],
        "performance": ctx["performance"],
        "rules_proposal": [{"routine_id": s["routine_id"], "title": s["title"], "why": s["why"],
                            "targets": {e["template_id"]: e["sets"] for e in s["exercises"]}} for s in rules["suggestions"]],
    }


def save_gpt_suggestion(db, reply: dict, now: datetime, today: date) -> dict:
    """Check a suggestion posted by the GPT and keep it (CoachError if it doesn't fit the programme)."""
    with db.read() as conn:
        ctx = context(conn, today)
    rules = rules_suggest(ctx)
    result = validate(reply, ctx, rules)
    with db.tx() as conn:
        sid = conn.execute(
            "INSERT INTO coach_suggestions (created_at, trigger, source, based_on, result_json, rules_json) "
            "VALUES (?, 'chatgpt', 'chatgpt', ?, ?, ?)", (iso(now), ctx["latest_workout_id"], json.dumps(result), json.dumps(rules))).lastrowid
        set_meta(conn, "gpt_last_used_at", iso(now))
    log.info("coach suggestion %s: chatgpt", sid)
    return {"id": sid, **result}


# ---------------------------------------------------------------- what both engines see

def _sets(sets: list[dict]) -> list[dict]:
    out = []
    for s in sets or []:
        out.append({"type": s.get("type") or "normal", "weight_kg": s.get("weight_kg"), "reps": s.get("reps"),
                    "duration_s": s.get("duration_seconds"), "distance_m": s.get("distance_meters")})
    return out


def context(conn: sqlite3.Connection, today: date) -> dict:
    ids = programme_ids(conn)
    tmpl = {r["id"]: dict(r) for r in conn.execute("SELECT id, title, type, primary_muscle, equipment FROM exercise_templates")}
    measure_of = {r[0]: r[1] for r in conn.execute("SELECT template_id, max(measure) FROM an_exercise_sessions GROUP BY template_id")}
    titles_by_id = {r[0]: r[1] for r in conn.execute("SELECT id, title FROM routines")}
    # every routine (any version) of each family, for "last done"
    fam_ids: dict[str, set] = {}
    for rid, t in titles_by_id.items():
        fam_ids.setdefault(family(t), set()).add(rid)
    programme = []
    for rid in ids:
        payload, deleted, _ = rawstore.current_payload(conn, "routine", rid)
        if not payload or deleted:
            continue
        fam = family(payload.get("title", ""))
        ids_ = tuple(fam_ids.get(fam, {rid}))
        last = conn.execute(f"SELECT max(a.local_date), count(*), min(a.start_ms), max(a.start_ms) FROM workouts w "
                            f"JOIN an_workouts a ON a.workout_id = w.id WHERE w.routine_id IN ({','.join('?' * len(ids_))})", ids_).fetchone()
        exercises = []
        for e in sorted(payload.get("exercises") or [], key=lambda e: e.get("index", 0)):
            tid = e.get("exercise_template_id")
            t = tmpl.get(tid, {})
            exercises.append({"template_id": tid, "title": e.get("title") or t.get("title"), "primary_muscle": t.get("primary_muscle"),
                              "measure": measure_of.get(tid) or ("cardio" if t.get("primary_muscle") == "cardio" else None),
                              "notes": e.get("notes") or "", "rest_s": e.get("rest_seconds"), "sets": _sets(e.get("sets"))})
        programme.append({"routine_id": rid, "title": (payload.get("title") or "").strip(), "family": fam, "last_done": last[0],
                          "times_done": last[1], "days_since": (today - date.fromisoformat(last[0])).days if last[0] else None,
                          "first_ms": last[2], "last_ms": last[3], "exercises": exercises})
    # the rhythm: gym sessions since the programme started, and since each routine was last done
    starts = [r["first_ms"] for r in programme if r["first_ms"]]
    gym = [x[0] for x in conn.execute("SELECT start_ms FROM an_workouts WHERE kind = 'gym' AND start_ms >= ? ORDER BY start_ms",
                                      (min(starts) if starts else 0,))] if starts else []
    for r in programme:
        r["gym_sessions_since"] = sum(1 for t in gym if t > r["last_ms"]) if r["last_ms"] else None
        r["usual_every_n_sessions"] = round(len(gym) / r["times_done"], 1) if r["times_done"] else None
        del r["first_ms"], r["last_ms"]
    start = (today - timedelta(days=RECENT_DAYS)).isoformat()
    recent = []
    for w in conn.execute("SELECT w.id, w.title, w.routine_id, a.local_date, a.kind, a.duration_s, a.working_sets, a.volume_kg, a.distance_m "
                          "FROM workouts w JOIN an_workouts a ON a.workout_id = w.id WHERE a.local_date >= ? ORDER BY a.start_ms", (start,)):
        exs = []
        for e in conn.execute("SELECT s.template_id, t.title, s.working_sets, s.top_weight_kg, s.top_weight_reps, s.volume_kg, s.total_distance_m, "
                              "s.total_duration_s FROM an_exercise_sessions s JOIN exercise_templates t ON t.id = s.template_id "
                              "WHERE s.workout_id = ? ORDER BY s.position", (w["id"],)):
            top = f"{e[3]:g} kg × {e[4]:g}" if e[3] and e[4] else (f"{e[6] / 1000:.1f} km" if e[6] else (f"{e[7] / 60:.0f} min" if e[7] else ""))
            exs.append({"title": e[1], "working_sets": e[2], "top_set": top})
        recent.append({"date": w["local_date"], "title": w["title"], "kind": w["kind"], "routine": titles_by_id.get(w["routine_id"]),
                       "minutes": round((w["duration_s"] or 0) / 60), "working_sets": w["working_sets"], "volume_kg": round(w["volume_kg"]),
                       "distance_km": round(w["distance_m"] / 1000, 1), "exercises": exs})
    since48 = (today - timedelta(days=1)).isoformat()                        # today and yesterday ~ the last 48 h
    recent_muscles = {m: round(v, 1) for m, v in conn.execute(
        "SELECT muscle, sum(sets) FROM an_muscle_days WHERE local_date >= ? GROUP BY muscle", (since48,))}
    cardio_min = conn.execute("SELECT coalesce(sum(cardio_duration_s), 0) / 60 FROM an_workouts WHERE local_date >= ?",
                              ((today - timedelta(days=6)).isoformat(),)).fetchone()[0]
    last_gym = conn.execute("SELECT max(local_date) FROM an_workouts WHERE kind = 'gym'").fetchone()[0]
    latest = conn.execute("SELECT workout_id FROM an_workouts ORDER BY start_ms DESC LIMIT 1").fetchone()
    # last performance of every programme exercise
    perf = {}
    for r in programme:
        for e in r["exercises"]:
            tid = e["template_id"]
            if tid in perf or not tid:
                continue
            s = conn.execute("SELECT we_id, local_date, best_e1rm_kg FROM an_exercise_sessions WHERE template_id = ? ORDER BY start_ms DESC LIMIT 1",
                             (tid,)).fetchone()
            if not s:
                perf[tid] = None
                continue
            sets = [dict(x) for x in conn.execute("SELECT weight_kg, reps, duration_s, distance_m, rpe, set_type FROM an_sets WHERE we_id = ? "
                                                  "AND working = 1 ORDER BY set_position", (s[0],))]
            best_w = conn.execute("SELECT max(weight_kg) FROM an_sets WHERE template_id = ? AND working = 1 AND suspect IS NULL", (tid,)).fetchone()[0]
            weights = [x[0] for x in conn.execute("SELECT DISTINCT weight_kg FROM an_sets WHERE template_id = ? AND weight_kg > 0", (tid,))]
            perf[tid] = {"date": s[1], "days_ago": (today - date.fromisoformat(s[1])).days, "sets": sets,
                         "best_e1rm": round(s[2], 1) if s[2] else None, "heaviest": best_w, "step": _step(weights) if weights else 2.5}
    return {"today": today.isoformat(), "programme": programme, "recent": recent, "muscles_last_48h": recent_muscles,
            "cardio_minutes_7d": round(cardio_min), "last_gym_session": last_gym,
            "days_since_gym": (today - date.fromisoformat(last_gym)).days if last_gym else None,
            "latest_workout_id": latest[0] if latest else None, "performance": perf}


# ---------------------------------------------------------------- the rules engine

def _fmt_sets(sets):
    parts = []
    for s in sets:
        if s.get("weight_kg") and s.get("reps"):
            parts.append(f"{s['weight_kg']:g}×{s['reps']:g}")
        elif s.get("reps"):
            parts.append(f"{s['reps']:g} reps")
        elif s.get("duration_s"):
            parts.append(f"{s['duration_s'] / 60:.0f} min" if s["duration_s"] >= 120 else f"{s['duration_s']:g} s")
        elif s.get("distance_m"):
            parts.append(f"{s['distance_m'] / 1000:.1f} km")
    return ", ".join(parts)


def _plain(s: dict) -> dict:
    return {"type": s.get("type") or "normal", "weight_kg": s.get("weight_kg"), "reps": s.get("reps"),
            "duration_s": s.get("duration_s"), "distance_m": s.get("distance_m")}


def targets(ex: dict, perf: dict | None) -> dict:
    """Targets for one exercise: the routine's plan, moved by what happened last time (double progression)."""
    plan = [s for s in ex["sets"] if s["type"] != "warmup"]
    warm = [s for s in ex["sets"] if s["type"] == "warmup"]
    out = {"template_id": ex["template_id"], "title": ex["title"], "notes": ex.get("notes") or None, "last_time": None, "change": None}
    if perf:
        out["last_time"] = f"{_fmt_sets(perf['sets'])} ({perf['date']})" if perf["sets"] else None
    weighted = [s for s in plan if s.get("weight_kg") and s.get("reps")]
    if not weighted or not perf or not perf["sets"]:
        out["sets"] = [_plain(s) for s in ex["sets"]]
        out["change"] = "as planned" if perf else "first time: start with the planned sets"
        return out
    step = perf["step"]
    last_w = [s for s in perf["sets"] if s.get("weight_kg") and s.get("reps")]
    last_top = max((s["weight_kg"] for s in last_w), default=None)
    planned_top = max(s["weight_kg"] for s in weighted)
    planned_reps = [s["reps"] for s in weighted]
    hit = bool(last_w) and len(last_w) >= len(weighted) and all(
        a["reps"] >= b for a, b in zip(sorted(last_w, key=lambda s: -s["weight_kg"]), planned_reps))
    base = max(planned_top, last_top or 0)
    if last_top is None:
        target, why = planned_top, "as planned"
    elif hit and last_top >= planned_top:
        target, why = base + step, f"+{step:g} kg: you hit every rep last time"
    else:
        target, why = (last_top if last_top >= planned_top * 0.9 else planned_top), "same weight: build the reps first"
    if perf["days_ago"] > EASE_BACK_DAYS:
        eased = math.floor(target * 0.9 / step) * step
        target, why = eased, f"eased back about 10% after {perf['days_ago']} days off this exercise"
    if perf.get("heaviest"):
        target = min(target, perf["heaviest"] + step)
    scale = target / planned_top if planned_top else 1
    sets = [_plain(s) for s in warm]
    for s in plan:
        if s.get("weight_kg") and s.get("reps"):
            sets.append({**_plain(s), "weight_kg": round(s["weight_kg"] * scale / step) * step})
        else:
            sets.append(_plain(s))
    out["sets"], out["change"] = sets, why
    return out


def rules_suggest(ctx: dict, n: int = 2) -> dict:
    if not ctx["programme"]:
        return {"suggestions": [], "summary": "Pick your programme routines on the System page to get suggestions."}
    scored = []
    for r in ctx["programme"]:
        planned = {}
        for e in r["exercises"]:
            m = e.get("primary_muscle")
            if m and m not in ("cardio", "other", "full_body"):
                planned[m] = planned.get(m, 0) + len([s for s in e["sets"] if s["type"] != "warmup"])
        total = sum(planned.values()) or 1
        overlap = sum(min(1.0, ctx["muscles_last_48h"].get(m, 0) / max(1, n_)) * n_ for m, n_ in planned.items()) / total
        is_cardio = all(e.get("measure") == "cardio" or e.get("primary_muscle") == "cardio" for e in r["exercises"])
        cardio_done = is_cardio and ctx["cardio_minutes_7d"] >= CARDIO_ENOUGH_MIN
        if r["times_done"]:
            # how overdue, against how often it's usually done: 1.0 = right on time
            due = (r["gym_sessions_since"] + 1) / max(1.0, r["usual_every_n_sessions"])
            why = [f"Usually every {r['usual_every_n_sessions']:g} gym sessions; {r['gym_sessions_since']} since you last did it "
                   f"({r['days_since']} days ago)."]
        else:
            due, why = 0.5, ["Not done yet on this programme."]
        score = due * (1 - 0.7 * overlap) * (0.5 if cardio_done else 1) + (r["days_since"] or 0) / 1000   # days break ties
        if overlap >= 0.3:
            why.append(f"Its main muscles were trained in the last 48 hours ({overlap * 100:.0f}% overlap), so it ranks lower.")
        if cardio_done:
            why.append(f"You've already done {ctx['cardio_minutes_7d']} minutes of cardio this week.")
        main = sorted(planned, key=lambda m: -planned[m])[:3]
        if main:
            why.append("Main focus: " + ", ".join(m.replace("_", " ") for m in main) + ".")
        scored.append((score, r, why))
    scored.sort(key=lambda x: -x[0])
    out = []
    for score, r, why in scored[:n]:
        out.append({"routine_id": r["routine_id"], "title": r["title"], "why": " ".join(why),
                    "exercises": [targets(e, ctx["performance"].get(e["template_id"])) for e in r["exercises"]]})
    gap = ctx["days_since_gym"]
    summary = (f"{gap} days since your last gym session: weights are eased back where you've been away." if gap and gap > EASE_BACK_DAYS
               else "Rotating through your programme by what's most due and what's recovered.")
    return {"suggestions": out, "summary": summary}


# ---------------------------------------------------------------- checking an OpenAI reply

def _num(v, lo, hi, integer=False):
    if v is None:
        return None
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or not lo <= v <= hi:
        raise ValueError
    return int(v) if integer else round(float(v), 2)


def validate(reply: dict, ctx: dict, rules: dict) -> dict:
    """An OpenAI reply in the page's shape, or CoachError. Routines must be in the programme and exercises in the
    routine; numbers must be plausible. An exercise with implausible targets falls back to the rules' targets."""
    by_id = {r["routine_id"]: r for r in ctx["programme"]}
    rule_targets = {}
    for r in ctx["programme"]:
        for e in r["exercises"]:
            rule_targets.setdefault(e["template_id"], targets(e, ctx["performance"].get(e["template_id"])))
    if not isinstance(reply, dict) or not isinstance(reply.get("suggestions"), list):
        raise CoachError("the answer had no suggestions list", transient=True)
    out, seen, problems = [], set(), []
    for sug in reply["suggestions"]:
        rid = sug.get("routine_id") if isinstance(sug, dict) else None
        if rid not in by_id:
            problems.append(f"routine_id {rid!r} is not in the programme")
            continue
        if rid in seen:
            continue
        seen.add(rid)
        routine = by_id[rid]
        given = {e.get("template_id"): e for e in sug.get("exercises") or [] if isinstance(e, dict)}
        exercises = []
        for ex in routine["exercises"]:
            tid = ex["template_id"]
            if tid not in given:
                continue
            base = rule_targets[tid]
            perf = ctx["performance"].get(tid) or {}
            planned = max([s.get("weight_kg") or 0 for s in ex["sets"]] or [0])
            cap = max(planned, perf.get("heaviest") or 0) * 1.3 + 5
            try:
                sets = []
                for s in given[tid].get("sets") or []:
                    if s.get("type") not in ("warmup", "normal", "failure", "dropset"):
                        raise ValueError
                    sets.append({"type": s["type"], "weight_kg": _num(s.get("weight_kg"), 0, cap),
                                 "reps": _num(s.get("reps"), 1, 100, True), "duration_s": _num(s.get("duration_s"), 1, 4 * 3600, True),
                                 "distance_m": _num(s.get("distance_m"), 1, 50000)})
                if not 1 <= len(sets) <= 12:
                    raise ValueError
                change = str(given[tid].get("change") or "")[:160]
            except (ValueError, TypeError, AttributeError):
                sets, change = base["sets"], "rules targets (the suggested numbers looked wrong)"
            exercises.append({**base, "sets": sets, "change": change})
        if len(exercises) < max(1, len(routine["exercises"]) // 2):     # dropping most of the routine isn't the routine
            problems.append(f"{routine['title']}: only {len(exercises)} of its {len(routine['exercises'])} exercises matched "
                            "its template_ids")
            continue
        out.append({"routine_id": rid, "title": routine["title"], "why": str(sug.get("why") or "")[:600], "exercises": exercises})
        if len(out) == 2:
            break
    if not out:
        raise CoachError("the answer didn't match the programme" + (f" ({'; '.join(problems)})" if problems else ""))
    return {"suggestions": out, "summary": str(reply.get("summary") or "")[:300]}


# ---------------------------------------------------------------- making and keeping suggestions

def latest(conn) -> dict | None:
    row = conn.execute("SELECT * FROM coach_suggestions ORDER BY id DESC LIMIT 1").fetchone()
    if not row:
        return None
    d = dict(row)
    d["result"] = json.loads(d.pop("result_json"))
    d.pop("rules_json")
    return d


def calls_last_day(conn, now: datetime) -> int:
    return conn.execute("SELECT count(*) FROM coach_suggestions WHERE model IS NOT NULL AND created_at >= ?",
                        (iso(now - timedelta(days=1)),)).fetchone()[0]


def refresh(db, settings, trigger: str, now: datetime, today: date, ai=None) -> int:
    """Make a new suggestion and keep it. The rules answer is always made; OpenAI's replaces it when usable."""
    with db.read() as conn:
        ctx = context(conn, today)
        rules = rules_suggest(ctx)
        instr = instructions(conn)
        used = calls_last_day(conn, now)
    result, source, model, error, t_in, t_out, retry = rules, "rules", None, None, None, None, False
    if ai is None:
        error = "no OpenAI key installed"
    elif not rules["suggestions"]:
        error = "no programme chosen"
    elif used >= settings.coach_daily_calls:
        error = f"daily limit of {settings.coach_daily_calls} OpenAI calls reached"
    else:
        try:
            model = ai.choose_model()
            reply, model, t_in, t_out = ai.suggest(ctx, rules, instr)
            result, source = validate(reply, ctx, rules), "openai"
        except CoachError as exc:
            error, retry = str(exc), exc.transient
        except Exception as exc:                      # a coach problem must never break syncing
            log.exception("OpenAI coach failed")
            error, retry = f"unexpected error ({type(exc).__name__})", True
    with db.tx() as conn:
        sid = conn.execute(
            "INSERT INTO coach_suggestions (created_at, trigger, source, model, based_on, result_json, rules_json, openai_error, "
            "tokens_in, tokens_out) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (iso(now), trigger, source, model, ctx["latest_workout_id"], json.dumps(result), json.dumps(rules), error, t_in, t_out)).lastrowid
        set_state(conn, "coach_retry_at", iso(now + RETRY_AFTER) if retry and ai is not None else None)
        set_state(conn, "coach_last_attempt_at", iso(now))
    log.info("coach suggestion %s: %s (%s)%s", sid, source, trigger, f", OpenAI: {error}" if error and ai else "")
    return sid


def due(conn, now: datetime) -> tuple[str, list[int]] | None:
    """Whether a new suggestion is needed now: (trigger, coach_requests ids it answers)."""
    reqs = [r[0] for r in conn.execute("SELECT id FROM coach_requests WHERE picked_up_at IS NULL ORDER BY id")]
    if reqs:
        return "manual", reqs
    last_try = parse_ts(get_state(conn, "coach_last_attempt_at"))
    if last_try and now - last_try < MIN_GAP:
        return None
    cur = conn.execute("SELECT created_at, based_on, source FROM coach_suggestions ORDER BY id DESC LIMIT 1").fetchone()
    if cur is None:
        return "stale", []
    newest = conn.execute("SELECT workout_id FROM an_workouts ORDER BY start_ms DESC LIMIT 1").fetchone()
    if newest and newest[0] != cur[1]:
        return "sync", []
    if cur[2] == "chatgpt":
        return None                          # asked for in ChatGPT: kept until a new workout arrives
    if now - parse_ts(cur[0]) >= STALE_AFTER:
        return "stale", []
    retry_at = parse_ts(get_state(conn, "coach_retry_at"))
    if retry_at and now >= retry_at:
        return "retry", []
    return None
