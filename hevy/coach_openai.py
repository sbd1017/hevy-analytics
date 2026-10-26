"""The OpenAI coach: asks a chat model to pick the next workouts from the programme, in a fixed JSON shape.

Runs only in the sync worker (the web app has no network). The key is read from a file (systemd LoadCredential),
held privately, and never appears in reprs, errors or logs. What is sent: the programme routines, the last three
weeks of sessions, recent muscle work and the last performance of each programme exercise. Nothing else.
Every reply is checked by coach.validate() before it can reach the page.
"""
from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable

from . import VERSION

# (url, headers, body or None for GET) -> (status, body)
Post = Callable[[str, dict, "bytes | None"], "tuple[int, bytes]"]
TIMEOUT_S = 150

SYSTEM = """You plan the next gym session for one person, from their own programme.

Rules you must follow:
- Choose one or two different workouts from PROGRAMME only, identified by routine_id, best first.
- For each one, give targets for that routine's exercises, by template_id, in the routine's order. You may leave out
  an exercise only if there is a clear reason (say so in `why`).
- Weights are in kg. Use the exercise's usual increments (`step` in PERFORMANCE) and never go above `heaviest` plus
  one step. Keep warm-up sets as type "warmup". For timed or distance work use duration_s / distance_m.
- `why`: one to three short sentences that refer to their actual recent training (dates, what was done).
- `change`: a few words per exercise on how the target compares with last time.
- `summary`: one short sentence about the overall situation (for example a long break).
- RULES_PROPOSAL is a simple deterministic baseline. Agree or differ, using judgement.

Their own instructions follow and take priority over style, but not over the rules above.
"""

SET = {"type": "object", "additionalProperties": False, "required": ["type", "weight_kg", "reps", "duration_s", "distance_m"],
       "properties": {"type": {"type": "string", "enum": ["warmup", "normal", "failure", "dropset"]},
                      "weight_kg": {"type": ["number", "null"]}, "reps": {"type": ["integer", "null"]},
                      "duration_s": {"type": ["integer", "null"]}, "distance_m": {"type": ["number", "null"]}}}
SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["summary", "suggestions"],
    "properties": {
        "summary": {"type": "string"},
        "suggestions": {"type": "array", "items": {
            "type": "object", "additionalProperties": False, "required": ["routine_id", "why", "exercises"],
            "properties": {
                "routine_id": {"type": "string"}, "why": {"type": "string"},
                "exercises": {"type": "array", "items": {
                    "type": "object", "additionalProperties": False, "required": ["template_id", "change", "sets"],
                    "properties": {"template_id": {"type": "string"}, "change": {"type": "string"},
                                   "sets": {"type": "array", "items": SET}}}}}}}}}


class CoachError(Exception):
    def __init__(self, message: str, transient: bool = False):
        super().__init__(message)
        self.transient = transient          # worth trying again later (network, overload), not a bad key or reply


def load_key(path: Path | None) -> str | None:
    """The key, or None when none is installed (an empty placeholder file counts as none)."""
    if path is None:
        return None
    try:
        key = Path(path).read_text().strip()
    except OSError:
        return None
    if not key:
        return None
    if not (20 <= len(key) <= 512) or any(c.isspace() for c in key):
        raise CoachError("the OpenAI key file does not contain a single key")
    return key


