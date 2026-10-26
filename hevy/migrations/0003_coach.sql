-- Next-workout suggestions ("coach"). Each row is one suggestion as shown on the homepage, kept as a history.
CREATE TABLE coach_suggestions (
    id              INTEGER PRIMARY KEY,
    created_at      TEXT NOT NULL,
    trigger         TEXT NOT NULL,            -- sync | manual | stale | settings
    source          TEXT NOT NULL CHECK (source IN ('openai', 'rules')),
    model           TEXT,                     -- the OpenAI model used, when source = 'openai'
    based_on        TEXT,                     -- id of the latest workout the suggestion knew about
    result_json     TEXT NOT NULL,            -- what the page shows (same shape for both sources)
    rules_json      TEXT NOT NULL,            -- the rules engine's answer (always computed, the fallback)
    openai_error    TEXT,                     -- why OpenAI wasn't used or was rejected, if it wasn't
    tokens_in       INTEGER,
    tokens_out      INTEGER
);
CREATE INDEX coach_suggestions_created ON coach_suggestions (created_at);

-- "Refresh suggestion" from the page: picked up by the worker (the only process with network access).
CREATE TABLE coach_requests (
    id           INTEGER PRIMARY KEY,
    requested_at TEXT NOT NULL,
    picked_up_at TEXT,
    suggestion_id INTEGER REFERENCES coach_suggestions (id)
);
