"""Capture setup for the native timer widget."""

import os
import sys
from pathlib import Path

from nicegui import ui

from focusdeck.capture.installer import SettingsChange, preview
from focusdeck.capture.policy import Policy, clean_paths
from focusdeck.db.repo import Repo


def settings_path() -> Path:
    override = os.environ.get("FOCUSDECK_CLAUDE_CONFIG_DIR") or os.environ.get("CLAUDE_CONFIG_DIR")
    directory = Path(override).expanduser() if override else Path.home() / ".claude"
    return directory.resolve() / "settings.json"


def capture_dialog(repo: Repo, folder: Path, settings: Path, on_dialog_toggled, toast):
    pending: SettingsChange | None = None

    with ui.dialog() as dialog, ui.card().classes("w-80 max-h-[85vh] overflow-y-auto gap-2"):
        ui.label("Claude Code capture").classes("font-medium")

        with ui.column().classes("w-full gap-2") as setup:
            status = ui.label().classes("text-xs")
            hook_status = ui.label().classes("text-xs")
            excluded = ui.textarea("Excluded projects").classes("w-full").props("dense rows=2")
            metadata = ui.textarea("Metadata-only projects").classes("w-full").props("dense rows=2")
            ui.label("Use absolute paths, one per line. Rules include subfolders.").classes(
                "text-xs"
            )
            gap = (
                ui.number("Active gap limit (min)", min=1, max=60, step=1)
                .classes("w-full")
                .props("dense")
            )
            reviewed = ui.checkbox("I reviewed both project lists.").props("dense")
            enabled = ui.switch("Capture enabled").props("dense")
            save_button = ui.button("Save capture policy").props("dense unelevated")
            with ui.row().classes("w-full gap-1"):
                install_button = ui.button("Install hooks").props("dense flat")
                uninstall_button = ui.button("Uninstall hooks").props("dense flat")
            if getattr(sys, "frozen", False):
                ui.label(
                    "If you move the bundle, install the hooks again from its new location."
                ).classes("text-xs")
            ui.button("Close", on_click=dialog.close).props("dense flat")

        with ui.column().classes("w-full min-w-0 gap-2") as confirmation:
            operation = ui.label().classes("font-medium text-sm")
            target = ui.label().classes("w-full break-all text-xs")
            diff = ui.label().classes(
                "w-full whitespace-pre-wrap break-all text-xs font-mono max-h-44 overflow-auto"
            )
            ui.label("Applying the change backs up existing settings.").classes("text-xs")
            with ui.row().classes("w-full gap-1"):
                apply_button = ui.button("Apply settings change").props("dense unelevated")
                cancel_button = ui.button("Cancel preview").props("dense flat")
        confirmation.set_visibility(False)

    def update_status() -> None:
        current = Policy.load(folder)
        name = "Enabled" if current.enabled else "Disabled"
        count = repo.conn.execute("SELECT count(*) FROM claude_event").fetchone()[0]
        status.text = f"Policy {name.lower()} · {count} events stored"
        try:
            registered = preview(settings, folder, install=False).has_changes
        except (ValueError, OSError, RecursionError):
            hook_status.text = "Hook status unavailable"
        else:
            hook_status.text = (
                "FocusDeck hooks present" if registered else "FocusDeck hooks not installed"
            )

    def save_policy() -> None:
        try:
            value = gap.value
            if value is None or value not in range(1, 61):
                raise ValueError("Enter a whole number from 1 to 60 minutes.")
            if enabled.value and not reviewed.value:
                raise ValueError("Review both project lists before enabling capture.")
            policy = Policy(
                reviewed=reviewed.value,
                enabled=enabled.value,
                excluded=clean_paths(excluded.value or ""),
                metadata_only=clean_paths(metadata.value or ""),
                gap_minutes=int(value),
            )
            policy.save(folder)
        except ValueError as error:
            toast(str(error))
            return
        except (OSError, RuntimeError):
            toast("Could not save the capture policy.")
            return
        update_status()
        toast("Capture policy saved.", "positive")

    def show_setup() -> None:
        nonlocal pending
        pending = None
        diff.text = ""
        confirmation.set_visibility(False)
        setup.set_visibility(True)
        update_status()

    def show_preview(install: bool) -> None:
        nonlocal pending
        if install and not Policy.load(folder).reviewed:
            toast("Save your reviewed project lists before installing hooks.")
            return
        try:
            change = preview(settings, folder, install)
        except ValueError as error:
            toast(str(error))
            return
        except (OSError, RecursionError):
            toast("Could not preview Claude settings. Check that the file has valid JSON.")
            return
        if not change.has_changes:
            toast("Settings already match.", "info")
            return
        pending = change
        operation.text = "Install hooks" if install else "Uninstall hooks"
        target.text = str(settings)
        diff.text = change.diff
        setup.set_visibility(False)
        confirmation.set_visibility(True)

    def apply() -> None:
        if pending is None:
            return
        try:
            backup = pending.apply()
        except ValueError as error:
            show_setup()
            toast(str(error))
            return
        except OSError:
            toast("Could not write Claude settings or their backup.")
            return
        show_setup()
        toast(f"Saved. Backup: {backup.name}" if backup else "Claude settings saved.", "positive")

    save_button.on_click(save_policy)
    install_button.on_click(lambda: show_preview(True))
    uninstall_button.on_click(lambda: show_preview(False))
    apply_button.on_click(apply)
    cancel_button.on_click(show_setup)
    dialog.on_value_change(on_dialog_toggled)

    def open_dialog() -> None:
        current = Policy.load(folder)
        excluded.value = "\n".join(current.excluded)
        metadata.value = "\n".join(current.metadata_only)
        reviewed.value = current.reviewed
        enabled.value = current.enabled
        gap.value = current.gap_minutes
        show_setup()
        dialog.open()

    return open_dialog
