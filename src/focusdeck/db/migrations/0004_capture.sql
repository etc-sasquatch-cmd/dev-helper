CREATE TABLE claude_session (
    id TEXT PRIMARY KEY,
    project_keys TEXT NOT NULL,
    detail_allowed INTEGER NOT NULL CHECK (detail_allowed IN (0, 1)),
    first_event_at TEXT NOT NULL,
    transcript_relative TEXT
);

CREATE TABLE claude_event (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES claude_session (id),
    occurred_at TEXT NOT NULL,
    event TEXT NOT NULL,
    tool TEXT,
    result TEXT
);
CREATE INDEX claude_event_session_time ON claude_event (session_id, occurred_at);

CREATE TABLE claude_transcript_record (
    session_id TEXT NOT NULL REFERENCES claude_session (id),
    record_id TEXT NOT NULL,
    occurred_at TEXT NOT NULL,
    details TEXT NOT NULL,
    PRIMARY KEY (session_id, record_id)
);

CREATE TABLE capture_checkpoint (
    source TEXT PRIMARY KEY,
    file_identity TEXT NOT NULL,
    byte_offset INTEGER NOT NULL
);

CREATE TABLE activity_interval (
    session_id TEXT NOT NULL REFERENCES claude_session (id),
    started_at TEXT NOT NULL,
    ended_at TEXT NOT NULL,
    local_date TEXT NOT NULL,
    quarter TEXT NOT NULL,
    PRIMARY KEY (session_id, started_at)
);
