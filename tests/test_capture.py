import json
import os
import subprocess
import sys
from dataclasses import replace
from datetime import UTC, datetime, timedelta, timezone

import pytest

from focusdeck.capture.activity import active_intervals
from focusdeck.capture.hook import EVENTS, SPOOL, make_event
from focusdeck.capture.ingest import Ingestor
from focusdeck.capture.installer import preview
from focusdeck.capture.policy import Policy, clean_paths
from focusdeck.capture.transcript import extract, redact


@pytest.fixture
def capture(tmp_path):
    config = tmp_path / "scratch claude"
    projects = config / "projects"
    projects.mkdir(parents=True)
    folder = tmp_path / "scratch data"
    folder.mkdir()
    return config / "settings.json", projects, folder


def test_installer_previews_backs_up_and_preserves_unrelated_hooks(capture):
    settings, _, folder = capture
    other = {"matcher": "Bash", "hooks": [{"type": "command", "command": "other"}]}
    original = {"theme": "dark", "hooks": {"Stop": [other], "PreToolUse": [other]}}
    before = json.dumps(original).encode()
    settings.write_bytes(before)

    install = preview(settings, folder, install=True)
    assert any(
        line.startswith("+") and '"--focusdeck-capture-v1"' in line
        for line in install.diff.splitlines()
    )
    assert settings.read_bytes() == before
    backup = install.apply()
    assert backup.read_bytes() == before
    installed = json.loads(settings.read_bytes())
    assert installed["hooks"]["Stop"][0] == other
    assert installed["hooks"]["PreToolUse"] == [other]
    assert all(installed["hooks"][name] for name in EVENTS)
    assert not preview(settings, folder, install=True).diff

    uninstall = preview(settings, folder, install=False)
    installed["theme"] = "light"
    settings.write_text(json.dumps(installed), encoding="utf-8")
    with pytest.raises(ValueError, match="changed"):
        uninstall.apply()
    backup = preview(settings, folder, install=False).apply()
    assert json.loads(backup.read_bytes()) == installed
    assert json.loads(settings.read_bytes()) == {**original, "theme": "light"}


def test_installer_handles_first_install_and_malformed_settings(capture):
    settings, _, folder = capture
    assert not preview(settings, folder, install=False).diff
    assert preview(settings, folder, install=True).apply() is None
    assert settings.exists()
    settings.write_text('{"hooks": {}, "hooks": {}}', encoding="utf-8")
    with pytest.raises(ValueError, match="duplicate"):
        preview(settings, folder, install=True)


def test_portable_hook_replaces_source_entry_and_preserves_unrelated_settings(capture, monkeypatch):
    settings, projects, folder = capture
    unrelated = {"hooks": [{"type": "command", "command": "other"}]}
    settings.write_text(json.dumps({"hooks": {"Stop": [unrelated]}}), encoding="utf-8")
    preview(settings, folder, install=True).apply()
    source_settings = settings.read_bytes()

    bundle = folder / "Moved bundle"
    helper = bundle / "FocusDeckCapture" / "FocusDeckCapture.exe"
    helper.parent.mkdir(parents=True)
    helper.touch()
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(bundle / "FocusDeck.exe"))

    change = preview(settings, folder, install=True)
    assert settings.read_bytes() == source_settings
    assert change.apply().read_bytes() == source_settings
    hooks = json.loads(settings.read_bytes())["hooks"]
    assert hooks["Stop"][0] == unrelated
    for event in EVENTS:
        (owned,) = hooks[event][int(event == "Stop") :]
        assert owned["hooks"] == [
            {
                "type": "command",
                "command": str(helper),
                "args": ["--focusdeck-capture-v1", str(folder), str(projects)],
                "async": True,
                "timeout": 2,
            }
        ]
    assert not preview(settings, folder, install=True).has_changes
    preview(settings, folder, install=False).apply()
    assert json.loads(settings.read_bytes()) == {"hooks": {"Stop": [unrelated]}}

    helper.unlink()
    with pytest.raises(ValueError, match="portable capture helper is missing"):
        preview(settings, folder, install=True)


