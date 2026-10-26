"""Deeper analyses (docs/METRICS.md, "Plateaus", "Momentum", "Training DNA", "Muscle balance", "Exercise order effect").

Every result says why it was produced (`reasons`) and how much data it rests on; below the minimum it says
"insufficient data" instead of guessing. Nothing here is stored: it is computed from the an_* tables on request.
"""
from __future__ import annotations

import random
from datetime import date, timedelta

from . import rules
from .queries import DAY_MS, pct, r1, r2, titles
from .stats import median, theil_sen

# ---------------------------------------------------------------- thresholds (documented in METRICS.md)
PLATEAU_WINDOW = 8                 # look at the last 8 sessions that have an e1RM
PLATEAU_MIN_SESSIONS = 6
FLAT_PCT_PER_MONTH = 1.0           # |e1RM trend| below this is "flat"
STALLED_SESSIONS = 6               # flat, and no new best for this many sessions
PRODUCTIVE_GAIN_PCT = 5.0          # volume or reps up at least this much (first half vs second half of the window)
RECENT_DAYS = 90                   # an exercise counts as current if done within 90 days of the last workout
NEW_DAYS = 42                      # momentum "new": first done within 6 weeks of the last workout
ORDER_EARLY_MAX, ORDER_LATE_MIN = 2, 4     # positions 1-2 vs 4 and later (1-based)
ORDER_MIN_PER_GROUP = 5
ORDER_BASELINE_DAYS = 56           # baseline: median e1RM of the exercise's other sessions within ±8 weeks
ORDER_BOOTSTRAP, ORDER_SEED = 2000, 20261020

UPPER = {"chest", "shoulders", "biceps", "triceps", "forearms", "lats", "upper_back", "traps", "neck"}
LOWER = {"quadriceps", "hamstrings", "glutes", "calves", "abductors", "adductors"}
CORE = {"abdominals", "lower_back"}
PUSH = {"chest", "shoulders", "triceps"}
PULL = {"lats", "upper_back", "biceps"}
ANTERIOR = {"chest", "abdominals", "quadriceps", "biceps", "shoulders", "adductors"}
POSTERIOR = {"upper_back", "lats", "lower_back", "glutes", "hamstrings", "calves", "triceps", "traps", "abductors"}
FREE = {"barbell", "dumbbell", "kettlebell", "plate"}
MACHINE = {"machine"}
BODYWEIGHT = {"none", "resistance_band", "suspension"}


def _sessions(conn, template_id: str | None = None):
    sql = ("SELECT s.*, t.title, t.primary_muscle, t.equipment FROM an_exercise_sessions s JOIN exercise_templates t ON t.id = s.template_id"
           + (" WHERE s.template_id = ?" if template_id else "") + " ORDER BY s.template_id, s.start_ms")
    out: dict[str, list[dict]] = {}
    for r in conn.execute(sql, (template_id,) if template_id else ()):
        out.setdefault(r["template_id"], []).append(dict(r))
    return out


def _last_workout_ms(conn) -> int | None:
    return conn.execute("SELECT max(start_ms) FROM an_workouts").fetchone()[0]


# ---------------------------------------------------------------- plateaus

