-- 3. DERIVED analytics (docs/METRICS.md). Entirely rebuildable from the normalised layer by
-- hevy.analytics.build.rebuild(); the rules version that produced them is app_meta.analytics_version.
-- No foreign keys: the builder replaces everything in one transaction.

CREATE TABLE an_sets (
    set_id         INTEGER PRIMARY KEY,       -- sets.id
    workout_id     TEXT NOT NULL,
    we_id          INTEGER NOT NULL,          -- workout_exercises.id
    template_id    TEXT NOT NULL,
    local_date     TEXT NOT NULL,
    start_ms       INTEGER NOT NULL,
    ex_position    INTEGER NOT NULL,
    set_position   INTEGER NOT NULL,
    set_type       TEXT NOT NULL,
    working        INTEGER NOT NULL,          -- 1 for normal / failure / dropset
    measure        TEXT NOT NULL,             -- load_reps | added_load | assisted | reps | time | load_time | load_distance | cardio | none
    weight_kg      REAL,
    reps           REAL,
    distance_m     REAL,
    duration_s     REAL,
    rpe            REAL,
    volume_kg      REAL,                      -- load x reps (working, not suspect), else NULL
    e1rm_kg        REAL,                      -- Epley, working sets of 1..12 reps, else NULL
    e1rm_note      TEXT,                      -- high_reps | no_load | suspect (load_reps sets without an e1RM)
    suspect        TEXT                       -- NULL, or why the set looks like a typo
);
CREATE INDEX an_sets_template ON an_sets (template_id, start_ms);
CREATE INDEX an_sets_workout ON an_sets (workout_id);

CREATE TABLE an_exercise_sessions (
    we_id           INTEGER PRIMARY KEY,      -- one exercise entry in one workout
    workout_id      TEXT NOT NULL,
    template_id     TEXT NOT NULL,
    local_date      TEXT NOT NULL,
    start_ms        INTEGER NOT NULL,
    position        INTEGER NOT NULL,         -- 0 = first exercise of the workout
    exercises_in_workout INTEGER NOT NULL,
    session_index   INTEGER NOT NULL,         -- 1 = first time this exercise was ever done
    measure         TEXT NOT NULL,
    working_sets    INTEGER NOT NULL,
    warmup_sets     INTEGER NOT NULL,
    failure_sets    INTEGER NOT NULL,
    reps            REAL NOT NULL DEFAULT 0,
    volume_kg       REAL NOT NULL DEFAULT 0,
    best_e1rm_kg    REAL,
    best_e1rm_set_id INTEGER,
    top_weight_kg   REAL,
    top_weight_reps REAL,
    max_reps        REAL,
    max_duration_s  REAL,
    total_duration_s REAL NOT NULL DEFAULT 0,
    total_distance_m REAL NOT NULL DEFAULT 0,
    avg_rpe         REAL,
    rpe_sets        INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX an_ex_sessions_template ON an_exercise_sessions (template_id, start_ms);
CREATE INDEX an_ex_sessions_workout ON an_exercise_sessions (workout_id);
CREATE INDEX an_ex_sessions_date ON an_exercise_sessions (local_date);

CREATE TABLE an_workouts (
    workout_id     TEXT PRIMARY KEY,
    local_date     TEXT NOT NULL,
    start_ms       INTEGER NOT NULL,
    duration_s     INTEGER,
    kind           TEXT NOT NULL CHECK (kind IN ('gym', 'cardio')),
    exercises      INTEGER NOT NULL,
    working_sets   INTEGER NOT NULL,
    warmup_sets    INTEGER NOT NULL,
    reps           REAL NOT NULL,
    volume_kg      REAL NOT NULL,
    distance_m     REAL NOT NULL,
    cardio_duration_s REAL NOT NULL,
    avg_rpe        REAL,
    prs            INTEGER NOT NULL DEFAULT 0,
    meaningful_prs INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX an_workouts_date ON an_workouts (local_date);
CREATE INDEX an_workouts_start ON an_workouts (start_ms);

CREATE TABLE an_prs (
    id            INTEGER PRIMARY KEY,
    template_id   TEXT,                       -- NULL for whole-workout records
    workout_id    TEXT NOT NULL,
    we_id         INTEGER,
    set_id        INTEGER,
    local_date    TEXT NOT NULL,
    start_ms      INTEGER NOT NULL,
    category      TEXT NOT NULL,              -- e1rm | weight | reps_at_weight | set_volume | session_volume | max_reps | longest_hold | longest_distance | longest_session | workout_volume
    value         REAL NOT NULL,
    previous      REAL NOT NULL,
    unit          TEXT NOT NULL,              -- kg | reps | s | m
    at_weight_kg  REAL,                       -- reps_at_weight: the load
    detail        TEXT NOT NULL,              -- e.g. "100 kg × 5"
    session_index INTEGER NOT NULL,
    early         INTEGER NOT NULL,
    meaningful    INTEGER NOT NULL
);
CREATE INDEX an_prs_start ON an_prs (start_ms);
CREATE INDEX an_prs_template ON an_prs (template_id, start_ms);
CREATE INDEX an_prs_workout ON an_prs (workout_id);

CREATE TABLE an_muscle_days (
    local_date    TEXT NOT NULL,
    muscle        TEXT NOT NULL,
    sets          REAL NOT NULL,              -- credited working sets (primary 1, secondary 0.5)
    volume_kg     REAL NOT NULL,              -- credited volume
    primary_sets  INTEGER NOT NULL,
    workouts      INTEGER NOT NULL,
    PRIMARY KEY (local_date, muscle)
);
CREATE INDEX an_muscle_days_muscle ON an_muscle_days (muscle, local_date);

CREATE TABLE an_days (
    local_date      TEXT PRIMARY KEY,
    workouts        INTEGER NOT NULL,
    gym_sessions    INTEGER NOT NULL,
    cardio_sessions INTEGER NOT NULL,
    duration_s      INTEGER NOT NULL,
    gym_duration_s  INTEGER NOT NULL,
    working_sets    INTEGER NOT NULL,
    reps            REAL NOT NULL,
    volume_kg       REAL NOT NULL,
    distance_m      REAL NOT NULL,
    prs             INTEGER NOT NULL,
    meaningful_prs  INTEGER NOT NULL
);
