"""Day view and local report exports for the native widget."""

import base64
import logging
from contextlib import closing
from datetime import date

from nicegui import run, ui

from focusdeck.core.clock import Clock
from focusdeck.core.day import DayReport, build_day
from focusdeck.core.timer import WORK_TYPES
from focusdeck.core.trends import TrendsReport, build_trends
from focusdeck.db.connection import connect
from focusdeck.db.day import DayRepo
from focusdeck.db.repo import Repo
from focusdeck.reports.charts import CHARTS, EMPTY, draw_chart, draw_trends_chart
from focusdeck.reports.export import allocation_lines, export_day, summary_lines

log = logging.getLogger(__name__)
DIAGRAM_OPTIONS = {
    "timeline": "Day timeline",
    "work-types": "Work types by quarter",
    "allocation": "Skill allocation",
    "roadmap": "Roadmap progress",
}


def day_dialog(store: DayRepo, clock: Clock, folder, on_dialog_toggled, toast, show_review):
    today = clock.now().astimezone().date()
    report: DayReport | None = None
    chart_request = 0
    trends_request = 0

    with ui.dialog() as dialog, ui.card().classes("w-[520px] max-h-[85vh] overflow-y-auto gap-2"):
        with ui.row().classes("w-full items-center justify-between no-wrap"):
            ui.label("Day report").classes("font-medium")
            review_button = ui.button("Review").props("dense flat")
        selected = (
            ui.input("Date", value=today.isoformat()).classes("w-full").props("type=date dense")
        )
        with ui.tabs().classes("w-full").props("dense no-caps align=justify") as tabs:
            day_tab, chart_tab, trends_tab, setup_tab = (
                ui.tab(name) for name in ("Day", "Charts", "Trends", "Setup")
            )
            for tab in (day_tab, chart_tab, trends_tab, setup_tab):
                tab.classes("px-1").style("min-width: 0")

        with ui.tab_panels(tabs, value=day_tab, animated=False).classes("w-full"):
            with ui.tab_panel(day_tab).classes("p-0"):

                @ui.refreshable
                def totals():
                    if report is None:
                        ui.label("Choose a valid report date.")
                        return
                    ui.label(f"{report.seconds / 60:.1f} active minutes").classes("font-medium")
                    for line in summary_lines(report.total):
                        ui.label(line).classes("w-full break-words text-xs")
                    if report.estimated:
                        ui.label("Placement of older paused focus time is estimated.").classes(
                            "text-xs"
                        )
                    for name, quarter in report.quarters.items():
                        with ui.expansion(name).classes("w-full").props("dense"):
                            for line in summary_lines(quarter):
                                ui.label(line).classes("w-full break-words text-xs")
                    with ui.expansion("Skill allocation").classes("w-full").props("dense"):
                        for line in allocation_lines(report):
                            ui.label(line).classes("w-full break-words text-xs")

                totals()

            with ui.tab_panel(chart_tab).classes("p-0"):
                pick_chart = (
                    ui.select(
                        DIAGRAM_OPTIONS,
                        value="timeline",
                        label="Diagram",
                    )
                    .classes("w-full")
                    .props("dense")
                )
                chart_status = ui.label().classes("w-full text-xs")
                zoom = ui.switch("Full-size diagram").props("dense")
                zoom.set_visibility(False)
                with ui.element("div").classes("w-full overflow-x-auto") as chart_frame:
                    chart_image = ui.image().style("width: 100%").props("fit=contain")
                chart_frame.set_visibility(False)

            with ui.tab_panel(trends_tab).classes("p-0"):
                period = ui.select(["Week", "Month"], value="Week", label="Period").props("dense")
                trends_status = ui.label().classes("w-full text-xs")
                trend_body = ui.column().classes("w-full gap-2")

            with ui.tab_panel(setup_tab).classes("p-0"):
                ui.label("Correct Claude activity").classes("font-medium")
                session = (
                    ui.select({}, label="Session for this day").classes("w-full").props("dense")
                )
                work = (
                    ui.select(list(WORK_TYPES), label="Work type").classes("w-full").props("dense")
                )
                skill = (
                    ui.select({}, label="Skill (optional)")
                    .classes("w-full")
                    .props("dense clearable")
                )
                with ui.row().classes("w-full gap-1"):
                    save_correction_button = ui.button("Save correction").props("dense unelevated")
                    clear_correction_button = ui.button("Clear correction").props("dense flat")
                with ui.expansion("Quarter hours").classes("w-full").props("dense"):
                    ui.label("Windows local time, in 24-hour HH:MM.").classes("text-xs")
                    fields = []
                    for name, start, end in store.schedule():
                        with ui.row().classes("w-full gap-2 no-wrap"):
                            opens = ui.input(f"{name} start", value=start.strftime("%H:%M"))
                            closes = ui.input(f"{name} end", value=end.strftime("%H:%M"))
                            opens.classes("w-1/2").props("dense")
                            closes.classes("w-1/2").props("dense")
                            fields.append((opens, closes))
                    save_hours_button = ui.button("Save quarter hours").props("dense unelevated")
        export_status = ui.label().classes("w-full break-all text-xs")
        with ui.row().classes("w-full gap-1"):
            export_button = ui.button("Save Markdown + HTML").props("dense unelevated")
            ui.button("Close", on_click=dialog.close).props("dense flat")

    def update_correction() -> None:
        selected_session = report.sessions.get(session.value) if report else None
        work.value, skill.value = selected_session or (None, None)
        for control in (work, skill, save_correction_button, clear_correction_button):
            control.set_enabled(selected_session is not None)

    def update_report() -> None:
        nonlocal report
        try:
            day = date.fromisoformat(selected.value or "")
            if not 1900 <= day.year <= 9998:
                raise ValueError
        except ValueError:
            report = None
        else:
            report = build_day(store, day)
        totals.refresh()
        export_status.text = ""
        export_button.set_enabled(report is not None)
        review_button.set_enabled(report is not None)
        skills = {item.id: item.name for item in store.repo.list_skills()}
        skill.set_options(skills)
        options = {item: item for item in report.sessions} if report else {}
        session.set_options(options, value=session.value if session.value in options else None)
        update_correction()

    async def update_chart() -> None:
        nonlocal chart_request
        chart_request += 1
        request = chart_request
        current, key = report, pick_chart.value
        if tabs.value != chart_tab.props["name"]:
            return
        chart_frame.set_visibility(False)
        zoom.set_visibility(False)
        chart_status.text = "Drawing diagram…" if current else "Choose a valid report date."
        if current is None:
            return
        try:
            png = await run.io_bound(draw_chart, current, key)
        except Exception:
            log.exception("Could not render the selected diagram.")
            if request == chart_request:
                chart_status.text = "Could not draw this diagram."
            return
        if request != chart_request:
            return
        if png:
            chart_image.props["alt"] = CHARTS[key]
            chart_image.set_source("data:image/png;base64," + base64.b64encode(png).decode("ascii"))
            chart_frame.set_visibility(True)
            zoom.set_visibility(True)
        chart_status.text = "" if png else EMPTY[key]

    def load_trends(day, selected_period, local_today):
        with closing(connect(folder / "focusdeck.sqlite")) as conn:
            conn.execute("BEGIN")
            snapshot = build_trends(DayRepo(Repo(conn)), day, selected_period, local_today)
        return snapshot, draw_trends_chart(snapshot)

    def render_trends(snapshot: TrendsReport, png: bytes | None) -> None:
        suffix = ""
        if snapshot.start <= snapshot.today <= snapshot.end:
            suffix = " · In progress"
        elif snapshot.today < snapshot.start:
            suffix = " · Upcoming"
        with trend_body:
            ui.label(f"{snapshot.start:%Y-%m-%d} to {snapshot.end:%Y-%m-%d}{suffix}").classes(
                "font-medium"
            )
            ui.label(f"{snapshot.seconds / 60:.1f} active minutes").classes("font-medium")
            if png:
                ui.image("data:image/png;base64," + base64.b64encode(png).decode("ascii")).props(
                    'alt="Active minutes by day" fit=contain'
                ).classes("w-full")
            else:
                ui.label("No active time recorded for this period.").classes("text-xs")
            ui.label(
                f"{snapshot.completed} completed focus blocks"
                f" · {snapshot.saved_recaps} recaps saved · {snapshot.skipped_recaps} skipped"
                f" · Newly verified topics {snapshot.newly_verified}"
            ).classes("w-full text-xs")
            if snapshot.estimated:
                ui.label("Placement of older paused focus time is estimated.").classes("text-xs")
            if snapshot.work:
                with ui.expansion("Work types").classes("w-full").props("dense"):
                    for name, seconds in snapshot.work.most_common():
                        ui.label(f"{name} · {seconds / 60:.1f} min").classes("text-xs")
                with ui.expansion("Skills and allocation").classes("w-full").props("dense"):
                    for name, seconds in snapshot.skills.most_common():
                        ui.label(f"{name} · {seconds / 60:.1f} min").classes(
                            "w-full break-words text-xs"
                        )
                    for line in allocation_lines(snapshot):
                        ui.label(line).classes("w-full break-words text-xs")

    async def update_trends() -> None:
        nonlocal trends_request
        trends_request += 1
        request = trends_request
        active = tabs.value == trends_tab.props["name"]
        review_button.set_visibility(not active)
        export_button.set_visibility(not active)
        export_status.set_visibility(not active)
        if not active:
            return
        trend_body.clear()
        current = report
        trends_status.text = "Loading trends…" if current else "Choose a valid report date."
        if current is None:
            return
        try:
            snapshot, png = await run.io_bound(
                load_trends, current.day, period.value, clock.now().astimezone().date()
            )
        except Exception:
            log.exception("Could not load trends.")
            if request == trends_request:
                trends_status.text = "Could not load trends for this period."
            return
        if request == trends_request:
            trends_status.text = ""
            render_trends(snapshot, png)

    async def update_visible_report() -> None:
        await update_trends()
        await update_chart()

    async def change_date() -> None:
        update_report()
        await update_visible_report()

    def save_correction(clear: bool = False) -> None:
        if report is None or session.value not in report.sessions:
            return
        if not clear and not work.value:
            toast("Choose a work type.")
            return
        store.save_classification(
            session.value,
            report.day,
            None if clear else work.value,
            None if clear else skill.value,
        )
        update_report()
        toast("Correction cleared." if clear else "Correction saved.", "positive")

    def save_hours() -> None:
        try:
            store.save_schedule(
                [(opens.value or "", closes.value or "") for opens, closes in fields]
            )
        except ValueError as error:
            toast(str(error))
            return
        update_report()
        toast("Quarter hours saved.", "positive")

    async def save_export() -> None:
        current = report
        if current is None:
            return
        export_button.disable()
        export_status.text = "Saving reports…"
        try:
            markdown, _ = await run.io_bound(export_day, current, folder)
        except Exception:
            log.exception("Could not save the daily report.")
            export_status.text = "Could not save the daily report."
        else:
            export_status.text = f"Saved report.md and report.html in {markdown.parent}"
        finally:
            export_button.set_enabled(report is not None)

    selected.on_value_change(change_date)
    session.on_value_change(lambda _: update_correction())
    zoom.on_value_change(lambda e: chart_image.style(f"width: {'700px' if e.value else '100%'}"))
    pick_chart.on_value_change(update_chart)
    tabs.on_value_change(update_visible_report)
    period.on_value_change(update_trends)
    save_correction_button.on_click(save_correction)
    clear_correction_button.on_click(lambda: save_correction(clear=True))
    save_hours_button.on_click(save_hours)
    export_button.on_click(save_export)
    review_button.on_click(lambda: report and show_review(report.day, update_report))
    dialog.on_value_change(on_dialog_toggled)

    async def open_dialog() -> None:
        update_report()
        dialog.open()
        await update_visible_report()

    return open_dialog
