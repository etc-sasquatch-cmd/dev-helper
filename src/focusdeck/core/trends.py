"""Calendar-period trends using the same activity attribution as Day reports."""

from calendar import monthrange
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, timedelta, tzinfo
from typing import Literal

from focusdeck.core.allocation import AllocationRow
from focusdeck.core.day import active_allocation, build_day
from focusdeck.db.day import DayRepo

Period = Literal["Week", "Month"]


@dataclass
class TrendsReport:
    start: date
    end: date
    today: date
    daily_seconds: dict[date, float] = field(default_factory=dict)
    work: Counter[str] = field(default_factory=Counter)
    skills: Counter[str] = field(default_factory=Counter)
    completed: int = 0
    saved_recaps: int = 0
    skipped_recaps: int = 0
    newly_verified: int = 0
    estimated: bool = False
    allocation: list[AllocationRow] = field(default_factory=list)

    @property
    def seconds(self) -> float:
        return sum(self.daily_seconds.values())


@dataclass(frozen=True)
class TrendsComparison:
    current: TrendsReport
    previous: TrendsReport


def _period_bounds(selected: date, period: Period) -> tuple[date, date]:
    if period == "Week":
        start = selected - timedelta(days=selected.weekday())
        return start, start + timedelta(days=6)
    if period == "Month":
        return selected.replace(day=1), selected.replace(
            day=monthrange(selected.year, selected.month)[1]
        )
    raise ValueError("Choose Week or Month.")


def _build_range(
    store: DayRepo, start: date, end: date, today: date, zone: tzinfo | None
) -> TrendsReport:
    trends = TrendsReport(start, end, today)
    days = [
        build_day(store, start + timedelta(days=offset), zone)
        for offset in range((min(end, today) - start).days + 1)
    ]
    for day in days:
        trends.daily_seconds[day.day] = day.seconds
        trends.work.update(day.total.work)
        trends.skills.update(day.total.skills)
        trends.completed += day.total.completed
        trends.saved_recaps += len(day.total.recaps)
        trends.skipped_recaps += day.total.skipped_recap_count
        trends.newly_verified += day.total.verified_count
        trends.estimated |= day.estimated

    if trends.seconds:
        trends.allocation = active_allocation(
            store,
            [span for day in days for span in day.spans],
            days[0].windows[0][0],
            days[-1].windows[-1][1],
            {skill.id: skill.name for skill in store.repo.list_skills()},
        )
    return trends


def build_trends(
    store: DayRepo, selected: date, period: Period, today: date, zone: tzinfo | None = None
) -> TrendsReport:
    start, end = _period_bounds(selected, period)
    return _build_range(store, start, end, today, zone)


def compare_trends(
    store: DayRepo, report: TrendsReport, period: Period, zone: tzinfo | None = None
) -> TrendsComparison | None:
    if report.today <= report.start:
        return None
    previous_start, previous_end = _period_bounds(report.start - timedelta(days=1), period)
    if report.today > report.end:
        current = report
    else:
        days = min((report.today - report.start).days, (previous_end - previous_start).days + 1)
        offset = timedelta(days=days - 1)
        previous_end = previous_start + offset
        current = _build_range(store, report.start, report.start + offset, report.today, zone)
    previous = _build_range(store, previous_start, previous_end, report.today, zone)
    return TrendsComparison(current, previous)