def plateau(sessions: list[dict], ref_ms: int | None) -> dict:
    """Classify an exercise's recent progress from its sessions (oldest first)."""
    e = [s for s in sessions if s["best_e1rm_kg"]]
    last = sessions[-1] if sessions else None
    recent = bool(last and ref_ms and ref_ms - last["start_ms"] <= RECENT_DAYS * DAY_MS)
    base = {"recent": recent, "last_date": last["local_date"] if last else None, "sessions_considered": 0}
    if len(e) < PLATEAU_MIN_SESSIONS:
        return {**base, "status": "insufficient", "reasons": [f"Needs {PLATEAU_MIN_SESSIONS} sessions with an estimated 1RM (has {len(e)})."]}
    w = e[-PLATEAU_WINDOW:]
    t0 = w[0]["start_ms"]
    slope = theil_sen([((s["start_ms"] - t0) / DAY_MS, s["best_e1rm_kg"]) for s in w])
    mid = median([s["best_e1rm_kg"] for s in w])
    trend = slope * 30.44 / mid * 100 if slope is not None and mid else 0.0
    best_before = max((s["best_e1rm_kg"] for s in e[:-len(w)]), default=None)
    best_idx = max(range(len(e)), key=lambda i: (e[i]["best_e1rm_kg"], i))
    since_best = len(e) - 1 - best_idx
    half = len(w) // 2
    first, second = w[:half], w[half:]
    mean = lambda xs: sum(xs) / len(xs) if xs else 0
    vol = pct(mean([s["volume_kg"] for s in second]), mean([s["volume_kg"] for s in first]))
    reps = pct(mean([s["reps"] for s in second]), mean([s["reps"] for s in first]))
    load = pct(mean([s["top_weight_kg"] or 0 for s in second]), mean([s["top_weight_kg"] or 0 for s in first]))
    sets = pct(mean([s["working_sets"] for s in second]), mean([s["working_sets"] for s in first]))
    span_days = round((w[-1]["start_ms"] - t0) / DAY_MS)
    reasons = [f"e1RM trend {trend:+.1f}% a month over the last {len(w)} sessions ({span_days} days)."]
    if since_best == 0:
        reasons.append("The latest session set a new best e1RM.")
    else:
        reasons.append(f"No new e1RM high for {since_best} session{'s' if since_best != 1 else ''}.")
    if since_best >= len(w):
        below = (1 - w[-1]["best_e1rm_kg"] / e[best_idx]["best_e1rm_kg"]) * 100
        reasons.append(f"Your best e1RM, {e[best_idx]['best_e1rm_kg']:.1f} kg on {e[best_idx]['local_date']}, is older than these sessions; "
                       f"the latest is {below:.0f}% below it.")
    changes = f"First {len(first)} vs last {len(second)} sessions: volume {_s(vol)}, reps {_s(reps)}, top load {_s(load)}, sets {_s(sets)}."
    if trend >= FLAT_PCT_PER_MONTH:
        status = "improving"
    elif trend <= -FLAT_PCT_PER_MONTH:
        status = "declining"
    elif since_best >= STALLED_SESSIONS and ((vol or 0) >= PRODUCTIVE_GAIN_PCT or (reps or 0) >= PRODUCTIVE_GAIN_PCT):
        status = "productive_plateau"
        reasons.append(f"e1RM is flat but the work is growing ({'volume' if (vol or 0) >= PRODUCTIVE_GAIN_PCT else 'reps'} up at least {PRODUCTIVE_GAIN_PCT:g}%).")
    elif since_best >= STALLED_SESSIONS:
        status = "stalled"
        reasons.append(f"e1RM is flat, no new high for {STALLED_SESSIONS}+ sessions, and volume and reps aren't rising either.")
    else:
        status = "stable"
        reasons.append("e1RM is flat but a best was set recently.")
    reasons.append(changes)
    if not recent:
        reasons.append(f"Last done {base['last_date']}, more than {RECENT_DAYS} days before your latest workout.")
    return {**base, "status": status, "reasons": reasons, "sessions_considered": len(w), "trend_pct_per_month": r1(trend),
            "sessions_since_best": since_best, "best_e1rm": r1(e[best_idx]["best_e1rm_kg"]), "best_date": e[best_idx]["local_date"],
            "window_best_before": r1(best_before), "changes": {"volume_pct": vol, "reps_pct": reps, "load_pct": load, "sets_pct": sets}}


def _s(v):
    return "–" if v is None else f"{v:+.0f}%"


# ---------------------------------------------------------------- momentum

MOMENTUM_ORDER = ("accelerating", "improving", "maintaining", "plateauing", "declining", "new")


