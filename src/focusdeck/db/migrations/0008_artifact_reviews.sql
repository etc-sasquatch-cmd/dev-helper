CREATE TABLE artifact_review (
    node_id INTEGER NOT NULL REFERENCES roadmap_node (id),
    request_hash TEXT NOT NULL,
    artifact_reference TEXT NOT NULL,
    evidence_id INTEGER NOT NULL UNIQUE REFERENCES node_evidence (id),
    model TEXT NOT NULL,
    verdict TEXT NOT NULL,
    PRIMARY KEY (node_id, request_hash)
);
