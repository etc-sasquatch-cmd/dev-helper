"""Connects the pure timer state machine to storage.

Every transition is written to the database before it is reported to the caller, so the
database alone is enough to rebuild the timer after a crash or restart.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta

from focusdeck.core import timer
from focusdeck.core.clock import Clock
from focusdeck.core.timer import Phase, Preset, Status, TimerError, TimerState
from focusdeck.db.repo import BlockRow, PresetRow, Repo

# A break starts by itself after a focus block ends, unless the end was noticed this late.
# That happens after a restart or after the PC slept through the end of the block.
AUTO_START_GRACE_SECONDS = 120

KEY_NEXT_PHASE = "timer.next_phase"
KEY_COMPLETED_FOCUS = "timer.completed_focus"
KEY_PRESET_ID = "timer.preset_id"
KEY_AUTO_START_BREAKS = "timer.auto_start_breaks"


@dataclass(frozen=True)
class PhaseEnded:
    block_id: int
    phase: Phase
    next_phase: Phase
    late_seconds: float
    next_started: bool


class TimerService:
    def __init__(self, repo: Repo, clock: Clock) -> None:
        self.repo = repo
        self.clock = clock
        self.block_id: int | None = None
        self.state = TimerState()
        self._load()

    # loading and saving -----------------------------------------------------

    def _load(self) -> None:
        completed = int(self.repo.get_setting(KEY_COMPLETED_FOCUS, "0") or 0)
        active = self.repo.active_block()
        if active is None:
            try:
                phase = Phase(self.repo.get_setting(KEY_NEXT_PHASE, Phase.FOCUS.value))
            except ValueError:
                phase = Phase.FOCUS
            self.block_id = None
            self.state = TimerState(phase=phase, completed_focus=completed)
            return
        self.block_id = active.id
        self.state = TimerState(
            phase=Phase(active.phase),
            status=Status(active.status),
            completed_focus=completed,
            planned_seconds=active.planned_seconds,
            ends_at=active.ends_at,
            paused_remaining=active.remaining_seconds,
        )

    def _save_idle(self, state: TimerState) -> None:
        self.repo.set_setting(KEY_NEXT_PHASE, state.phase.value)
        self.repo.set_setting(KEY_COMPLETED_FOCUS, str(state.completed_focus))

    # presets ----------------------------------------------------------------

    def preset_row(self) -> PresetRow:
        stored = self.repo.get_setting(KEY_PRESET_ID)
        row = self.repo.get_preset(int(stored)) if stored else None
        if row is None:
            presets = self.repo.list_presets()
            if not presets:
                raise TimerError("no presets exist")
            row = presets[0]
        return row

    def preset(self) -> Preset:
        row = self.preset_row()
        return Preset(
            focus_seconds=row.focus_seconds,
            short_break_seconds=row.short_break_seconds,
            long_break_seconds=row.long_break_seconds,
            cycles_before_long=row.cycles_before_long,
        )

    def select_preset(self, preset_id: int) -> None:
        """Takes effect from the next phase that starts. A phase in progress keeps its length."""
        if self.repo.get_preset(preset_id) is None:
            raise TimerError("preset does not exist")
        self.repo.set_setting(KEY_PRESET_ID, str(preset_id))

    def delete_preset(self, preset_id: int) -> None:
        if len(self.repo.list_presets()) <= 1:
            raise TimerError("the last preset cannot be deleted")
        self.repo.delete_preset(preset_id)

    @property
    def auto_start_breaks(self) -> bool:
        return self.repo.get_setting(KEY_AUTO_START_BREAKS, "1") == "1"

    @auto_start_breaks.setter
    def auto_start_breaks(self, value: bool) -> None:
        self.repo.set_setting(KEY_AUTO_START_BREAKS, "1" if value else "0")

    # reading ----------------------------------------------------------------

    def remaining_seconds(self) -> float:
        if self.state.status is Status.IDLE:
            return float(self.preset().seconds_for(self.state.phase))
        return timer.remaining_seconds(self.state, self.clock.now())

    def active_block(self) -> BlockRow | None:
        return self.repo.get_block(self.block_id) if self.block_id is not None else None

    def completed_focus_today(self) -> int:
        """Completed focus blocks since local midnight (OS timezone)."""
        local_now = self.clock.now().astimezone()
        midnight = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
        return self.repo.count_completed_focus(midnight, midnight + timedelta(days=1))

    # actions ----------------------------------------------------------------

    def _start(self, skill_id: int | None, work_type: str | None, now: datetime) -> None:
        phase = self.state.phase
        if phase is Phase.FOCUS:
            if skill_id is None:
                raise TimerError("choose a skill before starting a focus block")
            if work_type not in timer.WORK_TYPES:
                raise TimerError("choose a work type before starting a focus block")
        else:
            skill_id, work_type = None, None
        new_state = timer.start(self.state, self.preset(), now)
        assert new_state.planned_seconds is not None and new_state.ends_at is not None
        self.block_id = self.repo.insert_running_block(
            phase=phase.value,
            skill_id=skill_id,
            work_type=work_type,
            planned_seconds=new_state.planned_seconds,
            started_at=now,
            ends_at=new_state.ends_at,
        )
        self.state = new_state

    def start(self, skill_id: int | None = None, work_type: str | None = None) -> None:
        self._start(skill_id, work_type, self.clock.now())

    def pause(self) -> None:
        new_state = timer.pause(self.state, self.clock.now())
        assert self.block_id is not None and new_state.paused_remaining is not None
        self.repo.mark_paused(self.block_id, new_state.paused_remaining)
        self.state = new_state

    def resume(self) -> None:
        new_state = timer.resume(self.state, self.clock.now())
        assert self.block_id is not None and new_state.ends_at is not None
        self.repo.mark_running(self.block_id, new_state.ends_at)
        self.state = new_state

    def _abandon(self, status: str, new_state: TimerState) -> None:
        now = self.clock.now()
        with self.repo.transaction():
            if self.block_id is not None:
                self.repo.finish_block(
                    self.block_id,
                    status=status,
                    ended_at=now,
                    elapsed_seconds=timer.elapsed_seconds(self.state, now),
                )
            self._save_idle(new_state)
        self.block_id = None
        self.state = new_state

    def skip(self) -> None:
        """Move to the next phase. Works on a phase in progress or one waiting to start."""
        self._abandon("skipped", timer.skip(self.state, self.preset()))

    def reset(self) -> None:
        """In progress: drop the phase and return to its start. Idle: restart the whole cycle."""
        if self.state.status is Status.IDLE:
            self._abandon("reset", TimerState())
        else:
            self._abandon("reset", timer.reset(self.state))

    def tick(self) -> PhaseEnded | None:
        """Call regularly, and once at startup. Closes out a phase whose end time has passed.

        The block is recorded as ending at its planned end time, not at the moment this
        was noticed, so a frozen UI or a restart does not stretch the block.
        """
        now = self.clock.now()
        if not timer.is_due(self.state, now):
            return None
        assert self.block_id is not None and self.state.ends_at is not None
        assert self.state.planned_seconds is not None
        ended_block, ended_phase, ended_at = self.block_id, self.state.phase, self.state.ends_at
        late = (now - ended_at).total_seconds()
        new_state = timer.complete(self.state, self.preset())
        with self.repo.transaction():
            self.repo.finish_block(
                ended_block,
                status="completed",
                ended_at=ended_at,
                elapsed_seconds=float(self.state.planned_seconds),
            )
            self._save_idle(new_state)
        self.block_id = None
        self.state = new_state

        next_started = False
        if (
            new_state.phase is not Phase.FOCUS
            and self.auto_start_breaks
            and late <= AUTO_START_GRACE_SECONDS
        ):
            self._start(None, None, now)
            next_started = True
        return PhaseEnded(
            block_id=ended_block,
            phase=ended_phase,
            next_phase=new_state.phase,
            late_seconds=late,
            next_started=next_started,
        )

    # recaps -----------------------------------------------------------------

    def pending_recap(self) -> BlockRow | None:
        return self.repo.pending_recap_block()

    def save_recap(self, block_id: int, text: str) -> None:
        text = " ".join(text.split())
        if not text:
            raise TimerError("recap is empty")
        self.repo.save_recap(block_id, text)

    def skip_recap(self, block_id: int) -> None:
        self.repo.skip_recap(block_id)

    def skipped_recap_count(self) -> int:
        return self.repo.count_skipped_recaps()
