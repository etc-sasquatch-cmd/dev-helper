"""Write portable Markdown and self-contained HTML day reports."""

import base64
import re
from html import escape
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

from focusdeck.core.day import DayReport, QuarterSummary
from focusdeck.reports.charts import CHARTS, EMPTY, draw_chart


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


def allocation_lines(report: DayReport) -> list[str]:
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
    output = folder / "reports"
    output.mkdir(parents=True, exist_ok=True)
    charts = {key: draw_chart(report, key) for key in CHARTS}
    destination = output / f"{report.day.isoformat()}-{uuid4().hex[:12]}"
    with TemporaryDirectory(prefix=".report-", dir=output) as temporary:
        staged = Path(temporary) / "report"
        staged.mkdir()
        _write_report(report, staged, charts)
        staged.rename(destination)
    return destination / "report.md", destination / "report.html"


def _write_report(report: DayReport, destination: Path, charts: dict[str, bytes | None]) -> None:
    title = f"FocusDeck daily report · {report.day.isoformat()}"
    overview = f"{report.seconds / 60:.1f} active minutes"
    sections = [
        ("Day total", [overview, *summary_lines(report.total)]),
        *((name, summary_lines(summary)) for name, summary in report.quarters.items()),
        ("Skill allocation", allocation_lines(report)),
    ]
    if report.estimated:
        sections[0][1].append("Placement of older paused focus time is estimated.")
    markdown = [f"# {title}"]
    html = [
        '<!doctype html><html lang="en"><meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        f"<title>{escape(title)}</title>",
        "<style>body{font:16px system-ui;max-width:58rem;margin:2rem auto;padding:0 1rem}"
        "h2{margin-top:2rem}img{width:min(100%,720px);height:auto}li{margin:.32rem 0}</style>",
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
    for key, name in CHARTS.items():
        markdown.extend(["", f"## {name}", ""])
        html.append(f"<h2>{escape(name)}</h2>")
        if data := charts[key]:
            filename = f"{key}.png"
            (destination / filename).write_bytes(data)
            markdown.append(f"![{name}]({filename})")
            inline = base64.b64encode(data).decode("ascii")
            html.append(f'<img alt="{escape(name)}" src="data:image/png;base64,{inline}">')
        else:
            markdown.append(EMPTY[key])
            html.append(f"<p>{escape(EMPTY[key])}</p>")
    html.append("</main></html>")
    markdown_path, html_path = destination / "report.md", destination / "report.html"
    markdown_path.write_text("\n".join(markdown) + "\n", encoding="utf-8")
    html_path.write_text("\n".join(html) + "\n", encoding="utf-8")
