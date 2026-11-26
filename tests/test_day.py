import base64
import re
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from focusdeck.core.calendar import day_windows, parse_schedule
from focusdeck.core.day import build_day
from focusdeck.core.service import TimerService
from focusdeck.db.day import DayRepo
from focusdeck.db.repo import to_db
from focusdeck.db.study import StudyRepo
from focusdeck.reports.export import export_day
from focusdeck.roadmaps.importer import RoadmapInput, Topic

DAY = date(2026, 11, 9)


def moment(hour, minute=0, *, day=DAY):
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=UTC)


def record_session(repo, identifier, start, end):
    repo.conn.execute(
        "INSERT INTO claude_session VALUES (?, '[]', 0, ?, NULL)",
        (identifier, to_db(start)),
    )
    repo.conn.execute(
        "INSERT INTO activity_interval VALUES (?, ?, ?, ?, 'cached')",
        (identifier, to_db(start), to_db(end), start.date().isoformat()),
    )


def test_report_deduplicates_paused_focus_and_concurrent_sessions(repo, clock, skill_id):
    store = DayRepo(repo)
    service = TimerService(repo, clock)
    start = moment(9, 45)
    clock.advance((start - clock.now()).total_seconds())
    service.start(skill_id, "Study")
    clock.advance(minutes=10)
    service.pause()
    clock.advance(minutes=10)
    service = TimerService(repo, clock)  # A paused state and its closed interval survive restart.
    service.resume()
    clock.advance(minutes=15)
    service.tick()
    record_session(repo, "first", moment(9, 50), moment(10, 10))
    record_session(repo, "second", moment(9, 55), moment(10, 25))

    before = build_day(store, DAY, UTC)
    assert before.seconds == 40 * 60
    assert before.quarters["Q1"].work == {"Study": 10 * 60, "Other": 5 * 60}
    assert before.quarters["Q2"].work == {"Study": 15 * 60, "Other": 10 * 60}
    assert before.quarters["Q2"].completed == 1
    assert not before.estimated

    store.save_classification("second", DAY, "Review", skill_id)
    corrected = build_day(store, DAY, UTC)
    assert corrected.total.work == {"Study": 25 * 60, "Review": 15 * 60}
    assert corrected.total.skills == {"Python": 40 * 60}
    store.save_classification("second", DAY, None, None)
    assert build_day(store, DAY, UTC).total.work == before.total.work


def test_focus_recovery_and_legacy_estimate(repo, clock, skill_id):
    store = DayRepo(repo)
    service = TimerService(repo, clock)
    start = moment(23, 50)
    clock.advance((start - clock.now()).total_seconds())
    service.start(skill_id, "Study")
    clock.advance(minutes=60)
    TimerService(repo, clock).tick()
    tomorrow = DAY + timedelta(days=1)
    assert build_day(store, DAY, UTC).seconds == 10 * 60
    after_midnight = build_day(store, tomorrow, UTC)
    assert after_midnight.seconds == 15 * 60
    assert after_midnight.total.completed == 1

    old = repo.insert_running_block(
        phase="focus",
        skill_id=skill_id,
        work_type="Build",
        planned_seconds=20 * 60,
        started_at=moment(10, 50),
        ends_at=moment(11, 10),
    )
    repo.finish_block(old, status="completed", ended_at=moment(11, 30), elapsed_seconds=20 * 60)
    repo.conn.execute("DELETE FROM pomodoro_run WHERE block_id = ?", (old,))
    report = build_day(store, DAY, UTC)
    assert report.estimated
    assert report.total.work["Build"] == 20 * 60


def test_calendar_uses_real_local_boundaries_on_dst_days():
    zone = ZoneInfo("America/New_York")
    schedule = parse_schedule(
        [("01:30", "02:30"), ("08:00", "10:00"), ("13:00", "15:00"), ("15:00", "17:00")]
    )
    for day, day_hours, first_quarter in [(date(2026, 3, 8), 23, 30), (date(2026, 11, 1), 25, 90)]:
        spans = list(day_windows(day, schedule, zone))
        assert spans[-1][1] - spans[0][0] == timedelta(hours=day_hours)
        assert sum(
            (end - start for start, end, quarter in spans if quarter == "Q1"),
            timedelta(),
        ) == timedelta(minutes=first_quarter)
    with pytest.raises(ValueError, match="overlap"):
        parse_schedule(
            [("08:00", "10:00"), ("09:00", "12:00"), ("13:00", "15:00"), ("15:00", "17:00")]
        )


def test_report_uses_target_versions_and_exports_four_diagrams(repo, skill_id, tmp_path):
    store, study = DayRepo(repo), StudyRepo(repo)
    sql = repo.create_skill("SQL")
    study.save_targets({skill_id: 100, sql: 0}, moment(8))
    study.save_targets({skill_id: 0, sql: 100}, moment(9))
    study.import_roadmap(
        "synthetic",
        RoadmapInput(
            id="test",
            name="Backend",
            nodes=[Topic(id="sql", title="SQL", url="https://example.test/sql")],
        ),
    )
    node_id = study.nodes()[0]["id"]
    block_id = repo.insert_running_block(
        phase="focus",
        skill_id=skill_id,
        work_type="Study",
        node_id=node_id,
        planned_seconds=2 * 3600,
        started_at=moment(8),
        ends_at=moment(10),
    )
    repo.finish_block(block_id, status="completed", ended_at=moment(10), elapsed_seconds=2 * 3600)
    repo.save_recap(block_id, "Reviewed <script>alert(1)</script> safely")
    report = build_day(store, DAY, UTC)
    assert report.seconds == 7200
    assert [(row.target, row.actual, row.gap) for row in report.allocation] == [
        (50, 100, 50),
        (50, 0, -50),
    ]
    assert report.roadmap == {"claimed": 1}
    assert report.quarters["Q2"].evidence == [
        "Backend · SQL · Time logged (E0) → Learning",
        "Backend · SQL · Recap (E1) → Claimed",
    ]

    markdown, html = export_day(report, tmp_path)
    assert markdown.read_text(encoding="utf-8").count(".png)") == 4
    content = html.read_text(encoding="utf-8")
    encoded_images = re.findall(r"data:image/png;base64,([^\" ]+)", content)
    assert len(encoded_images) == 4
    assert all(base64.b64decode(image).startswith(b"\x89PNG") for image in encoded_images)
    assert "&lt;script&gt;" in content and "<script>" not in content


def test_empty_report_exports_explicit_empty_states(repo, tmp_path):
    report = build_day(DayRepo(repo), DAY, UTC)
    markdown, html = export_day(report, tmp_path)
    assert report.seconds == 0
    assert "No activity recorded on this day." in markdown.read_text(encoding="utf-8")
    assert "data:image/png" not in html.read_text(encoding="utf-8")
