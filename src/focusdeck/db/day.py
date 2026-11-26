"""Queries and settings used by the Day view."""

import json
from datetime import date, datetime

from focusdeck.core.calendar import QUARTERS, parse_schedule
from focusdeck.core.timer import WORK_TYPES
from focusdeck.db.repo import Repo, to_db

SCHEDULE_KEY = "report.quarter_hours"


class DayRepo:
    def __init__(self, repo: Repo) -> None:
        self.repo = repo
        self.conn = repo.conn

    def schedule(self):
        saved = self.repo.get_setting(SCHEDULE_KEY)
        return parse_schedule(json.loads(saved)) if saved else QUARTERS

    def save_schedule(self, values: list[tuple[str, str]]) -> None:
        normalized = parse_schedule(values)
        self.repo.set_setting(
            SCHEDULE_KEY,
            json.dumps(
                [(start.strftime("%H:%M"), end.strftime("%H:%M")) for _, start, end in normalized]
            ),
        )

    def focus_blocks(self, start: datetime, end: datetime):
        return self.conn.execute(
            """
            SELECT * FROM pomodoro_block
            WHERE phase = 'focus' AND status IN ('completed', 'skipped')
                AND ended_at >= ? AND started_at < ?
            ORDER BY started_at, id
            """,
            (to_db(start), to_db(end)),
        ).fetchall()

    def runs(self, block_id: int):
        return self.conn.execute(
            """
            SELECT started_at, ended_at FROM pomodoro_run
            WHERE block_id = ? AND ended_at IS NOT NULL ORDER BY started_at, id
            """,
            (block_id,),
        ).fetchall()

    def claude_intervals(self, day: date, start: datetime, end: datetime):
        return self.conn.execute(
            """
            SELECT i.*, c.work_type AS manual_work_type, c.skill_id,
                c.work_type AS work_type
            FROM activity_interval i
            LEFT JOIN classification c ON c.session_id = i.session_id AND c.local_date = ?
            WHERE i.ended_at > ? AND i.started_at < ?
            ORDER BY i.started_at, i.session_id
            """,
            (day.isoformat(), to_db(start), to_db(end)),
        ).fetchall()

    def save_classification(
        self,
        session_id: str,
        day: date,
        work_type: str | None,
        skill_id: int | None,
    ) -> None:
        if work_type is None:
            self.conn.execute(
                "DELETE FROM classification WHERE session_id = ? AND local_date = ?",
                (session_id, day.isoformat()),
            )
            return
        if work_type not in WORK_TYPES:
            raise ValueError("Choose a supported work type.")
        self.conn.execute(
            """
            INSERT INTO classification VALUES (?, ?, ?, ?)
            ON CONFLICT (session_id, local_date) DO UPDATE SET
                work_type = excluded.work_type, skill_id = excluded.skill_id
            """,
            (session_id, day.isoformat(), work_type, skill_id),
        )

    def evidence(self, start: datetime, end: datetime):
        return self.conn.execute(
            """
            SELECT e.kind, e.recorded_at, n.title, r.name AS roadmap,
                NOT EXISTS (
                    SELECT 1 FROM node_evidence prior WHERE prior.node_id = e.node_id
                    AND (prior.recorded_at, prior.id) < (e.recorded_at, e.id)
                    AND CASE prior.kind WHEN 'time' THEN 0 WHEN 'recap' THEN 1 ELSE 2 END
                        >= CASE e.kind WHEN 'time' THEN 0 WHEN 'recap' THEN 1 ELSE 2 END
                ) AS first_level
            FROM node_evidence e JOIN roadmap_node n ON n.id = e.node_id
            JOIN roadmap r ON r.id = n.roadmap_id
            WHERE e.recorded_at >= ? AND e.recorded_at < ?
            ORDER BY e.recorded_at, e.id
            """,
            (to_db(start), to_db(end)),
        ).fetchall()

    def target_changes(self, start: datetime, end: datetime):
        return self.conn.execute(
            """
            SELECT DISTINCT effective_at FROM skill_target_version
            WHERE effective_at > ? AND effective_at < ? ORDER BY effective_at
            """,
            (to_db(start), to_db(end)),
        ).fetchall()
