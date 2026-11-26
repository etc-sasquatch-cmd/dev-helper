"""Build the totals used by the Day view, diagrams, and exports."""

from collections import Counter, defaultdict
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta, tzinfo
from heapq import heappop, heappush

from focusdeck.core.allocation import AllocationRow
from focusdeck.core.calendar import BUCKETS, day_windows
from focusdeck.db.day import DayRepo
from focusdeck.db.repo import from_db
from focusdeck.db.study import StudyRepo

EVIDENCE_NAMES = {
    "time": ("Time logged (E0)", "Learning"),
    "recap": ("Recap (E1)", "Claimed"),
    "passed_quiz": ("Passed quiz (E2)", "Verified"),
    "reviewed_artifact": ("Reviewed artifact (E2)", "Verified"),
}


@dataclass(frozen=True)
class Span:
    start: datetime
    end: datetime
    source_id: str
    work_type: str
    skill_id: int | None
    priority: int
    estimated: bool = False
    quarter: str = ""

    @property
    def seconds(self) -> float:
        return (self.end - self.start).total_seconds()


@dataclass
class QuarterSummary:
    work: Counter[str] = field(default_factory=Counter)
    skills: Counter[str] = field(default_factory=Counter)
    completed: int = 0
    recaps: list[str] = field(default_factory=list)
    evidence: list[str] = field(default_factory=list)
    skipped_recap_count: int = 0


@dataclass(frozen=True)
class DayReport:
    day: date
    zone: tzinfo | None
    windows: tuple[tuple[datetime, datetime, str], ...]
    spans: list[Span]
    quarters: dict[str, QuarterSummary]
    total: QuarterSummary
    allocation: list[AllocationRow]
    roadmap: Counter[str]
    sessions: dict[str, tuple[str | None, int | None]]
    estimated: bool

    @property
    def seconds(self) -> float:
        return sum(self.total.work.values())


def _merge(spans: list[Span], windows) -> list[Span]:
    """Choose one owner per instant. Earlier starts and stable IDs break equal priorities."""
    starts: dict[datetime, list[int]] = defaultdict(list)
    edges = {instant for a, b, _ in windows for instant in (a, b)}
    for index, span in enumerate(spans):
        if span.start < span.end:
            starts[span.start].append(index)
            edges.update((span.start, span.end))

    timeline: list[Span] = []
    active: list[tuple[int, datetime, str, int]] = []
    boundaries = sorted(edges)
    quarter_index = 0
    for start, end in zip(boundaries, boundaries[1:], strict=False):
        for index in starts.get(start, ()):
            span = spans[index]
            heappush(active, (span.priority, span.start, span.source_id, index))
        while active and spans[active[0][3]].end <= start:
            heappop(active)
        while start >= windows[quarter_index][1]:
            quarter_index += 1
        if active:
            span = spans[active[0][3]]
            part = replace(span, start=start, end=end, quarter=windows[quarter_index][2])
            if (
                timeline
                and timeline[-1].end == start
                and replace(timeline[-1], start=start, end=end) == part
            ):
                timeline[-1] = replace(timeline[-1], end=end)
            else:
                timeline.append(part)
    return timeline


def _focus_spans(store: DayRepo, block) -> list[Span]:
    start, end = from_db(block["started_at"]), from_db(block["ended_at"])
    assert start is not None and end is not None
    metadata = {
        "source_id": str(block["id"]),
        "work_type": block["work_type"] or "Other",
        "skill_id": block["skill_id"],
        "priority": 0 if block["skill_id"] is not None and block["work_type"] else 3,
    }
    spans = []
    for run in store.runs(block["id"]):
        a, b = from_db(run["started_at"]), from_db(run["ended_at"])
        if a is not None and b is not None and a < b:
            spans.append(Span(a, b, **metadata))

    # Legacy runs can be missing all active intervals or only the portion before an upgrade.
    missing = (block["elapsed_seconds"] or 0) - sum(span.seconds for span in spans)
    if missing > 0.000001:
        known_from = spans[0].start if spans else end
        if not spans and abs((end - start).total_seconds() - missing) <= 0.000001:
            spans.append(Span(start, end, **metadata))
        else:
            estimated_start = max(start, known_from - timedelta(seconds=missing))
            spans.insert(0, Span(estimated_start, known_from, estimated=True, **metadata))
    return spans


