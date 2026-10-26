"""Read-only client for the official Hevy API (https://api.hevyapp.com/docs/, spec v0.0.1).

Only GET requests exist here: /hevy never writes to Hevy. The key is held privately by the client and never
appears in reprs, exceptions or logs. Transport is injectable so tests can run against a fake Hevy.
"""
from __future__ import annotations

import json
import random
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Callable, Iterator

from . import VERSION

# (method, url, headers) -> (status, headers, body)
Transport = Callable[[str, str, dict], "tuple[int, dict, bytes]"]


class HevyError(Exception):
    kind = "internal"


class AuthError(HevyError):
    kind = "auth"            # 401/403: wrong key, or Hevy Pro lapsed


class NotFound(HevyError):
    kind = "not_found"


class RateLimited(HevyError):
    kind = "rate_limited"


class ServerError(HevyError):
    kind = "server"


class NetworkError(HevyError):
    kind = "network"


class BadData(HevyError):
    kind = "bad_data"        # unparseable or unexpected response shape


class BadRequest(BadData):
    pass                     # HTTP 400 (GET /v1/routines/{id} answers 400, not 404, for an unknown id)


def load_key(path: Path | None) -> str:
    if path is None:
        raise AuthError("no API key file configured")
    try:
        key = Path(path).read_text().strip()
    except OSError as exc:
        raise AuthError(f"API key file unreadable ({type(exc).__name__})") from None
    if not (8 <= len(key) <= 256) or any(c.isspace() for c in key):
        raise AuthError("API key file does not contain a single key")
    return key


def urllib_transport(timeout: float = 30) -> Transport:
    def send(method: str, url: str, headers: dict) -> tuple[int, dict, bytes]:
        req = urllib.request.Request(url, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.status, dict(resp.headers), resp.read(32 * 1024 * 1024)
        except urllib.error.HTTPError as exc:
            return exc.code, dict(exc.headers or {}), exc.read(64 * 1024) if exc.fp else b""
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as exc:
            raise NetworkError(type(exc).__name__) from None
    return send


class HevyClient:
    PAGE_WORKOUTS = 10        # the API's maximum for workouts and events
    PAGE_TEMPLATES = 100
    PAGE_OTHER = 10

    def __init__(self, key: str, base: str = "https://api.hevyapp.com", transport: Transport | None = None,
                 gap_s: float = 0.4, max_attempts: int = 4, sleep: Callable[[float], None] = time.sleep):
        self.__key = key
        self.base = base.rstrip("/")
        self.transport = transport or urllib_transport()
        self.gap_s = gap_s
        self.max_attempts = max_attempts
        self.sleep = sleep
        self.requests = 0

    def __repr__(self) -> str:
        return f"HevyClient(base={self.base!r}, key=<hidden>)"

    # ------------------------------------------------------------ transport with retries

    def get(self, path: str, params: dict | None = None) -> dict:
        url = self.base + path + ("?" + urllib.parse.urlencode(params) if params else "")
        headers = {"api-key": self.__key, "accept": "application/json", "user-agent": f"hevy-analytics/{VERSION}"}
        delay = 1.0
        for attempt in range(1, self.max_attempts + 1):
            self.requests += 1
            try:
                status, resp_headers, body = self.transport("GET", url, headers)
            except NetworkError:
                if attempt == self.max_attempts:
                    raise
                self.sleep(delay + random.random())
                delay *= 2
                continue
            if status == 200:
                try:
                    data = json.loads(body)
                except (ValueError, UnicodeDecodeError):
                    raise BadData(f"{path}: response is not JSON") from None
                if not isinstance(data, dict):
                    raise BadData(f"{path}: response is not an object")
                return data
            if status in (401, 403):
                raise AuthError(f"Hevy refused the key (HTTP {status})")
            if status == 404:
                raise NotFound(path)
            if status == 400:
                raise BadRequest(f"{path}: HTTP 400")
            if status == 429 or status >= 500:
                if attempt == self.max_attempts:
                    raise (RateLimited if status == 429 else ServerError)(f"{path}: HTTP {status} after {attempt} attempts")
                wait = delay
                retry_after = {k.lower(): v for k, v in resp_headers.items()}.get("retry-after")
                if retry_after and str(retry_after).isdigit():
                    wait = min(120.0, max(wait, float(retry_after)))
                self.sleep(wait + random.random())
                delay *= 2
                continue
            raise BadData(f"{path}: unexpected HTTP {status}")
        raise ServerError(f"{path}: gave up")     # not reached

    def paged(self, path: str, list_key: str, page_size: int, params: dict | None = None) -> Iterator[dict]:
        page = 1
        while True:
            data = self.get(path, {**(params or {}), "page": page, "pageSize": page_size})
            items = data.get(list_key)
            page_count = data.get("page_count")
            if items is None and page_count in (0, None):
                return                                    # an empty collection
            if items is None:
                # Observed Oct 2026: with nothing to report, /v1/workouts/events answers
                # {"page": 1, "page_count": 1, "workouts": []}, the list under another name. Accept that only
                # when every list in the reply is empty; a non-empty list under an unexpected name is still refused.
                lists = [v for v in data.values() if isinstance(v, list)]
                if lists and all(len(v) == 0 for v in lists):
                    return
            if not isinstance(items, list) or not isinstance(page_count, int):
                raise BadData(f"{path}: page {page} has no '{list_key}' list or page_count")
            for item in items:
                if not isinstance(item, dict):
                    raise BadData(f"{path}: page {page} contains a non-object")
                yield item
            if page >= page_count or not items:
                return
            page += 1
            self.sleep(self.gap_s + random.random() * self.gap_s)

    # ------------------------------------------------------------ endpoints (read-only)

    def user_info(self) -> dict:
        data = self.get("/v1/user/info")
        return data.get("data", data)

    def workout_count(self) -> int:
        n = self.get("/v1/workouts/count").get("workout_count")
        if not isinstance(n, int):
            raise BadData("/v1/workouts/count: no workout_count")
        return n

    def workouts(self) -> Iterator[dict]:
        return self.paged("/v1/workouts", "workouts", self.PAGE_WORKOUTS)

    def workout(self, workout_id: str) -> dict:
        return self.get(f"/v1/workouts/{urllib.parse.quote(workout_id, safe='')}")

    def events(self, since: str) -> Iterator[dict]:
        return self.paged("/v1/workouts/events", "events", self.PAGE_WORKOUTS, {"since": since})

    def templates(self) -> Iterator[dict]:
        return self.paged("/v1/exercise_templates", "exercise_templates", self.PAGE_TEMPLATES)

    def template(self, template_id: str) -> dict:
        return self.get(f"/v1/exercise_templates/{urllib.parse.quote(template_id, safe='')}")

    def routines(self) -> Iterator[dict]:
        return self.paged("/v1/routines", "routines", self.PAGE_OTHER)

    def routine(self, routine_id: str) -> dict:
        data = self.get(f"/v1/routines/{urllib.parse.quote(routine_id, safe='')}")
        return data.get("routine", data)

    def body_measurements(self) -> Iterator[dict]:
        return self.paged("/v1/body_measurements", "body_measurements", self.PAGE_OTHER)

    def body_measurement(self, date: str) -> dict:
        return self.get(f"/v1/body_measurements/{urllib.parse.quote(date, safe='')}")
