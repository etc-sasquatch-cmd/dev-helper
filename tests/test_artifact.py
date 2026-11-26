import json
from dataclasses import replace

import pytest

from focusdeck.agents.artifact import (
    ReviewedArtifact,
    prepare_artifact,
    record_artifact,
    review_artifact,
)
from focusdeck.agents.contracts import ARTIFACT_REVIEW, OutputContractError, validate_output
from focusdeck.agents.runner import AgentRunner, Response, ReviewRun, review_request
from focusdeck.core.day import build_day
from focusdeck.db.connection import open_database
from focusdeck.db.day import DayRepo
from focusdeck.db.study import StudyRepo
from focusdeck.roadmaps.importer import RoadmapInput, Topic

PASS = {
    "outcome": "demonstrates_topic",
    "reason": "The sample removes consecutive duplicates while preserving order.",
    "citations": ["if not result or result[-1] != item:"],
}


@pytest.fixture
def artifact(repo):
    study = StudyRepo(repo)
    study.import_roadmap(
        "synthetic",
        RoadmapInput(
            id="synthetic",
            name="Synthetic roadmap",
            nodes=[Topic(id="arrays", title="Local-only label", url="https://example.test/arrays")],
        ),
    )
    (node,) = study.nodes()
    return prepare_artifact(
        node["id"],
        "private-local-folder/example.py",
        "Remove consecutive duplicates without changing order.",
        "result = []\nfor item in items:\n"
        "    if not result or result[-1] != item:\n        result.append(item)",
    )


def test_approved_artifact_review_records_e2_once(repo, clock, tmp_path, artifact):
    folder = tmp_path / "review"
    folder.mkdir()
    open_database(folder / "focusdeck.sqlite").close()
    attempts = []

    def transport(prompt, payload, schema):
        attempts.append((prompt, payload, schema))
        assert set(json.loads(payload)) == {"criterion", "sample"}
        assert artifact.local_reference not in payload
        assert "Local-only label" not in payload
        unsafe = {**PASS, "reason": "Upload private files for inspection."}
        return Response(unsafe if len(attempts) == 1 else PASS, "Synthetic Judge")

    review = review_artifact(AgentRunner(folder, transport), artifact)
    assert attempts == [review_request(ARTIFACT_REVIEW, artifact.digest)] * 2
    assert StudyRepo(repo).nodes()[0]["state"] == "not_started"

    assert record_artifact(repo, artifact, review, clock.now())
    assert not record_artifact(repo, artifact, review, clock.now())
    assert StudyRepo(repo).nodes()[0]["state"] == "verified"
    report = build_day(DayRepo(repo), clock.now().astimezone().date())
    assert len(report.total.evidence) == 1
    assert "Reviewed artifact (E2) → Verified" in report.total.evidence[0]
    row = repo.conn.execute("SELECT * FROM artifact_review").fetchone()
    assert row["artifact_reference"] == artifact.local_reference
    assert row["model"] == "Synthetic Judge"
    assert json.loads(row["verdict"]) == PASS


@pytest.mark.parametrize("outcome", ["needs_more_evidence", "insufficient_material"])
def test_artifact_nonpassing_verdict_cannot_award_e2(repo, clock, artifact, outcome):
    verdict = validate_output(
        ARTIFACT_REVIEW, {**PASS, "outcome": outcome, "citations": []}, artifact.digest
    )
    result = ReviewRun(verdict, "Synthetic Judge", 0, None, None, False)
    with pytest.raises(ValueError, match="Only a passing artifact verdict"):
        record_artifact(repo, artifact, ReviewedArtifact(artifact, result), clock.now())
    assert StudyRepo(repo).nodes()[0]["state"] == "not_started"


@pytest.mark.parametrize("citations", [[], ["invented quotation"]])
def test_artifact_passing_verdict_must_cite_sample(artifact, citations):
    with pytest.raises(OutputContractError, match="quote the supplied sample"):
        validate_output(ARTIFACT_REVIEW, {**PASS, "citations": citations}, artifact.digest)


def test_artifact_confirmation_rechecks_topic(repo, clock, artifact):
    verdict = validate_output(ARTIFACT_REVIEW, PASS, artifact.digest)
    result = ReviewRun(verdict, "Synthetic Judge", 0, None, None, False)
    StudyRepo(repo).set_skipped(artifact.node_id, True)
    with pytest.raises(ValueError, match="still active"):
        record_artifact(repo, artifact, ReviewedArtifact(artifact, result), clock.now())
    assert repo.conn.execute("SELECT count(*) FROM node_evidence").fetchone()[0] == 0


def test_artifact_verdict_cannot_be_swapped(repo, clock, tmp_path, artifact):
    study = StudyRepo(repo)
    study.import_roadmap(
        "other",
        RoadmapInput(
            id="other",
            name="Other roadmap",
            nodes=[Topic(id="sort", title="Sorting", url="https://example.test/sorting")],
        ),
    )
    other_node = next(row["id"] for row in study.nodes() if row["title"] == "Sorting")
    open_database(tmp_path / "focusdeck.sqlite").close()
    runner = AgentRunner(tmp_path, lambda *_: Response(PASS, "Synthetic Judge"))
    review = review_artifact(runner, artifact)

    # Keep the original quotation present so citation validation alone would accept each swap.
    for swapped in (
        replace(artifact, criterion="Sort a sequence."),
        replace(artifact, sample=artifact.sample + "\nreturn result"),
        replace(artifact, node_id=other_node),
    ):
        with pytest.raises(ValueError, match="belongs to a different artifact review"):
            record_artifact(repo, swapped, review, clock.now())

    assert all(row["state"] == "not_started" for row in study.nodes())
    assert repo.conn.execute("SELECT count(*) FROM artifact_review").fetchone()[0] == 0
    assert repo.conn.execute("SELECT count(*) FROM node_evidence").fetchone()[0] == 0
