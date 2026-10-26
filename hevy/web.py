"""The /hevy web app: a JSON API and a script-built front end. No Hevy key here, and no outbound network.

Access, in production, comes from a login in front of the app, trusted only from the 127.0.0.1 peer:
* SITE_AUTH=1: a site-wide login (nginx auth_request) passes X-Site-User / X-Site-CSRF / X-Site-Expires, alongside
  this app's proxy key (X-Site-Proxy-Key, from a root-only nginx snippet), which is then required;
* HEVY_AUTH=proxy: any authenticating reverse proxy on this machine sets X-Remote-User.
The app refuses to start in production with neither. In development (local preview only) requests from this machine
get a development session instead; HEVY_DEMO=1 is the open public demo.
"""
from __future__ import annotations

import hmac
import logging
import os
import secrets
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles

from datetime import date, datetime
from zoneinfo import ZoneInfo

from . import VERSION, coach, site_auth
from .analytics import periods
from .analytics import explore as X
from .analytics import forecast as F
from .analytics import insights as I
from .analytics import wrapped as WR
from .analytics.strength_index import strength_index
from . import exports as EX
from .analytics import queries as Q
from .analytics.periods import Period
from .coach_openai import CoachError
from .config import Settings, load_settings
from .db import Database, get_meta, set_meta
from .sync import get_state
from .status import build as build_status
from .timeutil import iso, utcnow

log = logging.getLogger("hevy.web")
HERE = Path(__file__).parent
CSP = ("default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; "
       "font-src 'self'; manifest-src 'self'; form-action 'self'; base-uri 'none'; frame-ancestors 'none'")
SHELL_ROUTES = {"/", "/system", "/exercises", "/exercises/{rest:path}", "/muscles/{rest:path}", "/sessions",
                "/sessions/{rest:path}", "/compare", "/insights", "/wrapped", "/data"}       # client-side pages served by the same shell


