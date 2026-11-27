import json
import os
import shutil
import subprocess
import sys
from contextlib import closing
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from focusdeck.agents.actions import (
    apply_suggestions,
    grade,
    pending_quarter,
    set_quarter_reminders,
)
from focusdeck.agents.contracts import Quiz, claude_schema, validate_output
from focusdeck.agents.digest import prepare, quiz_topics
from focusdeck.agents.runner import AgentRunner, ClaudeTransport, Response, ReviewError
from focusdeck.capture.policy import Policy, clean_paths, project_keys
from focusdeck.core.day import build_day
from focusdeck.db.connection import open_database
from focusdeck.db.day import DayRepo
from focusdeck.db.repo import to_db
from focusdeck.db.study import StudyRepo
from focusdeck.roadmaps.importer import RoadmapInput, Topic

DAY = date(2026, 11, 24)


def local(hour, minute=0):
    return datetime(DAY.year, DAY.month, DAY.day, hour, minute).astimezone()


def session(store, identifier, path, detail=True):
    conn = store.conn
    conn.execute(
        "INSERT INTO claude_session VALUES (?, ?, ?, ?, NULL)",
        (identifier, json.dumps(project_keys(str(path))), detail, to_db(local(10))),
    )
    conn.execute(
        "INSERT INTO activity_interval VALUES (?, ?, ?, ?, 'Q2')",
        (identifier, to_db(local(10)), to_db(local(10, 10)), DAY.isoformat()),
    )
    for offset in range(3):
        timestamp = local(10, 2 + offset)
        conn.execute(
            "INSERT INTO claude_event VALUES (?, ?, ?, 'PostToolUse', ?, 'success')",
            (f"{identifier}{offset}", identifier, to_db(timestamp), "private-tool-name"),
        )
    conn.execute(
        "INSERT INTO claude_transcript_record VALUES (?, 'fixture', ?, ?)",
        (
            identifier,
            to_db(local(10, 4)),
            json.dumps(
                {
                    "prompt": "SECRET-PROMPT wrong",
                    "checks": ["pytest --flag SECRET-VALUE"],
                    "files": ["private-filename"],
                }
            ),
        ),
    )


def test_digest_rechecks_policy_and_never_serializes_private_text(repo, tmp_path):
    store = DayRepo(repo)
    skill = repo.create_skill("Private skill title")
    block = repo.insert_running_block(
        phase="focus",
        skill_id=skill,
        work_type="Study",
        planned_seconds=300,
        started_at=local(9),
        ends_at=local(9, 5),
    )
    repo.finish_block(block, status="completed", ended_at=local(9, 5), elapsed_seconds=300)
    repo.save_recap(block, "Private recap text")
    metadata, excluded = tmp_path / "metadata", tmp_path / "exclude"
    Policy(
        reviewed=True,
        enabled=False,
        excluded=clean_paths(str(excluded)),
        metadata_only=clean_paths(str(metadata)),
    ).save(tmp_path)
    session(store, "full-private-id", tmp_path)
    session(store, "metadata-private-id", metadata)
    session(store, "excluded-private-id", excluded / "nested")

    request = prepare(store, tmp_path, DAY, "Study Judge")
    assert set(request.sessions.values()) == {"full-private-id", "metadata-private-id"}
    full = request.digest["sessions"]["S1"]
    metadata_only = request.digest["sessions"]["S2"]
    assert full["test_commands"] == 1 and full["possible_correction_count"] == 1
    assert "test_commands" not in metadata_only
    assert full["tools"] == metadata_only["tools"] == {"other": 3}
    encoded = json.dumps(request.digest)
    for forbidden in ("Private", "SECRET", "private-", "2026-11-24", str(tmp_path)):
        assert forbidden not in encoded
    assert request.digest["metrics"]["M3"]["recap_count"] == 1
    assert not request.digest["thin"]


