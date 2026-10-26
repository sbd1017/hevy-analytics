-- /hevy schema, version 1. Never edit this file after it has been deployed: add 000N_*.sql instead.
--
-- Three layers:
--   1. RAW (append-only): every distinct payload Hevy ever sent, plus deletions. The local source of truth.
--   2. NORMALISED (rebuildable from raw by `hevy.cli rebuild-normalised`): workouts, exercises, sets, templates.
--   3. DERIVED (rebuildable from normalised; added in later migrations with a formula_version).
-- Times are UTC ISO-8601 text ('...Z') plus integer epoch milliseconds where they are queried by range.

CREATE TABLE app_meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);

-- ---------------------------------------------------------------- sync bookkeeping

CREATE TABLE sync_runs (
    id               INTEGER PRIMARY KEY,
    kind             TEXT NOT NULL CHECK (kind IN ('backfill', 'incremental', 'reconcile', 'reference')),
    trigger          TEXT NOT NULL CHECK (trigger IN ('schedule', 'manual', 'cli', 'startup', 'test')),
    started_at       TEXT NOT NULL,
    finished_at      TEXT,
    status           TEXT NOT NULL DEFAULT 'running' CHECK (status IN ('running', 'ok', 'failed')),
    since_cursor     TEXT,                    -- incremental: the `since` sent to Hevy
    new_cursor       TEXT,                    -- the cursor stored when the run committed
    api_requests     INTEGER NOT NULL DEFAULT 0,
    workouts_seen    INTEGER NOT NULL DEFAULT 0,
    workouts_changed INTEGER NOT NULL DEFAULT 0,   -- new or edited (a new raw version was stored)
    workouts_deleted INTEGER NOT NULL DEFAULT 0,
    other_changed    INTEGER NOT NULL DEFAULT 0,   -- templates, routines, body measurements
    error_kind       TEXT,                    -- auth | network | server | rate_limited | bad_data | internal
    error            TEXT                     -- short, never contains the key or full payloads
);
CREATE INDEX sync_runs_started ON sync_runs (started_at);

CREATE TABLE sync_state (
    key   TEXT PRIMARY KEY,
    value TEXT
);

-- "Sync now" from the web page: a row the worker picks up within seconds.
CREATE TABLE sync_requests (
    id           INTEGER PRIMARY KEY,
    requested_at TEXT NOT NULL,
    source       TEXT NOT NULL CHECK (source IN ('web', 'cli')),
    picked_up_at TEXT,
    run_id       INTEGER REFERENCES sync_runs (id)
);

-- ---------------------------------------------------------------- 1. RAW (append-only)

CREATE TABLE raw_objects (
    id              INTEGER PRIMARY KEY,
    kind            TEXT NOT NULL CHECK (kind IN ('workout', 'exercise_template', 'routine', 'routine_folder',
                                                  'body_measurement', 'user_info')),
    hevy_id         TEXT NOT NULL,            -- workout/template/routine id; body measurements: their date
    payload         TEXT NOT NULL,            -- canonical JSON (sorted keys, no spaces) exactly as received
    sha256          TEXT NOT NULL,
    hevy_updated_at TEXT,                     -- from the payload when Hevy provides one
    fetched_at      TEXT NOT NULL,            -- when this version was first received
    sync_run_id     INTEGER REFERENCES sync_runs (id),
    UNIQUE (kind, hevy_id, sha256)
);
CREATE INDEX raw_objects_lookup ON raw_objects (kind, hevy_id, id);

CREATE TABLE raw_tombstones (
    id          INTEGER PRIMARY KEY,
    kind        TEXT NOT NULL,
    hevy_id     TEXT NOT NULL,
    deleted_at  TEXT NOT NULL,                -- Hevy's deleted_at, or when we noticed it was gone
    source      TEXT NOT NULL CHECK (source IN ('event', 'missing_from_full_list')),
    recorded_at TEXT NOT NULL,
    sync_run_id INTEGER REFERENCES sync_runs (id),
    UNIQUE (kind, hevy_id, deleted_at)
);

CREATE TRIGGER raw_objects_no_update BEFORE UPDATE ON raw_objects
BEGIN SELECT RAISE(ABORT, 'raw_objects is append-only'); END;
CREATE TRIGGER raw_objects_no_delete BEFORE DELETE ON raw_objects
BEGIN SELECT RAISE(ABORT, 'raw_objects is append-only'); END;
CREATE TRIGGER raw_tombstones_no_update BEFORE UPDATE ON raw_tombstones
BEGIN SELECT RAISE(ABORT, 'raw_tombstones is append-only'); END;
CREATE TRIGGER raw_tombstones_no_delete BEFORE DELETE ON raw_tombstones
BEGIN SELECT RAISE(ABORT, 'raw_tombstones is append-only'); END;

