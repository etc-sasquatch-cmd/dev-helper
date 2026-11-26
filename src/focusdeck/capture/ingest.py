"""Local spool and transcript ingestion with transactional byte checkpoints."""

import hashlib
import json
import os
import re
from collections.abc import Callable
from pathlib import Path
from typing import BinaryIO

from focusdeck.capture.activity import active_intervals
from focusdeck.capture.hook import EVENTS, SESSION_ID, SPOOL, TOOL_NAME, TOOL_RESULTS
from focusdeck.capture.policy import Policy, project_keys
from focusdeck.capture.transcript import extract, transcript_timestamp
from focusdeck.db.connection import open_database
from focusdeck.db.repo import Repo, from_db, to_db

MAX_RECORD = 1_000_000
MAX_BATCH_BYTES = 4_000_000
KEY = re.compile(r"[a-f0-9]{32}(?:[a-f0-9]{32})?")


def _read_line(stream: BinaryIO) -> bytes | None:
    raw = stream.readline(MAX_RECORD + 1)
    if not raw:
        return None
    if raw.endswith(b"\n"):
        return raw if len(raw) <= MAX_RECORD else b""
    if len(raw) <= MAX_RECORD:
        return None
    while raw and not raw.endswith(b"\n"):
        raw = stream.readline(MAX_RECORD + 1)
    return b"" if raw else None


