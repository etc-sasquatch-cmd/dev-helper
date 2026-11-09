"""Pure state machine tests. Time is passed in explicitly, so no clock is needed."""

from datetime import UTC, datetime, timedelta

import pytest

from focusdeck.core import timer
from focusdeck.core.timer import Phase, Preset, Status, TimerError, TimerState

T0 = datetime(2026, 11, 9, 15, 0, 0, tzinfo=UTC)
PRESET = Preset(focus_seconds=1500, short_break_seconds=300, long_break_seconds=900,
                cycles_before_long=4)  # fmt: skip


def at(seconds: float) -> datetime:
    return T0 + timedelta(seconds=seconds)


def test_start_stores_end_timestamp():
    state = timer.start(TimerState(), PRESET, T0)
    assert state.status is Status.RUNNING
    assert state.ends_at == at(1500)
    assert state.planned_seconds == 1500


def test_remaining_comes_from_end_timestamp_not_from_ticks():
    state = timer.start(TimerState(), PRESET, T0)
    # No ticks happened in between: asking late gives the same answer as asking often.
    assert timer.remaining_seconds(state, at(600)) == 900
    assert timer.remaining_seconds(state, at(1499.5)) == 0.5


def test_remaining_never_negative():
    state = timer.start(TimerState(), PRESET, T0)
    assert timer.remaining_seconds(state, at(99999)) == 0


def test_is_due_exactly_at_end():
    state = timer.start(TimerState(), PRESET, T0)
    assert not timer.is_due(state, at(1499.999))
    assert timer.is_due(state, at(1500))


def test_cannot_start_twice():
    state = timer.start(TimerState(), PRESET, T0)
    with pytest.raises(TimerError):
        timer.start(state, PRESET, at(1))


def test_pause_freezes_remaining_and_resume_moves_end():
    state = timer.start(TimerState(), PRESET, T0)
    paused = timer.pause(state, at(600))
    assert paused.status is Status.PAUSED
    assert paused.ends_at is None
    # An hour passes while paused: nothing is lost.
    assert timer.remaining_seconds(paused, at(600 + 3600)) == 900
    resumed = timer.resume(paused, at(600 + 3600))
    assert resumed.ends_at == at(600 + 3600 + 900)
    assert timer.remaining_seconds(resumed, at(600 + 3600 + 100)) == 800


def test_elapsed_excludes_paused_time():
    state = timer.start(TimerState(), PRESET, T0)
    paused = timer.pause(state, at(600))
    resumed = timer.resume(paused, at(5000))
    assert timer.elapsed_seconds(resumed, at(5100)) == 700


def test_pause_and_resume_reject_wrong_states():
    with pytest.raises(TimerError):
        timer.pause(TimerState(), T0)
    with pytest.raises(TimerError):
        timer.resume(timer.start(TimerState(), PRESET, T0), T0)


def test_full_cycle_order_with_long_break_after_four():
    state = TimerState()
    seen = []
    for _ in range(9):
        seen.append(state.phase)
        state = timer.complete(timer.start(state, PRESET, T0), PRESET)
    assert seen == [
        Phase.FOCUS, Phase.SHORT_BREAK,
        Phase.FOCUS, Phase.SHORT_BREAK,
        Phase.FOCUS, Phase.SHORT_BREAK,
        Phase.FOCUS, Phase.LONG_BREAK,
        Phase.FOCUS,
    ]  # fmt: skip
    assert state.completed_focus == 1  # the count restarted after the long break


def test_single_cycle_preset_always_takes_long_break():
    preset = Preset(60, 30, 90, cycles_before_long=1)
    state = timer.complete(timer.start(TimerState(), preset, T0), preset)
    assert state.phase is Phase.LONG_BREAK


def test_skipped_focus_does_not_count():
    running = timer.start(TimerState(completed_focus=2), PRESET, T0)
    state = timer.skip(running, PRESET)
    assert state.phase is Phase.SHORT_BREAK
    assert state.completed_focus == 2
    assert state.status is Status.IDLE


def test_skip_breaks_leads_to_focus():
    short = TimerState(phase=Phase.SHORT_BREAK, completed_focus=2)
    assert timer.skip(short, PRESET) == TimerState(phase=Phase.FOCUS, completed_focus=2)
    long = TimerState(phase=Phase.LONG_BREAK, completed_focus=4)
    assert timer.skip(long, PRESET) == TimerState(phase=Phase.FOCUS, completed_focus=0)


def test_reset_returns_to_start_of_same_phase():
    running = timer.start(TimerState(phase=Phase.SHORT_BREAK, completed_focus=1), PRESET, T0)
    assert timer.reset(running) == TimerState(phase=Phase.SHORT_BREAK, completed_focus=1)


def test_preset_rejects_bad_values():
    with pytest.raises(ValueError):
        Preset(0, 300, 900, 4)
    with pytest.raises(ValueError):
        Preset(1500, 300, 900, 0)