-- Which raw version is current for each object, and whether it is deleted. The only mutable raw-layer table;
-- it can always be recomputed from raw_objects + raw_tombstones (`hevy.cli verify` checks this).
CREATE TABLE raw_current (
    kind            TEXT NOT NULL,
    hevy_id         TEXT NOT NULL,
    raw_object_id   INTEGER NOT NULL REFERENCES raw_objects (id),
    hevy_updated_at TEXT,
    deleted         INTEGER NOT NULL DEFAULT 0 CHECK (deleted IN (0, 1)),
    tombstone_id    INTEGER REFERENCES raw_tombstones (id),
    PRIMARY KEY (kind, hevy_id)
);

-- ---------------------------------------------------------------- 2. NORMALISED (rebuildable)

CREATE TABLE exercise_templates (
    id                   TEXT PRIMARY KEY,
    title                TEXT NOT NULL,
    type                 TEXT NOT NULL,       -- weight_reps, reps_only, duration, ... (unknown values kept as-is)
    primary_muscle       TEXT,
    equipment            TEXT,
    is_custom            INTEGER NOT NULL DEFAULT 0,
    known_to_hevy        INTEGER NOT NULL DEFAULT 1,   -- 0: referenced by a workout but missing from Hevy's list
    raw_object_id        INTEGER REFERENCES raw_objects (id)
);

CREATE TABLE template_muscles (
    template_id TEXT NOT NULL REFERENCES exercise_templates (id) ON DELETE CASCADE,
    muscle      TEXT NOT NULL,
    role        TEXT NOT NULL CHECK (role IN ('primary', 'secondary')),
    PRIMARY KEY (template_id, muscle)
);

CREATE TABLE workouts (
    id            TEXT PRIMARY KEY,
    title         TEXT NOT NULL DEFAULT '',
    description   TEXT,
    routine_id    TEXT,
    start_utc     TEXT NOT NULL,
    end_utc       TEXT,
    start_ms      INTEGER NOT NULL,
    end_ms        INTEGER,
    duration_s    INTEGER,                    -- NULL when the end is missing or before the start
    local_date    TEXT NOT NULL,              -- YYYY-MM-DD in the configured time zone
    created_utc   TEXT,
    updated_utc   TEXT,
    raw_object_id INTEGER NOT NULL REFERENCES raw_objects (id)
);
CREATE INDEX workouts_start ON workouts (start_ms);
CREATE INDEX workouts_local_date ON workouts (local_date);

CREATE TABLE workout_exercises (
    id          INTEGER PRIMARY KEY,
    workout_id  TEXT NOT NULL REFERENCES workouts (id) ON DELETE CASCADE,
    position    INTEGER NOT NULL,             -- Hevy's exercise index (0 = first in the workout)
    template_id TEXT NOT NULL REFERENCES exercise_templates (id),
    title       TEXT NOT NULL,                -- the title as logged in this workout (templates can be renamed)
    notes       TEXT,
    superset_id INTEGER,
    UNIQUE (workout_id, position)
);
CREATE INDEX workout_exercises_template ON workout_exercises (template_id);

CREATE TABLE sets (
    id                  INTEGER PRIMARY KEY,
    workout_exercise_id INTEGER NOT NULL REFERENCES workout_exercises (id) ON DELETE CASCADE,
    workout_id          TEXT NOT NULL REFERENCES workouts (id) ON DELETE CASCADE,
    position            INTEGER NOT NULL,     -- Hevy's set index within the exercise
    set_type            TEXT NOT NULL,        -- normal | warmup | dropset | failure (unknown values kept)
    weight_kg           REAL,
    reps                REAL,
    distance_m          REAL,
    duration_s          REAL,
    rpe                 REAL,
    custom_metric       REAL
);
CREATE INDEX sets_exercise ON sets (workout_exercise_id);
CREATE INDEX sets_workout ON sets (workout_id);

CREATE TABLE routines (
    id            TEXT PRIMARY KEY,
    title         TEXT NOT NULL DEFAULT '',
    folder_id     TEXT,
    updated_utc   TEXT,
    raw_object_id INTEGER NOT NULL REFERENCES raw_objects (id)
);

-- Problems found while normalising (rebuilt with it). Raw data is never "fixed"; issues are reported here.
CREATE TABLE data_issues (
    id       INTEGER PRIMARY KEY,
    kind     TEXT NOT NULL,                   -- workout | exercise_template | ...
    hevy_id  TEXT NOT NULL,
    severity TEXT NOT NULL CHECK (severity IN ('info', 'warning', 'error')),
    code     TEXT NOT NULL,                   -- e.g. missing_template, end_before_start, unparseable
    detail   TEXT
);
CREATE INDEX data_issues_object ON data_issues (kind, hevy_id);

CREATE TABLE body_measurements (
    date          TEXT PRIMARY KEY,           -- YYYY-MM-DD as Hevy stores it
    weight_kg     REAL,
    fat_percent   REAL,
    lean_mass_kg  REAL,
    other_json    TEXT,                       -- any other measurements (cm), as received
    raw_object_id INTEGER NOT NULL REFERENCES raw_objects (id)
);