def urllib_post(timeout: float = TIMEOUT_S) -> Post:
    def send(url: str, headers: dict, body: bytes | None) -> tuple[int, bytes]:
        req = urllib.request.Request(url, data=body, method="POST" if body is not None else "GET", headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.status, resp.read(4 * 1024 * 1024)
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read(64 * 1024) if exc.fp else b""
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as exc:
            raise CoachError(f"OpenAI unreachable ({type(exc).__name__})", transient=True) from None
    return send


def pick_model(ids: list[str]) -> str | None:
    """The newest plain flagship chat model (gpt-N or gpt-N.M), else the newest -mini, else gpt-4o."""
    def newest(pattern):
        found = []
        for i in ids:
            m = re.fullmatch(pattern, i)
            if m:
                found.append(((int(m.group(1)), int(m.group(2) or 0)), i))
        return max(found)[1] if found else None
    return newest(r"gpt-(\d+)(?:\.(\d+))?") or newest(r"gpt-(\d+)(?:\.(\d+))?-mini") or ("gpt-4o" if "gpt-4o" in ids else None)


class OpenAICoach:
    def __init__(self, key: str, base: str = "https://api.openai.com", model: str | None = None, post: Post | None = None):
        self.__key = key
        self.base = base.rstrip("/")
        self.model = model
        self.post = post or urllib_post()

    def __repr__(self) -> str:
        return f"OpenAICoach(base={self.base!r}, model={self.model!r}, key=<hidden>)"

    def _call(self, path: str, payload: dict | None) -> dict:
        headers = {"authorization": f"Bearer {self.__key}", "content-type": "application/json",
                   "user-agent": f"hevy-analytics/{VERSION}"}
        body = json.dumps(payload).encode() if payload is not None else None
        status, raw = self.post(self.base + path, headers, body)
        if status in (401, 403):
            raise CoachError(f"OpenAI refused the key (HTTP {status})")
        if status == 429 or status >= 500:
            raise CoachError(f"OpenAI busy or out of credit (HTTP {status})", transient=status != 429 or b"quota" not in raw)
        if status == 404 and path.endswith("/completions"):
            self.model = None
            raise CoachError("the OpenAI model isn't available to this key (HTTP 404)", transient=True)
        if status != 200:
            raise CoachError(f"OpenAI answered HTTP {status}")
        try:
            return json.loads(raw)
        except (ValueError, UnicodeDecodeError):
            raise CoachError("OpenAI's reply was not JSON", transient=True) from None

    def choose_model(self) -> str:
        if not self.model:
            ids = [m.get("id", "") for m in self._call("/v1/models", None).get("data", []) if isinstance(m, dict)]
            self.model = pick_model(ids)
            if not self.model:
                raise CoachError("no suitable OpenAI chat model is available to this key")
        return self.model

    def suggest(self, ctx: dict, rules: dict, instructions: str) -> tuple[dict, str, int | None, int | None]:
        """(reply, model, tokens in, tokens out). The reply is unchecked: pass it to coach.validate()."""
        model = self.choose_model()
        brief = {
            "TODAY": ctx["today"],
            "PROGRAMME": ctx["programme"],
            "RECENT_SESSIONS_21_DAYS": ctx["recent"],
            "MUSCLE_SETS_LAST_48H": ctx["muscles_last_48h"],
            "CARDIO_MINUTES_LAST_7_DAYS": ctx["cardio_minutes_7d"],
            "DAYS_SINCE_LAST_GYM_SESSION": ctx["days_since_gym"],
            "PERFORMANCE": ctx["performance"],
            "RULES_PROPOSAL": [{"routine_id": s["routine_id"], "title": s["title"], "why": s["why"],
                                "targets": {e["template_id"]: e["sets"] for e in s["exercises"]}} for s in rules["suggestions"]],
        }
        data = self._call("/v1/chat/completions", {
            "model": model,
            "messages": [{"role": "system", "content": SYSTEM + "\n" + instructions},
                         {"role": "user", "content": json.dumps(brief, separators=(",", ":"))}],
            "response_format": {"type": "json_schema", "json_schema": {"name": "next_workouts", "strict": True, "schema": SCHEMA}},
        })
        try:
            choice = data["choices"][0]
            msg = choice["message"]
        except (KeyError, IndexError, TypeError):
            raise CoachError("OpenAI's reply had no answer", transient=True) from None
        if msg.get("refusal"):
            raise CoachError("OpenAI declined to answer")
        if choice.get("finish_reason") == "length":
            raise CoachError("OpenAI's answer was cut off", transient=True)
        try:
            reply = json.loads(msg.get("content") or "")
        except ValueError:
            raise CoachError("OpenAI's answer was not valid JSON", transient=True) from None
        usage = data.get("usage") or {}
        return reply, data.get("model") or model, usage.get("prompt_tokens"), usage.get("completion_tokens")
