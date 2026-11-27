"""The FocusDeck window. Layout and event wiring only: rules live in focusdeck.core."""

import logging
import multiprocessing
import sqlite3
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

from nicegui import app, ui
from nicegui import run as nicegui_run

from focusdeck.agents.actions import pending_quarter
from focusdeck.agents.runner import AgentRunner
from focusdeck.capture.ingest import ingest
from focusdeck.core.clock import SystemClock
from focusdeck.core.service import AUTO_START_GRACE_SECONDS, PhaseEnded, TimerService
from focusdeck.core.timer import WORK_TYPES, Phase, Status, TimerError
from focusdeck.db.connection import data_dir, open_database
from focusdeck.db.day import DayRepo
from focusdeck.db.repo import Repo
from focusdeck.db.study import StudyRepo
from focusdeck.music.controller import MusicController
from focusdeck.music.links import parse_youtube_link
from focusdeck.music.provider import MusicSource
from focusdeck.music.youtube import PLAYER_ELEMENT_ID, PLAYER_JS, YouTubeProvider, describe_error
from focusdeck.ui import alerts, window
from focusdeck.ui.artifact import artifact_dialog
from focusdeck.ui.capture import capture_dialog, settings_path
from focusdeck.ui.day import day_dialog
from focusdeck.ui.review import reviewer_dialog
from focusdeck.ui.study import study_dialog
from focusdeck.ui.theme import WIDGET_CSS

log = logging.getLogger("focusdeck.ui")

TITLE = "FocusDeck"
PLAYER_JS_URL = "/fd/youtube_player.js"
KEY_VOLUME = "music.volume"
YT_PLAYING = 1
SILENCE = 0  # select value meaning "no source"
KEY_SOUND = "ui.sound"
KEY_NOTIFY = "ui.notify"
KEY_LAST_SKILL = "ui.last_skill_id"
KEY_LAST_WORK_TYPE = "ui.last_work_type"

PHASE_NAMES = {
    Phase.FOCUS: "Focus",
    Phase.SHORT_BREAK: "Short break",
    Phase.LONG_BREAK: "Long break",
}