class Ingestor:
    def __init__(self, repo: Repo, folder: Path, projects: Path) -> None:
        self.repo = repo
        self.conn = repo.conn
        self.folder = folder
        self.projects = projects.resolve()
        self.policy = Policy.load(folder)

    def _consume(self, path: Path, source: str, consume: Callable[[dict, bytes], None]) -> None:
        try:
            stream = path.open("rb")
        except (FileNotFoundError, PermissionError):
            return
        with stream:
            stat = os.fstat(stream.fileno())
            identity = f"{stat.st_dev}:{stat.st_ino}"
            checkpoint = self.conn.execute(
                "SELECT file_identity, byte_offset FROM capture_checkpoint WHERE source = ?",
                (source,),
            ).fetchone()
            offset = 0
            if checkpoint and checkpoint["file_identity"] == identity:
                previous = checkpoint["byte_offset"]
                if previous <= stat.st_size:
                    offset = previous
            if offset == stat.st_size:
                return
            stream.seek(offset)
            end = offset
            for _ in range(512):
                if end - offset >= MAX_BATCH_BYTES:
                    break
                raw = _read_line(stream)
                if raw is None:
                    break
                end = stream.tell()
                if not raw:
                    continue
                try:
                    payload = json.loads(raw.decode("utf-8-sig"))
                except (ValueError, RecursionError):
                    continue
                if isinstance(payload, dict):
                    consume(payload, raw)
            if end != offset:
                self.conn.execute(
                    """
                    INSERT INTO capture_checkpoint VALUES (?, ?, ?)
                    ON CONFLICT (source) DO UPDATE SET
                        file_identity = excluded.file_identity,
                        byte_offset = excluded.byte_offset
                    """,
                    (source, identity, end),
                )

    def _event(self, entry: dict, touched: set[str]) -> None:
        event_id, session = entry.get("id"), entry.get("session")
        timestamp = transcript_timestamp(entry.get("ts"))
        event, keys = entry.get("event"), entry.get("projects")
        if (
            not isinstance(session, str)
            or not SESSION_ID.fullmatch(session)
            or not isinstance(event_id, str)
            or not KEY.fullmatch(event_id)
            or timestamp is None
            or event not in EVENTS
            or not isinstance(keys, list)
            or not keys
            or len(keys) > 128
            or any(
                not isinstance(key, str) or len(key) != 64 or not KEY.fullmatch(key) for key in keys
            )
        ):
            return
        mode = self.policy.mode(keys)
        if mode is None:
            self.conn.execute(
                "UPDATE claude_session SET detail_allowed = 0 WHERE id = ?", (session,)
            )
            return
        event_at = to_db(timestamp)
        full = mode == entry.get("mode") == "full"
        relative = entry.get("transcript")
        relative = relative if full and self._transcript_path(relative) is not None else None
        self.conn.execute(
            """
            INSERT INTO claude_session VALUES (?, ?, ?, ?, ?)
            ON CONFLICT (id) DO UPDATE SET
                detail_allowed = min(claude_session.detail_allowed, excluded.detail_allowed)
                    AND claude_session.project_keys = excluded.project_keys,
                first_event_at = min(claude_session.first_event_at, excluded.first_event_at),
                transcript_relative = coalesce(
                    claude_session.transcript_relative, excluded.transcript_relative
                )
            """,
            (session, json.dumps(keys), full, event_at, relative),
        )
        tool = entry.get("tool")
        result = TOOL_RESULTS.get(event)
        changed = self.conn.execute(
            "INSERT OR IGNORE INTO claude_event VALUES (?, ?, ?, ?, ?, ?)",
            (
                event_id,
                session,
                event_at,
                event,
                tool if result and isinstance(tool, str) and TOOL_NAME.fullmatch(tool) else None,
                result,
            ),
        ).rowcount
        if changed:
            touched.add(session)

    def _transcript_path(self, relative: object) -> Path | None:
        if not isinstance(relative, str) or not relative:
            return None
        try:
            path = Path(relative)
            if path.anchor or ".." in path.parts or path.suffix.lower() != ".jsonl":
                return None
            target = (self.projects / path).resolve()
            return target if target.is_relative_to(self.projects) else None
        except (ValueError, OSError):
            return None

    def _record(self, session: str, first_event_at: str, record: dict, raw: bytes) -> None:
        timestamp = transcript_timestamp(record.get("timestamp"))
        if timestamp is None or timestamp < from_db(first_event_at):
            return
        cwd = record.get("cwd")
        if cwd is not None:
            try:
                if (
                    not isinstance(cwd, str)
                    or not Path(cwd).is_absolute()
                    or self.policy.mode(project_keys(cwd)) != "full"
                ):
                    return
            except (ValueError, OSError, RuntimeError):
                return
        details = extract(record, session)
        if details is None:
            return
        uuid = record.get("uuid")
        stable_id = (
            uuid.encode("utf-8", errors="surrogatepass")
            if isinstance(uuid, str) and uuid
            else raw.rstrip(b"\r\n")
        )
        self.conn.execute(
            "INSERT OR IGNORE INTO claude_transcript_record VALUES (?, ?, ?, ?)",
            (session, hashlib.sha256(stable_id).hexdigest(), to_db(timestamp), json.dumps(details)),
        )

    def run(self) -> None:
        if not self.policy.reviewed or not self.policy.enabled:
            return
        with self.repo.transaction():
            touched: set[str] = set()
            self._consume(
                self.folder / SPOOL, "spool", lambda payload, _: self._event(payload, touched)
            )
            previous_gap = self.repo.get_setting("capture.interval_gap")
            if previous_gap != str(self.policy.gap_minutes):
                touched.update(
                    row["session_id"]
                    for row in self.conn.execute("SELECT DISTINCT session_id FROM claude_event")
                )
                self.repo.set_setting("capture.interval_gap", str(self.policy.gap_minutes))
            for session in touched:
                self.conn.execute("DELETE FROM activity_interval WHERE session_id = ?", (session,))
                times = (
                    from_db(row["occurred_at"])
                    for row in self.conn.execute(
                        "SELECT occurred_at FROM claude_event WHERE session_id = ?", (session,)
                    )
                )
                self.conn.executemany(
                    "INSERT INTO activity_interval VALUES (?, ?, ?, ?, ?)",
                    (
                        (
                            session,
                            to_db(part.start),
                            to_db(part.end),
                            str(part.local_date),
                            part.quarter,
                        )
                        for part in active_intervals(times, self.policy.gap_minutes)
                    ),
                )
        sessions = self.conn.execute(
            """
            SELECT * FROM claude_session
            WHERE detail_allowed AND transcript_relative IS NOT NULL
            """
        ).fetchall()
        for row in sessions:
            if self.policy.mode(json.loads(row["project_keys"])) != "full":
                continue
            path = self._transcript_path(row["transcript_relative"])
            if path is not None:
                session, first_event_at = row["id"], row["first_event_at"]

                def record(payload, raw, session=session, first_event_at=first_event_at):
                    self._record(session, first_event_at, payload, raw)

                with self.repo.transaction():
                    self._consume(path, f"transcript:{session}", record)


def ingest(folder: Path, projects: Path) -> None:
    if not Policy.load(folder).enabled:
        return
    conn = open_database(folder / "focusdeck.sqlite")
    try:
        Ingestor(Repo(conn), folder, projects).run()
    finally:
        conn.close()
