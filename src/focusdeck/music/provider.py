"""What every music backend looks like to the rest of the app."""

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class MusicSource:
    id: int
    name: str
    provider: str  # "youtube"
    kind: str  # "video" or "playlist"
    external_id: str


class MusicProvider(Protocol):
    def is_available(self) -> bool: ...
    def play(self, source: MusicSource) -> None: ...
    def pause(self) -> None: ...
    def resume(self) -> None: ...
    def set_volume(self, percent: int) -> None: ...
