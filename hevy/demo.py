"""Made-up training history for the public demo (and for trying the app without a Hevy account).

``python -m hevy.cli demo-seed`` fills an empty database with about three and a half years of believable training
that ends yesterday: three programmes in turn (full body, push/pull/legs, then an upper/lower block with versioned
routines), strength that climbs quickly and then levels off, two breaks with some detraining, walks and cardio days,
bodyweight entries, and one obvious typo for the data notes. It is generated through the same raw layer and
normaliser as real Hevy data, so every page works on it exactly as it would on a real account.

The random seed is fixed, so the history is the same every night, shifted to end the day before.
"""
from __future__ import annotations

import hashlib
import math
import random
import uuid
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from . import normalise, rawstore
from .analytics import build as analytics
from .db import set_meta

SEED = 7
DAYS = 1300                     # about 3.6 years

# key: (title, type, primary, secondary, equipment, start, end, step)
# start/end: estimated 1RM in kg (per hand for dumbbells), reps for reps_only, seconds for timed work.
EXERCISES = {
    "BENCH": ("Bench Press (Barbell)", "weight_reps", "chest", ["triceps", "shoulders"], "barbell", 62, 102, 2.5),
    "INCLINE": ("Incline Bench Press (Dumbbell)", "weight_reps", "chest", ["shoulders", "triceps"], "dumbbell", 22, 38, 2),
    "OHP": ("Overhead Press (Barbell)", "weight_reps", "shoulders", ["triceps"], "barbell", 40, 63, 2.5),
    "SQUAT": ("Squat (Barbell)", "weight_reps", "quadriceps", ["glutes", "hamstrings"], "barbell", 72, 142, 2.5),
    "DEADLIFT": ("Deadlift (Barbell)", "weight_reps", "hamstrings", ["glutes", "lower_back", "traps"], "barbell", 95, 182, 2.5),
    "RDL": ("Romanian Deadlift (Barbell)", "weight_reps", "hamstrings", ["glutes", "lower_back"], "barbell", 65, 125, 2.5),
    "LEGPRESS": ("Leg Press (Machine)", "weight_reps", "quadriceps", ["glutes"], "machine", 130, 250, 5),
    "HIPTHRUST": ("Hip Thrust (Barbell)", "weight_reps", "glutes", ["hamstrings"], "barbell", 70, 160, 2.5),
    "LEGEXT": ("Leg Extension (Machine)", "weight_reps", "quadriceps", [], "machine", 45, 88, 2.5),
    "LEGCURL": ("Seated Leg Curl (Machine)", "weight_reps", "hamstrings", [], "machine", 40, 74, 2.5),
    "CALF": ("Standing Calf Raise (Machine)", "weight_reps", "calves", [], "machine", 70, 135, 5),
    "ROW": ("Bent Over Row (Barbell)", "weight_reps", "upper_back", ["lats", "biceps"], "barbell", 55, 98, 2.5),
    "CROW": ("Seated Cable Row - V Grip (Cable)", "weight_reps", "upper_back", ["lats", "biceps"], "cable", 50, 90, 2.5),
    "PULLDOWN": ("Lat Pulldown (Cable)", "weight_reps", "lats", ["biceps"], "cable", 50, 86, 2.5),
    "PULLUP": ("Pull Up", "reps_only", "lats", ["biceps", "upper_back"], "none", 5, 13, 1),
    "FLY": ("Chest Fly (Machine)", "weight_reps", "chest", [], "machine", 35, 68, 2.5),
    "LATERAL": ("Lateral Raise (Dumbbell)", "weight_reps", "shoulders", [], "dumbbell", 9, 15, 1),
    "FACEPULL": ("Face Pull (Cable)", "weight_reps", "shoulders", ["upper_back"], "cable", 18, 34, 2.5),
    "CURL": ("Bicep Curl (Dumbbell)", "weight_reps", "biceps", ["forearms"], "dumbbell", 13, 21, 1),
    "HAMMER": ("Hammer Curl (Dumbbell)", "weight_reps", "biceps", ["forearms"], "dumbbell", 14, 23, 1),
    "PUSHDOWN": ("Triceps Rope Pushdown", "weight_reps", "triceps", [], "cable", 24, 44, 2.5),
    "CRUNCH": ("Cable Crunch", "weight_reps", "abdominals", [], "cable", 35, 68, 2.5),
    "LEGRAISE": ("Hanging Leg Raise", "reps_only", "abdominals", [], "none", 8, 16, 1),
    "PLANK": ("Plank", "duration", "abdominals", [], "none", 40, 95, 5),
    "WALK": ("Walking", "distance_duration", "cardio", [], "none", 0, 0, 0),
    "TREADMILL": ("Treadmill", "distance_duration", "cardio", [], "machine", 0, 0, 0),
    "ROWER": ("Rowing Machine", "distance_duration", "cardio", [], "machine", 0, 0, 0),
    "BIKE": ("Cycling", "distance_duration", "cardio", [], "machine", 0, 0, 0),
}

