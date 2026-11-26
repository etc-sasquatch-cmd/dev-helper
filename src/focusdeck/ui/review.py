"""Reviewer preview, results, and local quiz controls."""

import json
from datetime import date
from pathlib import Path

from nicegui import run, ui

from focusdeck.agents.actions import (
    QUARTER_ENABLED,
    QUARTER_SEEN,
    apply_suggestions,
    grade,
    pending_quarter,
    set_quarter_reminders,
)
from focusdeck.agents.contracts import SCHEMAS, Classification, Critique, Quiz, StudyReview
from focusdeck.agents.digest import Request, prepare, quiz_topics
from focusdeck.agents.runner import AgentRunner, ReviewError, ReviewRun
from focusdeck.core.calendar import BUCKETS
from focusdeck.core.clock import Clock
from focusdeck.db.day import DayRepo


def reviewer_dialog(
    store: DayRepo,
    clock: Clock,
    folder: Path,
    runner: AgentRunner,
    on_dialog_toggled,
    toast,
):
    request: Request | None = None
    result: ReviewRun | None = None
    on_classified = None
    busy = False

    with ui.dialog() as dialog, ui.card().classes("w-96 max-h-[85vh] overflow-y-auto gap-2"):
        ui.label("AI reviewers").classes("font-medium")
        with ui.column().classes("w-full gap-2") as setup:
            selected_date = ui.label().classes("text-xs")
            task = ui.select(list(SCHEMAS), value="Work Critic", label="Review").classes("w-full")
            period = ui.select(["Day", *BUCKETS], value="Day", label="Period").classes("w-full")
            node = ui.select({}, label="Public quiz topic", with_input=True).classes("w-full")
            topic_note = ui.label(
                "Quizzes are available for supported public subjects. Answers stay local."
            ).classes("text-xs")
            checkout = (
                ui.input("Optional checkout path (relative)", placeholder=".")
                .classes("w-full")
                .props("dense")
            )
            reminders = ui.switch("Queue a preview at quarter end").props("dense")
            ui.label("Each send requires your approval.").classes("text-xs")
            with ui.row().classes("gap-1"):
                preview_button = ui.button("Preview request").props("dense unelevated")
                ui.button("Close", on_click=dialog.close).props("flat dense")

        with ui.column().classes("w-full gap-2") as confirmation:
            request_title = ui.label().classes("font-medium")
            ui.label(
                "Only the data below is sent with the review instructions. "
                "Private text and project identifiers stay local."
            ).classes("text-xs")
            digest_label = ui.label().classes(
                "w-full max-h-52 overflow-auto whitespace-pre-wrap break-all font-mono text-xs"
            )
            error_label = ui.label().classes("text-xs text-red-400")
            with ui.row().classes("gap-1"):
                send_button = ui.button("Approve and send").props("dense unelevated")
                back_button = ui.button("Back").props("flat dense")
        confirmation.set_visibility(False)

        with ui.column().classes("w-full gap-2") as results:
            result_title = ui.label().classes("font-medium")
            usage = ui.label().classes("w-full break-all text-xs")
            body = ui.column().classes("w-full gap-2")
            with ui.row().classes("gap-1"):
                another_button = ui.button("New review").props("flat dense")
                ui.button("Close", on_click=dialog.close).props("flat dense")
        results.set_visibility(False)

    def show_setup() -> None:
        nonlocal request, result
        request, result = None, None
        confirmation.set_visibility(False)
        results.set_visibility(False)
        setup.set_visibility(True)

    def task_changed() -> None:
        quiz = task.value == "Recall quiz"
        node.set_visibility(quiz)
        topic_note.set_visibility(quiz)
        period.set_visibility(not quiz)
        checkout.set_visibility(task.value == "Work Critic")

    def show_preview() -> None:
        nonlocal request
        try:
            request = prepare(
                store,
                folder,
                date.fromisoformat(selected_date.text),
                task.value,
                period.value,
                node_id=node.value,
                checkout=checkout.value or "",
            )
            if task.value == "Classify" and not request.sessions:
                raise ValueError("No eligible Claude activity in this period.")
        except (ValueError, OSError) as error:
            toast(str(error))
            return
        request_title.text = task.value
        digest_label.text = json.dumps(request.digest, indent=2, ensure_ascii=True, sort_keys=True)
        error_label.text = ""
        setup.set_visibility(False)
        confirmation.set_visibility(True)

    def evidence_line(aliases: list[str]) -> None:
        labels = [f"{alias} · {request.labels.get(alias, alias)}" for alias in aliases]
        ui.label("Evidence " + ", ".join(labels)).classes("w-full break-words text-xs")

    def render() -> None:
        assert request is not None and result is not None
        body.clear()
        output = result.output
        result_title.text = request.task
        source = "Cached result" if result.cached else f"{result.seconds:.1f} s"
        count = (
            f" · {result.input_tokens or 0} in / {result.output_tokens or 0} out tokens"
            if result.input_tokens is not None or result.output_tokens is not None
            else " · Token usage unavailable"
        )
        usage.text = f"{result.model} · {source}{count}"
        with body:
            if isinstance(output, Critique):
                ui.label(f"{output.confidence.capitalize()} confidence").classes("text-xs")
                ui.label(output.summary).classes("w-full break-words")
                for finding in output.findings:
                    ui.label(finding.observation).classes("w-full break-words")
                    evidence_line(finding.evidence)
                    ui.label(f"Next action · {finding.next_action}").classes(
                        "w-full break-words text-sm"
                    )
            if isinstance(output, StudyReview):
                for verdict in output.skill_verdicts:
                    ui.label(f"{request.labels[verdict.skill]} · {verdict.verdict}").classes(
                        "w-full break-words text-sm"
                    )
                    evidence_line(verdict.evidence)
                if output.overclaimed:
                    ui.label("Claims needing evidence").classes("font-medium")
                    evidence_line(output.overclaimed)
                if output.neglected:
                    ui.label("Skills needing attention").classes("font-medium")
                    evidence_line(output.neglected)
                ui.label("Next study actions").classes("font-medium")
                for action in output.next_actions:
                    ui.label(action).classes("w-full break-words text-sm")
            elif isinstance(output, Classification):
                for suggestion in output.suggestions:
                    proposed = suggestion.work_type if suggestion.confidence != "low" else "Other"
                    ui.label(
                        f"{request.labels[suggestion.session]} · {proposed}"
                        f" · {suggestion.confidence} confidence"
                    ).classes("w-full break-words text-sm")
                    evidence_line(suggestion.evidence)
                if output.suggestions:
                    apply_button = ui.button("Apply suggestions").props("dense unelevated")

                    def apply() -> None:
                        apply_suggestions(store, request, output)
                        apply_button.disable()
                        if on_classified:
                            on_classified()
                        toast(
                            "Suggestions applied. Manual corrections still take precedence.",
                            "positive",
                        )

                    apply_button.on_click(apply)
                else:
                    ui.label("No classifications were suggested.")
            elif isinstance(output, Quiz):
                fields = []
                for number, question in enumerate(output.questions, 1):
                    ui.label(f"{number}. {question.prompt}").classes("w-full break-words text-sm")
                    fields.append(
                        ui.select(dict(enumerate(question.options)), label=f"Answer {number}")
                        .classes("w-full")
                        .props("dense")
                    )
                score = ui.label().classes("w-full text-sm")
                grade_button = ui.button("Grade locally").props("dense unelevated")

                def submit() -> None:
                    try:
                        correct = grade(
                            store, request, output, [field.value for field in fields], clock.now()
                        )
                    except ValueError as error:
                        toast(str(error))
                        return
                    score.text = f"{correct}/3 · " + (
                        "Passed. E2 evidence recorded."
                        if correct == 3
                        else "Review the topic and try again."
                    )
                    grade_button.disable()
                    if on_classified:
                        on_classified()

                grade_button.on_click(submit)

    async def send() -> None:
        nonlocal result, busy
        current = request
        if busy or current is None:
            return
        busy = True
        send_button.disable()
        back_button.disable()
        error_label.text = "Running reviewer…"
        try:
            result = await run.io_bound(runner.run, current.task, current.digest)
        except ReviewError as error:
            error_label.text = str(error)
            return
        except Exception:
            error_label.text = "Could not complete the reviewer request."
            return
        finally:
            busy = False
            send_button.enable()
            back_button.enable()
        render()
        confirmation.set_visibility(False)
        results.set_visibility(True)

    def open_dialog(day: date, notify=None) -> None:
        nonlocal on_classified
        if busy:
            return
        on_classified = notify
        selected_date.text = day.isoformat()
        review = pending_quarter(store, clock.now())
        if review and review.startswith(day.isoformat() + ":"):
            period.value = review.split(":")[1]
            task.value = "Summarize"
            store.repo.set_setting(QUARTER_SEEN, review)
        else:
            period.value = "Day"
        reminders.value = store.repo.get_setting(QUARTER_ENABLED) == "1"
        node.set_options(quiz_topics(store), value=None)
        task_changed()
        show_setup()
        dialog.open()

    task.on_value_change(task_changed)
    reminders.on_value_change(lambda e: set_quarter_reminders(store, bool(e.value), clock.now()))
    preview_button.on_click(show_preview)
    send_button.on_click(send)
    back_button.on_click(show_setup)
    another_button.on_click(show_setup)
    dialog.on_value_change(on_dialog_toggled)
    return open_dialog
