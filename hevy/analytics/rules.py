"""The calculation rules, in one place (docs/METRICS.md explains each one in words).

Changing any rule here means bumping ANALYTICS_VERSION: the worker then rebuilds every derived table.
"""
from __future__ import annotations

ANALYTICS_VERSION = 1

WORKING_TYPES = {"normal", "failure", "dropset"}
E1RM_MAX_REPS = 12
EARLY_SESSIONS = 3                 # records in an exercise's first three sessions are "early"
MEANINGFUL_PCT = 1.0               # load/volume records need >= 1% improvement to be "meaningful"
SUSPECT_MIN_SETS = 5               # need this many working sets of an exercise to judge a typo
SUSPECT_REPS_FACTOR, SUSPECT_REPS_MIN = 4.0, 40
SUSPECT_LOAD_FACTOR, SUSPECT_LOAD_MIN = 3.0, 50.0
SECONDARY_CREDIT = 0.5
NOT_ON_BODY_MAP = {"cardio", "full_body", "other"}

TYPE_MEASURE = {
    "weight_reps": "load_reps",
    "bodyweight_weighted": "added_load",
    "bodyweight_assisted_reps": "assisted",
    "reps_only": "reps",
    "bodyweight_reps": "reps",
    "duration": "time",
    "weight_duration": "load_time",
    "short_distance_weight": "load_distance",
    "distance_duration": "cardio",
    "floors_duration": "cardio",
}
VOLUME_MEASURES = {"load_reps", "added_load"}           # external load x reps
LOAD_MEASURES = {"load_reps", "added_load", "load_time", "load_distance"}


def measure(template_type: str, primary_muscle: str | None, s: dict) -> str:
    """How an exercise is measured: from its Hevy type, or (unknown types) from the fields that are filled in."""
    m = TYPE_MEASURE.get(template_type)
    if m is None:
        w, r, d, t = (s.get(k) for k in ("weight_kg", "reps", "distance_m", "duration_s"))
        if w and r:
            m = "load_reps"
        elif w and d:
            m = "load_distance"
        elif d:
            m = "cardio"
        elif w and t:
            m = "load_time"
        elif t:
            m = "time"
        elif r:
            m = "reps"
        else:
            m = "none"
    if m == "time" and primary_muscle == "cardio":
        m = "cardio"                                   # e.g. elliptical logged by time only
    return m


def is_cardio_exercise(measure_name: str, primary_muscle: str | None) -> bool:
    return measure_name == "cardio" or primary_muscle == "cardio"


def epley(weight: float, reps: float) -> float:
    return weight if reps == 1 else weight * (1 + reps / 30.0)


def brzycki(weight: float, reps: float) -> float | None:
    return weight * 36.0 / (37.0 - reps) if reps < 37 else None


def suspect_reason(m: str, weight, reps, median_reps, median_load, n_sets: int) -> str | None:
    if m not in VOLUME_MEASURES or n_sets < SUSPECT_MIN_SETS:
        return None
    if reps and median_reps and reps > SUSPECT_REPS_FACTOR * median_reps and reps > SUSPECT_REPS_MIN:
        return f"{reps:g} reps is over {SUSPECT_REPS_FACTOR:g}x the usual {median_reps:g} for this exercise"
    if weight and median_load and weight > SUSPECT_LOAD_FACTOR * median_load and weight > SUSPECT_LOAD_MIN:
        return f"{weight:g} kg is over {SUSPECT_LOAD_FACTOR:g}x the usual {median_load:g} kg for this exercise"
    return None


def is_meaningful(category: str, value: float, previous: float, early: bool) -> bool:
    if early:
        return False
    if category in ("e1rm", "weight", "set_volume", "session_volume", "workout_volume"):
        return previous > 0 and (value - previous) / previous * 100 >= MEANINGFUL_PCT
    return value - previous >= 1          # reps, seconds, metres
