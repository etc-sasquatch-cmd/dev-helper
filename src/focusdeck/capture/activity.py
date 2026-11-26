"""Derive active intervals and split them at local calendar boundaries."""

from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta, tzinfo

from focusdeck.core.calendar import QUARTERS


@dataclass(frozen=True)
class Interval:
    start: datetime
    end: datetime
    local_date: date
    quarter: str


def active_intervals(
    timestamps: Iterable[datetime],
    gap_minutes: int,
    zone: tzinfo | None = None,
) -> Iterator[Interval]:
    ordered = sorted({timestamp.astimezone(UTC) for timestamp in timestamps})
    for start, end in zip(ordered, ordered[1:], strict=False):
        if end - start > timedelta(minutes=gap_minutes):
            continue
        cursor = start
        while cursor < end:
            local = cursor.astimezone(zone)
            day = local.date()
            local_time = local.time()
            quarter = "Off-hours"
            next_time = None
            for name, opens, closes in QUARTERS:
                if local_time < opens:
                    next_time = opens
                    break
                if local_time < closes:
                    quarter, next_time = name, closes
                    break
            if next_time is None:
                boundary = datetime.combine(day + timedelta(days=1), time.min)
            else:
                boundary = datetime.combine(day, next_time)
            # With no injected timezone, interpret each boundary in the OS timezone
            # for its own date, so the offset can change between events.
            if zone is not None:
                boundary = boundary.replace(tzinfo=zone)
            segment_end = min(end, boundary.astimezone(UTC))
            if segment_end <= cursor:
                segment_end = end
            yield Interval(cursor, segment_end, day, quarter)
            cursor = segment_end
