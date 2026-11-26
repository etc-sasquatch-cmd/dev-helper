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


def build_trends(
    store: DayRepo, selected: date, period: Period, today: date, zone: tzinfo | None = None
) -> TrendsReport:
    if period == "Week":
        start = selected - timedelta(days=selected.weekday())
        end = start + timedelta(days=6)
    elif period == "Month":
        start = selected.replace(day=1)
        end = selected.replace(day=monthrange(selected.year, selected.month)[1])
    else:
        raise ValueError("Choose Week or Month.")

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