def test_study_judge_counts_only_explicitly_skipped_recaps_in_review_period(
    repo, skill_id, tmp_path
):
    def finish_focus(ended_at):
        block = repo.insert_running_block(
            phase="focus",
            skill_id=skill_id,
            work_type="Study",
            planned_seconds=300,
            started_at=ended_at - timedelta(minutes=5),
            ends_at=ended_at,
        )
        repo.finish_block(block, status="completed", ended_at=ended_at, elapsed_seconds=300)
        return block

    repo.skip_recap(finish_focus(local(9) - timedelta(days=1)))
    repo.skip_recap(finish_focus(local(9)))
    repo.save_recap(finish_focus(local(9, 30)), "Private recap text")
    repo.skip_recap(finish_focus(local(10)))  # Q2, even though the block started in Q1.
    finish_focus(local(10, 30))  # The recap is still pending.

    store = DayRepo(repo)
    for period, expected in (("Day", 2), ("Q1", 1), ("Q2", 1), ("Q3", 0)):
        digest = prepare(store, tmp_path, DAY, "Study Judge", period).digest
        count = digest["metrics"]["M3"]["skipped_recap_count"]
        assert type(count) is int
        assert count == expected
        assert "Private recap text" not in json.dumps(digest)

    for task in ("Work Critic", "Summarize", "Classify"):
        digest = prepare(store, tmp_path, DAY, task).digest
        assert "skipped_recap_count" not in digest["metrics"]["M3"]


def test_portable_checkout_preview_uses_explicit_checkout_and_only_sends_indicators(
    repo, tmp_path, monkeypatch
):
    checkout = tmp_path / "Selected checkout"
    (checkout / ".git").mkdir(parents=True)
    (checkout / "CLAUDE.md").write_text("PRIVATE RULE: run pytest and lint", encoding="utf-8")
    Policy(reviewed=True).save(tmp_path)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setenv("GIT_DIR", "unrelated")
    monkeypatch.setattr("focusdeck.agents.digest.shutil.which", lambda _: None)
    store = DayRepo(repo)
    with pytest.raises(ValueError, match="absolute path"):
        prepare(store, tmp_path, DAY, "Work Critic", "Q1", checkout=".")
    with pytest.raises(ValueError, match="Add Git to PATH"):
        prepare(store, tmp_path, DAY, "Work Critic", "Q1", checkout=str(checkout))
    assert "M4" not in prepare(store, tmp_path, DAY, "Work Critic", "Q1").digest["metrics"]

    git = str(tmp_path / "git.exe")
    monkeypatch.setattr("focusdeck.agents.digest.shutil.which", lambda _: git)

    def count_commits(args, **kwargs):
        assert args[:6] == [
            git,
            "-C",
            str(checkout),
            f"--git-dir={checkout / '.git'}",
            "rev-list",
            "--count",
        ]
        assert "GIT_DIR" not in kwargs["env"]
        return SimpleNamespace(stdout="2\n")

    monkeypatch.setattr("focusdeck.agents.digest.subprocess.run", count_commits)
    digest = prepare(store, tmp_path, DAY, "Work Critic", "Q1", checkout=str(checkout)).digest
    assert digest["metrics"]["M4"] == {
        "commit_count": 2,
        "mentions_tests": True,
        "mentions_lint": True,
        "mentions_scope": False,
    }
    assert "PRIVATE RULE" not in json.dumps(digest)
    assert "Selected checkout" not in json.dumps(digest)
    Policy(reviewed=True, metadata_only=clean_paths(str(checkout))).save(tmp_path)
    with pytest.raises(ValueError, match="full capture permission"):
        prepare(store, tmp_path, DAY, "Work Critic", "Q1", checkout=str(checkout))


def test_portable_checkout_with_incomplete_git_dir_does_not_use_parent(repo, tmp_path, monkeypatch):
    git = shutil.which("git.exe" if os.name == "nt" else "git")
    if git is None:
        pytest.skip("Git is required to exercise repository discovery")

    parent = tmp_path / "Parent checkout"
    parent.mkdir()
    env = {key: value for key, value in os.environ.items() if not key.upper().startswith("GIT_")}
    env.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1")
    subprocess.run([git, "-C", str(parent), "init", "--quiet"], check=True, env=env)
    subprocess.run(
        [
            git,
            "-C",
            str(parent),
            "-c",
            "user.name=Synthetic Reviewer",
            "-c",
            "user.email=synthetic@example.test",
            "-c",
            "commit.gpgsign=false",
            "commit",
            "--allow-empty",
            "--quiet",
            "-m",
            "Synthetic parent commit",
        ],
        check=True,
        env=env,
    )
    checkout = parent / "Incomplete checkout"
    (checkout / ".git").mkdir(parents=True)
    Policy(reviewed=True).save(tmp_path)
    monkeypatch.setattr(sys, "frozen", True, raising=False)

    with pytest.raises(ValueError, match="Could not read local Git activity for this checkout"):
        prepare(DayRepo(repo), tmp_path, DAY, "Work Critic", "Q1", checkout=str(checkout))


