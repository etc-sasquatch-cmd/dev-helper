CREATE TABLE skill_target_version (
    id INTEGER PRIMARY KEY,
    effective_at TEXT NOT NULL
);
CREATE INDEX skill_target_effective ON skill_target_version (effective_at);
CREATE TABLE skill_target (
    version_id INTEGER NOT NULL REFERENCES skill_target_version (id),
    skill_id INTEGER NOT NULL REFERENCES skill (id),
    percent INTEGER NOT NULL CHECK (percent BETWEEN 0 AND 100),
    PRIMARY KEY (version_id, skill_id)
);
CREATE TABLE skill_project (
    skill_id INTEGER NOT NULL REFERENCES skill (id),
    path TEXT NOT NULL COLLATE NOCASE,
    PRIMARY KEY (skill_id, path)
);
CREATE TABLE roadmap (
    id INTEGER PRIMARY KEY,
    source TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL
);
CREATE TABLE roadmap_node (
    id INTEGER PRIMARY KEY,
    roadmap_id INTEGER NOT NULL REFERENCES roadmap (id),
    source_id TEXT NOT NULL,
    title TEXT NOT NULL,
    url TEXT NOT NULL,
    skipped INTEGER NOT NULL DEFAULT 0 CHECK (skipped IN (0, 1)),
    UNIQUE (roadmap_id, source_id)
);
CREATE TABLE skill_roadmap_node (
    skill_id INTEGER NOT NULL REFERENCES skill (id),
    node_id INTEGER NOT NULL REFERENCES roadmap_node (id),
    PRIMARY KEY (skill_id, node_id)
);
ALTER TABLE pomodoro_block ADD COLUMN node_id INTEGER REFERENCES roadmap_node (id);
CREATE TABLE node_evidence (
    id INTEGER PRIMARY KEY,
    node_id INTEGER NOT NULL REFERENCES roadmap_node (id),
    block_id INTEGER REFERENCES pomodoro_block (id),
    kind TEXT NOT NULL CHECK (kind IN ('time', 'recap', 'passed_quiz', 'reviewed_artifact')),
    recorded_at TEXT NOT NULL,
    UNIQUE (block_id, kind)
);
CREATE INDEX evidence_node ON node_evidence (node_id);
CREATE INDEX focus_block_end ON pomodoro_block (ended_at) WHERE phase = 'focus';

CREATE TRIGGER record_node_time AFTER UPDATE OF elapsed_seconds ON pomodoro_block
WHEN NEW.phase = 'focus' AND NEW.node_id IS NOT NULL AND NEW.elapsed_seconds > 0
    AND NEW.status IN ('completed', 'skipped')
BEGIN
    INSERT OR IGNORE INTO node_evidence (node_id, block_id, kind, recorded_at)
    VALUES (NEW.node_id, NEW.id, 'time', NEW.ended_at);
END;

CREATE TRIGGER record_node_recap AFTER UPDATE OF recap ON pomodoro_block
WHEN NEW.phase = 'focus' AND NEW.node_id IS NOT NULL AND NEW.status = 'completed'
    AND length(trim(NEW.recap)) > 0
BEGIN
    INSERT OR IGNORE INTO node_evidence (node_id, block_id, kind, recorded_at)
    VALUES (NEW.node_id, NEW.id, 'recap', NEW.ended_at);
END;
