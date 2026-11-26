from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from focusdeck.core.day import build_day
from focusdeck.core.trends import build_trends
from focusdeck.db.day import DayRepo
from focusdeck.db.repo import to_db
from focusdeck.db.study import StudyRepo
from focusdeck.reports.charts import draw_trends_chart
from focusdeck.roadmaps.importer import RoadmapInput, Topic


def test_trends_reconcile_with_day_reports_across_midnight_and_target_changes(repo, skill_id):
    store, study = DayRepo(repo), StudyRepo(repo)
    other = repo.create_skill("SQL")
    start = datetime(2026, 11, 29, 22, tzinfo=UTC)  # Sunday
    midnight = start + timedelta(hours=2)
    end = midnight + timedelta(hours=1)
    study.save_targets({skill_id: 100, other: 0}, start)
    study.save_targets({skill_id: 0, other: 100}, midnight)
    block = repo.insert_running_block(
        phase="focus",
        skill_id=skill_id,
        work_type="Study",
        planned_seconds=3 * 3600,
        started_at=start,
        ends_at=end,
    )
    repo.finish_block(block, status="completed", ended_at=end, elapsed_seconds=3 * 3600)
    repo.skip_recap(block)
    repo.conn.execute(
        "INSERT INTO claude_session VALUES ('session', '[]', 0, ?, NULL)", (to_db(start),)
    )
    repo.conn.execute(
        "INSERT INTO activity_interval VALUES ('session', ?, ?, ?, 'cached')",
        (to_db(start), to_db(end + timedelta(hours=1)), start.date().isoformat()),
    )
    store.save_classification("session", midnight.date(), "Review", other)

    month = build_trends(store, end.date(), "Month", end.date(), UTC)
    sunday = build_day(store, start.date(), UTC)
    monday = build_day(store, midnight.date(), UTC)
    assert (month.start, month.end) == (date(2026, 11, 1), date(2026, 11, 30))
    assert month.seconds == sunday.seconds + monday.seconds == 4 * 3600
    assert month.work == {"Study": 3 * 3600, "Review": 3600}
    assert month.skills == {"Python": 3 * 3600, "SQL": 3600}
    assert (month.completed, month.saved_recaps, month.skipped_recaps) == (1, 0, 1)
    assert [(row.target, row.actual, row.gap) for row in month.allocation] == [
        (50, 75, 25),
        (50, 25, -25),
    ]
    assert draw_trends_chart(month).startswith(b"\x89PNG")

    week = build_trends(store, end.date(), "Week", end.date(), UTC)
    assert (week.start, week.end) == (date(2026, 11, 30), date(2026, 12, 6))
    assert week.daily_seconds == {midnight.date(): monday.seconds}
    assert week.completed == 1


def test_trends_count_only_first_verification_in_calendar_period(repo):
    study = StudyRepo(repo)
    study.import_roadmap(
        "synthetic",
        RoadmapInput(
            id="synthetic",
            name="Synthetic",
            nodes=[
                Topic(id="one", title="One", url="https://example.test/one"),
                Topic(id="two", title="Two", url="https://example.test/two"),
            ],
        ),
    )
    one, two = [row["id"] for row in study.nodes()]
    first = datetime(2026, 11, 30, 23, 59, tzinfo=UTC)
    second = first + timedelta(minutes=1)
    repo.conn.executemany(
        "INSERT INTO node_evidence (node_id, kind, recorded_at) VALUES (?, ?, ?)",
        [
            (one, "passed_quiz", to_db(first)),
            (one, "reviewed_artifact", to_db(second)),
            (two, "passed_quiz", to_db(second)),
        ],
    )
    store = DayRepo(repo)
    november = build_trends(store, first.date(), "Month", second.date(), UTC)
    december = build_trends(store, second.date(), "Month", second.date(), UTC)
    assert november.newly_verified == december.newly_verified == 1
    assert draw_trends_chart(december) is None


@pytest.mark.parametrize(("day", "hours"), [(date(2026, 3, 8), 23), (date(2026, 11, 1), 25)])
def test_trends_respect_local_dst_days(repo, day, hours):
    zone = ZoneInfo("America/New_York")
    start = datetime.combine(day, datetime.min.time(), zone).astimezone(UTC)
    end = start + timedelta(hours=hours)
    repo.conn.execute(
        "INSERT INTO claude_session VALUES ('session', '[]', 0, ?, NULL)", (to_db(start),)
    )
    repo.conn.execute(
        "INSERT INTO activity_interval VALUES ('session', ?, ?, ?, 'cached')",
        (to_db(start), to_db(end), day.isoformat()),
    )
    trends = build_trends(DayRepo(repo), day, "Week", day, zone)
    assert trends.daily_seconds[day] == hours * 3600
    assert trends.seconds == hours * 3600
