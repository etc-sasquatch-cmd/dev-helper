"""YouTube through the official IFrame player. Python side: it only sends short commands
to youtube_player.js, which owns the player itself."""

import json
import logging
from collections.abc import Callable
from pathlib import Path

from focusdeck.music.provider import MusicSource

log = logging.getLogger("focusdeck.music")

PLAYER_JS = Path(__file__).parent / "youtube_player.js"
PLAYER_ELEMENT_ID = "fd-player"

# Codes from the IFrame API reference, except 151, which is not documented. On this PC it
# came with the player's own text "This video is restricted. Please check the Google Workspace
# administrator and/or the network administrator restrictions", so the message says that.
_NOT_EMBEDDABLE = "Its owner does not allow playback outside youtube.com. Pick another source."
ERROR_MESSAGES = {
    2: "The saved link has an invalid ID. Delete the source and add it again.",
    5: "The YouTube player could not play this. Try another source.",
    100: "Not found. It was removed or is private.",
    101: _NOT_EMBEDDABLE,
    150: _NOT_EMBEDDABLE,
    151: "YouTube restricted this video here. The player shows the reason, often a network "
    "or Google Workspace restriction. Try another source or another network.",
    153: "YouTube did not receive this app's identity (Referer). This is a FocusDeck bug.",
}


def describe_error(code: object) -> str:
    return ERROR_MESSAGES.get(code, f"YouTube player error {code}.")  # type: ignore[arg-type]


class YouTubeProvider:
    """Implements MusicProvider. `run_js` runs a line of JavaScript in the window."""

    def __init__(self, run_js: Callable[[str], object], volume: int = 50) -> None:
        self._run_js = run_js
        self.volume = _clamp(volume)
        self.available = False  # becomes True once the page is connected

    def is_available(self) -> bool:
        return self.available

    def play(self, source: MusicSource) -> None:
        if source.provider != "youtube":
            raise ValueError(f"not a YouTube source: {source.provider}")
        log.info("play source_id=%s kind=%s", source.id, source.kind)
        # json.dumps quotes the values, so stored text can never run as code in the page.
        self._run_js(
            f"fdLoad({json.dumps(source.kind)}, {json.dumps(source.external_id)}, {self.volume})"
        )

    def pause(self) -> None:
        log.info("pause")
        self._run_js("fdPause()")

    def resume(self) -> None:
        log.info("resume")
        self._run_js("fdPlay()")

    def set_volume(self, percent: int) -> None:
        self.volume = _clamp(percent)
        self._run_js(f"fdSetVolume({self.volume})")


def _clamp(percent: int) -> int:
    return max(0, min(100, int(percent)))