def format_time(seconds: float) -> str:
    whole = int(-(-seconds // 1))  # round up, so 0.4s left shows 00:01 and not 00:00
    return f"{whole // 60:02d}:{whole % 60:02d}"


def toast(message: str, kind: str = "warning") -> None:
    # Always at the top: a message at the bottom would sit on top of the YouTube player.
    ui.notify(message, type=kind, position="top")


def build_page(
    service: TimerService,
    repo: Repo,
    cache_dir: Path,
    capture_settings: Path | None = None,
    reviewer: AgentRunner | None = None,
) -> None:
    compact = {"on": repo.get_setting(window.KEY_COMPACT, "0") == "1"}
    client = ui.context.client
    ui.add_css(WIDGET_CSS)
    ui.colors(primary="#5945c7")

    # YouTube only plays when it gets a Referer from a real http origin (else error 153).
    ui.add_head_html('<meta name="referrer" content="strict-origin-when-cross-origin">')
    version = int(PLAYER_JS.stat().st_mtime)
    ui.add_head_html(f'<script src="{PLAYER_JS_URL}?v={version}"></script>')

    def flag(key: str) -> bool:
        return repo.get_setting(key, "1") == "1"

    # Alerts -------------------------------------------------------------------

    def announce(event: PhaseEnded) -> None:
        ended = PHASE_NAMES[event.phase]
        upcoming = PHASE_NAMES[event.next_phase]
        if event.late_seconds > AUTO_START_GRACE_SECONDS:
            toast(f"{ended} ended while FocusDeck was not running.", "info")
            return
        verb = "started" if event.next_started else "is next"
        message = f"{ended} finished. {upcoming} {verb}."
        toast(message, "positive")
        if flag(KEY_SOUND):
            kind = "focus_end" if event.phase is Phase.FOCUS else "break_end"
            alerts.play_sound(kind, cache_dir)
        if flag(KEY_NOTIFY):
            alerts.notify("FocusDeck", message)

    # Timer display --------------------------------------------------------------

    timer_classes = "fd-timer w-full items-center gap-1"
    with ui.column().classes("fd-widget w-full items-center gap-2"):
        with ui.column().classes(timer_classes) as timer_panel:
            phase_label = ui.label().classes("fd-phase")
            time_label = ui.label().classes("fd-time")
            cycle_label = ui.label().classes("fd-muted text-xs")
            tag_label = ui.label().classes("fd-muted text-xs text-center break-words")
            with ui.row().classes("w-full justify-center gap-1 mt-2 no-wrap"):
                main_button = ui.button("Start").props("unelevated").classes("grow max-w-32")
                skip_button = ui.button("Skip").props("flat")
                reset_button = ui.button("Reset").props("flat")

        with ui.column().classes("fd-card w-full gap-1"):
            # Keep the official player at least 200x200 and uncovered while it plays.
            player_box = ui.element("div").classes("w-full relative bg-black rounded-lg")
            player_box.style("height: 200px; min-width: 200px; flex-shrink: 0")
            with player_box:
                ui.label("No music playing").classes(
                    "absolute inset-0 flex items-center justify-center text-xs text-slate-300"
                )
                ui.html(f'<div id="{PLAYER_ELEMENT_ID}"></div>', sanitize=False).classes(
                    "absolute inset-0"
                )
            with ui.row().classes("w-full items-center gap-1 no-wrap"):
                music_label = ui.label().classes("fd-muted grow text-xs leading-tight")
                play_button = ui.button("Play", icon="play_arrow").props("dense unelevated size=sm")
            with ui.row().classes("w-full items-center gap-2 no-wrap"):
                ui.icon("volume_up").classes("fd-muted")
                volume_slider = ui.slider(
                    min=0, max=100, step=1, value=int(repo.get_setting(KEY_VOLUME, "50") or 50)
                ).classes("grow")
                music_button = ui.button(icon="queue_music").props("flat dense").tooltip("Music")

        # Recap prompt. Inline, not a pop-up, so it never covers the player.
        with ui.card().props("flat bordered").classes("w-full gap-1 p-2") as recap_panel:
            ui.label("What did you get done?").classes("text-sm font-medium")
            recap_info = ui.label().classes("fd-muted text-xs")
            recap_input = ui.input(placeholder="One line").props("dense outlined")
            recap_input.classes("w-full")
            with ui.row().classes("w-full justify-end gap-1"):
                recap_skip_button = ui.button("Skip").props("flat dense")
                recap_save_button = ui.button("Save").props("unelevated dense")
        recap_panel.set_visibility(False)

        details = ui.column().classes("fd-card w-full gap-2")
        footer = ui.row().classes("w-full justify-between items-center no-wrap")

    # Skill, work type, preset ---------------------------------------------------

    def skill_options() -> dict[int, str]:
        return {skill.id: skill.name for skill in repo.list_skills()}

    study = StudyRepo(repo)

    def refresh_topic_options(*, keep_selection: bool = True) -> None:
        options = (
            {
                row["id"]: f"{row['roadmap']} · {row['title']}"
                for row in study.nodes(skill_select.value)
                if row["state"] != "skipped"
            }
            if skill_select.value is not None
            else {}
        )
        value = topic_select.value if keep_selection and topic_select.value in options else None
        topic_select.set_options(options, value=value)

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
        with ui.row().classes("w-full items-center gap-1 no-wrap"):
            topic_select = (
                ui.select(
                    {},
                    label="Topic (optional)",
                    with_input=True,
                )
                .classes("grow min-w-0")
                .props("dense outlined clearable")
            )
            study_button = ui.button("Study").props("flat dense")
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
            capture_button = ui.button("Capture").props("dense flat")

    with footer:
        stats_label = ui.label().classes("fd-muted grow min-w-0 text-xs")
        with ui.row().classes("shrink-0 gap-1 no-wrap"):
            day_button = ui.button("Day").props("flat dense")
            compact_button = ui.button(icon="unfold_less").props("flat dense").tooltip("Compact")

    skill_select.on_value_change(lambda _: refresh_topic_options(keep_selection=False))
    refresh_topic_options()

    # New skill dialog -------------------------------------------------------------

    with ui.dialog() as skill_dialog, ui.card().classes("w-64"):
        ui.label("New skill").classes("font-medium")
        skill_input = ui.input("Name").props("autofocus dense outlined").classes("w-full")

        def add_skill() -> None:
            try:
                new_id = repo.create_skill(skill_input.value or "")
            except ValueError:
                toast("Type a skill name.")
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
                toast("Fill in every field.")
                return None
            focus, short, long = (round(float(v) * 60) for v in values[:3])
            cycles = int(values[3])
            if min(focus, short, long) < 1 or cycles < 1:
                toast("Durations and cycles must be greater than zero.")
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
                toast("Another preset already has that name.")
                return
            repo.update_preset(current.id, *form)
            after_preset_change()

        def save_preset_as_new() -> None:
            form = read_form()
            if form is None:
                return
            if name_taken(form[0], None):
                toast("Give the new preset a different name.")
                return
            after_preset_change(repo.create_preset(*form))

        def delete_preset() -> None:
            try:
                service.delete_preset(service.preset_row().id)
            except TimerError as error:
                toast(str(error).capitalize() + ".")
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

    # Recap panel ------------------------------------------------------------------

    recap_state: dict[str, int | None] = {"block_id": None}

    def window_size() -> tuple[int, int]:
        """Saved size for the current mode. Compact grows while the recap panel shows."""
        width, height = window.load_size(repo, compact["on"])
        if compact["on"] and recap_state["block_id"] is not None:
            height += window.RECAP_PANEL_HEIGHT
        return width, height

    def fit_window() -> None:
        if compact["on"]:
            app.native.main_window.resize(*window_size())

    def close_recap() -> None:
        recap_state["block_id"] = None
        recap_input.value = ""
        recap_panel.set_visibility(False)
        fit_window()
        refresh()

    def save_recap() -> None:
        block_id = recap_state["block_id"]
        if block_id is None:
            return
        try:
            service.save_recap(block_id, recap_input.value or "")
        except TimerError:
            toast("Write one line, or press Skip.")
            return
        close_recap()

    def skip_recap() -> None:
        block_id = recap_state["block_id"]
        if block_id is not None:
            service.skip_recap(block_id)
        close_recap()

    recap_input.on("keydown.enter", save_recap)
    recap_skip_button.on_click(skip_recap)
    recap_save_button.on_click(save_recap)

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
        recap_panel.set_visibility(True)
        fit_window()

    # Music ------------------------------------------------------------------------

    provider = YouTubeProvider(client.run_javascript, volume=int(volume_slider.value))
    music = MusicController(provider)
    library: dict = {"sources": {}, "profile": repo.get_profile()}
    player_note = {"error": "", "blocked": False}

    def reload_library() -> None:
        library["sources"] = {source.id: source for source in repo.list_music_sources()}
        library["profile"] = repo.get_profile()

    def phase_source(phase: Phase) -> MusicSource | None:
        return library["sources"].get(library["profile"].source_id_for(phase.value))

    def update_music_label() -> None:
        playing = music.wanted()
        assigned = phase_source(service.state.phase)
        if music.failed is not None and player_note["error"]:
            text = player_note["error"]
        elif player_note["blocked"] and playing is not None:
            text = "Autoplay was blocked. Press Play."
        elif playing is not None:
            text = f"♪ {playing.name}"
        elif assigned is not None:
            text = f"{assigned.name} plays when the timer runs"
        else:
            text = f"Silence for {PHASE_NAMES[service.state.phase].lower()}"
        music_label.text = text
        play_button.set_visibility(player_note["blocked"] and playing is not None)

    def sync_music() -> None:
        state = service.state
        music.sync(state.status, phase_source(state.phase))
        update_music_label()

    def on_player_state(e) -> None:
        log.info("player state=%s", e.args.get("state"))
        if e.args.get("state") == YT_PLAYING:
            player_note["blocked"] = False
            update_music_label()

    def on_player_error(e) -> None:
        code, skipped = e.args.get("code"), e.args.get("skipped")
        log.warning("player error code=%s skipped=%s", code, skipped)
        if skipped:
            return  # one unplayable video inside a playlist; the player moved on
        name = music.loaded.name if music.loaded else "Music"
        player_note["error"] = f"{name}: {describe_error(code)}"
        music.on_error()
        toast(player_note["error"])
        update_music_label()

    def on_player_unavailable(_e) -> None:
        log.warning("YouTube player script could not be loaded")
        player_note["error"] = "Could not reach YouTube. Check the internet connection."
        music.on_error()
        toast(player_note["error"])
        update_music_label()

    def on_autoplay_blocked(_e) -> None:
        log.info("autoplay blocked")
        player_note["blocked"] = True
        update_music_label()

    def on_play_pressed() -> None:
        player_note["blocked"] = False
        update_music_label()

    def on_connected() -> None:
        provider.available = True

    ui.on("yt_ready", lambda _e: log.info("player ready"))
    ui.on("yt_state", on_player_state)
    ui.on("yt_error", on_player_error)
    ui.on("yt_unavailable", on_player_unavailable)
    ui.on("yt_autoplay_blocked", on_autoplay_blocked)
    client.on_connect(on_connected)
    # The click itself starts playback in the page, which is what an autoplay block requires.
    play_button.on("click", on_play_pressed, js_handler="() => { fdPlay(); emit(); }")

    def on_volume(e) -> None:
        provider.set_volume(int(e.value))
        repo.set_setting(KEY_VOLUME, str(provider.volume))

    volume_slider.on_value_change(on_volume)

    # Music dialog -----------------------------------------------------------------

    def source_options() -> dict[int, str]:
        options = {SILENCE: "Silence"}
        options.update({source.id: source.name for source in library["sources"].values()})
        return options

    with ui.dialog() as music_dialog, ui.card().classes("w-80 gap-2"):
        ui.label("Music").classes("font-medium")
        ui.label("Music pauses while this is open.").classes("text-xs text-gray-500")
        phase_selects = {}
        for phase in Phase:
            phase_selects[phase] = (
                ui.select(source_options(), label=PHASE_NAMES[phase], value=SILENCE)
                .classes("w-full")
                .props("dense outlined")
            )
        ui.separator()
        ui.label("Saved sources").classes("text-sm font-medium")
        sources_list = ui.column().classes("w-full gap-0")
        source_name_input = ui.input("Name").props("dense outlined").classes("w-full")
        source_link_input = ui.input("YouTube video or playlist link")
        source_link_input.props("dense outlined").classes("w-full")
        with ui.row().classes("w-full justify-end gap-1"):
            ui.button("Close", on_click=music_dialog.close).props("flat")
            add_source_button = ui.button("Add source").props("unelevated")

    def show_library() -> None:
        reload_library()
        options = source_options()
        for phase, select in phase_selects.items():
            current = library["profile"].source_id_for(phase.value)
            select.set_options(options, value=current if current in options else SILENCE)
        sources_list.clear()
        with sources_list:
            if not library["sources"]:
                ui.label("None yet. Paste a link below.").classes("text-xs text-gray-500")
            for source in library["sources"].values():
                with ui.row().classes("w-full items-center gap-1 no-wrap"):
                    ui.label(source.name).classes("grow truncate text-sm")
                    ui.label(source.kind).classes("text-xs text-gray-500")
                    ui.button(
                        icon="delete", on_click=lambda _e, s=source: delete_source(s.id)
                    ).props("flat dense size=sm color=negative")

    def delete_source(source_id: int) -> None:
        repo.delete_music_source(source_id)
        show_library()

    def add_source() -> None:
        try:
            kind, external_id = parse_youtube_link(source_link_input.value or "")
            repo.create_music_source(source_name_input.value or "", "youtube", kind, external_id)
        except ValueError as error:  # LinkError is a ValueError too
            toast(str(error).capitalize() + ".")
            return
        source_name_input.value = ""
        source_link_input.value = ""
        show_library()

    def on_phase_source_selected(phase: Phase, value) -> None:
        wanted = value or None
        if value is not None and library["profile"].source_id_for(phase.value) != wanted:
            repo.set_profile_source(phase.value, wanted)
            reload_library()

    for phase, select in phase_selects.items():
        select.on_value_change(lambda e, p=phase: on_phase_source_selected(p, e.value))
    add_source_button.on_click(add_source)
    source_link_input.on("keydown.enter", add_source)

    def open_music_dialog() -> None:
        show_library()
        music_dialog.open()

    music_button.on_click(open_music_dialog)

    # A dialog would cover the player, and it may not play while covered: pause meanwhile.
    open_dialogs: set[int] = set()

    def on_dialog_toggled(e) -> None:
        if e.value:
            open_dialogs.add(e.sender.id)
        else:
            open_dialogs.discard(e.sender.id)
        music.set_covered(bool(open_dialogs))
        update_music_label()

    for dialog in (skill_dialog, preset_dialog, music_dialog):
        dialog.on_value_change(on_dialog_toggled)
    reviewer = reviewer or AgentRunner(cache_dir)
    show_artifact = artifact_dialog(repo, service.clock, reviewer, on_dialog_toggled, toast)
    study_button.on_click(
        study_dialog(
            study,
            service,
            refresh_topic_options,
            on_dialog_toggled,
            toast,
            show_artifact,
            roadmap_root=cache_dir / "roadmaps" if getattr(sys, "frozen", False) else None,
        )
    )
    capture_button.on_click(
        capture_dialog(
            repo, cache_dir, capture_settings or settings_path(), on_dialog_toggled, toast
        )
    )
    day_store = DayRepo(repo)
    show_review = reviewer_dialog(
        day_store,
        service.clock,
        cache_dir,
        reviewer,
        on_dialog_toggled,
        toast,
    )
    day_button.on_click(
        day_dialog(day_store, service.clock, cache_dir, on_dialog_toggled, toast, show_review)
    )

    def check_quarter_review() -> None:
        upcoming = pending_quarter(day_store, service.clock.now())
        day_button.text = f"Day · {upcoming[-2:]}" if upcoming else "Day"

    # Controls -------------------------------------------------------------------------

    def guarded(action) -> None:
        try:
            action()
        except TimerError as error:
            toast(str(error).capitalize() + ".")
        refresh()

    def on_main_button() -> None:
        status = service.state.status
        if status is Status.IDLE:
            guarded(
                lambda: service.start(skill_select.value, work_select.value, topic_select.value)
            )
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
        app.native.main_window.resize(*window_size())

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
        timer_panel.classes(replace=f"{timer_classes} fd-{state.phase.value.replace('_', '-')}")
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
        for control in (skill_select, add_skill_button, work_select, topic_select):
            control.set_enabled(not in_focus)

        main_button.text = {
            Status.IDLE: "Start",
            Status.RUNNING: "Pause",
            Status.PAUSED: "Resume",
        }[state.status]
        stats_label.text = (
            f"Today: {service.completed_focus_today()}"
            f" · Recaps skipped: {service.skipped_recap_count()}"
        )

    def on_tick() -> None:
        event = service.tick()
        if event is not None:
            announce(event)
        show_pending_recap()
        refresh()
        if provider.available:
            sync_music()

    async def save_geometry() -> None:
        if recap_state["block_id"] is not None:
            return  # compact mode is temporarily taller; do not remember that
        try:
            position = await app.native.main_window.get_position()
            size = await app.native.main_window.get_size()
        except Exception:
            return  # the window is closing
        if position and size:
            window.save_position_and_size(repo, tuple(position), tuple(size), compact["on"])

    reload_library()
    apply_compact()
    refresh()
    update_music_label()
    ui.timer(0.5, on_tick)
    ui.timer(3.0, save_geometry, immediate=False)
    ui.timer(15.0, check_quarter_review)


def run(reviewer: AgentRunner | None = None) -> None:
    # Native mode starts a second process for the window, which imports this module again.
    # Only the main process may open the database and start the server.
    if multiprocessing.current_process().name != "MainProcess":
        return

    folder = data_dir()
    handler = RotatingFileHandler(
        folder / "focusdeck.log", maxBytes=200_000, backupCount=1, encoding="utf-8"
    )
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    logging.getLogger("focusdeck").addHandler(handler)
    logging.getLogger("focusdeck").setLevel(logging.INFO)
    app.add_static_file(local_file=PLAYER_JS, url_path=PLAYER_JS_URL)

    repo = Repo(open_database(folder / "focusdeck.sqlite"))
    service = TimerService(repo, SystemClock())
    capture_settings = settings_path()

    async def ingest_capture() -> None:
        try:
            await nicegui_run.io_bound(ingest, folder, capture_settings.parent / "projects")
        except (OSError, sqlite3.Error):
            log.warning("Capture could not process local records. It will retry.")

    app.timer(5.0, ingest_capture, immediate=False)

    geometry = window.load_geometry(repo, window.virtual_screen())
    app.native.window_args["on_top"] = geometry.on_top
    app.native.window_args["min_size"] = window.MIN_SIZE
    if geometry.position is not None:
        app.native.window_args["x"], app.native.window_args["y"] = geometry.position

    @ui.page("/")
    def index() -> None:
        build_page(service, repo, folder, capture_settings, reviewer)

    ui.run(
        native=True,
        reload=False,
        title=TITLE,
        window_size=geometry.size,
        dark=None,
        show_welcome_message=False,
    )