def _allocation(store: DayRepo, spans: list[Span], windows, skills) -> list[AllocationRow]:
    if not spans:
        return []
    study = StudyRepo(store.repo)
    start, end = windows[0][0], windows[-1][1]
    changes = [from_db(row["effective_at"]) for row in store.target_changes(start, end)]
    versions = [(point, study.targets_at(point)) for point in (start, *changes)]
    actual: Counter[int | None] = Counter()
    weighted: Counter[int] = Counter()
    covered = 0.0
    version = 0
    for span in spans:
        actual[span.skill_id] += span.seconds
        cursor = span.start
        while cursor < span.end:
            while version + 1 < len(versions) and versions[version + 1][0] <= cursor:
                version += 1
            following = versions[version + 1][0] if version + 1 < len(versions) else span.end
            stop = min(span.end, following)
            targets = versions[version][1]
            if targets:
                seconds = (stop - cursor).total_seconds()
                covered += seconds
                for skill_id, percent in targets.items():
                    weighted[skill_id] += seconds * percent
            cursor = stop
    total = sum(actual.values())
    rows = []
    for skill_id, name in skills.items():
        target = weighted[skill_id] / total if abs(covered - total) < 0.000001 else None
        share = 100 * actual[skill_id] / total
        gap = share - target if target is not None and total >= 7200 else None
        rows.append(AllocationRow(name, target, share, gap))
    if actual[None]:
        rows.append(AllocationRow("Unassigned", None, 100 * actual[None] / total, None))
    return rows


def build_day(
    store: DayRepo,
    day: date,
    zone: tzinfo | None = None,
    *,
    allowed_sessions: set[str] | None = None,
) -> DayReport:
    windows = tuple(day_windows(day, store.schedule(), zone))
    start, end = windows[0][0], windows[-1][1]
    skills = {skill.id: skill.name for skill in store.repo.list_skills()}
    quarters = {name: QuarterSummary() for name in BUCKETS}
    total = QuarterSummary()
    spans: list[Span] = []
    sessions = {}

    def bucket(moment: datetime) -> QuarterSummary:
        return quarters[next(name for a, b, name in windows if a <= moment < b)]

    for block in store.focus_blocks(start, end):
        spans.extend(_focus_spans(store, block))
        finished = from_db(block["ended_at"])
        if block["status"] == "completed" and start <= finished < end:
            for summary in (total, bucket(finished)):
                summary.completed += 1
                if block["recap"]:
                    summary.recaps.append(block["recap"])
                elif block["recap_skipped"]:
                    summary.skipped_recap_count += 1

    for row in store.claude_intervals(day, start, end):
        session_id, work, skill = row["session_id"], row["work_type"], row["skill_id"]
        if allowed_sessions is not None and session_id not in allowed_sessions:
            continue
        sessions[session_id] = (row["manual_work_type"], skill)
        spans.append(
            Span(
                from_db(row["started_at"]),
                from_db(row["ended_at"]),
                session_id,
                work or "Other",
                skill,
                1 if row["manual_work_type"] else 2,
            )
        )

    clipped = [
        replace(span, start=max(span.start, start), end=min(span.end, end))
        for span in spans
        if span.start < end and span.end > start
    ]
    merged = _merge(clipped, windows)
    for part in merged:
        for summary in (total, quarters[part.quarter]):
            summary.work[part.work_type] += part.seconds
            summary.skills[skills.get(part.skill_id, "Unassigned")] += part.seconds

    for row in store.evidence(start, end):
        label, state = EVIDENCE_NAMES[row["kind"]]
        movement = f" → {state}" if row["first_level"] else ""
        line = f"{row['roadmap']} · {row['title']} · {label}{movement}"
        total.evidence.append(line)
        bucket(from_db(row["recorded_at"])).evidence.append(line)

    roadmap = Counter(node["state"] for node in StudyRepo(store.repo).nodes())
    return DayReport(
        day,
        zone,
        windows,
        merged,
        quarters,
        total,
        _allocation(store, merged, windows, skills),
        roadmap,
        sessions,
        any(span.estimated for span in clipped),
    )
