"""Preview and apply an owned subset of Claude Code command hooks."""

import difflib
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from focusdeck.capture.hook import EVENTS
from focusdeck.capture.policy import atomic_write

MARKER = "--focusdeck-capture-v1"


def _unique_object(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Claude settings contain a duplicate JSON key.")
        result[key] = value
    return result


def _read(path: Path) -> bytes | None:
    try:
        return path.read_bytes()
    except FileNotFoundError:
        return None


def _owns(hook: object) -> bool:
    if not isinstance(hook, dict) or hook.get("type") != "command":
        return False
    args = hook.get("args")
    return (
        isinstance(args, list)
        and len(args) == 5
        and args[0] == "-B"
        and isinstance(args[1], str)
        and Path(args[1]).name == "hook.py"
        and args[2] == MARKER
    )


@dataclass(frozen=True)
class SettingsChange:
    path: Path
    before: bytes | None
    after: bytes

    @property
    def has_changes(self) -> bool:
        return self.after != (self.before or b"")

    @property
    def diff(self) -> str:
        original = (self.before or b"").decode("utf-8-sig").splitlines()
        updated = self.after.decode("utf-8").splitlines()
        return "\n".join(
            difflib.unified_diff(original, updated, "Current", "Proposed", lineterm="")
        )

    def apply(self) -> Path | None:
        if _read(self.path) != self.before:
            raise ValueError("Settings changed since the preview. Open a new preview.")
        if not self.has_changes:
            return None
        self.path.parent.mkdir(parents=True, exist_ok=True)
        backup = None
        if self.before is not None:
            backup = self.path.with_name(f"{self.path.name}.focusdeck-{uuid4().hex[:12]}.bak")
            with backup.open("xb") as stream:
                stream.write(self.before)

        def validate() -> None:
            if _read(self.path) != self.before:
                raise ValueError("Settings changed while saving. Open a new preview.")

        atomic_write(self.path, self.after, validate)
        return backup


def preview(settings: Path, folder: Path, install: bool) -> SettingsChange:
    before = _read(settings)
    document = (
        {}
        if before is None
        else json.loads(before.decode("utf-8-sig"), object_pairs_hook=_unique_object)
    )
    if not isinstance(document, dict):
        raise ValueError("Claude settings must be a JSON object.")
    original = json.dumps(document, ensure_ascii=False)
    old_hooks = document.get("hooks", {})
    if not isinstance(old_hooks, dict):
        raise ValueError("The hooks setting must be a JSON object.")
    hooks = dict(old_hooks)
    for event in EVENTS:
        groups = hooks.get(event, [])
        if not isinstance(groups, list):
            raise ValueError(f"The {event} hooks must be a list.")
        kept = []
        for group in groups:
            if not isinstance(group, dict) or not isinstance(group.get("hooks"), list):
                raise ValueError(f"An existing {event} hook group has an unexpected format.")
            others = [hook for hook in group["hooks"] if not _owns(hook)]
            if others or not group["hooks"]:
                kept.append({**group, "hooks": others})
        if install:
            hook = {
                "type": "command",
                "command": str(Path(sys.executable).absolute()),
                "args": [
                    "-B",
                    str(Path(__file__).with_name("hook.py").resolve()),
                    MARKER,
                    str(folder.resolve()),
                    str((settings.parent / "projects").resolve()),
                ],
                "async": True,
                "timeout": 2,
            }
            kept.append({"hooks": [hook]})
        if kept:
            hooks[event] = kept
        elif event in hooks and groups:
            del hooks[event]

    if hooks or "hooks" in document:
        document["hooks"] = hooks
    if json.dumps(document, ensure_ascii=False) == original:
        return SettingsChange(settings, before, before or b"")
    after = (json.dumps(document, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
    return SettingsChange(settings, before, after)
