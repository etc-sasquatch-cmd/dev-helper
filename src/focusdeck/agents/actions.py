"""Persist reviewer suggestions, quiz evidence and quarter review preferences."""

import hashlib
from datetime import datetime

from focusdeck.agents.contracts import Classification, Quiz
from focusdeck.agents.digest import Request, quiz_topics
from focusdeck.core.calendar import day_windows
from focusdeck.db.day import DayRepo
from focusdeck.db.repo import to_db

QUARTER_ENABLED = "review.quarter_enabled"
QUARTER_SEEN = "review.last_quarter_seen"


def apply_suggestions(store: DayRepo, request: Request, result: Classification) -> int:
    with store.repo.transaction():
        for entry in result.suggestions:
            session = request.sessions[entry.session]
            work = entry.work_type if entry.confidence in ("medium", "high") else "Other"
            store.conn.execute(
                """
                INSERT INTO agent_classification VALUES (?, ?, ?, ?)
                ON CONFLICT (session_id, local_date) DO UPDATE SET
                    work_type = excluded.work_type, confidence = excluded.confidence
                """,
                (session, request.day.isoformat(), work, entry.confidence),
            )
    return len(result.suggestions)


def grade(
    store: DayRepo,
    request: Request,
    quiz: Quiz,
    answers: list[int],
    now: datetime,
) -> int:
    node = request.node_id
    if node not in quiz_topics(store):
        raise ValueError("The roadmap topic is no longer available for quizzes.")
    if len(answers) != 3 or any(type(item) is not int or not 0 <= item <= 3 for item in answers):
        raise ValueError("Choose an answer for all three questions.")
    correct = sum(
        answer == question.answer for answer, question in zip(answers, quiz.questions, strict=True)
    )
    question_set = hashlib.sha256(quiz.model_dump_json().encode("utf-8")).hexdigest()
    with store.repo.transaction():
        result = store.conn.execute(
            """
            INSERT OR IGNORE INTO quiz_attempt (node_id, attempted_at, correct, question_set)
            VALUES (?, ?, ?, ?)
            """,
            (node, to_db(now), correct, question_set),
        )
        if not result.rowcount:
            raise ValueError("This question set was already graded.")
        if correct == 3:
            store.conn.execute(
                "INSERT INTO node_evidence (node_id, kind, recorded_at) "
                "VALUES (?, 'passed_quiz', ?)",
                (node, to_db(now)),
            )
    return correct


def latest_quarter(store: DayRepo, now: datetime) -> str | None:
    day = now.astimezone().date()
    ends = {}
    for _, end, quarter in day_windows(day, store.schedule()):
        if quarter != "Off-hours":
            ends[quarter] = end
    closed = [quarter for quarter, end in ends.items() if end <= now]
    return f"{day.isoformat()}:{closed[-1]}" if closed else None


def pending_quarter(store: DayRepo, now: datetime) -> str | None:
    if store.repo.get_setting(QUARTER_ENABLED) != "1":
        return None
    latest = latest_quarter(store, now)
    return latest if latest != store.repo.get_setting(QUARTER_SEEN) else None


def set_quarter_reminders(store: DayRepo, enabled: bool, now: datetime) -> None:
    with store.repo.transaction():
        store.repo.set_setting(QUARTER_ENABLED, "1" if enabled else "0")
        if enabled:
            store.repo.set_setting(QUARTER_SEEN, latest_quarter(store, now) or "")
