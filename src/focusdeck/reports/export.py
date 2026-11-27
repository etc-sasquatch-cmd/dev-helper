"""Write portable Markdown and self-contained HTML day reports."""

import base64
import re
from html import escape
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

from focusdeck.core.day import DayReport, QuarterSummary
from focusdeck.core.trends import TrendsComparison, TrendsReport
from focusdeck.reports.charts import CHARTS, EMPTY, draw_chart
from focusdeck.reports.trends import (
    COMPARISON_HEADERS,
    NO_COMPARISON,
    comparison_notes,
    comparison_rows,
    overview_lines,
    period_label,
)


def summary_lines(summary: QuarterSummary) -> list[str]:
    work = [f"{name} · {seconds / 60:.1f} min" for name, seconds in summary.work.most_common()]
    skills = [f"{name} · {seconds / 60:.1f} min" for name, seconds in summary.skills.most_common()]
    return [
        "Work done: " + (", ".join(work) if work else "No activity recorded."),
        "Skill time: " + (", ".join(skills) if skills else "No skill time recorded."),
        f"Completed Pomodoros: {summary.completed}",
        "Learning recap: "
        + (" / ".join(summary.recaps) if summary.recaps else "No recap recorded."),
        "Roadmap evidence: "
        + (" / ".join(summary.evidence) if summary.evidence else "None recorded."),
    ]


def allocation_lines(report: DayReport | TrendsReport) -> list[str]:
    def percent(value):
        return f"{value:.1f}%" if value is not None else "—"

    lines = []
    if report.seconds < 7200 and report.allocation:
        lines.append("Gaps need at least 120 logged minutes.")
    for row in report.allocation:
        gap = f"{row.gap:+.1f} pp" if row.gap is not None else "—"
        lines.append(
            f"{row.skill} · Target {percent(row.target)} · Actual {percent(row.actual)} · Gap {gap}"
        )
    return lines or ["No active time to compare."]


def _markdown(text: str) -> str:
    return re.sub(r"([\\`*_{}\[\]()<>!#|])", r"\\\1", " ".join(text.split()))


def export_day(report: DayReport, folder: Path) -> tuple[Path, Path]:
    title = f"FocusDeck daily report · {report.day.isoformat()}"
    overview = f"{report.seconds / 60:.1f} active minutes"
    sections = [
        ("Day total", [overview, *summary_lines(report.total)]),
        *((name, summary_lines(summary)) for name, summary in report.quarters.items()),
        ("Skill allocation", allocation_lines(report)),
    ]
    if report.estimated:
        sections[0][1].append("Placement of older paused focus time is estimated.")
    charts = {key: (name, draw_chart(report, key), EMPTY[key]) for key, name in CHARTS.items()}
    return _export(folder, report.day.isoformat(), title, sections, charts)


def export_trends(
    report: TrendsReport,
    folder: Path,
    png: bytes | None,
    comparison: TrendsComparison | None = None,
    compare_requested: bool = False,
) -> tuple[Path, Path]:
    title = f"FocusDeck Trends · {period_label(report)}"
    sections = [
        ("Overview", overview_lines(report)),
        (
            "Work types",
            [f"{name} · {seconds / 60:.1f} min" for name, seconds in report.work.most_common()]
            or ["No activity recorded."],
        ),
        (
            "Skill time",
            [f"{name} · {seconds / 60:.1f} min" for name, seconds in report.skills.most_common()]
            or ["No skill time recorded."],
        ),
        ("Skill allocation", allocation_lines(report)),
        (
            "Daily active minutes",
            [
                f"{day.isoformat()} · {seconds / 60:.1f} min"
                for day, seconds in report.daily_seconds.items()
            ]
            or ["No days recorded."],
        ),
    ]
    if comparison:
        sections.append(("Period comparison", comparison_notes(comparison)))
    elif compare_requested:
        sections.append(("Period comparison", [NO_COMPARISON]))
    charts = {
        "active-minutes": ("Active minutes by day", png, "No active time recorded for this period.")
    }
    return _export(
        folder, f"trends-{report.start}-{report.end}", title, sections, charts, comparison
    )


def _export(folder, prefix, title, sections, charts, comparison=None) -> tuple[Path, Path]:
    output = folder / "reports"
    output.mkdir(parents=True, exist_ok=True)
    destination = output / f"{prefix}-{uuid4().hex[:12]}"
    with TemporaryDirectory(prefix=".report-", dir=output) as temporary:
        staged = Path(temporary) / "report"
        staged.mkdir()
        _write_report(staged, title, sections, charts, comparison)
        staged.rename(destination)
    return destination / "report.md", destination / "report.html"


def _write_report(destination, title, sections, charts, comparison) -> None:
    markdown = [f"# {title}"]
    html = [
        '<!doctype html><html lang="en"><meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        f"<title>{escape(title)}</title>",
        "<style>body{font:16px system-ui;max-width:58rem;margin:2rem auto;padding:0 1rem}"
        "h2{margin-top:2rem}img{width:min(100%,720px);height:auto}li{margin:.32rem 0}"
        "th,td{padding:.35rem .7rem;text-align:left}table{border-collapse:collapse}"
        "th,td{border-bottom:1px solid #ddd}</style>",
        f"<main><h1>{escape(title)}</h1>",
    ]
    for name, lines in sections:
        markdown.extend(["", f"## {name}", "", *(f"- {_markdown(line)}" for line in lines)])
        html.extend(
            [
                f"<h2>{escape(name)}</h2><ul>",
                *(f"<li>{escape(line)}</li>" for line in lines),
                "</ul>",
            ]
        )
    if comparison:
        rows = comparison_rows(comparison)
        markdown.extend(
            ["", "| " + " | ".join(COMPARISON_HEADERS) + " |", "| --- | --- | --- | --- |"]
        )
        markdown.extend("| " + " | ".join(map(_markdown, row)) + " |" for row in rows)
        html.append(
            "<table><thead><tr>"
            + "".join(f'<th scope="col">{name}</th>' for name in COMPARISON_HEADERS)
            + "</tr></thead><tbody>"
        )
        html.extend(
            "<tr>" + "".join(f"<td>{escape(value)}</td>" for value in row) + "</tr>" for row in rows
        )
        html.append("</tbody></table>")
    for key, (name, data, empty) in charts.items():
        markdown.extend(["", f"## {name}", ""])
        html.append(f"<h2>{escape(name)}</h2>")
        if data:
            filename = f"{key}.png"
            (destination / filename).write_bytes(data)
            markdown.append(f"![{name}]({filename})")
            inline = base64.b64encode(data).decode("ascii")
            html.append(f'<img alt="{escape(name)}" src="data:image/png;base64,{inline}">')
        else:
            markdown.append(empty)
            html.append(f"<p>{escape(empty)}</p>")
    html.append("</main></html>")
    markdown_path, html_path = destination / "report.md", destination / "report.html"
    markdown_path.write_text("\n".join(markdown) + "\n", encoding="utf-8")
    html_path.write_text("\n".join(html) + "\n", encoding="utf-8")