def momentum_of(sessions: list[dict], ref_ms: int | None) -> dict | None:
    """Momentum for an exercise done recently (relative to the latest workout), or None if it isn't current."""
    if not sessions or not ref_ms or ref_ms - sessions[-1]["start_ms"] > RECENT_DAYS * DAY_MS:
        return None
    if ref_ms - sessions[0]["start_ms"] <= NEW_DAYS * DAY_MS:
        return {"momentum": "new", "reasons": [f"First done {sessions[0]['local_date']}, within 6 weeks of your latest workout."]}
    p = plateau(sessions, ref_ms)
    if p["status"] == "insufficient":
        return None
    reasons = list(p["reasons"])
    m = {"improving": "improving", "declining": "declining", "stalled": "plateauing", "productive_plateau": "maintaining",
         "stable": "maintaining"}[p["status"]]
    if m == "improving":
        e = [s for s in sessions if s["best_e1rm_kg"]][-4:]
        if len(e) == 4:
            t0 = e[0]["start_ms"]
            sl = theil_sen([((s["start_ms"] - t0) / DAY_MS, s["best_e1rm_kg"]) for s in e])
            short = sl * 30.44 / median([s["best_e1rm_kg"] for s in e]) * 100 if sl is not None else None
            if short is not None and short >= p["trend_pct_per_month"] + 1.0 and since_ok(p):
                m = "accelerating"
                reasons.insert(1, f"The last 4 sessions are rising faster ({short:+.1f}% a month) than the last 8.")
    return {"momentum": m, "plateau": p["status"], "trend_pct_per_month": p.get("trend_pct_per_month"), "reasons": reasons}


def since_ok(p):
    return p.get("sessions_since_best", 99) <= 2


def momentum(conn) -> dict:
    ref = _last_workout_ms(conn)
    names = titles(conn)
    groups = {k: [] for k in MOMENTUM_ORDER}
    for tid, ss in _sessions(conn).items():
        m = momentum_of(ss, ref)
        if m:
            groups[m["momentum"]].append({"template_id": tid, "title": names.get(tid, tid), "primary_muscle": ss[-1]["primary_muscle"],
                                          "sessions": len(ss), "last_date": ss[-1]["local_date"], **m})
    for g in groups.values():
        g.sort(key=lambda x: -(x.get("trend_pct_per_month") or 0))
    ref_date = conn.execute("SELECT max(local_date) FROM an_workouts").fetchone()[0]
    return {"as_of": ref_date, "exercises": groups, "muscles": muscle_momentum(conn, ref_date)}


def muscle_momentum(conn, ref_date: str | None) -> list[dict]:
    """Credited sets a week: the last 4 weeks vs the 8 weeks before, up to the latest workout."""
    if not ref_date:
        return []
    end = date.fromisoformat(ref_date)
    recent_start, base_start = end - timedelta(weeks=4) + timedelta(days=1), end - timedelta(weeks=12) + timedelta(days=1)
    q = "SELECT muscle, sum(sets) FROM an_muscle_days WHERE local_date BETWEEN ? AND ? GROUP BY muscle"
    recent = {m: v / 4 for m, v in conn.execute(q, (recent_start.isoformat(), end.isoformat()))}
    base = {m: v / 8 for m, v in conn.execute(q, (base_start.isoformat(), (recent_start - timedelta(days=1)).isoformat()))}
    out = []
    for m in sorted(set(recent) | set(base)):
        if m in rules.NOT_ON_BODY_MAP:
            continue
        r, b = recent.get(m, 0.0), base.get(m, 0.0)
        if b < 1 and r < 1:
            continue
        status = "rising" if b < 1 or r >= b * 1.25 else "falling" if r <= b * 0.75 else "steady"
        out.append({"muscle": m, "sets_per_week": r1(r), "before_per_week": r1(b), "change_pct": pct(r, b) if b >= 1 else None,
                    "status": status})
    return sorted(out, key=lambda x: (["rising", "steady", "falling"].index(x["status"]), -x["sets_per_week"]))


def statuses(conn) -> dict:
    """Plateau status for every exercise (for lists)."""
    ref = _last_workout_ms(conn)
    return {tid: plateau(ss, ref) for tid, ss in _sessions(conn).items()}


# ---------------------------------------------------------------- training DNA and balance

