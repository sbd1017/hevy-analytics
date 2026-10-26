"""An in-memory stand-in for the Hevy API, used as the client's transport in tests.

It follows the documented behaviour: newest-first paging, pageSize limits, `events?since=` with `updated`
(full workout) and `deleted` (id + deleted_at) events, 404 for unknown ids (400 for routines), 401 for a
wrong key. Failures and mid-paging changes can be injected.
"""
from __future__ import annotations

import copy
import json
import urllib.parse
from datetime import datetime, timedelta, timezone

KEY = "test-key-0000-0000"

TEMPLATES = [
    {"id": "T-BENCH", "title": "Bench Press (Barbell)", "type": "weight_reps", "primary_muscle_group": "chest",
     "secondary_muscle_groups": ["triceps", "shoulders"], "equipment": "barbell", "is_custom": False},
    {"id": "T-SQUAT", "title": "Squat (Barbell)", "type": "weight_reps", "primary_muscle_group": "quadriceps",
     "secondary_muscle_groups": ["glutes", "hamstrings"], "equipment": "barbell", "is_custom": False},
    {"id": "T-PULLDOWN", "title": "Lat Pulldown (Cable)", "type": "weight_reps", "primary_muscle_group": "lats",
     "secondary_muscle_groups": ["biceps"], "equipment": "machine", "is_custom": False},
    {"id": "T-PLANK", "title": "Plank", "type": "duration", "primary_muscle_group": "abdominals",
     "secondary_muscle_groups": [], "equipment": "none", "is_custom": False},
    {"id": "T-WALK", "title": "Walking", "type": "distance_duration", "primary_muscle_group": "cardio",
     "secondary_muscle_groups": [], "equipment": "none", "is_custom": False},
]