# routine: (folder, [(exercise, target reps per working set, warm-up sets)])
ROUTINES = {
    "Full Body A": ("101", [("SQUAT", [5, 5, 5], 2), ("BENCH", [5, 5, 5], 2), ("ROW", [8, 8, 8], 1), ("LATERAL", [12, 12], 0),
                            ("PLANK", [45, 45], 0)]),
    "Full Body B": ("101", [("DEADLIFT", [5, 5], 2), ("OHP", [6, 6, 6], 1), ("PULLDOWN", [10, 10, 10], 0), ("CURL", [10, 10], 0),
                            ("CRUNCH", [12, 12], 0)]),
    "Push": ("102", [("BENCH", [6, 6, 6, 6], 2), ("INCLINE", [10, 10, 8], 0), ("OHP", [8, 8, 8], 1), ("LATERAL", [15, 15, 12], 0),
                     ("PUSHDOWN", [12, 12, 10], 0)]),
    "Pull": ("102", [("DEADLIFT", [5, 5, 3], 2), ("PULLUP", [8, 7, 6], 0), ("CROW", [10, 10, 10], 0), ("FACEPULL", [15, 15], 0),
                     ("HAMMER", [10, 10], 0)]),
    "Legs": ("102", [("SQUAT", [6, 6, 6, 6], 2), ("RDL", [8, 8, 8], 1), ("LEGPRESS", [12, 12, 12], 0), ("LEGCURL", [12, 12], 0),
                     ("CALF", [15, 15, 15], 0), ("LEGRAISE", [12, 12], 0)]),
    "Upper Strength v1": ("103", [("BENCH", [5, 5, 5, 5], 2), ("ROW", [6, 6, 6, 6], 1), ("OHP", [6, 6, 6], 1), ("PULLUP", [8, 8, 6], 0),
                                  ("CURL", [10, 10], 0), ("PUSHDOWN", [12, 12], 0)]),
    "Upper Strength v2": ("103", [("BENCH", [5, 5, 5, 5], 2), ("ROW", [6, 6, 6, 6], 1), ("OHP", [5, 5, 5], 1), ("PULLUP", [8, 8, 6], 0),
                                  ("FACEPULL", [15, 15], 0), ("CURL", [10, 10], 0), ("PUSHDOWN", [12, 12], 0)]),
    "Lower Strength v1": ("103", [("SQUAT", [5, 5, 5, 5], 2), ("RDL", [6, 6, 6], 1), ("LEGPRESS", [10, 10, 10], 0), ("LEGCURL", [10, 10], 0),
                                  ("CALF", [12, 12, 12], 0)]),
    "Lower Strength v2": ("103", [("SQUAT", [5, 5, 5, 5], 2), ("RDL", [6, 6, 6], 1), ("LEGPRESS", [10, 10, 10], 0), ("LEGCURL", [10, 10], 0),
                                  ("CALF", [12, 12, 12], 0), ("CRUNCH", [12, 12, 12], 0)]),
    "Hypertrophy Full Body v1": ("103", [("INCLINE", [12, 10, 8], 1), ("FLY", [12, 12, 12], 0), ("CROW", [12, 12, 12], 0),
                                         ("PULLDOWN", [10, 10, 10], 0), ("LATERAL", [12, 12, 12], 0), ("LEGEXT", [15, 15], 0),
                                         ("PUSHDOWN", [12, 12], 0)]),
    "Deadlift Day v1": ("103", [("DEADLIFT", [5, 3, 3, 3], 3), ("HIPTHRUST", [8, 8, 8], 1), ("ROW", [8, 8], 0), ("FACEPULL", [15, 15], 0),
                                ("HAMMER", [12, 12], 0)]),
    "Cardio Day v1": ("103", [("ROWER", [600], 0), ("TREADMILL", [1200], 0), ("BIKE", [900], 0)]),
}
FREESTYLE = {"Arms and abs": [("CURL", [10, 10, 10], 0), ("HAMMER", [12, 12], 0), ("PUSHDOWN", [12, 12, 12], 0), ("CRUNCH", [15, 15], 0)],
             "Quick upper": [("BENCH", [8, 8, 8], 1), ("PULLDOWN", [10, 10, 10], 0), ("LATERAL", [15, 15], 0)]}