def dna(conn, start: date, end: date) -> dict:
    a, b = start.isoformat(), end.isoformat()
    sets = conn.execute(
        "SELECT a.set_type, a.reps, a.measure, a.rpe, t.equipment, a.template_id FROM an_sets a JOIN exercise_templates t ON t.id = a.template_id "
        "WHERE a.working = 1 AND a.local_date BETWEEN ? AND ?", (a, b)).fetchall()
    multi = {r[0] for r in conn.execute("SELECT template_id FROM template_muscles GROUP BY template_id HAVING count(*) > 1")}
    n = len(sets)
    load = [s for s in sets if s["measure"] in rules.VOLUME_MEASURES and s["reps"]]
    share = lambda k, of: r1(k / of * 100) if of else None
    heavy = sum(1 for s in load if s["reps"] <= 5)
    moderate = sum(1 for s in load if 6 <= s["reps"] <= 12)
    high = sum(1 for s in load if s["reps"] > 12)
    strength_sets = [s for s in sets if s["measure"] != "cardio"]
    eq = lambda group: sum(1 for s in strength_sets if (s["equipment"] or "other") in group)
    workouts = conn.execute("SELECT kind, duration_s FROM an_workouts WHERE local_date BETWEEN ? AND ?", (a, b)).fetchall()
    gym = [w["duration_s"] for w in workouts if w["kind"] == "gym" and w["duration_s"]]
    exercises = {s["template_id"] for s in sets}
    new = conn.execute("SELECT count(*) FROM an_exercise_sessions WHERE session_index = 1 AND local_date BETWEEN ? AND ?", (a, b)).fetchone()[0]
    rpe = [s["rpe"] for s in sets if s["rpe"] is not None]
    mus = {m: v for m, v in conn.execute("SELECT muscle, sum(sets) FROM an_muscle_days WHERE local_date BETWEEN ? AND ? GROUP BY muscle", (a, b))}
    body = sum(v for m, v in mus.items() if m not in rules.NOT_ON_BODY_MAP)
    part = lambda group: sum(v for m, v in mus.items() if m in group)
    traits = [
        {"key": "heavy", "label": "Heavy work (1–5 reps)", "pct": share(heavy, len(load)), "of": f"{len(load)} weighted sets"},
        {"key": "moderate", "label": "Moderate reps (6–12)", "pct": share(moderate, len(load)), "of": f"{len(load)} weighted sets"},
        {"key": "high_rep", "label": "High reps (13+)", "pct": share(high, len(load)), "of": f"{len(load)} weighted sets"},
        {"key": "failure", "label": "Sets taken to failure", "pct": share(sum(1 for s in sets if s["set_type"] == "failure"), n), "of": f"{n} working sets"},
        {"key": "dropset", "label": "Drop sets", "pct": share(sum(1 for s in sets if s["set_type"] == "dropset"), n), "of": f"{n} working sets"},
        {"key": "multi_muscle", "label": "Multi-muscle exercises", "pct": share(sum(1 for s in strength_sets if s["template_id"] in multi), len(strength_sets)),
         "of": f"{len(strength_sets)} strength sets", "note": "Exercises Hevy lists with secondary muscles; a stand-in for compound lifts."},
        {"key": "free_weights", "label": "Free weights", "pct": share(eq(FREE), len(strength_sets)), "of": f"{len(strength_sets)} strength sets"},
        {"key": "machines", "label": "Machines and cables", "pct": share(eq(MACHINE), len(strength_sets)), "of": f"{len(strength_sets)} strength sets"},
        {"key": "bodyweight", "label": "Bodyweight and bands", "pct": share(eq(BODYWEIGHT), len(strength_sets)), "of": f"{len(strength_sets)} strength sets"},
        {"key": "cardio", "label": "Cardio sessions", "pct": share(sum(1 for w in workouts if w["kind"] == "cardio"), len(workouts)), "of": f"{len(workouts)} workouts"},
    ]
    return {
        "start": a, "end": b, "working_sets": n, "workouts": len(workouts), "traits": traits,
        "facts": {"median_gym_minutes": r1(median(gym) / 60) if gym else None, "exercises": len(exercises), "new_exercises": new,
                  "avg_rpe": r1(sum(rpe) / len(rpe)) if rpe else None, "rpe_logged_pct": share(len(rpe), n)},
        "regions": {"upper": share(part(UPPER), body), "lower": share(part(LOWER), body), "core": share(part(CORE), body)},
        "balance": [
            _pair("Push", PUSH, "Pull", PULL, mus), _pair("Chest", {"chest"}, "Back", {"lats", "upper_back"}, mus),
            _pair("Quads", {"quadriceps"}, "Hamstrings", {"hamstrings"}, mus), _pair("Biceps", {"biceps"}, "Triceps", {"triceps"}, mus),
            _pair("Upper body", UPPER, "Lower body", LOWER, mus), _pair("Front of body", ANTERIOR, "Back of body", POSTERIOR, mus),
        ],
        "enough": n >= 30,
    }


