"""Pomodoro state machine. Pure logic: no database, UI, or system clock.

Remaining time is always derived from a stored end timestamp (`ends_at`). Nothing here
decrements a counter, so a frozen UI or an app restart cannot change the remaining time.
"""

from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from enum import StrEnum


class Phase(StrEnum):
    FOCUS = "focus"
    SHORT_BREAK = "short_break"
    LONG_BREAK = "long_break"


class Status(StrEnum):
    IDLE = "idle"
    RUNNING = "running"
    PAUSED = "paused"


WORK_TYPES = (
    "Build",
    "Debug",
    "Plan and Design",
    "Review",
    "Study",
    "Documentation",
    "Ops and Admin",
    "Other",
)


class TimerError(Exception):
    """An action was requested that the current timer state does not allow."""


@dataclass(frozen=True)
class Preset:
    focus_seconds: int
    short_break_seconds: int
    long_break_seconds: int
    cycles_before_long: int

    def __post_init__(self) -> None:
        if min(self.focus_seconds, self.short_break_seconds, self.long_break_seconds) <= 0:
            raise ValueError("phase durations must be positive")
        if self.cycles_before_long < 1:
            raise ValueError("cycles_before_long must be at least 1")

    def seconds_for(self, phase: Phase) -> int:
        return {
            Phase.FOCUS: self.focus_seconds,
            Phase.SHORT_BREAK: self.short_break_seconds,
            Phase.LONG_BREAK: self.long_break_seconds,
        }[phase]


@dataclass(frozen=True)
class TimerState:
    """Where the timer is.

    When idle, `phase` is the phase that will start next.
    `completed_focus` counts focus blocks completed since the last long break.
    """

    phase: Phase = Phase.FOCUS
    status: Status = Status.IDLE
    completed_focus: int = 0
    planned_seconds: int | None = None
    ends_at: datetime | None = None  # set only while running
    paused_remaining: float | None = None  # seconds, set only while paused


def start(state: TimerState, preset: Preset, now: datetime) -> TimerState:
    if state.status is not Status.IDLE:
        raise TimerError("a phase is already in progress")
    seconds = preset.seconds_for(state.phase)
    return replace(
        state,
        status=Status.RUNNING,
        planned_seconds=seconds,
        ends_at=now + timedelta(seconds=seconds),
        paused_remaining=None,
    )


def remaining_seconds(state: TimerState, now: datetime) -> float:
    """Seconds left in the current phase. Never negative. Zero when idle."""
    if state.status is Status.RUNNING:
        assert state.ends_at is not None
        return max(0.0, (state.ends_at - now).total_seconds())
    if state.status is Status.PAUSED:
        assert state.paused_remaining is not None
        return state.paused_remaining
    return 0.0


def elapsed_seconds(state: TimerState, now: datetime) -> float:
    """Seconds actually spent running in the current phase (pauses excluded)."""
    if state.status is Status.IDLE or state.planned_seconds is None:
        return 0.0
    return state.planned_seconds - remaining_seconds(state, now)


def is_due(state: TimerState, now: datetime) -> bool:
    return state.status is Status.RUNNING and state.ends_at is not None and now >= state.ends_at


def pause(state: TimerState, now: datetime) -> TimerState:
    if state.status is not Status.RUNNING:
        raise TimerError("only a running phase can be paused")
    return replace(
        state,
        status=Status.PAUSED,
        paused_remaining=remaining_seconds(state, now),
        ends_at=None,
    )


def resume(state: TimerState, now: datetime) -> TimerState:
    if state.status is not Status.PAUSED:
        raise TimerError("only a paused phase can be resumed")
    assert state.paused_remaining is not None
    return replace(
        state,
        status=Status.RUNNING,
        ends_at=now + timedelta(seconds=state.paused_remaining),
        paused_remaining=None,
    )


def _idle(phase: Phase, completed_focus: int) -> TimerState:
    return TimerState(phase=phase, status=Status.IDLE, completed_focus=completed_focus)


def complete(state: TimerState, preset: Preset) -> TimerState:
    """The current phase ran to its end. Returns the idle state for the next phase."""
    if state.status is Status.IDLE:
        raise TimerError("no phase is in progress")
    if state.phase is Phase.FOCUS:
        done = state.completed_focus + 1
        if done >= preset.cycles_before_long:
            return _idle(Phase.LONG_BREAK, done)
        return _idle(Phase.SHORT_BREAK, done)
    if state.phase is Phase.LONG_BREAK:
        return _idle(Phase.FOCUS, 0)
    return _idle(Phase.FOCUS, state.completed_focus)


def skip(state: TimerState, preset: Preset) -> TimerState:
    """Leave the current (or upcoming) phase and move to the next one.

    A skipped focus block does not count as a completed Pomodoro, so it is followed by a
    short break and the cycle count is unchanged. Skipping any break leads to focus.
    """
    if state.phase is Phase.FOCUS:
        return _idle(Phase.SHORT_BREAK, state.completed_focus)
    if state.phase is Phase.LONG_BREAK:
        return _idle(Phase.FOCUS, 0)
    return _idle(Phase.FOCUS, state.completed_focus)


def reset(state: TimerState) -> TimerState:
    """Abandon the current phase and return to its start, not yet running."""
    return _idle(state.phase, state.completed_focus)