def gpt_schema(server: str) -> dict:
    """The OpenAPI description ChatGPT imports for the GPT's two Actions."""
    nullable = lambda t: {"type": [t, "null"]}
    set_ = {"type": "object", "required": ["type"], "properties": {
        "type": {"type": "string", "enum": ["warmup", "normal", "failure", "dropset"]}, "weight_kg": nullable("number"),
        "reps": nullable("integer"), "duration_s": nullable("integer"), "distance_m": nullable("number")}}
    suggestion = {"type": "object", "required": ["suggestions"], "properties": {
        "summary": {"type": "string", "description": "One short sentence about the overall situation."},
        "suggestions": {"type": "array", "description": "One or two workouts, best first.", "items": {
            "type": "object", "required": ["routine_id", "why", "exercises"], "properties": {
                "routine_id": {"type": "string", "description": "From programme[].routine_id."},
                "why": {"type": "string", "description": "One to three short sentences."},
                "exercises": {"type": "array", "items": {"type": "object", "required": ["template_id", "sets"], "properties": {
                    "template_id": {"type": "string", "description": "From programme[].exercises[].template_id."},
                    "change": {"type": "string", "description": "A few words: how this compares with last time."},
                    "sets": {"type": "array", "items": set_}}}}}}}}}
    ok = {"description": "OK", "content": {"application/json": {"schema": {"type": "object"}}}}
    return {
        "openapi": "3.1.0",
        "info": {"title": "My training", "version": "1", "description": "Read my training data and save a next-workout suggestion to my site."},
        "servers": [{"url": server}],
        "paths": {
            "/context": {"get": {"operationId": "getTrainingContext", "summary": "My programme, recent sessions and last performances",
                                 "responses": {"200": ok}}},
            "/suggestion": {"post": {"operationId": "saveSuggestion", "summary": "Show a next-workout suggestion on my site",
                                     "requestBody": {"required": True, "content": {"application/json": {"schema": suggestion}}},
                                     "responses": {"200": ok, "422": ok}}},
        },
        "components": {"schemas": {}, "securitySchemes": {"key": {"type": "http", "scheme": "bearer"}}},
        "security": [{"key": []}],
    }


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or load_settings()
    demo = settings.demo                                 # public demo: made-up data, open to everyone, read-mostly
    central_auth = site_auth.enabled() and not demo
    # HEVY_AUTH=proxy: any authenticating reverse proxy on this machine (basic auth, Authelia, oauth2-proxy, ...) that
    # sets X-Remote-User and strips a client's own copy. docs/SELF-HOSTING.md.
    proxy_auth = os.environ.get("HEVY_AUTH") == "proxy" and not demo and not central_auth
    if settings.is_production and not demo and not (central_auth or proxy_auth):
        raise SystemExit("Refusing to start in production without a login in front: SITE_AUTH=1 or HEVY_AUTH=proxy")
    if settings.is_production and central_auth and site_auth.proxy_key() is None:
        raise SystemExit("Refusing to start in production without the site proxy key (LoadCredential site-proxy-key)")
    db = Database(settings.db_path)
    db.migrate()
    with db.read() as conn:
        origin = get_meta(conn, "data_origin")
    if demo and origin not in (None, "demo"):
        raise SystemExit(f"Refusing to run the open demo on a database holding {origin!r} data")
    if settings.is_production and not demo and origin not in (None, "hevy"):
        raise SystemExit(f"Refusing to serve a database holding {origin!r} data in production")
    dev_csrf = secrets.token_urlsafe(32)
    BASE = settings.base_path
    shell = (HERE / "static" / "index.html").read_text()
    if demo:
        shell = shell.replace('<a href="/logout">Sign out</a>', '<a href="/">All demos</a>').replace(
            '<main id="app"', '<p class="demo-bar">Demo with made-up training data. It resets every night and never '
            'connects to Hevy.</p>\n<main id="app"')
    DEMO_REFUSED = {BASE + "/api/sync": "The demo doesn't connect to Hevy: its data is made up.",
                    BASE + "/api/figure": "Saving is switched off in the demo.",
                    BASE + "/api/coach/settings": "Saving is switched off in the demo."}

    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None, redirect_slashes=False)
    app.state.settings, app.state.db = settings, db
    app.mount(BASE + "/static", StaticFiles(directory=str(HERE / "static")), name="static")
    lab = HERE.parent / "design" / "lab"
    if not settings.is_production and lab.is_dir():           # design prototypes: development only, never deployed
        app.mount(BASE + "/lab", StaticFiles(directory=str(lab), html=True), name="lab")

    def headers(response: Response) -> Response:
        h = response.headers
        h["Content-Security-Policy"] = CSP
        h["X-Content-Type-Options"] = "nosniff"
        h["X-Frame-Options"] = "DENY"
        h["Referrer-Policy"] = "same-origin"
        h["Permissions-Policy"] = "camera=(), microphone=(), geolocation=(), payment=(), usb=()"
        h["Cross-Origin-Opener-Policy"] = "same-origin"
        h["Cross-Origin-Resource-Policy"] = "same-origin"
        h["Cache-Control"] = "no-store"
        h["X-Robots-Tag"] = "noindex, nofollow, noarchive"
        return response

    def session_for(request: Request) -> dict | None:
        if demo:
            return {"u": "demo", "csrf": dev_csrf, "expires": 0}
        if central_auth:
            return site_auth.session_from_proxy(request)
        if proxy_auth:
            return site_auth.session_from_remote_user(request, dev_csrf)
        peer = request.client.host if request.client else ""
        if not settings.is_production and peer in ("127.0.0.1", "::1", "testclient"):
            return {"u": "owner", "csrf": dev_csrf, "expires": 0}
        return None

    def origin_problem(request: Request) -> bool:
        if request.headers.get("sec-fetch-site") == "cross-site":
            return True
        origin = request.headers.get("origin") or request.headers.get("referer")
        if origin == "null":
            return True
        if not origin:
            return False                                 # scripts without Origin still need the CSRF token
        parts = urlsplit(origin)
        allowed = set(settings.public_origins)
        if not settings.is_production:
            allowed.add(f"http://{request.headers.get('host', '')}")
        return f"{parts.scheme}://{parts.netloc}" not in allowed

    @app.middleware("http")
    async def boundary(request: Request, call_next):
        path = request.url.path
        if path == BASE:
            return headers(RedirectResponse(BASE + "/", status_code=308))
        if not path.startswith(BASE + "/"):
            return headers(PlainTextResponse("Not found", status_code=404))
        if request.method not in ("GET", "HEAD", "POST"):
            return headers(PlainTextResponse("Not allowed", status_code=405))
        if demo and (path.startswith(BASE + "/gpt/") or (request.method == "POST" and path in DEMO_REFUSED)):
            return headers(JSONResponse({"error": DEMO_REFUSED.get(path, "Not part of the demo.")},
                                        status_code=404 if "/gpt/" in path else 403))
        if path.startswith(BASE + "/gpt/"):              # the owner's GPT in ChatGPT: its own key, no site login
            if request.method == "POST" and len(await request.body()) > 64 * 1024:
                return headers(PlainTextResponse("Request too large", status_code=413))
            if path != BASE + "/gpt/openapi.json":
                with db.read() as conn:
                    ok = coach.gpt_key_ok(conn, request.headers.get("x-hevy-gpt-key"))
                if not ok:
                    return headers(JSONResponse({"error": "Missing or wrong key"}, status_code=401))
            try:
                return headers(await call_next(request))
            except Exception as exc:
                log.error("gpt request failed: %s", type(exc).__name__)
                return headers(JSONResponse({"error": "Something went wrong."}, status_code=500))
        session = session_for(request)
        if session is None:                              # identical for every path, existing or not
            return headers(JSONResponse({"error": "Site authentication required"}, status_code=401))
        request.state.session = session
        if request.method == "POST":
            body = await request.body()
            if len(body) > 16 * 1024:
                return headers(PlainTextResponse("Request too large", status_code=413))
            if origin_problem(request):
                return headers(PlainTextResponse("Cross-site request blocked", status_code=403))
            token = request.headers.get("x-csrf-token", "")
            if not (token and hmac.compare_digest(token, session["csrf"])):
                return headers(JSONResponse({"error": "Security token missing or expired. Reload the page."}, status_code=403))
        try:
            response = await call_next(request)
        except Exception as exc:                         # never leak details
            log.error("request failed: %s", type(exc).__name__)
            response = JSONResponse({"error": "Something went wrong."}, status_code=500)
        return headers(response)

    # ------------------------------------------------------------ pages

    def page(request: Request) -> HTMLResponse:
        html = shell.replace("{{CSRF}}", request.state.session["csrf"]).replace("{{VERSION}}", VERSION) \
                    .replace("{{BASE}}", BASE)
        return HTMLResponse(html)

    for route in SHELL_ROUTES:
        app.add_api_route(BASE + route, page, methods=["GET", "HEAD"], include_in_schema=False)

    # ------------------------------------------------------------ API

    @app.get(BASE + "/api/status")
    def api_status():
        st = build_status(db, settings)
        if demo:                                     # nothing syncs: say so instead of reporting a stopped worker
            st["demo"] = True
            st["sync"].update(state="demo", worker_alive=True)
        return JSONResponse(st)

    @app.post(BASE + "/api/sync")
    def api_sync():
        """Ask the worker to sync now. Repeated clicks while one is waiting don't queue more."""
        with db.tx() as conn:
            row = conn.execute("SELECT id FROM sync_requests WHERE run_id IS NULL ORDER BY id LIMIT 1").fetchone()
            if row:
                return JSONResponse({"request_id": row[0], "queued": False}, status_code=202)
            rid = conn.execute("INSERT INTO sync_requests (requested_at, source) VALUES (?, 'web')",
                               (iso(utcnow()),)).lastrowid
        return JSONResponse({"request_id": rid, "queued": True}, status_code=202)

    # ------------------------------------------------------------ next workout (made by the worker, see coach.py)

    def coach_state(conn) -> dict:
        chosen = coach.programme_ids(conn)
        routines = [{"id": r[0], "title": r[1].strip(), "folder_id": r[2], "selected": r[0] in chosen}
                    for r in conn.execute("SELECT id, title, folder_id FROM routines ORDER BY folder_id, title")]
        routines.sort(key=lambda r: (not r["selected"], r["title"].lower()))
        custom = get_meta(conn, "coach_instructions")
        pending = conn.execute("SELECT count(*) FROM coach_requests WHERE picked_up_at IS NULL").fetchone()[0]
        return {"suggestion": coach.latest(conn), "pending": bool(pending), "openai": get_state(conn, "coach_openai"), "demo": demo,
                "gpt": {"key_set": get_meta(conn, "gpt_key_sha256") is not None, "key_created_at": get_meta(conn, "gpt_key_created_at"),
                        "last_used_at": get_meta(conn, "gpt_last_used_at"), "instructions": coach.GPT_INSTRUCTIONS,
                        "schema_url": settings.public_origins[0] + BASE + "/gpt/openapi.json"},
                "calls_last_day": coach.calls_last_day(conn, utcnow()), "daily_limit": settings.coach_daily_calls,
                "programme_chosen": get_meta(conn, "coach_programme") is not None, "routines": routines,
                "instructions": custom or coach.DEFAULT_INSTRUCTIONS, "instructions_default": custom is None}

    def request_coach(conn) -> None:
        if not conn.execute("SELECT 1 FROM coach_requests WHERE picked_up_at IS NULL").fetchone():
            conn.execute("INSERT INTO coach_requests (requested_at) VALUES (?)", (iso(utcnow()),))

    @app.get(BASE + "/api/coach")
    def api_coach():
        with db.read() as conn:
            return JSONResponse(coach_state(conn))

    @app.post(BASE + "/api/coach/refresh")
    def api_coach_refresh():
        if demo:                                     # no worker in the demo: the free rules answer straight away
            coach.refresh(db, settings, "manual", utcnow(), today(), None)
            return JSONResponse({"queued": False}, status_code=202)
        with db.tx() as conn:
            request_coach(conn)
        return JSONResponse({"queued": True}, status_code=202)

    @app.post(BASE + "/api/coach/settings")
    async def api_coach_settings(request: Request):
        try:
            body = await request.json()
            if not isinstance(body, dict):
                raise ValueError
            ids = body.get("programme")
            text = body.get("instructions", ...)
            if ids is not None and not (isinstance(ids, list) and all(isinstance(i, str) for i in ids)):
                raise ValueError
            if text is not ... and text is not None and not isinstance(text, str):
                raise ValueError
        except ValueError:
            return JSONResponse({"error": "Expected {programme: [routine ids], instructions: text}"}, status_code=400)
        with db.tx() as conn:
            if ids is not None:
                coach.set_programme(conn, ids)
            if text is not ...:
                coach.set_instructions(conn, None if text is None or text.strip() == coach.DEFAULT_INSTRUCTIONS else text)
            request_coach(conn)
            return JSONResponse(coach_state(conn))

    # ------------------------------------------------------------ the body figure (static/app/figure.js)

    def figure_state(conn) -> dict:
        sex = get_meta(conn, "figure_sex") or "m"
        height = get_meta(conn, "figure_height_cm")
        height = int(height) if height else None
        w = conn.execute("SELECT date, weight_kg FROM body_measurements WHERE weight_kg > 0 ORDER BY date DESC LIMIT 1").fetchone()
        bmi = round(w[1] / (height / 100) ** 2, 1) if w and height else None
        # build: the figure is drawn wider or slimmer with BMI (22.5 = as drawn), within sensible limits
        width = round(min(1.18, max(0.9, (bmi / 22.5) ** 0.5)), 3) if bmi else 1
        return {"sex": sex, "height_cm": height, "weight_kg": w[1] if w else None, "weight_date": w[0] if w else None,
                "bmi": bmi, "width": width, "demo": demo}

    @app.get(BASE + "/api/figure")
    def api_figure():
        with db.read() as conn:
            return JSONResponse(figure_state(conn))

    @app.post(BASE + "/api/figure")
    async def api_figure_save(request: Request):
        try:
            body = await request.json()
            if not isinstance(body, dict):
                raise ValueError
            sex = body.get("sex", ...)
            height = body.get("height_cm", ...)
            if sex is not ... and sex not in ("m", "f"):
                raise ValueError
            if height is not ... and height is not None and (isinstance(height, bool) or not isinstance(height, (int, float)) or not 120 <= height <= 230):
                raise ValueError
        except ValueError:
            return JSONResponse({"error": "Expected {sex: 'm' or 'f', height_cm: 120 to 230 or null}"}, status_code=400)
        with db.tx() as conn:
            if sex is not ...:
                set_meta(conn, "figure_sex", sex)
            if height is not ...:
                set_meta(conn, "figure_height_cm", None if height is None else str(round(height)))
            return JSONResponse(figure_state(conn))

    # ------------------------------------------------------------ the owner's GPT in ChatGPT (Actions; see docs/CHATGPT.md)
    # nginx lets /hevy/gpt/ past the site login and hands the Authorization header over as X-Hevy-Gpt-Key; the
    # boundary above checks it against the hash kept by `python -m hevy.cli gpt-key new`.

    @app.get(BASE + "/gpt/openapi.json")
    def gpt_openapi():
        return JSONResponse(gpt_schema(settings.public_origins[0] + BASE + "/gpt"))

    @app.get(BASE + "/gpt/context")
    def gpt_context():
        with db.read() as conn:
            return JSONResponse(coach.gpt_context(conn, today()))

    @app.post(BASE + "/gpt/suggestion")
    async def gpt_suggestion(request: Request):
        try:
            body = await request.json()
        except ValueError:
            return JSONResponse({"saved": False, "error": "The body must be JSON."}, status_code=400)
        try:
            saved = coach.save_gpt_suggestion(db, body, utcnow(), today())
        except CoachError as exc:
            return JSONResponse({"saved": False, "error": f"Not saved: {exc}. Fix it and call saveSuggestion again."}, status_code=422)
        return JSONResponse({"saved": True, "shown_at": settings.public_origins[0] + BASE + "/",
                             "picks": [{"title": x["title"], "exercises": len(x["exercises"])} for x in saved["suggestions"]]})

    # ------------------------------------------------------------ analytics API (read-only, from the an_* tables)

    def today() -> date:
        return datetime.now(ZoneInfo(settings.timezone)).date()

    def period_from(request: Request, conn, default: str = "28d") -> Period:
        q = request.query_params
        key = q.get("range", default)
        try:
            start = date.fromisoformat(q["start"]) if q.get("start") else None
            end = date.fromisoformat(q["end"]) if q.get("end") else None
            return periods.resolve(key, today(), Q.first_date(conn), start, end)
        except ValueError as exc:
            raise BadRequest(str(exc)) from None

    class BadRequest(Exception):
        pass

    def answer(fn, *args):
        try:
            with db.read() as conn:
                return JSONResponse(fn(conn, *args))
        except BadRequest as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)

    @app.get(BASE + "/api/summary")
    def api_summary(request: Request):
        return answer(lambda c: Q.summary(c, period_from(request, c), today()))

    @app.get(BASE + "/api/exercises")
    def api_exercises(request: Request):
        def run(c):
            p = period_from(request, c, "all")
            rows = Q.exercises(c, p.start, p.end)
            if request.query_params.get("status") == "1":
                st = I.statuses(c)
                for r in rows:
                    x = st.get(r["template_id"], {})
                    r["recent"] = x.get("recent")
                    # progress status is about estimated 1RM: exercises measured otherwise never get one
                    r["status"] = x.get("status") if r["measure"] == "load_reps" else None
                    r["trend_pct_per_month"] = x.get("trend_pct_per_month") if r["measure"] == "load_reps" else None
            return {"period": p.as_dict(), "exercises": rows}
        return answer(run)

    @app.get(BASE + "/api/exercises/{template_id}")
    def api_exercise(template_id: str):
        with db.read() as conn:
            d = Q.exercise_detail(conn, template_id)
            if d and d["series"]:
                ss = I._sessions(conn, template_id).get(template_id, [])
                ref = I._last_workout_ms(conn)
                d["plateau"] = I.plateau(ss, ref) if d["template"]["measure"] == "load_reps" else None
                d["momentum"] = I.momentum_of(ss, ref) if d["template"]["measure"] == "load_reps" else None
                d["order_effect"] = I.order_effect(ss) if d["template"]["measure"] == "load_reps" else None
        return JSONResponse(d) if d else JSONResponse({"error": "Not found"}, status_code=404)

    @app.get(BASE + "/api/muscles/{muscle}")
    def api_muscle(muscle: str, request: Request):
        def run(c):
            if not c.execute("SELECT 1 FROM template_muscles WHERE muscle = ? LIMIT 1", (muscle,)).fetchone():
                raise BadRequest("unknown muscle")
            p = period_from(request, c, "3m")
            row = next((m for m in Q.muscles(c, p.start, p.end, p.prev_start, p.prev_end) if m["muscle"] == muscle), None)
            prev = Q.totals(c, p.prev_start, p.prev_end) if p.prev_start else None
            comparable = Q.compare(Q.totals(c, p.start, p.end), prev)
            if row and not comparable["available"]:
                row["change_pct"] = None                        # same minimum-sample rule as everywhere else
            return {"period": p.as_dict(), "summary": row, "comparison": comparable, **I.muscle_detail(c, muscle, p.start, p.end)}
        return answer(run)

    @app.get(BASE + "/api/insights/momentum")
    def api_momentum():
        return answer(I.momentum)

    @app.get(BASE + "/api/insights/order")
    def api_order():
        return answer(lambda c: {"exercises": I.order_effects(c)})

    @app.get(BASE + "/api/dna")
    def api_dna(request: Request):
        def run(c):
            p = period_from(request, c, "3m")
            return {"period": p.as_dict(), **I.dna(c, p.start, p.end)}
        return answer(run)

    @app.get(BASE + "/api/exercises/{template_id}/next")
    def api_next(template_id: str):
        def run(c):
            now = int(datetime.now(ZoneInfo(settings.timezone)).timestamp() * 1000)
            return F.next_session(c, template_id, now)
        return answer(run)

    @app.get(BASE + "/api/strength-index")
    def api_strength_index():
        return answer(strength_index)

    @app.get(BASE + "/api/forecast/backtest")
    def api_backtest():
        return answer(F.backtest)

    @app.get(BASE + "/api/wrapped")
    def api_wrapped(request: Request):
        def run(c):
            p = period_from(request, c, "year")
            seed = int(request.query_params.get("seed", "0")) if request.query_params.get("seed", "0").isdigit() else 0
            return {"period": p.as_dict(), **WR.wrapped(c, p.start, p.end, settings.timezone, seed=seed,
                                                        prev_start=p.prev_start, prev_end=p.prev_end, today=today())}
        return answer(run)

    def _num(q, key):
        v = q.get(key)
        if v in (None, ""):
            return None
        try:
            return float(v)
        except ValueError:
            raise BadRequest(f"{key} must be a number") from None

    @app.get(BASE + "/api/explore")
    def api_explore(request: Request):
        def run(c):
            p = period_from(request, c, "all")
            q = request.query_params
            f = {"start": p.start.isoformat(), "end": p.end.isoformat(), "exercises": [x for x in q.get("exercises", "").split(",") if x],
                 "muscle": q.get("muscle") or None, "primary_only": q.get("primary_only") == "1", "kind": q.get("kind") or None,
                 "set_type": q.get("set_type") or "working", "include_suspect": q.get("include_suspect") == "1",
                 "sort": q.get("sort") or "date", "limit": q.get("limit") or 100, "offset": q.get("offset") or 0}
            if not str(f["limit"]).isdigit() or not str(f["offset"]).isdigit():
                raise BadRequest("limit and offset must be whole numbers")
            for k in X.NUMERIC:
                f[f"{k}_min"], f[f"{k}_max"] = _num(q, f"{k}_min"), _num(q, f"{k}_max")
            return {"period": p.as_dict(), **X.explore(c, f)}
        return answer(run)

    @app.get(BASE + "/api/export")
    def api_export(request: Request):
        q = request.query_params
        fmt = q.get("format", "csv")
        datasets = [d for d in q.get("datasets", "sets").split(",") if d]
        if fmt not in ("csv", "json", "xlsx") or not datasets or any(d not in EX.DATASETS for d in datasets):
            return JSONResponse({"error": "format must be csv, json or xlsx; datasets from " + ", ".join(EX.DATASETS)}, status_code=400)
        try:
            with db.read() as conn:
                p = period_from(request, conn, "all")
                f = {"start": p.start.isoformat(), "end": p.end.isoformat(), "exercise": q.get("exercise") or None,
                     "muscle": q.get("muscle") or None, "workout": q.get("workout") or None}
                content, media, ext = EX.build(conn, fmt, datasets, f)
        except BadRequest as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        name = EX.filename(datasets, f, ext)
        return Response(content, media_type=media, headers={"Content-Disposition": f'attachment; filename="{name}"'})

    @app.get(BASE + "/api/compare")
    def api_compare(request: Request):
        q = request.query_params
        with db.read() as conn:
            d = I.compare_workouts(conn, q.get("a", ""), q.get("b", ""))
        return JSONResponse(d) if d else JSONResponse({"error": "Pick two existing workouts"}, status_code=404)

    @app.get(BASE + "/api/muscles")
    def api_muscles(request: Request):
        def run(c):
            p = period_from(request, c)
            return {"period": p.as_dict(), "muscles": Q.muscles(c, p.start, p.end, p.prev_start, p.prev_end),
                    "trained_less_than_usual": Q.less_than_usual(c, p)}
        return answer(run)

    @app.get(BASE + "/api/records")
    def api_records(request: Request):
        def run(c):
            p = period_from(request, c, "all")
            q = request.query_params
            limit = int(q.get("limit", "100")) if q.get("limit", "100").isdigit() else 100
            return {"period": p.as_dict(), "records": Q.prs(c, p.start, p.end, template_id=q.get("exercise") or None,
                                                            meaningful_only=q.get("meaningful") == "1", limit=limit)}
        return answer(run)

    @app.get(BASE + "/api/calendar")
    def api_calendar(request: Request):
        def run(c):
            p = period_from(request, c, "1y")
            return {"period": p.as_dict(), "days": Q.calendar_days(c, p.start, p.end), "streaks": Q.streaks(c, today())}
        return answer(run)

    @app.get(BASE + "/api/series")
    def api_series(request: Request):
        def run(c):
            p = period_from(request, c, "all")
            bucket = request.query_params.get("bucket", "week")
            try:
                rows = Q.series(c, p.start, p.end, bucket)
            except ValueError as exc:
                raise BadRequest(str(exc)) from None
            return {"period": p.as_dict(), "bucket": bucket, "series": rows}
        return answer(run)

    @app.get(BASE + "/api/workouts")
    def api_workouts(request: Request):
        def run(c):
            p = period_from(request, c, "all")
            return {"period": p.as_dict(), "workouts": Q.workouts(c, p.start, p.end)}
        return answer(run)

    @app.get(BASE + "/api/workouts/{workout_id}")
    def api_workout(workout_id: str):
        with db.read() as conn:
            d = Q.workout_detail(conn, workout_id)
            if d:
                d["similar"] = I.similar_workouts(conn, workout_id)
        return JSONResponse(d) if d else JSONResponse({"error": "Not found"}, status_code=404)

    @app.api_route(BASE + "/{rest:path}", methods=["GET", "HEAD", "POST"], include_in_schema=False)
    def not_found(rest: str):
        return JSONResponse({"error": "Not found"}, status_code=404)

    return app
