from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from focusdeck.core.day import build_day
from focusdeck.core.trends import build_trends, compare_trends
from focusdeck.db.day import DayRepo
from focusdeck.db.repo import to_db
from focusdeck.db.study import StudyRepo
from focusdeck.reports.charts import draw_trends_chart
from focusdeck.reports.export import export_trends
from focusdeck.reports.trends import NO_BASELINE, comparison_notes, comparison_rows
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


def test_partial_month_comparison_caps_both_ranges_at_shorter_month(repo):
    store = DayRepo(repo)
    today = date(2026, 3, 31)
    report = build_trends(store, today, "Month", today, UTC)
    comparison = compare_trends(store, report, "Month", UTC)
    assert len(report.daily_seconds) == 31
    assert (comparison.current.start, comparison.current.end) == (
        date(2026, 3, 1),
        date(2026, 3, 28),
    )
    assert (comparison.previous.start, comparison.previous.end) == (
        date(2026, 2, 1),
        date(2026, 2, 28),
    )
    assert NO_BASELINE in comparison_notes(comparison)
    assert comparison_rows(comparison)[0] == ("Active minutes", "0.0", "—", "—")
    monday = date(2026, 12, 28)
    assert compare_trends(store, build_trends(store, monday, "Week", monday), "Week") is None


def test_partial_week_comparison_uses_local_days_across_year_and_dst(repo):
    store = DayRepo(repo)
    for today in (date(2026, 1, 1), date(2026, 3, 10)):
        zone = ZoneInfo("America/New_York")
        report = build_trends(store, today, "Week", today, zone)
        comparison = compare_trends(store, report, "Week", zone)
        assert comparison.current.end == today - timedelta(days=1)
        assert comparison.previous.start == comparison.current.start - timedelta(days=7)
        assert list(comparison.previous.daily_seconds) == [
            day - timedelta(days=7) for day in comparison.current.daily_seconds
        ]


def test_complete_month_comparison_and_exports_include_rates_and_keep_recaps_local(repo, tmp_path):
    skill = repo.create_skill("Synthetic <skill>")
    for month, minutes in ((12, 180), (1, 240), (2, 280)):
        year = 2025 if month == 12 else 2026
        start = datetime(year, month, 2, 8, tzinfo=UTC)
        end = start + timedelta(minutes=minutes)
        block = repo.insert_running_block(
            phase="focus",
            skill_id=skill,
            work_type="Study",
            planned_seconds=minutes * 60,
            started_at=start,
            ends_at=end,
        )
        repo.finish_block(block, status="completed", ended_at=end, elapsed_seconds=minutes * 60)
        repo.save_recap(block, "PRIVATE RECAP TEXT")
    store = DayRepo(repo)
    today = date(2026, 3, 1)
    january = build_trends(store, date(2026, 1, 20), "Month", today, UTC)
    assert compare_trends(store, january, "Month", UTC).previous.start == date(2025, 12, 1)
    report = build_trends(store, date(2026, 2, 8), "Month", today, UTC)
    comparison = compare_trends(store, report, "Month", UTC)
    assert comparison_rows(comparison)[:2] == [
        ("Active minutes", "280.0", "240.0", "+40.0"),
        ("Minutes per calendar day", "10.0", "7.7", "+2.3"),
    ]
    assert comparison_rows(comparison)[-1] == (
        "Synthetic <skill> share",
        "100.0%",
        "100.0%",
        "+0.0 pp",
    )
    png = draw_trends_chart(report)
    markdown, html = export_trends(report, tmp_path, png, comparison, True)
    assert "| Active minutes | 280.0 | 240.0 | +40.0 |" in markdown.read_text(encoding="utf-8")
    assert (markdown.parent / "active-minutes.png").read_bytes() == png
    html_text = html.read_text(encoding="utf-8")
    assert "<td>Synthetic &lt;skill&gt; share</td>" in html_text
    assert '<img alt="Active minutes by day" src="data:image/png;base64,' in html_text
    for path in (markdown, html):
        assert "PRIVATE RECAP TEXT" not in path.read_text(encoding="utf-8")
    plain_md, plain_html = export_trends(report, tmp_path, png)
    assert plain_md.parent != markdown.parent
    assert "Period comparison" not in plain_html.read_text(encoding="utf-8")