def _pair(a_label, a, b_label, b, mus):
    x, y = sum(v for m, v in mus.items() if m in a), sum(v for m, v in mus.items() if m in b)
    tot = x + y
    return {"a": a_label, "b": b_label, "a_sets": r1(x), "b_sets": r1(y), "a_pct": r1(x / tot * 100) if tot else None,
            "b_pct": r1(y / tot * 100) if tot else None}


# ---------------------------------------------------------------- exercise order effect

def _local_level(points: list[tuple[float, float]], at: float) -> float | None:
    """Least-squares line through (day, e1RM) points, evaluated at `at`: the exercise's level at that time."""
    n = len(points)
    if n < 4:
        return None
    mx = sum(p[0] for p in points) / n
    my = sum(p[1] for p in points) / n
    sxx = sum((p[0] - mx) ** 2 for p in points)
    slope = sum((p[0] - mx) * (p[1] - my) for p in points) / sxx if sxx else 0.0
    return my + slope * (at - mx)


def order_effect(sessions: list[dict]) -> dict:
    """Is e1RM lower when the exercise comes later in the workout? Each session's best e1RM is compared with the
    exercise's level at that time: a straight line fitted through its other sessions within ±8 weeks. So steady
    progress over time (or the start and end of the history) doesn't masquerade as an order effect."""
    e = [s for s in sessions if s["best_e1rm_kg"]]
    rel = []
    for s in e:
        near = [((o["start_ms"] - s["start_ms"]) / DAY_MS, o["best_e1rm_kg"]) for o in e
                if o is not s and abs(o["start_ms"] - s["start_ms"]) <= ORDER_BASELINE_DAYS * DAY_MS]
        level = _local_level(near, 0.0)
        if level and level > 0:
            rel.append((s["position"] + 1, s["best_e1rm_kg"] / level, s["best_e1rm_kg"]))
    early = [r for r in rel if r[0] <= ORDER_EARLY_MAX]
    late = [r for r in rel if r[0] >= ORDER_LATE_MIN]
    out = {"early_label": f"exercise 1–{ORDER_EARLY_MAX}", "late_label": f"exercise {ORDER_LATE_MIN} or later",
           "early_n": len(early), "late_n": len(late)}
    if len(early) < ORDER_MIN_PER_GROUP or len(late) < ORDER_MIN_PER_GROUP:
        return {**out, "available": False, "reason": f"needs {ORDER_MIN_PER_GROUP} sessions in each group (has {len(early)} early, {len(late)} late)"}
    me = sum(r[1] for r in early) / len(early)
    ml = sum(r[1] for r in late) / len(late)
    effect = (ml / me - 1) * 100
    rng = random.Random(ORDER_SEED)
    boots = []
    for _ in range(ORDER_BOOTSTRAP):
        be = [early[rng.randrange(len(early))][1] for _ in early]
        bl = [late[rng.randrange(len(late))][1] for _ in late]
        boots.append((sum(bl) / len(bl)) / (sum(be) / len(be)) * 100 - 100)
    boots.sort()
    lo, hi = boots[int(0.05 * len(boots))], boots[int(0.95 * len(boots)) - 1]
    clear = hi < 0 or lo > 0
    return {**out, "available": True, "effect_pct": r1(effect), "interval_90": [r1(lo), r1(hi)], "clear": clear,
            "early_avg_e1rm": r1(sum(r[2] for r in early) / len(early)), "late_avg_e1rm": r1(sum(r[2] for r in late) / len(late)),
            "summary": (f"When done {out['late_label']}, your e1RM has averaged {abs(effect):.1f}% {'lower' if effect < 0 else 'higher'} "
                        f"than when done as {out['early_label']}, relative to your level at the time."
                        + ("" if clear else " The difference is within normal session-to-session variation, so it isn't clear.")),
            "caution": "An observed pattern in your history, not proof of cause: other things (fatigue, the day's plan) vary too."}


