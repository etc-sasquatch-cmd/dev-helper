"""The FocusDeck window. Layout and event wiring only: rules live in focusdeck.core."""

import multiprocessing
from pathlib import Path

from nicegui import app, ui

from focusdeck.core.clock import SystemClock
from focusdeck.core.service import AUTO_START_GRACE_SECONDS, PhaseEnded, TimerService
from focusdeck.core.timer import WORK_TYPES, Phase, Status, TimerError
from focusdeck.db.connection import data_dir, open_database
from focusdeck.db.repo import Repo
from focusdeck.ui import alerts, window

TITLE = "FocusDeck"
KEY_SOUND = "ui.sound"
KEY_NOTIFY = "ui.notify"
KEY_LAST_SKILL = "ui.last_skill_id"
KEY_LAST_WORK_TYPE = "ui.last_work_type"

PHASE_NAMES = {
    Phase.FOCUS: "Focus",
    Phase.SHORT_BREAK: "Short break",
    Phase.LONG_BREAK: "Long break",
}
PHASE_COLORS = {
    Phase.FOCUS: "text-red-500",
    Phase.SHORT_BREAK: "text-green-600",
    Phase.LONG_BREAK: "text-blue-500",
}


def format_time(seconds: float) -> str:
    whole = int(-(-seconds // 1))  # round up, so 0.4s left shows 00:01 and not 00:00
    return f"{whole // 60:02d}:{whole % 60:02d}"


def build_page(service: TimerService, repo: Repo, cache_dir: Path) -> None:
    compact = {"on": repo.get_setting(window.KEY_COMPACT, "0") == "1"}

    def flag(key: str) -> bool:
        return repo.get_setting(key, "1") == "1"

    # Alerts -------------------------------------------------------------------

    def announce(event: PhaseEnded) -> None:
        ended = PHASE_NAMES[event.phase]
        upcoming = PHASE_NAMES[event.next_phase]
        if event.late_seconds > AUTO_START_GRACE_SECONDS:
            ui.notify(f"{ended} ended while FocusDeck was not running.", type="info")
            return
        verb = "started" if event.next_started else "is next"
        message = f"{ended} finished. {upcoming} {verb}."
        ui.notify(message, type="positive", position="top")
        if flag(KEY_SOUND):
            kind = "focus_end" if event.phase is Phase.FOCUS else "break_end"
            alerts.play_sound(kind, cache_dir)
        if flag(KEY_NOTIFY):
            alerts.notify("FocusDeck", message)

    # Timer display --------------------------------------------------------------

    with ui.column().classes("w-full items-center gap-1 p-3"):
        phase_label = ui.label().classes("text-sm font-medium uppercase tracking-wide")
        time_label = ui.label().classes("text-6xl font-mono leading-none")
        cycle_label = ui.label().classes("text-xs text-gray-500")
        tag_label = ui.label().classes("text-xs text-gray-500")

        with ui.row().classes("gap-2 mt-1"):
            main_button = ui.button("Start").props("unelevated").classes("w-24")
            skip_button = ui.button("Skip").props("outline")
            reset_button = ui.button("Reset").props("outline")

        details = ui.column().classes("w-full gap-2 mt-2")
        footer = ui.row().classes("w-full justify-between items-center")

    # Skill, work type, preset ---------------------------------------------------

    def skill_options() -> dict[int, str]:
        return {skill.id: skill.name for skill in repo.list_skills()}

    def preset_options() -> dict[int, str]:
        return {preset.id: preset.name for preset in repo.list_presets()}

    def stored_skill() -> int | None:
        stored = repo.get_setting(KEY_LAST_SKILL)
        value = int(stored) if stored else None
        return value if value in skill_options() else None

    def stored_work_type() -> str | None:
        stored = repo.get_setting(KEY_LAST_WORK_TYPE)
        return stored if stored in WORK_TYPES else None

    with details:
        with ui.row().classes("w-full items-center gap-1 no-wrap"):
            skill_select = ui.select(
                skill_options(),
                label="Skill",
                value=stored_skill(),
                on_change=lambda e: e.value is not None
                and repo.set_setting(KEY_LAST_SKILL, str(e.value)),
            ).classes("grow").props("dense outlined")  # fmt: skip
            add_skill_button = ui.button(icon="add").props("flat dense").tooltip("New skill")
        work_select = ui.select(
            list(WORK_TYPES),
            label="Work type",
            value=stored_work_type(),
            on_change=lambda e: e.value is not None
            and repo.set_setting(KEY_LAST_WORK_TYPE, e.value),
        ).classes("w-full").props("dense outlined")  # fmt: skip
        with ui.row().classes("w-full items-center gap-1 no-wrap"):
            preset_select = ui.select(
                preset_options(), label="Preset", value=service.preset_row().id
            ).classes("grow").props("dense outlined")  # fmt: skip
            edit_preset_button = ui.button(icon="edit").props("flat dense").tooltip("Edit presets")
        with ui.row().classes("w-full gap-x-3 gap-y-0"):
            on_top_switch = ui.switch("On top", value=flag(window.KEY_ON_TOP)).props("dense")
            sound_switch = ui.switch("Sound", value=flag(KEY_SOUND)).props("dense")
            notify_switch = ui.switch("Notify", value=flag(KEY_NOTIFY)).props("dense")
            auto_switch = ui.switch("Auto-start breaks", value=service.auto_start_breaks).props(
                "dense"
            )

    with footer:
        stats_label = ui.label().classes("text-xs text-gray-500")
        compact_button = ui.button(icon="unfold_less").props("flat dense").tooltip("Compact")

    # New skill dialog -------------------------------------------------------------

    with ui.dialog() as skill_dialog, ui.card().classes("w-64"):
        ui.label("New skill").classes("font-medium")
        skill_input = ui.input("Name").props("autofocus dense outlined").classes("w-full")

        def add_skill() -> None:
            try:
                new_id = repo.create_skill(skill_input.value or "")
            except ValueError:
                ui.notify("Type a skill name.", type="warning")
                return
            skill_select.set_options(skill_options(), value=new_id)
            skill_input.value = ""
            skill_dialog.close()

        skill_input.on("keydown.enter", add_skill)
        with ui.row().classes("w-full justify-end"):
            ui.button("Cancel", on_click=skill_dialog.close).props("flat")
            ui.button("Add", on_click=add_skill).props("unelevated")

    add_skill_button.on_click(skill_dialog.open)

    # Preset editor ----------------------------------------------------------------

    with ui.dialog() as preset_dialog, ui.card().classes("w-72"):
        ui.label("Preset").classes("font-medium")
        name_input = ui.input("Name").props("dense outlined").classes("w-full")
        number_props = "dense outlined"
        focus_input = ui.number("Focus (minutes)", min=0.1, step=0.5).props(number_props)
        short_input = ui.number("Short break (minutes)", min=0.1, step=0.5).props(number_props)
        long_input = ui.number("Long break (minutes)", min=0.1, step=0.5).props(number_props)
        cycles_input = ui.number("Focus blocks before long break", min=1, step=1, precision=0)
        cycles_input.props(number_props)
        for field in (focus_input, short_input, long_input, cycles_input):
            field.classes("w-full")

        def read_form() -> tuple[str, int, int, int, int] | None:
            name = (name_input.value or "").strip()
            values = [focus_input.value, short_input.value, long_input.value, cycles_input.value]
            if not name or any(v is None for v in values):
                ui.notify("Fill in every field.", type="warning")
                return None
            focus, short, long = (round(float(v) * 60) for v in values[:3])
            cycles = int(values[3])
            if min(focus, short, long) < 1 or cycles < 1:
                ui.notify("Durations and cycles must be greater than zero.", type="warning")
                return None
            return name, focus, short, long, cycles

        def after_preset_change(select_id: int | None = None) -> None:
            if select_id is not None:
                service.select_preset(select_id)
            preset_select.set_options(preset_options(), value=service.preset_row().id)
            preset_dialog.close()
            refresh()

        def name_taken(name: str, own_id: int | None) -> bool:
            return any(
                p.name.lower() == name.lower() and p.id != own_id for p in repo.list_presets()
            )

        def save_preset() -> None:
            form = read_form()
            if form is None:
                return
            current = service.preset_row()
            if name_taken(form[0], current.id):
                ui.notify("Another preset already has that name.", type="warning")
                return
            repo.update_preset(current.id, *form)
            after_preset_change()

        def save_preset_as_new() -> None:
            form = read_form()
            if form is None:
                return
            if name_taken(form[0], None):
                ui.notify("Give the new preset a different name.", type="warning")
                return
            after_preset_change(repo.create_preset(*form))

        def delete_preset() -> None:
            try:
                service.delete_preset(service.preset_row().id)
            except TimerError as error:
                ui.notify(str(error).capitalize() + ".", type="warning")
                return
            after_preset_change()

        with ui.row().classes("w-full justify-between"):
            ui.button("Delete", on_click=delete_preset).props("flat color=negative")
            with ui.row().classes("gap-1"):
                ui.button("Save as new", on_click=save_preset_as_new).props("flat")
                ui.button("Save", on_click=save_preset).props("unelevated")

    def open_preset_dialog() -> None:
        row = service.preset_row()
        name_input.value = row.name
        focus_input.value = round(row.focus_seconds / 60, 2)
        short_input.value = round(row.short_break_seconds / 60, 2)
        long_input.value = round(row.long_break_seconds / 60, 2)
        cycles_input.value = row.cycles_before_long
        preset_dialog.open()

    edit_preset_button.on_click(open_preset_dialog)

    def on_preset_selected(e) -> None:
        if e.value is not None and e.value != service.preset_row().id:
            service.select_preset(e.value)
            refresh()

    preset_select.on_value_change(on_preset_selected)

    # Recap dialog -------------------------------------------------------------------

    recap_state: dict[str, int | None] = {"block_id": None}

    with ui.dialog().props("persistent") as recap_dialog, ui.card().classes("w-72"):
        ui.label("What did you get done?").classes("font-medium")
        recap_info = ui.label().classes("text-xs text-gray-500")
        recap_input = ui.input(placeholder="One line").props("autofocus dense outlined")
        recap_input.classes("w-full")

        def close_recap() -> None:
            recap_state["block_id"] = None
            recap_input.value = ""
            recap_dialog.close()
            refresh()

        def save_recap() -> None:
            block_id = recap_state["block_id"]
            if block_id is None:
                return
            try:
                service.save_recap(block_id, recap_input.value or "")
            except TimerError:
                ui.notify("Write one line, or press Skip.", type="warning")
                return
            close_recap()

        def skip_recap() -> None:
            block_id = recap_state["block_id"]
            if block_id is not None:
                service.skip_recap(block_id)
            close_recap()

        recap_input.on("keydown.enter", save_recap)
        with ui.row().classes("w-full justify-end"):
            ui.button("Skip", on_click=skip_recap).props("flat")
            ui.button("Save", on_click=save_recap).props("unelevated")

    def show_pending_recap() -> None:
        if recap_state["block_id"] is not None:
            return
        block = service.pending_recap()
        if block is None:
            return
        recap_state["block_id"] = block.id
        skill = skill_options().get(block.skill_id, "No skill")
        started = block.started_at.astimezone().strftime("%H:%M")
        ended = block.ended_at.astimezone().strftime("%H:%M") if block.ended_at else "?"
        recap_info.text = f"{started} to {ended} · {skill} · {block.work_type}"
        recap_dialog.open()

    # Controls -------------------------------------------------------------------------

    def guarded(action) -> None:
        try:
            action()
        except TimerError as error:
            ui.notify(str(error).capitalize() + ".", type="warning")
        refresh()

    def on_main_button() -> None:
        status = service.state.status
        if status is Status.IDLE:
            guarded(lambda: service.start(skill_select.value, work_select.value))
        elif status is Status.RUNNING:
            guarded(service.pause)
        else:
            guarded(service.resume)

    main_button.on_click(on_main_button)
    skip_button.on_click(lambda: guarded(service.skip))
    reset_button.on_click(lambda: guarded(service.reset))

    def set_flag(key: str, value: bool) -> None:
        repo.set_setting(key, "1" if value else "0")

    def on_top_changed(e) -> None:
        set_flag(window.KEY_ON_TOP, e.value)
        app.native.main_window.set_always_on_top(bool(e.value))

    on_top_switch.on_value_change(on_top_changed)
    sound_switch.on_value_change(lambda e: set_flag(KEY_SOUND, e.value))
    notify_switch.on_value_change(lambda e: set_flag(KEY_NOTIFY, e.value))
    auto_switch.on_value_change(lambda e: setattr(service, "auto_start_breaks", bool(e.value)))

    def apply_compact() -> None:
        details.set_visibility(not compact["on"])
        compact_button.props(f"icon={'unfold_more' if compact['on'] else 'unfold_less'}")

    def toggle_compact() -> None:
        compact["on"] = not compact["on"]
        set_flag(window.KEY_COMPACT, compact["on"])
        apply_compact()
        width, height = window.load_size(repo, compact["on"])
        app.native.main_window.resize(width, height)

    compact_button.on_click(toggle_compact)

    # Refresh ----------------------------------------------------------------------------

    def refresh() -> None:
        state = service.state
        phase_text = PHASE_NAMES[state.phase]
        if state.status is Status.PAUSED:
            phase_text += " (paused)"
        elif state.status is Status.IDLE:
            phase_text += " (ready)"
        phase_label.text = phase_text
        phase_label.classes(replace=f"text-sm font-medium uppercase {PHASE_COLORS[state.phase]}")
        time_label.text = format_time(service.remaining_seconds())

        preset = service.preset()
        cycle_label.text = (
            f"Focus block {min(state.completed_focus + 1, preset.cycles_before_long)}"
            f" of {preset.cycles_before_long}"
            if state.phase is Phase.FOCUS
            else f"{state.completed_focus} of {preset.cycles_before_long} focus blocks done"
        )

        block = service.active_block()
        in_focus = block is not None and block.phase == Phase.FOCUS.value
        if in_focus:
            skill = skill_options().get(block.skill_id, "No skill")
            tag_label.text = f"{skill} · {block.work_type}"
        else:
            tag_label.text = ""
        # The tag is chosen before a focus block starts and is fixed while it runs.
        for control in (skill_select, add_skill_button, work_select):
            control.set_enabled(not in_focus)

        main_button.text = {
            Status.IDLE: "Start",
            Status.RUNNING: "Pause",
            Status.PAUSED: "Resume",
        }[state.status]
        stats_label.text = (
            f"Today: {service.completed_focus_today()} done"
            f" · recaps skipped: {service.skipped_recap_count()}"
        )

    def on_tick() -> None:
        event = service.tick()
        if event is not None:
            announce(event)
        show_pending_recap()
        refresh()

    async def save_geometry() -> None:
        try:
            position = await app.native.main_window.get_position()
            size = await app.native.main_window.get_size()
        except Exception:
            return  # the window is closing
        if position and size:
            window.save_position_and_size(repo, tuple(position), tuple(size), compact["on"])

    apply_compact()
    refresh()
    ui.timer(0.5, on_tick)
    ui.timer(3.0, save_geometry, immediate=False)


def run() -> None:
    # Native mode starts a second process for the window, which imports this module again.
    # Only the main process may open the database and start the server.
    if multiprocessing.current_process().name != "MainProcess":
        return

    folder = data_dir()
    repo = Repo(open_database(folder / "focusdeck.sqlite"))
    service = TimerService(repo, SystemClock())

    geometry = window.load_geometry(repo, window.virtual_screen())
    app.native.window_args["on_top"] = geometry.on_top
    app.native.window_args["min_size"] = window.MIN_SIZE
    if geometry.position is not None:
        app.native.window_args["x"], app.native.window_args["y"] = geometry.position

    @ui.page("/")
    def index() -> None:
        build_page(service, repo, folder)

    ui.run(
        native=True,
        reload=False,
        title=TITLE,
        window_size=geometry.size,
        dark=None,
        show_welcome_message=False,
    )