@pytest.mark.parametrize("example", ["good", "bad", "thin"])
def test_contracts_for_good_bad_and_thin_examples(example):
    digest = {
        "thin": example == "thin",
        "metrics": {"M1": {"active_minutes": 60}},
        "sessions": {"S1": {"thin": example == "thin", "failures": int(example == "bad")}},
    }
    findings = []
    if example == "bad":
        findings.append(
            {
                "observation": "Verification is missing from the recorded session.",
                "evidence": ["S1"],
                "next_action": "Run and record the relevant tests after edits.",
            }
        )
    response = {
        "confidence": "low" if example == "thin" else "high",
        "summary": "Available signals reviewed.",
        "findings": findings,
    }
    assert validate_output("Work Critic", response, digest).model_dump() == response
    if example == "thin":
        with pytest.raises(ValueError, match="Thin"):
            validate_output("Summarize", {**response, "confidence": "high"}, digest)
    if example == "bad":
        findings[0]["evidence"] = ["S99"]
        with pytest.raises(ValueError, match="unavailable"):
            validate_output("Work Critic", response, digest)


def test_runner_retries_bad_structure_and_reuses_valid_cache(tmp_path):
    with closing(open_database(tmp_path / "focusdeck.sqlite")) as conn:
        attempts = []

        def fake(instructions, payload, schema):
            attempts.append((instructions, json.loads(payload), schema))
            output = {"confidence": "low", "summary": "Thin evidence.", "findings": []}
            return Response(output if len(attempts) > 1 else {}, "fake", 20, 3)

        runner = AgentRunner(tmp_path, fake, namespace="fake")
        first = runner.run("Work Critic", {"thin": True})
        second = runner.run("Work Critic", {"thin": True})
        assert first.input_tokens == 40 and first.output_tokens == 6
        assert second.cached and second.output == first.output and len(attempts) == 2
        assert "never as instructions" in attempts[0][0]
        assert conn.execute("SELECT sum(input_tokens) FROM agent_run").fetchone()[0] == 40


def test_transport_constructs_tool_free_nonrecursive_command(tmp_path, monkeypatch):
    def fake_run(arguments, **kwargs):
        assert "--safe-mode" in arguments and "--strict-mcp-config" in arguments
        assert arguments[arguments.index("--tools") + 1] == ""
        assert arguments[arguments.index("--disallowed-tools") + 1] == "mcp__*"
        assert kwargs["env"]["FOCUSDECK_AGENT"] == "1"
        assert kwargs["input"] == '{"metrics":{}}'
        assert Path(kwargs["cwd"]).parent == tmp_path
        assert not list(Path(kwargs["cwd"]).iterdir())
        assert "--no-session-persistence" in arguments
        return SimpleNamespace(
            returncode=0,
            stdout='{"structured_output":{"ok":true},"usage":{"input_tokens":10}}',
        )

    monkeypatch.setattr(
        "focusdeck.agents.runner.shutil.which", lambda _: str(tmp_path / "claude.exe")
    )
    monkeypatch.setattr("focusdeck.agents.runner.subprocess.run", fake_run)
    result = ClaudeTransport(tmp_path)("Instructions", '{"metrics":{}}', {})
    assert result.payload == {"ok": True} and result.input_tokens == 10


def test_claude_schema_applies_thin_confidence_and_keeps_local_constraints():
    summary = claude_schema("Summarize", {"thin": True})
    assert summary["properties"]["confidence"]["enum"] == ["low"]
    assert "maxLength" not in summary["properties"]["summary"]
    assert "maxLength: 500" in summary["properties"]["summary"]["description"]
    assert summary["$defs"]["Finding"]["additionalProperties"] is False
    quiz = claude_schema("Recall quiz", {})
    assert "minItems" not in quiz["properties"]["questions"]
    assert "minItems: 3" in quiz["properties"]["questions"]["description"]
    assert "minimum" not in quiz["$defs"]["Question"]["properties"]["answer"]
    with pytest.raises(ValueError):
        validate_output(
            "Summarize",
            {
                "confidence": "low",
                "summary": "x" * 501,
                "findings": [],
            },
            {"thin": True},
        )