# (fraction of the history where it starts, routines in rotation order, gym weekdays: Mon = 0)
PHASES = [(0.00, ["Full Body A", "Full Body B"], (0, 2, 4)),
          (0.38, ["Push", "Pull", "Legs"], (0, 1, 3, 5)),
          (0.74, ["Upper Strength v1", "Lower Strength v1", "Hypertrophy Full Body v1", "Upper Strength v1", "Deadlift Day v1",
                  "Lower Strength v1"], (0, 1, 3, 5)),
          (0.90, ["Upper Strength v2", "Lower Strength v2", "Hypertrophy Full Body v1", "Upper Strength v2", "Deadlift Day v1",
                  "Lower Strength v2"], (0, 1, 3, 5))]
BREAKS = [(0.30, 21), (0.62, 38)]           # (where, days): a holiday, then a longer break with some strength lost


def _uid(rng: random.Random) -> str:
    return str(uuid.UUID(int=rng.getrandbits(128), version=4))


def _tid(key: str) -> str:
    return hashlib.sha256(f"demo-template-{key}".encode()).hexdigest()[:8].upper()


def _rid(title: str) -> str:
    return str(uuid.UUID(hashlib.sha256(f"demo-routine-{title}".encode()).hexdigest()[:32], version=4))


def _z(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class Athlete:
    """Strength as a function of how far through the history we are, minus detraining after a break."""

    def __init__(self, rng: random.Random):
        self.rng = rng
        self.penalty, self.penalty_left = 0.0, 0

    def after_break(self, days: int) -> None:
        self.penalty, self.penalty_left = min(0.12, 0.12 * days / 42), 28

    def day_passed(self) -> None:
        if self.penalty_left:
            self.penalty_left -= 1
            self.penalty *= 0.93

    def level(self, key: str, progress: float) -> float:
        _, _, _, _, _, a, b, _ = EXERCISES[key]
        curve = (1 - math.exp(-3.2 * progress)) / (1 - math.exp(-3.2))        # quick at first, then levelling off
        return (a + (b - a) * curve) * (1 - self.penalty) * (1 + self.rng.gauss(0, 0.02))


def _round(x: float, step: float) -> float:
    return max(step, round(math.floor(x / step) * step, 2))


def _sets(rng, ath, key, reps, warm, progress, rpe_era):
    """Hevy-shaped sets for one exercise on one day."""
    title, kind, *_, step = EXERCISES[key]
    out = []
    if kind == "distance_duration":
        for secs in reps:
            if key == "WALK":
                km = rng.uniform(3.0, 8.5)
                out.append({"type": "normal", "distance_meters": round(km * 1000), "duration_seconds": round(km * rng.uniform(640, 760))})
            else:
                speed = {"TREADMILL": 2.4, "ROWER": 3.6, "BIKE": 6.0}[key] * (0.9 + 0.2 * progress)    # m/s
                dur = round(secs * rng.uniform(0.85, 1.15) / 60) * 60
                out.append({"type": "normal", "distance_meters": round(dur * speed * rng.uniform(0.95, 1.05)), "duration_seconds": dur})
        return out
    if kind == "duration":
        cap = ath.level(key, progress)
        return [{"type": "normal", "duration_seconds": int(_round(cap - i * 5 + rng.uniform(-5, 5), 5))} for i in range(len(reps))]
    if kind == "reps_only":
        cap = ath.level(key, progress)
        return [{"type": "normal", "reps": max(1, min(t + 2, round(cap - i * 1.2 + rng.uniform(-1, 1))))} for i, t in enumerate(reps)]
    rm = ath.level(key, progress)
    rir = rng.choice([1, 1, 2, 2, 3])
    work = _round(rm / (1 + (max(reps) + rir) / 30), step)
    for frac, n in [(0.45, 10), (0.65, 6), (0.8, 3)][:warm]:
        out.append({"type": "warmup", "weight_kg": _round(work * frac, step), "reps": n})
    for i, target in enumerate(reps):
        w = _round(rm / (1 + (target + rir) / 30), step)
        can = 30 * (rm / w - 1) - i * 0.8 + rng.gauss(0, 0.6)                 # reps in the tank, minus fatigue
        last = i == len(reps) - 1
        failure = last and rng.random() < 0.25
        done = max(1, math.floor(can) if failure else min(target, math.floor(can)))
        s = {"type": "failure" if failure else "normal", "weight_kg": w, "reps": done}
        if rpe_era:
            s["rpe"] = max(6.0, min(10.0, round(2 * (10 - max(0.0, can - done))) / 2))
        out.append(s)
    return out


def generate(today: date, tz: str = "Europe/London") -> dict:
    """All the payloads: {"exercise_template": [...], "routine": [...], "workout": [...], "body_measurement": [...]}."""
    rng = random.Random(SEED)
    zone = ZoneInfo(tz)
    start = today - timedelta(days=DAYS)
    templates = [{"id": _tid(k), "title": v[0], "type": v[1], "primary_muscle_group": v[2], "secondary_muscle_groups": v[3],
                  "equipment": v[4], "is_custom": False} for k, v in EXERCISES.items()]
    ath = Athlete(rng)
    workouts, bodies, typo_done = [], [], False
    rotation = {i: 0 for i in range(len(PHASES))}
    routine_last_used: dict[str, datetime] = {}
    break_until, break_len = None, 0
    for n in range(DAYS):
        day = start + timedelta(days=n)
        progress = n / DAYS
        ath.day_passed()
        for where, length in BREAKS:
            if int(where * DAYS) == n:
                break_until, break_len = day + timedelta(days=length), length
        if break_until and day < break_until:
            continue
        if break_until and day == break_until:
            ath.after_break(break_len)
            break_until = None
        if rng.random() < 0.28:
            bw = 77 + 7 * progress - (2.5 if 0.55 < progress < 0.66 else 0) + rng.gauss(0, 0.35)
            body = {"date": day.isoformat(), "weight_kg": round(bw, 1)}
            if rng.random() < 0.3:
                body["fat_percent"] = round(19 - 5 * progress + rng.gauss(0, 0.5), 1)
            bodies.append(body)
        phase_i = max(i for i, p in enumerate(PHASES) if progress >= p[0])
        _, order, gym_days = PHASES[phase_i]
        december = day.month == 12 and day.day > 20
        gym = day.weekday() in gym_days and rng.random() > (0.45 if december else 0.13)
        gym = gym or (day.weekday() == 6 and rng.random() < 0.05)
        exercises, title, routine = None, None, None
        if gym:
            if phase_i >= 1 and rng.random() < 0.05:
                title = rng.choice(list(FREESTYLE))
                exercises = FREESTYLE[title]
            elif phase_i >= 2 and rng.random() < 0.06:
                title = routine = "Cardio Day v1"
            else:
                title = routine = order[rotation[phase_i] % len(order)]
                rotation[phase_i] += 1
            exercises = exercises or ROUTINES[routine][1]
            weekend = day.weekday() >= 5
            hour = rng.uniform(9.3, 11) if weekend else rng.uniform(17.8, 19.6)
        elif rng.random() < (0.55 if 4 <= day.month <= 9 else 0.35):
            title, exercises = rng.choice(["Walk", "Evening walk", "Morning walk"]), [("WALK", [0], 0)]
            hour = rng.uniform(7, 8) if title == "Morning walk" else rng.uniform(12, 20)
        if not exercises:
            continue
        begin = datetime.combine(day, time(int(hour), int(hour % 1 * 60)), zone)
        payload_ex, n_sets, cardio_s = [], 0, 0
        for i, (key, reps, warm) in enumerate(exercises):
            sets = _sets(rng, ath, key, reps, warm, progress, rpe_era=0.38 <= progress < 0.62)
            if not typo_done and key == "CURL" and progress > 0.5:
                sets[-1]["reps"] *= 10                                         # a typo: 100 reps, flagged as suspect
                typo_done = True
            n_sets += len(sets)
            cardio_s += sum(s.get("duration_seconds") or 0 for s in sets if "distance_meters" in s)
            payload_ex.append({"index": i, "title": EXERCISES[key][0], "notes": "", "exercise_template_id": _tid(key), "superset_id": None,
                               "sets": [{"index": j, "type": s["type"], "weight_kg": s.get("weight_kg"), "reps": s.get("reps"),
                                         "distance_meters": s.get("distance_meters"), "duration_seconds": s.get("duration_seconds"),
                                         "rpe": s.get("rpe"), "custom_metric": None} for j, s in enumerate(sets)]})
        minutes = cardio_s / 60 + n_sets * rng.uniform(2.3, 3.0) + (rng.uniform(5, 10) if gym else 2)
        end = begin + timedelta(minutes=minutes)
        if routine:
            routine_last_used[routine] = end
        workouts.append({"id": _uid(rng), "title": title, "description": "", "routine_id": _rid(routine) if routine else None,
                         "start_time": _z(begin), "end_time": _z(end), "created_at": _z(end + timedelta(minutes=2)),
                         "updated_at": _z(end + timedelta(minutes=2)), "exercises": payload_ex})
    # routines as they stand now: planned loads from the current strength, last edited before they were last used
    routines = []
    for title, (folder, plan) in ROUTINES.items():
        edited = routine_last_used.get(title, datetime.combine(start, time(12), zone)) - timedelta(days=1)
        ex = [{"index": i, "title": EXERCISES[key][0], "exercise_template_id": _tid(key), "notes": "", "rest_seconds": 120,
               "superset_id": None, "sets": _planned(key, reps, warm)} for i, (key, reps, warm) in enumerate(plan)]
        routines.append({"id": _rid(title), "title": title, "folder_id": int(folder), "exercises": ex,
                         "created_at": _z(datetime.combine(start, time(12), zone)), "updated_at": _z(edited)})
    return {"exercise_template": templates, "routine": routines, "workout": workouts, "body_measurement": bodies}


def _planned(key: str, reps: list, warm: int) -> list:
    """A routine's planned sets: warm-ups, then the target reps at about two reps short of failure today."""
    _, kind, _, _, _, a, b, step = EXERCISES[key]
    blank = {"weight_kg": None, "reps": None, "distance_meters": None, "duration_seconds": None, "custom_metric": None}
    if kind == "distance_duration":
        return [{"index": j, "type": "normal", **blank, "duration_seconds": secs} for j, secs in enumerate(reps)]
    if kind == "duration":
        return [{"index": j, "type": "normal", **blank, "duration_seconds": t} for j, t in enumerate(reps)]
    if kind == "reps_only":
        return [{"index": j, "type": "normal", **blank, "reps": r} for j, r in enumerate(reps)]
    rm = a + (b - a) * 0.97
    out = []
    work = _round(rm / (1 + (max(reps) + 2) / 30), step)
    for frac, n in [(0.45, 10), (0.65, 6), (0.8, 3)][:warm]:
        out.append({"type": "warmup", **blank, "weight_kg": _round(work * frac, step), "reps": n})
    for r in reps:
        out.append({"type": "normal", **blank, "weight_kg": _round(rm / (1 + (r + 2) / 30), step), "reps": r})
    return [{"index": j, **s} for j, s in enumerate(out)]


def seed(db, today: date, tz: str = "Europe/London") -> dict:
    """Fill an empty database with the demo history and build everything on top of it."""
    with db.read() as conn:
        if conn.execute("SELECT count(*) FROM raw_objects").fetchone()[0]:
            raise SystemExit("demo-seed only fills an empty database")
    data = generate(today, tz)
    with db.tx() as conn:
        for kind, objects in data.items():
            for obj in objects:
                rawstore.apply_object(conn, kind, obj, None)
        counts = normalise.rebuild_all(conn, tz)
        set_meta(conn, "data_origin", "demo")
    analytics.refresh(db, force=True)
    return {k: len(v) for k, v in data.items()} | {"normalised": counts}
