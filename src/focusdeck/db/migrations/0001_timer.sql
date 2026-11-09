-- M1: timer and storage. All timestamps are UTC ISO 8601 text.

CREATE TABLE settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE phase_preset (
    id                  INTEGER PRIMARY KEY,
    name                TEXT    NOT NULL UNIQUE,
    focus_seconds       INTEGER NOT NULL CHECK (focus_seconds > 0),
    short_break_seconds INTEGER NOT NULL CHECK (short_break_seconds > 0),
    long_break_seconds  INTEGER NOT NULL CHECK (long_break_seconds > 0),
    cycles_before_long  INTEGER NOT NULL CHECK (cycles_before_long >= 1)
);

CREATE TABLE skill (
    id   INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE COLLATE NOCASE
);

CREATE TABLE pomodoro_block (
    id                INTEGER PRIMARY KEY,
    phase             TEXT    NOT NULL CHECK (phase IN ('focus', 'short_break', 'long_break')),
    status            TEXT    NOT NULL
                      CHECK (status IN ('running', 'paused', 'completed', 'skipped', 'reset')),
    skill_id          INTEGER REFERENCES skill (id),
    work_type         TEXT,
    planned_seconds   INTEGER NOT NULL,
    started_at        TEXT    NOT NULL,
    ends_at           TEXT,            -- set while running
    remaining_seconds REAL,            -- set while paused
    ended_at          TEXT,            -- set once finished
    elapsed_seconds   REAL,            -- running time, pauses excluded, set once finished
    recap             TEXT,
    recap_skipped     INTEGER NOT NULL DEFAULT 0
);

-- At most one block may be in progress.
CREATE UNIQUE INDEX one_active_block
    ON pomodoro_block ((1)) WHERE status IN ('running', 'paused');

CREATE INDEX pomodoro_block_started ON pomodoro_block (started_at);

INSERT INTO phase_preset
    (name, focus_seconds, short_break_seconds, long_break_seconds, cycles_before_long)
VALUES
    ('25/5/15', 1500, 300, 900, 4),
    ('50/10/30', 3000, 600, 1800, 4);
