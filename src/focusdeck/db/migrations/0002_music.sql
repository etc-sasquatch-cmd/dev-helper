-- M2: saved music sources and the per-phase music profile.

CREATE TABLE music_source (
    id          INTEGER PRIMARY KEY,
    name        TEXT NOT NULL UNIQUE COLLATE NOCASE,
    provider    TEXT NOT NULL,   -- 'youtube' for now; validated in Python
    kind        TEXT NOT NULL CHECK (kind IN ('video', 'playlist')),
    external_id TEXT NOT NULL,   -- the provider's own ID, for example a YouTube video ID
    UNIQUE (provider, kind, external_id)
);

-- A profile maps each phase to one source. NULL means silence.
-- Deleting a source turns the phases that used it silent.
CREATE TABLE music_profile (
    id                    INTEGER PRIMARY KEY,
    name                  TEXT NOT NULL UNIQUE,
    focus_source_id       INTEGER REFERENCES music_source (id) ON DELETE SET NULL,
    short_break_source_id INTEGER REFERENCES music_source (id) ON DELETE SET NULL,
    long_break_source_id  INTEGER REFERENCES music_source (id) ON DELETE SET NULL
);

INSERT INTO music_profile (id, name) VALUES (1, 'Default');

-- The window layout changed (it now holds the player), so saved sizes no longer fit.
DELETE FROM settings WHERE key LIKE 'ui.window.size.%';
