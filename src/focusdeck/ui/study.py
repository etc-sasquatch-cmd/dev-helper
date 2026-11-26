"""Study controls for the timer window."""

import logging
from pathlib import Path

from nicegui import ui
from pydantic import ValidationError

from focusdeck.core.allocation import allocation
from focusdeck.core.service import TimerService
from focusdeck.db.study import StudyRepo
from focusdeck.roadmaps.importer import load_roadmap

log = logging.getLogger(__name__)
STATE_LABELS = {
    "not_started": "Not started",
    "learning": "Learning",
    "claimed": "Claimed",
    "verified": "Verified",
    "skipped": "Skipped",
}


def study_dialog(
    study: StudyRepo, service: TimerService, on_links_changed, on_dialog_toggled, toast
):
    with ui.dialog() as dialog, ui.card().classes("w-80 max-h-[85vh] overflow-auto gap-2"):
        with ui.tabs().classes("w-full").props("dense no-caps align=justify") as tabs:
            targets_tab = ui.tab("Targets")
            roadmaps_tab = ui.tab("Roadmaps")
            allocation_tab = ui.tab("Time")
            for tab in (targets_tab, roadmaps_tab, allocation_tab):
                tab.classes("px-1").style("min-width: 0")

        with ui.tab_panels(tabs, value=targets_tab).classes("w-full"):
            with ui.tab_panel(targets_tab).classes("p-0"):

                @ui.refreshable
                def targets_panel() -> None:
                    targets = study.targets_at(service.clock.now())
                    fields = {}
                    for skill in study.repo.list_skills():
                        fields[skill.id] = (
                            ui.number(
                                skill.name,
                                value=targets.get(skill.id, 0),
                                min=0,
                                max=100,
                                step=1,
                                suffix="%",
                            )
                            .classes("w-full")
                            .props("dense")
                        )
                    if not fields:
                        ui.label("Add a skill in the timer to set targets.")
                        return

                    def save_targets() -> None:
                        values = {key: field.value for key, field in fields.items()}
                        if any(value is None or value != int(value) for value in values.values()):
                            toast("Enter a whole percentage for every skill.")
                            return
                        try:
                            study.save_targets(
                                {key: int(value) for key, value in values.items()},
                                service.clock.now(),
                            )
                        except ValueError as error:
                            toast(str(error))
                            return
                        toast("Targets saved.", "positive")
                        time_panel.refresh()

                    ui.button("Save targets", on_click=save_targets).props("dense unelevated")

                targets_panel()

            with ui.tab_panel(roadmaps_tab).classes("p-0"):
                source = (
                    ui.input(
                        "Path within data/roadmaps",
                        value="developer-roadmap/roadmaps/backend",
                    )
                    .classes("w-full")
                    .props("dense")
                )

                def import_topics() -> None:
                    try:
                        key, roadmap = load_roadmap(Path.cwd() / "data" / "roadmaps", source.value)
                        study.import_roadmap(key, roadmap)
                    except ValidationError as error:
                        first = error.errors(include_input=False)[0]
                        field = ".".join(map(str, first["loc"]))
                        toast(f"Invalid roadmap field: {field or 'document'}. {first['msg']}")
                        return
                    except ValueError as error:
                        toast(str(error))
                        return
                    except OSError:
                        log.warning("Could not read local roadmap input", exc_info=True)
                        toast("Could not read the local roadmap file.")
                        return
                    browse_panel.refresh()
                    links_panel.refresh()
                    on_links_changed()
                    count = len(roadmap.nodes)
                    noun = "topic" if count == 1 else "topics"
                    toast(f"Imported {count} {noun}.", "positive")

                ui.button("Import roadmap", on_click=import_topics).props("dense unelevated")

                @ui.refreshable
                def browse_panel() -> None:
                    nodes = {row["id"]: row for row in study.nodes()}
                    if not nodes:
                        return
                    topic = (
                        ui.select(
                            {
                                key: f"{row['title']} · {STATE_LABELS[row['state']]}"
                                for key, row in nodes.items()
                            },
                            label="Find a topic",
                            with_input=True,
                        )
                        .classes("w-full")
                        .props("dense clearable")
                    )

                    @ui.refreshable
                    def topic_details() -> None:
                        row = nodes.get(topic.value)
                        if row is None:
                            return
                        ui.label(f"{row['roadmap']} · {STATE_LABELS[row['state']]}")
                        ui.link("Open source", row["url"], new_tab=True)

                        def set_skipped() -> None:
                            study.set_skipped(row["id"], row["state"] != "skipped")
                            nodes.update({item["id"]: item for item in study.nodes()})
                            topic.set_options(
                                {
                                    key: f"{item['title']} · {STATE_LABELS[item['state']]}"
                                    for key, item in nodes.items()
                                },
                                value=row["id"],
                            )
                            topic_details.refresh()
                            on_links_changed()

                        label = "Restore topic" if row["state"] == "skipped" else "Skip topic"
                        ui.button(label, on_click=set_skipped).props("dense flat")

                    topic.on_value_change(lambda _: topic_details.refresh())
                    topic_details()

                browse_panel()

                @ui.refreshable
                def links_panel() -> None:
                    skills = {skill.id: skill.name for skill in study.repo.list_skills()}
                    if not skills:
                        return
                    pick = ui.select(skills, label="Skill to link").classes("w-full").props("dense")

                    @ui.refreshable
                    def link_fields() -> None:
                        skill_id = pick.value
                        if skill_id is None:
                            return
                        nodes = (
                            ui.select(
                                {
                                    row["id"]: f"{row['roadmap']} · {row['title']}"
                                    for row in study.nodes()
                                },
                                label="Linked topics",
                                multiple=True,
                                with_input=True,
                                value=study.node_links(skill_id),
                            )
                            .classes("w-full")
                            .props("dense")
                        )
                        paths = (
                            ui.textarea(
                                "Project paths, one per line",
                                value="\n".join(study.project_paths(skill_id)),
                            )
                            .classes("w-full")
                            .props("dense")
                        )

                        def save_links() -> None:
                            study.save_links(skill_id, nodes.value or [], paths.value.splitlines())
                            on_links_changed()
                            toast("Links saved.", "positive")

                        ui.button("Save links", on_click=save_links).props("dense unelevated")

                    pick.on_value_change(lambda _: link_fields.refresh())
                    link_fields()

                links_panel()

            with ui.tab_panel(allocation_tab).classes("p-0"):
                period = ui.select(
                    {1: "Today", 7: "7 days", 30: "30 days"}, value=1, label="Period"
                )
                period.classes("w-full").props("dense")

                @ui.refreshable
                def time_panel() -> None:
                    total, rows = allocation(study, service.clock.now(), period.value)
                    ui.label(f"{total / 60:.1f} min logged")
                    if total < 7200:
                        ui.label("Insufficient data for gaps (under 120 min).").classes("text-xs")
                    elif not any(row.gap is not None for row in rows):
                        ui.label("Some logged time has no saved target.").classes("text-xs")
                    if not rows:
                        ui.label("Add a skill to see its allocation.")
                    for row in rows:
                        target = "—" if row.target is None else f"{row.target:.1f}%"
                        actual = "—" if row.actual is None else f"{row.actual:.1f}%"
                        gap = "—" if row.gap is None else f"{row.gap:+.1f} pp"
                        ui.label(row.skill).classes("font-medium")
                        ui.label(f"Target {target} · Actual {actual} · Gap {gap}").classes(
                            "text-xs"
                        )

                time_panel()
                period.on_value_change(lambda _: time_panel.refresh())
                tabs.on_value_change(lambda _: time_panel.refresh())

        ui.button("Close", on_click=dialog.close).props("flat dense")

    def open_dialog() -> None:
        targets_panel.refresh()
        browse_panel.refresh()
        links_panel.refresh()
        time_panel.refresh()
        dialog.open()

    dialog.on_value_change(on_dialog_toggled)
    return open_dialog
