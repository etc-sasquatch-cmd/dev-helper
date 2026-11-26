"""Matplotlib diagrams from an already computed day snapshot."""

from datetime import timedelta
from io import BytesIO
from threading import Lock

from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure
from matplotlib.patches import Patch

from focusdeck.core.calendar import BUCKETS
from focusdeck.core.day import DayReport

CHARTS = {
    "timeline": "Day timeline",
    "work-types": "Work types by quarter",
    "allocation": "Target versus actual skill allocation",
    "roadmap": "Roadmap progress (current snapshot)",
}
COLORS = {
    "Build": "#2563eb",
    "Debug": "#c2410c",
    "Plan and Design": "#7c3aed",
    "Review": "#0f766e",
    "Study": "#16a34a",
    "Documentation": "#be185d",
    "Ops and Admin": "#92400e",
    "Other": "#64748b",
}
ROADMAP_STATES = {
    "not_started": "Not started",
    "learning": "Learning",
    "claimed": "Claimed",
    "verified": "Verified",
    "skipped": "Skipped",
}
_RENDER_LOCK = Lock()


def _timeline(fig: Figure, report: DayReport) -> bool:
    if not report.spans:
        return False
    ax = fig.subplots()
    origin, final = report.windows[0][0], report.windows[-1][1]
    length = (final - origin).total_seconds() / 60

    def minute(moment):
        return (moment - origin).total_seconds() / 60

    for start, end, bucket in report.windows:
        if bucket == "Off-hours":
            ax.axvspan(minute(start), minute(end), color="#e2e8f0")
    for part in report.spans:
        ax.barh(
            BUCKETS.index(part.quarter),
            part.seconds / 60,
            left=minute(part.start),
            color=COLORS.get(part.work_type, COLORS["Other"]),
            height=0.7,
            hatch="//" if part.estimated else None,
        )
    handles = [
        Patch(color=COLORS.get(key, COLORS["Other"]), label=key) for key in report.total.work
    ]
    ax.legend(handles=handles, loc="upper center", bbox_to_anchor=(0.5, -0.16), ncols=3)
    ticks = list(range(0, int(length), 180))
    ax.set(
        yticks=range(len(BUCKETS)),
        yticklabels=BUCKETS,
        xlim=(0, length),
        xticks=ticks,
        xticklabels=[
            (origin + timedelta(minutes=value)).astimezone(report.zone).strftime("%H:%M")
            for value in ticks
        ],
        xlabel="Local time",
    )
    ax.invert_yaxis()
    return True


def _work_types(fig: Figure, report: DayReport) -> bool:
    if not report.total.work:
        return False
    ax = fig.subplots()
    bottom = [0.0] * len(BUCKETS)
    for work_type, _ in report.total.work.most_common():
        minutes = [report.quarters[quarter].work[work_type] / 60 for quarter in BUCKETS]
        ax.bar(
            BUCKETS,
            minutes,
            bottom=bottom,
            label=work_type,
            color=COLORS.get(work_type, COLORS["Other"]),
        )
        bottom = [previous + added for previous, added in zip(bottom, minutes, strict=True)]
    ax.set_ylabel("Active minutes")
    ax.legend(loc="upper center", bbox_to_anchor=(0.5, -0.15), ncols=3)
    return True


def _allocation(fig: Figure, report: DayReport) -> bool:
    rows = [row for row in report.allocation if row.actual or row.target]
    if not rows:
        return False
    ax = fig.subplots()
    targets = [(i, row.target) for i, row in enumerate(rows) if row.target is not None]
    if targets:
        ax.barh(
            [i - 0.18 for i, _ in targets],
            [value for _, value in targets],
            height=0.34,
            label="Target",
            color="#475569",
        )
    ax.barh(
        [i + 0.18 for i in range(len(rows))],
        [row.actual or 0 for row in rows],
        height=0.34,
        label="Actual",
        color="#2563eb",
    )
    ax.set(
        yticks=range(len(rows)),
        yticklabels=[row.skill for row in rows],
        xlim=(0, 100),
        xlabel="Share of active time (%)",
    )
    ax.invert_yaxis()
    ax.legend()
    return True


def _roadmap(fig: Figure, report: DayReport) -> bool:
    if not report.roadmap:
        return False
    ax = fig.subplots()
    ax.barh(
        list(ROADMAP_STATES.values()),
        [report.roadmap[state] for state in ROADMAP_STATES],
        color=["#64748b", "#2563eb", "#7c3aed", "#15803d", "#b45309"],
    )
    ax.xaxis.get_major_locator().set_params(integer=True)
    ax.set_xlabel("Topics")
    ax.invert_yaxis()
    return True


_RENDERERS = {
    "timeline": _timeline,
    "work-types": _work_types,
    "allocation": _allocation,
    "roadmap": _roadmap,
}
EMPTY = {
    "timeline": "No activity recorded on this day.",
    "work-types": "No work-type minutes on this day.",
    "allocation": "No active time to compare.",
    "roadmap": "Import a roadmap to see progress.",
}


def draw_chart(report: DayReport, key: str) -> bytes | None:
    with _RENDER_LOCK:
        height = (
            min(12, max(3.4, 1.5 + len(report.allocation) * 0.35)) if key == "allocation" else 4
        )
        figure = Figure(figsize=(7, height), layout="constrained")
        if not _RENDERERS[key](figure, report):
            return None
        figure.suptitle(CHARTS[key], fontsize=13, fontweight="bold")
        buffer = BytesIO()
        FigureCanvasAgg(figure).print_png(buffer)
        return buffer.getvalue()
