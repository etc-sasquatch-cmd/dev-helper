"""Prepare shareable artifact samples and record confirmed E2 evidence."""

import hashlib
from dataclasses import dataclass
from datetime import datetime

from focusdeck.agents.contracts import ARTIFACT_REVIEW, ArtifactVerdict, validate_output
from focusdeck.agents.runner import AgentRunner, ReviewRun, review_request
from focusdeck.db.repo import Repo, to_db


@dataclass(frozen=True)
class ArtifactRequest:
    node_id: int
    local_reference: str
    criterion: str
    sample: str

    @property
    def digest(self) -> dict[str, str]:
        return {"criterion": self.criterion, "sample": self.sample}


def prepare_artifact(node_id: int, reference: str, criterion: str, sample: str) -> ArtifactRequest:
    reference, criterion, sample = reference.strip(), criterion.strip(), sample.strip()
    if not 1 <= len(reference) <= 500:
        raise ValueError("Enter a local artifact reference of up to 500 characters.")
    if not 1 <= len(criterion) <= 500:
        raise ValueError("Enter a shareable topic criterion of up to 500 characters.")
    if not 1 <= len(sample) <= 3000:
        raise ValueError("Enter a shareable sample of up to 3,000 characters.")
    return ArtifactRequest(node_id, reference, criterion, sample)


@dataclass(frozen=True)
class ReviewedArtifact:
    request: ArtifactRequest
    result: ReviewRun


def review_artifact(runner: AgentRunner, request: ArtifactRequest) -> ReviewedArtifact:
    return ReviewedArtifact(request, runner.run(ARTIFACT_REVIEW, request.digest))


def record_artifact(
    repo: Repo, request: ArtifactRequest, review: ReviewedArtifact, now: datetime
) -> bool:
    if review.request != request:
        raise ValueError("This verdict belongs to a different artifact review.")
    result = review.result
    if (
        not isinstance(result.output, ArtifactVerdict)
        or result.output.outcome != "demonstrates_topic"
    ):
        raise ValueError("Only a passing artifact verdict can be recorded as E2.")
    validate_output(ARTIFACT_REVIEW, result.output, request.digest)
    _, payload, _ = review_request(ARTIFACT_REVIEW, request.digest)
    request_hash = hashlib.sha256(payload.encode()).hexdigest()
    with repo.transaction():
        node = repo.conn.execute(
            "SELECT id FROM roadmap_node WHERE id = ? AND skipped = 0", (request.node_id,)
        ).fetchone()
        if node is None:
            raise ValueError("Choose a roadmap topic that is still active.")
        if repo.conn.execute(
            "SELECT 1 FROM artifact_review WHERE node_id = ? AND request_hash = ?",
            (request.node_id, request_hash),
        ).fetchone():
            return False
        evidence = repo.conn.execute(
            "INSERT INTO node_evidence (node_id, kind, recorded_at) "
            "VALUES (?, 'reviewed_artifact', ?)",
            (request.node_id, to_db(now)),
        )
        repo.conn.execute(
            "INSERT INTO artifact_review "
            "(node_id, request_hash, artifact_reference, evidence_id, model, verdict) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                request.node_id,
                request_hash,
                request.local_reference,
                evidence.lastrowid,
                result.model,
                result.output.model_dump_json(),
            ),
        )
    return True