@pytest.mark.parametrize("edited_during_retry", [False, True])
def test_installer_rechecks_settings_when_windows_replace_retries(
    capture, monkeypatch, edited_during_retry
):
    settings, _, folder = capture
    settings.write_bytes(b'{"theme":"dark"}')
    change = preview(settings, folder, install=True)
    real_replace = os.replace
    edited = b'{"theme":"light"}'
    attempts = 0

    def replace_once(source, target):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            if edited_during_retry:
                settings.write_bytes(edited)
            error = PermissionError("synthetic Windows sharing violation")
            error.winerror = 32
            raise error
        real_replace(source, target)

    monkeypatch.setattr(os, "replace", replace_once)
    if edited_during_retry:
        with pytest.raises(ValueError, match="changed"):
            change.apply()
        assert attempts == 1
        assert settings.read_bytes() == edited
    else:
        change.apply()
        assert attempts == 2
        assert settings.read_bytes() == change.after


def test_hook_process_filters_before_writing_and_exits_zero(capture, tmp_path):
    settings, projects, folder = capture
    excluded = tmp_path / "excluded"
    metadata = tmp_path / "metadata"
    policy = Policy(
        reviewed=True,
        enabled=True,
        excluded=clean_paths(str(excluded)),
        metadata_only=clean_paths(str(metadata)),
    )
    transcript = projects / "project" / "synthetic.jsonl"
    install = json.loads(preview(settings, folder, install=True).after)
    hook = install["hooks"]["PostToolUseFailure"][0]["hooks"][0]
    command = [hook["command"], *hook["args"]]
    payload = {
        "hook_event_name": "PostToolUseFailure",
        "session_id": "one",
        "tool_use_id": "tool_1",
        "tool_name": "Bash",
        "cwd": str(metadata),
        "transcript_path": str(transcript),
        "prompt": "synthetic secret prompt",
        "tool_input": {"command": "synthetic secret command"},
    }

    def send(value, *, agent="0"):
        raw = value if isinstance(value, bytes) else json.dumps(value).encode("utf-8-sig")
        returned = subprocess.run(
            command,
            input=raw,
            capture_output=True,
            check=True,
            timeout=5,
            env={**os.environ, "FOCUSDECK_AGENT": agent},
        )
        assert returned.stdout == returned.stderr == b""

    send(payload)
    replace(policy, reviewed=False).save(folder)
    send(payload)
    policy.save(folder)
    send(payload, agent="1")
    send({**payload, "cwd": str(excluded / "nested")})
    send(b"unparseable")
    assert not (folder / SPOOL).exists()

    send(payload)
    raw = (folder / SPOOL).read_bytes()
    event = json.loads(raw)
    assert event["mode"] == "metadata" and event["transcript"] is None
    assert event["tool"] == "Bash" and event["result"] == "failure"
    assert not any(text in raw for text in (b"synthetic", b"metadata\\", b"tool_input"))

    with (folder / SPOOL).open("ab") as stream:
        stream.write(b'{"incomplete":')
    send({**payload, "tool_use_id": "tool_2"})
    lines = (folder / SPOOL).read_bytes().splitlines()
    assert len(lines) == 3
    assert json.loads(lines[2])["id"] != event["id"]

    (folder / SPOOL).rename(folder / "saved.jsonl")
    (folder / SPOOL).mkdir()
    send(payload)  # A write failure still produces a silent successful exit.


def test_incremental_ingest_retries_partial_lines_and_deduplicates(repo, capture, tmp_path):
    _, projects, folder = capture
    private = tmp_path / "private"
    policy = Policy(reviewed=True, enabled=True, metadata_only=clean_paths(str(private)))
    policy.save(folder)
    transcript = projects / "project" / "synthetic.jsonl"
    transcript.parent.mkdir()
    timestamp = datetime(2026, 11, 25, 14, tzinfo=UTC)

    def event(kind, offset, *, session="one", cwd=tmp_path):
        payload = {
            "hook_event_name": kind,
            "cwd": str(cwd),
            "session_id": session,
            "transcript_path": str(transcript),
        }
        entry = make_event(payload, policy, projects)
        return json.dumps({**entry, "ts": (timestamp + timedelta(seconds=offset)).isoformat()})

    first = event("SessionStart", 0)
    second = event("Stop", 180)
    restricted = event("SessionStart", 0, session="restricted", cwd=private)
    spool = folder / SPOOL
    spool.write_text(first + "\n" + restricted + "\n" + second, encoding="utf-8")
    record = {
        "type": "user",
        "sessionId": "one",
        "uuid": "synthetic-1",
        "timestamp": (timestamp + timedelta(seconds=1)).isoformat(),
        "message": {"content": "Check email qa@example.test and token=totally-fake"},
    }
    with transcript.open("w", encoding="utf-8") as stream:
        stream.write(json.dumps({**record, "timestamp": "2020-01-01T00:00:00+00:00"}) + "\n")
        stream.write(json.dumps(record))

    def read():
        Ingestor(repo, folder, projects).run()

    def count(table):
        return repo.conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]

    read()
    assert count("claude_event") == 2
    assert count("activity_interval") == count("claude_transcript_record") == 0
    assert (
        repo.conn.execute(
            "SELECT transcript_relative FROM claude_session WHERE id = 'restricted'"
        ).fetchone()[0]
        is None
    )

    with spool.open("a", encoding="utf-8") as stream:
        stream.write("\n" + first + "\n{broken}\n")
    with transcript.open("a", encoding="utf-8") as stream:
        stream.write("\n" + json.dumps(record) + "\n")
        stream.write(json.dumps({**record, "uuid": "synthetic-2", "cwd": str(private)}) + "\n")
    read()
    read()
    assert count("claude_event") == 3
    assert count("claude_transcript_record") == 1
    details = json.loads(
        repo.conn.execute("SELECT details FROM claude_transcript_record").fetchone()[0]
    )
    assert details["prompt"] == "Check email [redacted] and [redacted]"
    seconds = repo.conn.execute(
        "SELECT sum(unixepoch(ended_at) - unixepoch(started_at)) FROM activity_interval"
    ).fetchone()[0]
    assert seconds == 180
    replace(policy, gap_minutes=2).save(folder)
    read()
    assert count("activity_interval") == 0