def order_effects(conn) -> list[dict]:
    names = titles(conn)
    out = []
    for tid, ss in _sessions(conn).items():
        o = order_effect(ss)
        if o["available"]:
            out.append({"template_id": tid, "title": names.get(tid, tid), **o})
    return sorted(out, key=lambda x: x["effect_pct"])


# ---------------------------------------------------------------- muscles

def muscle_detail(conn, muscle: str, start: date, end: date) -> dict:
    a, b = start.isoformat(), end.isoformat()
    contrib = [dict(r) for r in conn.execute(
        "SELECT s.template_id, t.title, m.role, count(*) AS sessions, sum(s.working_sets) AS sets, "
        "sum(s.working_sets * CASE m.role WHEN 'primary' THEN 1.0 ELSE ? END) AS credited, sum(s.volume_kg) AS volume_kg "
        "FROM an_exercise_sessions s JOIN template_muscles m ON m.template_id = s.template_id AND m.muscle = ? "
        "JOIN exercise_templates t ON t.id = s.template_id WHERE s.local_date BETWEEN ? AND ? AND s.working_sets > 0 "
        "GROUP BY s.template_id ORDER BY credited DESC", (rules.SECONDARY_CREDIT, muscle, a, b))]
    for c in contrib:
        c["credited"], c["volume_kg"] = r1(c["credited"]), r1(c["volume_kg"])
    weekly = []
    acc: dict[str, float] = {}
    for d, v in conn.execute("SELECT local_date, sets FROM an_muscle_days WHERE muscle = ? ORDER BY local_date", (muscle,)):
        dd = date.fromisoformat(d)
        acc[(dd - timedelta(days=dd.weekday())).isoformat()] = acc.get((dd - timedelta(days=dd.weekday())).isoformat(), 0) + v
    if acc:
        cur = date.fromisoformat(min(acc))
        last = date.fromisoformat(max(acc))
        while cur <= last:
            weekly.append({"start": cur.isoformat(), "sets": r1(acc.get(cur.isoformat(), 0.0))})
            cur += timedelta(weeks=1)
    ref = _last_workout_ms(conn)
    progress = []
    for tid, ss in _sessions(conn).items():
        if ss[-1]["primary_muscle"] != muscle:
            continue
        p = plateau(ss, ref)
        if p["status"] != "insufficient":
            progress.append({"template_id": tid, "title": ss[-1]["title"], "status": p["status"], "trend_pct_per_month": p["trend_pct_per_month"],
                             "recent": p["recent"], "last_date": p["last_date"]})
    progress.sort(key=lambda x: (not x["recent"], -(x["trend_pct_per_month"] or 0)))
    recs = conn.execute("SELECT count(*), sum(meaningful) FROM an_prs p JOIN exercise_templates t ON t.id = p.template_id "
                        "WHERE t.primary_muscle = ? AND p.local_date BETWEEN ? AND ?", (muscle, a, b)).fetchone()
    return {"muscle": muscle, "contributors": contrib, "weekly": weekly, "progress": progress,
            "records": {"all": recs[0] or 0, "meaningful": recs[1] or 0}}


# ---------------------------------------------------------------- workouts: similar and compare

def _workout_templates(conn, wid):
    return {r[0] for r in conn.execute("SELECT template_id FROM an_exercise_sessions WHERE workout_id = ?", (wid,))}