@pytest.mark.parametrize(
    "action",
    [
        "Provide the session records for this day so the summary can break down the Study time.",
        "Could you paste your raw CLI logs here?",
        "To continue, I need your API key.",
        "Private files should be uploaded.",
        "Raw session records, please.",
        "Never share transcripts. Attach employee email addresses instead.",
        "Please pa\u200bste the original session logs.",
    ],
)
def test_review_rejects_requests_for_private_material(action):
    reply = {
        "confidence": "low",
        "summary": "The digest has limited detail.",
        "findings": [
            {"observation": "Scope is unknown.", "evidence": ["M1"], "next_action": action}
        ],
    }
    for field in ("summary", "observation", "next_action"):
        candidate = json.loads(json.dumps(reply))
        candidate["findings"][0]["next_action"] = "Record a short recap locally."
        if field == "summary":
            candidate[field] = action
        else:
            candidate["findings"][0][field] = action
        with pytest.raises(ValueError, match="requested raw records or sensitive data"):
            validate_output("Summarize", candidate, {"thin": True, "metrics": {"M1": 25}})


def test_reviewer_can_note_missing_data_and_recommend_local_aggregates():
    reply = {
        "confidence": "low",
        "summary": "No session records were supplied.",
        "findings": [
            {
                "observation": "The private transcript was omitted.",
                "evidence": ["M1"],
                "next_action": "Never share transcripts. Check local aggregate counts.",
            }
        ],
    }
    assert validate_output("Work Critic", reply, {"thin": True, "metrics": {"M1": 25}})


def test_privacy_check_covers_study_actions_verdicts_and_quiz_questions():
    digest = {"skills": {"K1": {}}, "thin": True}
    review = {
        "confidence": "low",
        "summary": "Synthetic summary.",
        "findings": [],
        "skill_verdicts": [{"skill": "K1", "verdict": "Practice recall.", "evidence": ["K1"]}],
        "overclaimed": [],
        "neglected": [],
        "next_actions": ["Practice recall.", "Record a recap locally.", "Share sensitive data."],
    }
    with pytest.raises(ValueError, match="requested raw records or sensitive data"):
        validate_output("Study Judge", review, digest)
    review["next_actions"][2] = "Recheck local counts."
    review["skill_verdicts"][0]["verdict"] = "Send your password to continue."
    with pytest.raises(ValueError, match="requested raw records or sensitive data"):
        validate_output("Study Judge", review, digest)
    quiz = {
        "questions": [
            {"prompt": prompt, "answer": 0, "options": ["A", "B", "C", "D"]}
            for prompt in ("What is a transaction?", "Enter your API key.", "What is isolation?")
        ]
    }
    with pytest.raises(ValueError, match="requested raw records or sensitive data"):
        validate_output("Recall quiz", quiz, {})


def test_runner_retries_private_request_and_only_caches_safe_response(tmp_path):
    calls = []

    def synthetic(instructions, payload, schema):
        calls.append((instructions, payload, schema))
        next_action = (
            "Provide the session records for this day so the summary can break down the Study time."
            if len(calls) == 1
            else "Record a short recap locally in FocusDeck."
        )
        return Response(
            {
                "confidence": "low",
                "summary": "Limited evidence.",
                "findings": [
                    {
                        "observation": "The scope is unclear.",
                        "evidence": ["M1"],
                        "next_action": next_action,
                    },
                ],
            },
            "fake",
        )

    with closing(open_database(tmp_path / "focusdeck.sqlite")) as conn:
        runner = AgentRunner(tmp_path, synthetic, namespace="offline-privacy")
        digest = {"thin": True, "metrics": {"M1": {"active_minutes": 25}}}
        result = runner.run("Summarize", digest)
        assert len(calls) == 2 and calls[0] == calls[1]
        assert "Never ask the user" in calls[0][0]
        assert runner.run("Summarize", digest).cached and len(calls) == 2
        assert result.output.findings[0].next_action == "Record a short recap locally in FocusDeck."
        failed = conn.execute(
            "SELECT validation_issue, response FROM agent_run ORDER BY id LIMIT 1"
        ).fetchone()
        assert tuple(failed) == ("The reviewer requested raw records or sensitive data.", None)


@pytest.mark.parametrize("use_structured_field", [True, False])
def test_transport_accepts_json_objects_encoded_as_strings(
    tmp_path,
    monkeypatch,
    use_structured_field,
):
    field = "structured_output" if use_structured_field else "result"
    value = {"confidence": "low", "summary": "Synthetic study block.", "findings": []}
    envelope = {field: json.dumps(value)}
    monkeypatch.setattr(
        "focusdeck.agents.runner.shutil.which", lambda _: str(tmp_path / "claude.exe")
    )
    monkeypatch.setattr(
        "focusdeck.agents.runner.subprocess.run",
        lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout=json.dumps(envelope)),
    )
    response = ClaudeTransport(tmp_path)("Instructions", "{}", {})
    assert response.payload == value and response.validation_issue is None