def test_active_time_splits_at_quarters_and_local_midnight():
    zone = timezone(timedelta(hours=-7))

    def local(hour, minute, day=25):
        return datetime(2026, 11, day, hour, minute, tzinfo=zone)

    times = [
        local(10, 1),
        local(9, 59),
        local(11, 59),
        local(12, 1),
        local(23, 59),
        local(0, 1, day=26),
        local(10, 1),
    ]
    intervals = list(active_intervals(times, 5, zone))
    assert [row.quarter for row in intervals] == [
        "Q1",
        "Q2",
        "Q2",
        "Off-hours",
        "Off-hours",
        "Off-hours",
    ]
    assert [row.local_date.day for row in intervals] == [25, 25, 25, 25, 25, 26]
    assert all(row.end - row.start == timedelta(minutes=1) for row in intervals)


def test_transcript_extracts_only_supported_details_and_redacts():
    data = {
        "type": "assistant",
        "session_id": "real-shape",
        "timestamp": "2026-11-25T10:00:00Z",
        "message": {
            "usage": {"input_tokens": 10, "output_tokens": 20, "other": 40},
            "content": [
                {"type": "text", "text": "Do not capture assistant prose"},
                {
                    "type": "tool_use",
                    "name": "Edit",
                    "input": {
                        "file_path": "C:\\Users\\synthetic\\repo\\example.py",
                        "new_string": "uncaptured file content",
                    },
                },
                {
                    "type": "tool_use",
                    "name": "PowerShell",
                    "input": {"command": "pytest --token=synthetic-token"},
                },
                {"type": "tool_use", "name": "Bash", "input": {"command": "arbitrary command"}},
                {"type": "tool_result", "is_error": True, "content": "uncaptured output"},
                "unexpected block",
            ],
        },
    }
    details = extract(data, "real-shape")
    assert details == {
        "prompt": None,
        "tools": ["Edit", "PowerShell", "Bash"],
        "failures": 1,
        "files": ["~\\repo\\example.py"],
        "checks": ["pytest --[redacted]"],
        "usage": {"input_tokens": 10, "output_tokens": 20},
    }
    text = " ".join(
        (
            "password='some phrase'",
            "Bearer synthetic_token",
            "ghp_1234567890",
            "postgresql://alice:secret@localhost/db",
            "alice@example.test",
            "-----BEGIN PRIVATE KEY----- bytes -----END PRIVATE KEY-----",
        )
    )
    assert redact(text, 300) == " ".join(["[redacted]"] * 6)
    assert redact('APP_TOKEN="synthetic" "password": "synthetic"', 200) == (
        '[redacted] "[redacted]'
    )
    assert redact(r"C:\Users\Sample User\repo\main.py", 200) == r"~\repo\main.py"
    assert redact("Server=localhost;Database=db;User Id=fake;Password=fake;", 200) == ("[redacted]")
    assert extract({**data, "type": "unknown"}, "real-shape") is None
    assert extract(data, "wrong-session") is None
    assert extract({**data, "timestamp": "2026-11-25T10:00:00"}, "real-shape") is None