def similar_workouts(conn, workout_id: str, limit: int = 6) -> list[dict]:
    """Earlier and later workouts with the same routine, or with most of the same exercises (Jaccard >= 0.5)."""
    me = conn.execute("SELECT w.routine_id, a.kind FROM workouts w JOIN an_workouts a ON a.workout_id = w.id WHERE w.id = ?", (workout_id,)).fetchone()
    if not me:
        return []
    mine = _workout_templates(conn, workout_id)
    by_w: dict[str, set] = {}
    for wid, tid in conn.execute("SELECT workout_id, template_id FROM an_exercise_sessions"):
        by_w.setdefault(wid, set()).add(tid)
    out = []
    for r in conn.execute("SELECT w.id, w.title, w.routine_id, a.local_date, a.volume_kg, a.working_sets, a.duration_s FROM workouts w "
                          "JOIN an_workouts a ON a.workout_id = w.id WHERE w.id != ? AND a.kind = ?", (workout_id, me["kind"])):
        other = by_w.get(r["id"], set())
        j = len(mine & other) / len(mine | other) if mine | other else 0
        same_routine = bool(me["routine_id"] and r["routine_id"] == me["routine_id"])
        if same_routine or j >= 0.5:
            out.append({"workout_id": r["id"], "title": r["title"], "local_date": r["local_date"], "similarity": r2(1.0 if same_routine else j),
                        "same_routine": same_routine, "volume_kg": r1(r["volume_kg"]), "working_sets": r["working_sets"], "duration_s": r["duration_s"]})
    out.sort(key=lambda x: (-x["similarity"], x["local_date"]), reverse=False)
    return sorted(out[: limit * 3], key=lambda x: x["local_date"], reverse=True)[:limit]


def compare_workouts(conn, a: str, b: str) -> dict | None:
    rows = {r["workout_id"]: dict(r) for r in conn.execute(
        "SELECT a.*, w.title, w.start_utc FROM an_workouts a JOIN workouts w ON w.id = a.workout_id WHERE a.workout_id IN (?, ?)", (a, b))}
    if a not in rows or b not in rows:
        return None
    names = titles(conn)
    ex = {}
    for wid in (a, b):
        for r in conn.execute("SELECT template_id, position, working_sets, reps, volume_kg, best_e1rm_kg, top_weight_kg, top_weight_reps, "
                              "total_distance_m, total_duration_s, avg_rpe FROM an_exercise_sessions WHERE workout_id = ? ORDER BY position", (wid,)):
            ex.setdefault(r["template_id"], {})[wid] = dict(r)
    exercises = []
    for tid, d in ex.items():
        x, y = d.get(a), d.get(b)
        exercises.append({"template_id": tid, "title": names.get(tid, tid), "a": x, "b": y,
                          "e1rm_change_pct": pct(y["best_e1rm_kg"], x["best_e1rm_kg"]) if x and y else None,
                          "volume_change_pct": pct(y["volume_kg"], x["volume_kg"]) if x and y and x["volume_kg"] else None})
    exercises.sort(key=lambda e: (e["a"] is None, e["b"] is None, (e["a"] or e["b"])["position"]))
    musc = {}
    for wid in (a, b):
        for m, v in conn.execute("SELECT m.muscle, sum(CASE m.role WHEN 'primary' THEN 1.0 ELSE ? END * s.working_sets) FROM an_exercise_sessions s "
                                 "JOIN template_muscles m ON m.template_id = s.template_id WHERE s.workout_id = ? GROUP BY m.muscle",
                                 (rules.SECONDARY_CREDIT, wid)):
            musc.setdefault(m, {})[wid] = r1(v)
    fields = ("duration_s", "working_sets", "reps", "volume_kg", "distance_m", "prs", "meaningful_prs", "avg_rpe")
    return {"a": rows[a], "b": rows[b], "change": {f: pct(rows[b][f], rows[a][f]) for f in fields},
            "exercises": exercises, "shared_exercises": sum(1 for e in exercises if e["a"] and e["b"]),
            "muscles": sorted(({"muscle": m, "a": v.get(a, 0), "b": v.get(b, 0)} for m, v in musc.items()), key=lambda x: -(x["a"] + x["b"]))}
