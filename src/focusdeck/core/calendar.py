"""Local reporting dates and quarter windows, expressed as UTC intervals."""

from datetime import UTC, date, datetime, time, timedelta, tzinfo

QUARTERS = (
    ("Q1", time(8), time(10)),
    ("Q2", time(10), time(12)),
    ("Q3", time(13), time(15)),
    ("Q4", time(15), time(17)),
)
BUCKETS = ("Q1", "Q2", "Q3", "Q4", "Off-hours")


def parse_schedule(values: list[tuple[str, str]]):
    if len(values) != len(QUARTERS):
        raise ValueError("Enter four quarters.")
    hours = []
    previous = time.min
    for (name, _, _), (opens, closes) in zip(QUARTERS, values, strict=True):
        try:
            start = datetime.strptime(opens, "%H:%M").time()
            end = datetime.strptime(closes, "%H:%M").time()
        except ValueError:
            raise ValueError("Use 24-hour HH:MM times.") from None
        if not previous <= start < end:
            raise ValueError("Quarter hours must be in order and cannot overlap.")
        hours.append((name, start, end))
        previous = end
    return tuple(hours)


def _utc_candidates(wall: datetime, zone: tzinfo | None) -> set[datetime]:
    return {wall.replace(tzinfo=zone, fold=fold).astimezone(UTC) for fold in (0, 1)}


def day_windows(day: date, schedule=QUARTERS, zone: tzinfo | None = None):
    midnight = datetime.combine(day, time.min)
    tomorrow = midnight + timedelta(days=1)

    def boundary(wall: datetime) -> datetime:
        options = _utc_candidates(wall, zone)
        return min(t for t in options if t.astimezone(zone).replace(tzinfo=None) >= wall)

    start, end = boundary(midnight), boundary(tomorrow)
    points = {start, end}
    for _, opens, closes in schedule:
        for local_time in (opens, closes):
            wall = datetime.combine(day, local_time)
            points.update(
                t
                for t in _utc_candidates(wall, zone)
                if start < t < end and t.astimezone(zone).replace(tzinfo=None) == wall
            )

    # An offset change can cross an edited quarter boundary even if that wall time never occurs.
    cursor = start
    while cursor < end:
        following = min(cursor + timedelta(hours=1), end)
        offset = cursor.astimezone(zone).utcoffset()
        if following.astimezone(zone).utcoffset() != offset:
            low, high = cursor, following
            while high - low > timedelta(seconds=1):
                middle = low + timedelta(seconds=int((high - low).total_seconds()) // 2)
                if middle.astimezone(zone).utcoffset() == offset:
                    low = middle
                else:
                    high = middle
            points.add(high)
        cursor = following

    ordered = sorted(points)
    for earlier, later in zip(ordered, ordered[1:], strict=False):
        wall_time = earlier.astimezone(zone).time()
        quarter = next(
            (name for name, opens, closes in schedule if opens <= wall_time < closes),
            "Off-hours",
        )
        yield earlier, later, quarter
