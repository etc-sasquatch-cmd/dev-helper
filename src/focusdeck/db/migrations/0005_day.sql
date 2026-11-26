CREATE TABLE pomodoro_run (
    id INTEGER PRIMARY KEY,
    block_id INTEGER NOT NULL REFERENCES pomodoro_block (id),
    started_at TEXT NOT NULL,
    ended_at TEXT
);
CREATE INDEX pomodoro_run_block ON pomodoro_run (block_id, started_at);
CREATE UNIQUE INDEX one_open_pomodoro_run ON pomodoro_run (block_id) WHERE ended_at IS NULL;

CREATE TRIGGER start_pomodoro_run AFTER INSERT ON pomodoro_block
WHEN NEW.phase = 'focus' AND NEW.status = 'running'
BEGIN
    INSERT INTO pomodoro_run (block_id, started_at) VALUES (NEW.id, NEW.started_at);
END;

CREATE TRIGGER finish_pomodoro_run AFTER UPDATE OF ended_at ON pomodoro_block
WHEN NEW.ended_at IS NOT NULL
BEGIN
    UPDATE pomodoro_run SET ended_at = max(started_at, min(NEW.ended_at, OLD.ends_at))
    WHERE block_id = NEW.id AND ended_at IS NULL;
END;

CREATE TABLE classification (
    session_id TEXT NOT NULL REFERENCES claude_session (id),
    local_date TEXT NOT NULL,
    work_type TEXT NOT NULL,
    skill_id INTEGER REFERENCES skill (id),
    PRIMARY KEY (session_id, local_date)
);
CREATE INDEX capture_interval_start ON activity_interval (started_at);
