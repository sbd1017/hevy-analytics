"""Deterministic training histories for analytics tests: hand-written workouts whose answers are known."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from hevy import normalise, rawstore
from hevy.analytics import build

TEMPLATES = {
    "BENCH": ("Bench Press (Barbell)", "weight_reps", "chest", ["triceps", "shoulders"]),
    "SQUAT": ("Squat (Barbell)", "weight_reps", "quadriceps", ["glutes", "hamstrings"]),
    "CURL": ("Bicep Curl (Dumbbell)", "weight_reps", "biceps", []),
    "ROW": ("Dumbbell Row", "weight_reps", "upper_back", ["lats", "biceps"]),
    "PUSHUP": ("Push Up", "reps_only", "chest", ["triceps"]),
    "PLANK": ("Plank", "duration", "abdominals", []),
    "WALK": ("Walking", "distance_duration", "cardio", []),
    "FARMER": ("Farmers Walk", "short_distance_weight", "forearms", ["traps"]),
    "HYPER": ("Back Extension (Weighted Hyperextension)", "bodyweight_weighted", "lower_back", ["glutes"]),
}


class History:
    """h.day(date(2026, 9, 1), ("BENCH", [("normal", 100, 5), ...]), ...) adds one workout."""

    def __init__(self, db, tz="Europe/London"):
        self.db, self.tz, self.n = db, tz, 0
        with db.tx() as c:
            for tid, (title, ttype, primary, secondary) in TEMPLATES.items():
                rawstore.apply_object(c, "exercise_template", {"id": tid, "title": title, "type": ttype,
                                                               "primary_muscle_group": primary,
                                                               "secondary_muscle_groups": secondary}, None)
                normalise.rebuild_template(c, tid)

    def routine(self, rid: str, title: str, *exercises, folder_id=1) -> str:
        """A Hevy routine: exercises are (template_id, [(type, kg, reps), ...]) like day()."""
        payload = {"id": rid, "title": title, "folder_id": folder_id, "updated_at": "2026-06-01T00:00:00Z",
                   "exercises": [{"index": i, "title": TEMPLATES[tid][0], "exercise_template_id": tid, "notes": "", "rest_seconds": 120,
                                  "sets": [{"index": j, "type": st, "weight_kg": kg, "reps": reps} for j, (st, kg, reps) in enumerate(sets)]}
                                 for i, (tid, sets) in enumerate(exercises)]}
        with self.db.tx() as c:
            rawstore.apply_object(c, "routine", payload, None)
            normalise.rebuild_routine(c, rid)
        return rid

    def day(self, d: date, *exercises, minutes=60, hour=18, wid=None, routine_id=None) -> str:
        self.n += 1
        wid = wid or f"W{self.n:03d}"
        start = datetime(d.year, d.month, d.day, hour, 0, tzinfo=timezone.utc)
        payload = {"id": wid, "title": f"Workout {self.n}", "start_time": start.isoformat(),
                   "end_time": (start + timedelta(minutes=minutes)).isoformat(),
                   "updated_at": f"2026-10-01T00:00:{self.n % 60:02d}Z", "routine_id": routine_id, "exercises": []}
        for i, (tid, sets) in enumerate(exercises):
            ex = {"index": i, "title": TEMPLATES.get(tid, (tid,))[0], "exercise_template_id": tid, "sets": []}
            for j, s in enumerate(sets):
                if isinstance(s, dict):
                    ex["sets"].append({"index": j, "type": "normal", **s})
                else:
                    st, kg, reps = s
                    ex["sets"].append({"index": j, "type": st, "weight_kg": kg, "reps": reps})
            payload["exercises"].append(ex)
        with self.db.tx() as c:
            rawstore.apply_object(c, "workout", payload, None)
            normalise.rebuild_workout(c, wid, self.tz)
        return wid

    def build(self) -> dict:
        with self.db.tx() as c:
            return build.rebuild(c)

    def q(self, sql, *args):
        with self.db.read() as c:
            return [tuple(r) for r in c.execute(sql, args)]


def sets(*triples):
    return list(triples)
