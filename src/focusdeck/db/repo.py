"""All SQL for the timer lives here. Timestamps go in and out as UTC datetimes."""

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime


def to_db(moment: datetime) -> str:
    if moment.tzinfo is None:
        raise ValueError("timestamps must be timezone-aware")
    return moment.astimezone(UTC).isoformat()


def from_db(text: str | None) -> datetime | None:
    return datetime.fromisoformat(text) if text else None


@dataclass(frozen=True)
class PresetRow:
    id: int
    name: str
    focus_seconds: int
    short_break_seconds: int
    long_break_seconds: int
    cycles_before_long: int


@dataclass(frozen=True)
class SkillRow:
    id: int
    name: str


@dataclass(frozen=True)
class BlockRow:
    id: int
    phase: str
    status: str
    skill_id: int | None
    work_type: str | None
    planned_seconds: int
    started_at: datetime
    ends_at: datetime | None
    remaining_seconds: float | None
    ended_at: datetime | None
    elapsed_seconds: float | None
    recap: str | None
    recap_skipped: bool


def _block(row: sqlite3.Row) -> BlockRow:
    started_at = from_db(row["started_at"])
    assert started_at is not None
    return BlockRow(
        id=row["id"],
        phase=row["phase"],
        status=row["status"],
        skill_id=row["skill_id"],
        work_type=row["work_type"],
        planned_seconds=row["planned_seconds"],
        started_at=started_at,
        ends_at=from_db(row["ends_at"]),
        remaining_seconds=row["remaining_seconds"],
        ended_at=from_db(row["ended_at"]),
        elapsed_seconds=row["elapsed_seconds"],
        recap=row["recap"],
        recap_skipped=bool(row["recap_skipped"]),
    )


