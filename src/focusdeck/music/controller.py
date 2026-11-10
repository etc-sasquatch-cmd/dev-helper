"""Decides what the music should do from what the timer is doing. No UI, no network.

Call `sync()` as often as you like: the provider only hears about changes.
"""

from focusdeck.core.timer import Status
from focusdeck.music.provider import MusicProvider, MusicSource


class MusicController:
    def __init__(self, provider: MusicProvider) -> None:
        self.provider = provider
        self.loaded: MusicSource | None = None  # what the player currently holds
        self.playing = False  # whether we last told it to play
        self.failed: MusicSource | None = None  # a source that just failed; not retried
        self.covered = False  # a dialog is over the player
        self._status = Status.IDLE
        self._source: MusicSource | None = None

    def wanted(self) -> MusicSource | None:
        """The source that should be audible right now, or None for quiet."""
        if self.covered or self._status is not Status.RUNNING:
            return None
        return self._source

    def sync(self, status: Status, source: MusicSource | None) -> None:
        """`source` is the one assigned to the current phase, or None for silence."""
        self._status, self._source = status, source
        self._apply()

    def set_covered(self, covered: bool) -> None:
        """The player must not play while something covers it, so dialogs pause the music."""
        self.covered = covered
        self._apply()

    def on_error(self) -> None:
        """The player reported that the current source cannot be played."""
        self.failed = self.loaded
        self.loaded = None
        self.playing = False

    def _apply(self) -> None:
        wanted = self.wanted()
        if wanted != self.failed:
            self.failed = None  # the situation changed, so that source may be tried again
        if not self.provider.is_available():
            return
        if wanted is None:
            if self.playing:
                self.provider.pause()
                self.playing = False
            return
        if wanted == self.failed:
            return
        if wanted == self.loaded:
            if not self.playing:
                self.provider.resume()
                self.playing = True
            return
        self.provider.play(wanted)
        self.loaded = wanted
        self.playing = True
