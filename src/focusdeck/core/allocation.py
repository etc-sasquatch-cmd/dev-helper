"""Compare tagged focus time with the targets in force when it was logged."""

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, time, timedelta

from focusdeck.db.study import StudyRepo


@dataclass(frozen=True)
class AllocationRow:
    skill: str
    target: float | None
    actual: float | None
    gap: float | None


def allocation(study: StudyRepo, now: datetime, days: int) -> tuple[float, list[AllocationRow]]:
    local_date = now.astimezone().date() - timedelta(days=days - 1)
    start = datetime.combine(local_date, time.min).astimezone()
    actual_seconds: dict[int | None, float] = defaultdict(float)
    target_seconds: dict[int, float] = defaultdict(float)
    total = 0.0
    covered = 0.0
    # Historical blocks retain elapsed time but have no pause intervals. Assign their
    # full active duration to the local date on which the block ended.
    for skill_id, seconds, ended_at in study.focus_time(start, now):
        actual_seconds[skill_id] += seconds
        total += seconds
        targets = study.targets_at(ended_at)
        if targets:
            covered += seconds
            for skill, percent in targets.items():
                target_seconds[skill] += seconds * percent / 100

    current_targets = study.targets_at(now)
    rows = []
    for skill in study.repo.list_skills():
        target = current_targets.get(skill.id, 0) if current_targets else None
        if total:
            target = 100 * target_seconds[skill.id] / total if covered == total else None
        actual = 100 * actual_seconds[skill.id] / total if total else None
        gap = (
            actual - target if total >= 7200 and actual is not None and target is not None else None
        )
        rows.append(AllocationRow(skill.name, target, actual, gap))
    if actual_seconds[None]:
        rows.append(AllocationRow("Untagged", None, 100 * actual_seconds[None] / total, None))
    return total, rows
