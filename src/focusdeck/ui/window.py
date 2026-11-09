"""Remembering where the window was. Small helpers with no NiceGUI imports."""

import ctypes
import json
from dataclasses import dataclass

from focusdeck.db.repo import Repo

KEY_POSITION = "ui.window.position"
KEY_SIZE_NORMAL = "ui.window.size.normal"
KEY_SIZE_COMPACT = "ui.window.size.compact"
KEY_ON_TOP = "ui.window.on_top"
KEY_COMPACT = "ui.window.compact"

DEFAULT_SIZE_NORMAL = (340, 560)
DEFAULT_SIZE_COMPACT = (300, 250)
MIN_SIZE = (220, 160)


@dataclass(frozen=True)
class Geometry:
    position: tuple[int, int] | None
    size: tuple[int, int]
    on_top: bool
    compact: bool


def _pair(text: str | None) -> tuple[int, int] | None:
    try:
        a, b = json.loads(text or "")
        return int(a), int(b)
    except (ValueError, TypeError):
        return None


def virtual_screen() -> tuple[int, int, int, int] | None:
    """Left, top, width, height of all monitors together. None if it cannot be read."""
    try:
        metrics = ctypes.windll.user32.GetSystemMetrics
        return metrics(76), metrics(77), metrics(78), metrics(79)
    except Exception:
        return None


def is_visible_position(
    position: tuple[int, int], size: tuple[int, int], screen: tuple[int, int, int, int] | None
) -> bool:
    """True when enough of the window would be on screen to grab its title bar."""
    if screen is None:
        return True
    x, y = position
    left, top, width, height = screen
    return (
        x + size[0] > left + 80
        and x < left + width - 80
        and y >= top - 10
        and y < top + height - 60
    )  # fmt: skip


def size_key(compact: bool) -> str:
    return KEY_SIZE_COMPACT if compact else KEY_SIZE_NORMAL


def load_size(repo: Repo, compact: bool) -> tuple[int, int]:
    default = DEFAULT_SIZE_COMPACT if compact else DEFAULT_SIZE_NORMAL
    size = _pair(repo.get_setting(size_key(compact)))
    if size is None or size[0] < MIN_SIZE[0] or size[1] < MIN_SIZE[1]:
        return default
    return size


def load_geometry(repo: Repo, screen: tuple[int, int, int, int] | None) -> Geometry:
    compact = repo.get_setting(KEY_COMPACT, "0") == "1"
    size = load_size(repo, compact)
    position = _pair(repo.get_setting(KEY_POSITION))
    if position is not None and not is_visible_position(position, size, screen):
        position = None  # the monitor layout changed: let Windows place the window
    return Geometry(
        position=position,
        size=size,
        on_top=repo.get_setting(KEY_ON_TOP, "1") == "1",
        compact=compact,
    )


def save_position_and_size(
    repo: Repo, position: tuple[int, int], size: tuple[int, int], compact: bool
) -> None:
    # A minimized window reports a position far off screen; never store that.
    if position[0] <= -30000 or position[1] <= -30000:
        return
    if size[0] < MIN_SIZE[0] or size[1] < MIN_SIZE[1]:
        return
    # On scaled displays the reported position can be one pixel off from what was requested.
    # Ignoring a one-pixel difference stops the window creeping a pixel on every restart.
    stored = _pair(repo.get_setting(KEY_POSITION))
    moved = stored is None or max(abs(stored[0] - position[0]), abs(stored[1] - position[1])) > 1
    if moved:
        repo.set_setting(KEY_POSITION, json.dumps(list(position)))
    if _pair(repo.get_setting(size_key(compact))) != size:
        repo.set_setting(size_key(compact), json.dumps(list(size)))
