-- Suggestions can also come from the owner's own GPT in ChatGPT (posted to /hevy/gpt/suggestion).
-- SQLite can't change a CHECK constraint, so both coach tables are recreated (history so far: a few rules answers;
-- the worker makes a fresh one straight away).
DROP TABLE coach_requests;
DROP TABLE coach_suggestions;
CREATE TABLE coach_suggestions (
    id              INTEGER PRIMARY KEY,
    created_at      TEXT NOT NULL,
    trigger         TEXT NOT NULL,            -- sync | manual | stale | retry | chatgpt
    source          TEXT NOT NULL CHECK (source IN ('openai', 'rules', 'chatgpt')),
    model           TEXT,                     -- the OpenAI API model used, when source = 'openai'
    based_on        TEXT,                     -- id of the latest workout the suggestion knew about
    result_json     TEXT NOT NULL,            -- what the page shows (same shape for every source)
    rules_json      TEXT NOT NULL,            -- the rules engine's answer at the time (the fallback)
    openai_error    TEXT,                     -- why OpenAI wasn't used or was rejected, if it wasn't
    tokens_in       INTEGER,
    tokens_out      INTEGER
);
CREATE INDEX coach_suggestions_created ON coach_suggestions (created_at);
CREATE TABLE coach_requests (
    id            INTEGER PRIMARY KEY,
    requested_at  TEXT NOT NULL,
    picked_up_at  TEXT,
    suggestion_id INTEGER REFERENCES coach_suggestions (id)
);
