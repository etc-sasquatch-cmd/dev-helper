"""Claude Code hook entry point. Standard library only, always succeeds silently."""

import hashlib
import json
import os
import re
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

if __package__:
    from focusdeck.capture.policy import Policy, project_keys
else:
    from policy import Policy, project_keys

EVENTS = (
    "SessionStart",
    "UserPromptSubmit",
    "PostToolUse",
    "PostToolUseFailure",
    "Stop",
    "SessionEnd",
)
TOOL_RESULTS = {"PostToolUse": "success", "PostToolUseFailure": "failure"}
SPOOL = "capture-spool.jsonl"
SESSION_ID = re.compile(r"[A-Za-z0-9_-]{1,128}")
TOOL_NAME = re.compile(r"[A-Za-z0-9_-]{1,128}")


def make_event(payload: dict, policy: Policy, projects: Path) -> dict | None:
    event = payload.get("hook_event_name")
    cwd, session = payload.get("cwd"), payload.get("session_id")
    if (
        event not in EVENTS
        or not isinstance(cwd, str)
        or not Path(cwd).is_absolute()
        or not isinstance(session, str)
        or not SESSION_ID.fullmatch(session)
    ):
        return None
    keys = project_keys(cwd)
    mode = policy.mode(keys)
    if mode is None:
        return None
    transcript = None
    if mode == "full" and isinstance(payload.get("transcript_path"), str):
        candidate = Path(payload["transcript_path"])
        if candidate.is_absolute() and candidate.suffix.lower() == ".jsonl":
            resolved = candidate.resolve()
            if resolved.is_relative_to(projects):
                transcript = resolved.relative_to(projects).as_posix()
    tool = payload.get("tool_name")
    occurrence_key = payload.get("tool_use_id") or payload.get("prompt_id")
    event_id = uuid4().hex
    if isinstance(occurrence_key, str) and occurrence_key:
        identity = json.dumps([session, event, occurrence_key], ensure_ascii=True)
        event_id = hashlib.sha256(identity.encode("ascii")).hexdigest()
    return {
        "id": event_id,
        "event": event,
        "session": session,
        "projects": keys,
        "mode": mode,
        "ts": datetime.now(UTC).isoformat(timespec="microseconds"),
        "transcript": transcript,
        "tool": tool
        if event in TOOL_RESULTS and isinstance(tool, str) and TOOL_NAME.fullmatch(tool)
        else None,
        "result": TOOL_RESULTS.get(event),
    }


def append_event(folder: Path, event: dict) -> None:
    line = (json.dumps(event, separators=(",", ":")) + "\n").encode("ascii")
    with (folder / SPOOL).open("a+b", buffering=0) as stream:
        if os.name == "nt":
            import msvcrt

            # Lock a byte beyond normal data, leaving completed lines readable by the app.
            stream.seek(2**62)
            for attempt in range(4):
                try:
                    msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                    break
                except OSError:
                    if attempt == 3:
                        raise
                    time.sleep(0.005)
        else:
            import fcntl

            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if stream.seek(0, os.SEEK_END):
            stream.seek(-1, os.SEEK_END)
            if stream.read(1) != b"\n":
                line = b"\n" + line
        stream.write(line)
        # Closing the handle releases the lock even if the append raises.


def main() -> None:
    if os.environ.get("FOCUSDECK_AGENT") == "1":
        return
    try:
        if len(sys.argv) != 4 or sys.argv[1] != "--focusdeck-capture-v1":
            return
        folder = Path(sys.argv[2])
        policy = Policy.load(folder)
        if not (policy.enabled and policy.reviewed):
            return
        raw = sys.stdin.buffer.read(2_000_001)
        if len(raw) > 2_000_000:
            return
        payload = json.loads(raw.decode("utf-8-sig"))
        if isinstance(payload, dict):
            event = make_event(payload, policy, Path(sys.argv[3]).resolve())
            if event is not None:
                append_event(folder, event)
    except Exception:
        pass


if __name__ == "__main__":
    main()