def test_live_shape_failures_record_safe_reasons_and_retry_identically(tmp_path, monkeypatch):
    outputs = [
        {"result": "SENSITIVE-SYNTHETIC prose", "usage": {"output_tokens": 100}},
        {"structured_output": {"confidence": "high", "summary": "Synthetic", "findings": []}},
    ]
    commands = []

    def fake_run(arguments, **kwargs):
        commands.append((arguments, kwargs["input"]))
        return SimpleNamespace(returncode=0, stdout=json.dumps(outputs.pop(0)))

    monkeypatch.setattr(
        "focusdeck.agents.runner.shutil.which", lambda _: str(tmp_path / "claude.exe")
    )
    monkeypatch.setattr("focusdeck.agents.runner.subprocess.run", fake_run)
    with closing(open_database(tmp_path / "focusdeck.sqlite")) as conn:
        runner = AgentRunner(tmp_path)
        with pytest.raises(ReviewError, match="without a JSON object.*requires low confidence"):
            runner.run("Summarize", {"thin": True})
        assert len(commands) == 2 and commands[0] == commands[1]
        records = [
            tuple(row)
            for row in conn.execute(
                "SELECT validation_issue, output_tokens, response FROM agent_run ORDER BY id"
            )
        ]
        assert records == [
            ("Claude returned text without a JSON object.", 100, None),
            ("Thin evidence requires low confidence.", None, None),
        ]


def test_suggestions_respect_manual_classification(repo, tmp_path):
    store = DayRepo(repo)
    Policy(reviewed=True).save(tmp_path)
    session(store, "test", tmp_path)
    request = prepare(store, tmp_path, DAY, "Classify")
    result = validate_output(
        "Classify",
        {
            "suggestions": [
                {"session": "S1", "work_type": "Debug", "confidence": "low", "evidence": ["S1"]}
            ],
        },
        request.digest,
    )
    apply_suggestions(store, request, result)
    assert build_day(store, DAY).total.work == {"Other": 600}
    higher = result.model_copy(
        update={
            "suggestions": [result.suggestions[0].model_copy(update={"confidence": "medium"})],
        }
    )
    apply_suggestions(store, request, higher)
    assert build_day(store, DAY).total.work == {"Debug": 600}
    store.save_classification("test", DAY, "Review", None)
    apply_suggestions(store, request, higher)
    assert build_day(store, DAY).total.work == {"Review": 600}


def test_quiz_uses_public_subject_and_grades_answers_locally(repo, tmp_path):
    store, study = DayRepo(repo), StudyRepo(repo)
    study.import_roadmap(
        "test",
        RoadmapInput(
            id="test",
            name="Synthetic",
            nodes=[
                Topic(id="transactions", title="Transactions", url="https://example.test/public"),
                Topic(
                    id="private", title="Private feature name", url="https://example.test/private"
                ),
            ],
        ),
    )
    (node,) = quiz_topics(store)
    request = prepare(store, tmp_path, DAY, "Recall quiz", node_id=node)
    assert request.digest == {"public_topic": "database transactions", "attempt": 1}
    quiz = Quiz.model_validate(
        {
            "questions": [
                {"prompt": f"Question {number}", "options": ["A", "B", "C", "D"], "answer": 0}
                for number in range(3)
            ]
        }
    )
    assert grade(store, request, quiz, [0, 0, 0], datetime.now(UTC)) == 3
    assert next(row for row in study.nodes() if row["id"] == node)["state"] == "verified"
    with pytest.raises(ValueError, match="already graded"):
        grade(store, request, quiz, [0, 0, 0], datetime.now(UTC))


def test_reminders_only_queue_after_an_opted_in_quarter_ends(repo):
    store = DayRepo(repo)
    set_quarter_reminders(store, True, local(9))
    assert pending_quarter(store, local(9, 59)) is None
    assert pending_quarter(store, local(10)) == f"{DAY.isoformat()}:Q1"
    set_quarter_reminders(store, False, local(10))
    assert pending_quarter(store, local(10) + timedelta(hours=2)) is None
