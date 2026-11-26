"""Preview an artifact review and confirm its evidence from a roadmap topic."""

import json
import sqlite3

from nicegui import run, ui

from focusdeck.agents.artifact import prepare_artifact, record_artifact, review_artifact
from focusdeck.agents.contracts import ARTIFACT_REVIEW, ArtifactVerdict
from focusdeck.agents.runner import AgentRunner, ReviewError, review_request
from focusdeck.core.clock import Clock
from focusdeck.db.repo import Repo

OUTCOMES = {
    "demonstrates_topic": "Demonstrates the topic",
    "needs_more_evidence": "Needs more evidence",
    "insufficient_material": "Insufficient material",
}


def artifact_dialog(repo: Repo, clock: Clock, runner: AgentRunner, on_dialog_toggled, toast):
    node_id = None
    on_recorded = None
    request = None
    review = None

    with (
        ui.dialog().props("persistent") as dialog,
        ui.card().classes("w-[520px] max-h-[85vh] overflow-y-auto gap-2"),
    ):
        ui.label("Review artifact").classes("font-medium")
        topic = ui.label().classes("w-full break-words")
        with ui.column().classes("w-full") as input_panel:
            reference = ui.input("Local artifact reference").classes("w-full")
            criterion = ui.textarea("Shareable topic criterion").classes("w-full")
            sample = ui.textarea("Shareable artifact sample").classes("w-full")
            ui.label(
                "Use a short excerpt cleared for sharing. The local reference stays in FocusDeck."
            ).classes("text-xs")
            preview_button = ui.button("Preview request")
        with ui.column().classes("w-full") as preview_panel:
            ui.label("The prompt, JSON input, and response schema below will be sent to Claude.")
            preview = ui.label().classes(
                "w-full max-h-64 overflow-auto whitespace-pre-wrap break-all font-mono text-xs"
            )
            with ui.row():
                send_button = ui.button("Approve and send")
                back_button = ui.button("Back").props("flat")
        with ui.column().classes("w-full") as result_panel:
            verdict = ui.label().classes("w-full whitespace-pre-wrap break-words")
            record_button = ui.button("Confirm and record E2")
        status = ui.label().classes("w-full break-words text-xs")
        close_button = ui.button("Close", on_click=dialog.close).props("flat dense")

    def show_input() -> None:
        input_panel.set_visibility(True)
        preview_panel.set_visibility(False)
        result_panel.set_visibility(False)
        status.text = ""

    def show_preview() -> None:
        nonlocal request
        if node_id is None or not dialog.value or not input_panel.visible:
            return
        try:
            request = prepare_artifact(node_id, reference.value, criterion.value, sample.value)
        except ValueError as error:
            status.text = str(error)
            return
        instructions, payload, schema = review_request(ARTIFACT_REVIEW, request.digest)
        preview.text = f"{instructions}\n\n{payload}\n\n{json.dumps(schema)}"
        input_panel.set_visibility(False)
        preview_panel.set_visibility(True)
        status.text = ""

    async def send() -> None:
        nonlocal review
        if request is None or not preview_panel.visible or not send_button.enabled:
            return
        for button in (send_button, back_button, close_button):
            button.disable()
        status.text = "Running Study Judge…"
        try:
            review = await run.io_bound(review_artifact, runner, request)
        except ReviewError as error:
            status.text = str(error)
            return
        except (OSError, sqlite3.Error):
            status.text = "Could not run the artifact review."
            return
        finally:
            for button in (send_button, back_button, close_button):
                button.enable()

        output = review.result.output
        assert isinstance(output, ArtifactVerdict)
        verdict.text = "\n\n".join([OUTCOMES[output.outcome], output.reason, *output.citations])
        record_button.set_visibility(output.outcome == "demonstrates_topic")
        record_button.enable()
        status.text = ""
        preview_panel.set_visibility(False)
        result_panel.set_visibility(True)

    def record() -> None:
        if (
            request is None
            or review is None
            or not result_panel.visible
            or not record_button.enabled
        ):
            return
        try:
            saved = record_artifact(repo, request, review, clock.now())
        except (ValueError, sqlite3.Error) as error:
            status.text = str(error) if isinstance(error, ValueError) else "Could not record E2."
            return
        record_button.disable()
        status.text = "E2 evidence recorded." if saved else "This review was already recorded."
        if saved and on_recorded:
            on_recorded()

    def open_dialog(row, refresh) -> None:
        nonlocal node_id, on_recorded, request, review
        if dialog.value:
            return
        node_id, on_recorded = row["id"], refresh
        request = review = None
        topic.text = row["title"]
        for field in (reference, criterion, sample):
            field.value = ""
        show_input()
        dialog.open()

    preview_button.on_click(show_preview)
    back_button.on_click(show_input)
    send_button.on_click(send)
    record_button.on_click(record)
    show_input()
    dialog.on_value_change(on_dialog_toggled)
    return open_dialog