def ts(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%S+00:00")


class FakeHevy:
    def __init__(self, now: datetime | None = None):
        self.now = now or datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)
        self.workouts: dict[str, dict] = {}
        self.templates = {t["id"]: copy.deepcopy(t) for t in TEMPLATES}
        self.routines: dict[str, dict] = {}
        self.body: dict[str, dict] = {}
        self.events: list[dict] = []          # {"at", "type", "id", "workout"}
        self.calls: list[str] = []
        self.failures: list[dict] = []        # {"match", "status", "times"}
        self.on_page = None                   # callback(path, page) before a page is served
        self.network_down = False
        self._n = 0

    # ------------------------------------------------------------ test helpers

    def tick(self, minutes: int = 1) -> datetime:
        self.now += timedelta(minutes=minutes)
        return self.now

    def add_workout(self, start: datetime, exercises=None, title="Workout", minutes=60, wid=None) -> str:
        self._n += 1
        wid = wid or f"W{self._n:04d}"
        exercises = exercises if exercises is not None else [("T-BENCH", [("normal", 60, 8), ("normal", 60, 8)])]
        payload = {
            "id": wid, "title": title, "routine_id": None, "description": "",
            "start_time": ts(start), "end_time": ts(start + timedelta(minutes=minutes)),
            "created_at": ts(self.now), "updated_at": ts(self.now),
            "exercises": [
                {"index": i, "title": self.templates.get(tid, {}).get("title", tid), "notes": "",
                 "exercise_template_id": tid, "superset_id": None,
                 "sets": [{"index": j, "type": st, "weight_kg": kg, "reps": reps, "distance_meters": None,
                           "duration_seconds": None, "rpe": None, "custom_metric": None}
                          for j, (st, kg, reps) in enumerate(sets)]}
                for i, (tid, sets) in enumerate(exercises)],
        }
        self.workouts[wid] = payload
        self.events.append({"at": self.now, "type": "updated", "id": wid, "workout": copy.deepcopy(payload)})
        return wid

    def edit_workout(self, wid: str, change) -> None:
        payload = self.workouts[wid]
        change(payload)
        payload["updated_at"] = ts(self.now)
        self.events.append({"at": self.now, "type": "updated", "id": wid, "workout": copy.deepcopy(payload)})

    def delete_workout(self, wid: str, record_event: bool = True) -> None:
        del self.workouts[wid]
        if record_event:
            self.events.append({"at": self.now, "type": "deleted", "id": wid})

    def fail(self, match: str, status: int, times: int = 1) -> None:
        self.failures.append({"match": match, "status": status, "times": times})

    # ------------------------------------------------------------ transport

    def __call__(self, method: str, url: str, headers: dict):
        parts = urllib.parse.urlsplit(url)
        path, q = parts.path, {k: v[0] for k, v in urllib.parse.parse_qs(parts.query).items()}
        self.calls.append(path + ("?" + parts.query if parts.query else ""))
        if self.network_down:
            from hevy.client import NetworkError
            raise NetworkError("ConnectionRefusedError")
        if headers.get("api-key") != KEY:
            return 401, {}, b'{"error":"Invalid api key"}'
        for f in self.failures:
            if f["times"] > 0 and f["match"] in url:
                f["times"] -= 1
                return f["status"], {}, b"{}"
        if method != "GET":
            return 405, {}, b"{}"
        status, body = self.route(path, q)
        return status, {"content-type": "application/json"}, json.dumps(body).encode()

    def route(self, path, q):
        seg = path.strip("/").split("/")
        if path == "/v1/user/info":
            return 200, {"data": {"id": "U1", "username": "tester", "name": "Tester", "url": "", "weight_unit": "kg",
                                  "distance_unit": "kilometers", "is_pro": True}}
        if path == "/v1/workouts/count":
            return 200, {"workout_count": len(self.workouts)}
        if path == "/v1/workouts":
            return self.paged(path, q, "workouts", 10,
                              lambda: sorted(self.workouts.values(), key=lambda w: w["start_time"], reverse=True))
        if path == "/v1/workouts/events":
            since = datetime.fromisoformat(q.get("since", "1970-01-01T00:00:00Z").replace("Z", "+00:00"))
            def events():
                out = []
                for e in sorted((e for e in self.events if e["at"] > since), key=lambda e: e["at"], reverse=True):
                    if e["type"] == "updated":
                        out.append({"type": "updated", "workout": copy.deepcopy(e["workout"])})
                    else:
                        out.append({"type": "deleted", "id": e["id"], "deleted_at": ts(e["at"]).replace("+00:00", ".000Z")})
                return out
            return self.paged(path, q, "events", 10, events)
        if len(seg) == 3 and seg[:2] == ["v1", "workouts"]:
            w = self.workouts.get(seg[2])
            return (200, copy.deepcopy(w)) if w else (404, {"error": "not found"})
        if path == "/v1/exercise_templates":
            return self.paged(path, q, "exercise_templates", 100, lambda: list(self.templates.values()))
        if len(seg) == 3 and seg[:2] == ["v1", "exercise_templates"]:
            t = self.templates.get(seg[2])
            return (200, copy.deepcopy(t)) if t else (404, {"error": "not found"})
        if path == "/v1/routines":
            return self.paged(path, q, "routines", 10, lambda: list(self.routines.values()))
        if len(seg) == 3 and seg[:2] == ["v1", "routines"]:
            r = self.routines.get(seg[2])
            return (200, {"routine": copy.deepcopy(r)}) if r else (400, {"error": "invalid routine id"})
        if path == "/v1/body_measurements":
            return self.paged(path, q, "body_measurements", 10, lambda: sorted(self.body.values(), key=lambda b: b["date"], reverse=True))
        if len(seg) == 3 and seg[:2] == ["v1", "body_measurements"]:
            b = self.body.get(seg[2])
            return (200, copy.deepcopy(b)) if b else (404, {"error": "not found"})
        return 404, {"error": "no route"}

    def paged(self, path, q, key, max_size, items_fn):
        size, page = int(q.get("pageSize", 5)), int(q.get("page", 1))
        if not (1 <= size <= max_size) or page < 1:
            return 400, {"error": "pageSize out of range"}
        if self.on_page:
            self.on_page(path, page)
        items = items_fn()
        pages = (len(items) + size - 1) // size
        return 200, {"page": page, "page_count": pages, key: items[(page - 1) * size: page * size]}
