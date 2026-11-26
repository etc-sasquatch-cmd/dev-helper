"""Storage for skill targets, roadmap progress, and tagged focus time."""

from datetime import datetime

from focusdeck.db.repo import Repo, from_db, to_db
from focusdeck.roadmaps.importer import RoadmapInput


class StudyRepo:
    def __init__(self, repo: Repo) -> None:
        self.repo = repo
        self.conn = repo.conn

    def save_targets(self, targets: dict[int, int], now: datetime) -> None:
        if (
            any(type(percent) is not int or not 0 <= percent <= 100 for percent in targets.values())
            or sum(targets.values()) != 100
        ):
            raise ValueError("Use whole percentages that add up to 100.")
        with self.repo.transaction():
            if targets.keys() != {skill.id for skill in self.repo.list_skills()}:
                raise ValueError("Enter a target for every skill.")
            cursor = self.conn.execute(
                "INSERT INTO skill_target_version (effective_at) VALUES (?)", (to_db(now),)
            )
            self.conn.executemany(
                "INSERT INTO skill_target VALUES (?, ?, ?)",
                ((cursor.lastrowid, skill, percent) for skill, percent in targets.items()),
            )

    def targets_at(self, now: datetime) -> dict[int, int]:
        rows = self.conn.execute(
            """
            SELECT skill_id, percent FROM skill_target
            WHERE version_id = (
                SELECT id FROM skill_target_version WHERE effective_at <= ?
                ORDER BY effective_at DESC, id DESC LIMIT 1
            )
            """,
            (to_db(now),),
        )
        return dict(rows)

    def focus_time(self, start: datetime, end: datetime):
        rows = self.conn.execute(
            """
            SELECT skill_id, elapsed_seconds, ended_at FROM pomodoro_block
            WHERE phase = 'focus' AND status IN ('completed', 'skipped')
                AND elapsed_seconds > 0 AND ended_at >= ? AND ended_at <= ?
            ORDER BY ended_at
            """,
            (to_db(start), to_db(end)),
        )
        for row in rows:
            ended_at = from_db(row["ended_at"])
            assert ended_at is not None
            yield row["skill_id"], row["elapsed_seconds"], ended_at

    def import_roadmap(self, source: str, roadmap: RoadmapInput) -> int:
        with self.repo.transaction():
            row = self.conn.execute(
                """
                INSERT INTO roadmap (source, name) VALUES (?, ?)
                ON CONFLICT (source) DO UPDATE SET name = excluded.name
                RETURNING id
                """,
                (source, roadmap.name),
            ).fetchone()
            roadmap_id = row["id"]
            self.conn.executemany(
                """
                INSERT INTO roadmap_node (roadmap_id, source_id, title, url)
                VALUES (?, ?, ?, ?)
                ON CONFLICT (roadmap_id, source_id)
                DO UPDATE SET title = excluded.title, url = excluded.url
                """,
                ((roadmap_id, node.id, node.title, node.url) for node in roadmap.nodes),
            )
        return roadmap_id

    def nodes(self, skill_id: int | None = None):
        return self.conn.execute(
            """
            SELECT n.id, n.title, n.url, r.name AS roadmap,
                CASE
                    WHEN n.skipped THEN 'skipped'
                    WHEN max(e.kind IN ('passed_quiz', 'reviewed_artifact')) THEN 'verified'
                    WHEN max(e.kind = 'recap') THEN 'claimed'
                    WHEN max(e.kind = 'time') THEN 'learning'
                    ELSE 'not_started'
                END AS state
            FROM roadmap_node n JOIN roadmap r ON r.id = n.roadmap_id
            LEFT JOIN node_evidence e ON e.node_id = n.id
            WHERE ? IS NULL OR n.id IN (
                SELECT node_id FROM skill_roadmap_node WHERE skill_id = ?
            )
            GROUP BY n.id ORDER BY r.name, n.title COLLATE NOCASE, n.id
            """,
            (skill_id, skill_id),
        ).fetchall()

    def node_links(self, skill_id: int) -> list[int]:
        return [
            row["node_id"]
            for row in self.conn.execute(
                "SELECT node_id FROM skill_roadmap_node WHERE skill_id = ?", (skill_id,)
            )
        ]

    def project_paths(self, skill_id: int) -> list[str]:
        return [
            row["path"]
            for row in self.conn.execute(
                "SELECT path FROM skill_project WHERE skill_id = ? ORDER BY path", (skill_id,)
            )
        ]

    def save_links(self, skill_id: int, node_ids: list[int], paths: list[str]) -> None:
        with self.repo.transaction():
            self.conn.execute("DELETE FROM skill_roadmap_node WHERE skill_id = ?", (skill_id,))
            self.conn.executemany(
                "INSERT OR IGNORE INTO skill_roadmap_node VALUES (?, ?)",
                ((skill_id, node_id) for node_id in node_ids),
            )
            self.conn.execute("DELETE FROM skill_project WHERE skill_id = ?", (skill_id,))
            self.conn.executemany(
                "INSERT OR IGNORE INTO skill_project VALUES (?, ?)",
                ((skill_id, path.strip()) for path in paths if path.strip()),
            )

    def set_skipped(self, node_id: int, skipped: bool) -> None:
        self.conn.execute("UPDATE roadmap_node SET skipped = ? WHERE id = ?", (skipped, node_id))
