CREATE TABLE agent_run (
    id INTEGER PRIMARY KEY,
    cache_key TEXT NOT NULL,
    task TEXT NOT NULL,
    model TEXT NOT NULL,
    ran_at TEXT NOT NULL,
    seconds REAL NOT NULL,
    input_tokens INTEGER,
    output_tokens INTEGER,
    response TEXT
);
CREATE INDEX agent_run_cache ON agent_run (cache_key, id) WHERE response IS NOT NULL;

CREATE TABLE agent_classification (
    session_id TEXT NOT NULL REFERENCES claude_session (id),
    local_date TEXT NOT NULL,
    work_type TEXT NOT NULL,
    confidence TEXT NOT NULL,
    PRIMARY KEY (session_id, local_date)
);

CREATE TABLE quiz_attempt (
    id INTEGER PRIMARY KEY,
    node_id INTEGER NOT NULL REFERENCES roadmap_node (id),
    attempted_at TEXT NOT NULL,
    correct INTEGER NOT NULL CHECK (correct BETWEEN 0 AND 3),
    question_set TEXT NOT NULL,
    UNIQUE (node_id, question_set)
);
