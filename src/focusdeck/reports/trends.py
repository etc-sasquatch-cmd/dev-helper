"""Trend labels shared by the widget and the local exports."""

from focusdeck.core.trends import TrendsComparison, TrendsReport

COMPARISON_HEADERS = ("Metric", "Selected", "Previous", "Change")
NO_COMPARISON = "No completed days in the selected period to compare."
NO_BASELINE = "No activity or learning records in the previous date range."
ESTIMATED_TIME = "Placement of older paused focus time is estimated."
DailyRow = tuple[str, str, str, str]


def date_range(report: TrendsReport) -> str:
    return f"{report.start.isoformat()} to {report.end.isoformat()}"


def period_label(report: TrendsReport) -> str:
    suffix = ""
    if report.start <= report.today <= report.end:
        suffix = f" · In progress through {report.today.isoformat()}"
    elif report.today < report.start:
        suffix = " · Upcoming"
    return date_range(report) + suffix


def overview_lines(report: TrendsReport) -> list[str]:
    lines = [
        f"{report.seconds / 60:.1f} active minutes",
        f"{report.completed} completed focus blocks",
        f"{report.saved_recaps} recaps saved · {report.skipped_recaps} skipped",
        f"Newly verified topics {report.newly_verified}",
    ]
    if report.estimated:
        lines.append(ESTIMATED_TIME)
    return lines


def comparison_notes(comparison: TrendsComparison) -> list[str]:
    current, previous = comparison.current, comparison.previous
    lines = [f"Selected {date_range(current)}", f"Previous {date_range(previous)}"]
    if not _has_baseline(previous):
        lines.append(NO_BASELINE)
    if current.estimated or previous.estimated:
        lines.append(ESTIMATED_TIME)
    return lines


def _has_baseline(report: TrendsReport) -> bool:
    return bool(report.seconds or report.completed or report.newly_verified)


def comparison_rows(comparison: TrendsComparison) -> list[DailyRow]:
    current, previous = comparison.current, comparison.previous
    baseline = _has_baseline(previous)

    def row(label: str, value: float, prior: float, digits: int) -> DailyRow:
        change = f"{value - prior:+.{digits}f}" if baseline else "—"
        return label, f"{value:.{digits}f}", f"{prior:.{digits}f}" if baseline else "—", change

    rows = [row("Active minutes", current.seconds / 60, previous.seconds / 60, 1)]
    if len(current.daily_seconds) != len(previous.daily_seconds):
        rows.append(
            row(
                "Minutes per calendar day",
                current.seconds / 60 / len(current.daily_seconds),
                previous.seconds / 60 / len(previous.daily_seconds),
                1,
            )
        )
    rows.extend(
        [
            row("Completed focus blocks", current.completed, previous.completed, 0),
            row("Saved recaps", current.saved_recaps, previous.saved_recaps, 0),
            row("Skipped recaps", current.skipped_recaps, previous.skipped_recaps, 0),
            row("Newly verified topics", current.newly_verified, previous.newly_verified, 0),
        ]
    )
    if min(current.seconds, previous.seconds) >= 7200:
        for skill in sorted(current.skills.keys() | previous.skills.keys(), key=str.casefold):
            value = 100 * current.skills[skill] / current.seconds
            prior = 100 * previous.skills[skill] / previous.seconds
            rows.append(
                (f"{skill} share", f"{value:.1f}%", f"{prior:.1f}%", f"{value - prior:+.1f} pp")
            )
    return rows