class Repo:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    @contextmanager
    def transaction(self) -> Iterator[None]:
        """Group several writes so they all happen or none do."""
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            yield
        except BaseException:
            self.conn.execute("ROLLBACK")
            raise
        self.conn.execute("COMMIT")

    # settings ---------------------------------------------------------------

    def get_setting(self, key: str, default: str | None = None) -> str | None:
        row = self.conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else default

    def set_setting(self, key: str, value: str) -> None:
        self.conn.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT (key) DO UPDATE SET value = excluded.value",
            (key, value),
        )

    # presets ----------------------------------------------------------------

    def list_presets(self) -> list[PresetRow]:
        rows = self.conn.execute("SELECT * FROM phase_preset ORDER BY id").fetchall()
        return [PresetRow(**dict(row)) for row in rows]

    def get_preset(self, preset_id: int) -> PresetRow | None:
        row = self.conn.execute("SELECT * FROM phase_preset WHERE id = ?", (preset_id,)).fetchone()
        return PresetRow(**dict(row)) if row else None

    def create_preset(
        self, name: str, focus: int, short_break: int, long_break: int, cycles: int
    ) -> int:
        cursor = self.conn.execute(
            "INSERT INTO phase_preset (name, focus_seconds, short_break_seconds, "
            "long_break_seconds, cycles_before_long) VALUES (?, ?, ?, ?, ?)",
            (name, focus, short_break, long_break, cycles),
        )
        assert cursor.lastrowid is not None
        return cursor.lastrowid

    def update_preset(
        self, preset_id: int, name: str, focus: int, short_break: int, long_break: int, cycles: int
    ) -> None:
        self.conn.execute(
            "UPDATE phase_preset SET name = ?, focus_seconds = ?, short_break_seconds = ?, "
            "long_break_seconds = ?, cycles_before_long = ? WHERE id = ?",
            (name, focus, short_break, long_break, cycles, preset_id),
        )

    def delete_preset(self, preset_id: int) -> None:
        self.conn.execute("DELETE FROM phase_preset WHERE id = ?", (preset_id,))

    # skills -----------------------------------------------------------------

    def list_skills(self) -> list[SkillRow]:
        rows = self.conn.execute("SELECT id, name FROM skill ORDER BY name").fetchall()
        return [SkillRow(**dict(row)) for row in rows]

    def create_skill(self, name: str) -> int:
        """Create a skill, or return the existing one with the same name (case-insensitive)."""
        name = name.strip()
        if not name:
            raise ValueError("skill name is empty")
        row = self.conn.execute("SELECT id FROM skill WHERE name = ?", (name,)).fetchone()
        if row:
            return row["id"]
        cursor = self.conn.execute("INSERT INTO skill (name) VALUES (?)", (name,))
        assert cursor.lastrowid is not None
        return cursor.lastrowid

    # blocks -----------------------------------------------------------------

    def get_block(self, block_id: int) -> BlockRow | None:
        row = self.conn.execute("SELECT * FROM pomodoro_block WHERE id = ?", (block_id,)).fetchone()
        return _block(row) if row else None

    def active_block(self) -> BlockRow | None:
        row = self.conn.execute(
            "SELECT * FROM pomodoro_block WHERE status IN ('running', 'paused')"
        ).fetchone()
        return _block(row) if row else None

    def insert_running_block(
        self,
        *,
        phase: str,
        skill_id: int | None,
        work_type: str | None,
        planned_seconds: int,
        started_at: datetime,
        ends_at: datetime,
    ) -> int:
        cursor = self.conn.execute(
            "INSERT INTO pomodoro_block (phase, status, skill_id, work_type, planned_seconds, "
            "started_at, ends_at) VALUES (?, 'running', ?, ?, ?, ?, ?)",
            (phase, skill_id, work_type, planned_seconds, to_db(started_at), to_db(ends_at)),
        )
        assert cursor.lastrowid is not None
        return cursor.lastrowid

    def mark_paused(self, block_id: int, remaining_seconds: float) -> None:
        self.conn.execute(
            "UPDATE pomodoro_block SET status = 'paused', ends_at = NULL, remaining_seconds = ? "
            "WHERE id = ?",
            (remaining_seconds, block_id),
        )

    def mark_running(self, block_id: int, ends_at: datetime) -> None:
        self.conn.execute(
            "UPDATE pomodoro_block SET status = 'running', ends_at = ?, remaining_seconds = NULL "
            "WHERE id = ?",
            (to_db(ends_at), block_id),
        )

    def finish_block(
        self, block_id: int, *, status: str, ended_at: datetime, elapsed_seconds: float
    ) -> None:
        self.conn.execute(
            "UPDATE pomodoro_block SET status = ?, ended_at = ?, elapsed_seconds = ?, "
            "ends_at = NULL, remaining_seconds = NULL WHERE id = ?",
            (status, to_db(ended_at), elapsed_seconds, block_id),
        )

    # recaps -----------------------------------------------------------------

    def pending_recap_block(self) -> BlockRow | None:
        """Oldest completed focus block whose recap is neither written nor skipped."""
        row = self.conn.execute(
            "SELECT * FROM pomodoro_block WHERE phase = 'focus' AND status = 'completed' "
            "AND recap IS NULL AND recap_skipped = 0 ORDER BY id LIMIT 1"
        ).fetchone()
        return _block(row) if row else None

    def save_recap(self, block_id: int, text: str) -> None:
        self.conn.execute(
            "UPDATE pomodoro_block SET recap = ?, recap_skipped = 0 WHERE id = ?",
            (text, block_id),
        )

    def skip_recap(self, block_id: int) -> None:
        self.conn.execute(
            "UPDATE pomodoro_block SET recap_skipped = 1 WHERE id = ? AND recap IS NULL",
            (block_id,),
        )

    def count_skipped_recaps(self) -> int:
        return self.conn.execute(
            "SELECT COUNT(*) FROM pomodoro_block WHERE recap_skipped = 1"
        ).fetchone()[0]

    def count_completed_focus(self, since: datetime, until: datetime) -> int:
        return self.conn.execute(
            "SELECT COUNT(*) FROM pomodoro_block WHERE phase = 'focus' AND status = 'completed' "
            "AND ended_at >= ? AND ended_at < ?",
            (to_db(since), to_db(until)),
        ).fetchone()[0]
