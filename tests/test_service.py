"""Service tests: state machine plus a real SQLite file, driven by a fake clock."""

from datetime import timedelta

import pytest

from focusdeck.core.service import AUTO_START_GRACE_SECONDS, TimerService
from focusdeck.core.timer import Phase, Status, TimerError
from focusdeck.db.connection import open_database
from focusdeck.db.repo import Repo


def restart(db_path, clock) -> TimerService:
    """Simulate closing the app and opening it again: new connection, new service."""
    return TimerService(Repo(open_database(db_path)), clock)


def test_focus_requires_skill_and_work_type(service, skill_id):
    with pytest.raises(TimerError):
        service.start(None, "Build")
    with pytest.raises(TimerError):
        service.start(skill_id, None)
    with pytest.raises(TimerError):
        service.start(skill_id, "Not a work type")
    assert service.state.status is Status.IDLE


def test_idle_shows_full_length_of_next_phase(service):
    assert service.remaining_seconds() == 1500


def test_completed_focus_block_is_stored(service, repo, clock, skill_id):
    start = clock.now()
    service.start(skill_id, "Build")
    block_id = service.block_id
    clock.advance(minutes=25)
    event = service.tick()

    assert event is not None and event.phase is Phase.FOCUS
    assert event.next_phase is Phase.SHORT_BREAK
    block = repo.get_block(block_id)
    assert block.status == "completed"
    assert block.skill_id == skill_id and block.work_type == "Build"
    assert block.started_at == start
    assert block.ended_at == start + timedelta(minutes=25)
    assert block.elapsed_seconds == 1500
    assert block.started_at.utcoffset() == timedelta(0)  # stored in UTC


def test_tick_before_end_does_nothing(service, clock, skill_id):
    service.start(skill_id, "Build")
    clock.advance(minutes=24, seconds=59)
    assert service.tick() is None
    assert service.remaining_seconds() == 1


def test_break_auto_starts_after_focus(service, clock, skill_id):
    service.start(skill_id, "Build")
    clock.advance(minutes=25, seconds=1)
    event = service.tick()
    assert event.next_started
    assert service.state.status is Status.RUNNING
    assert service.state.phase is Phase.SHORT_BREAK
    assert service.active_block().skill_id is None
    # The break is timed from when it actually started.
    assert service.remaining_seconds() == 300


def test_auto_start_can_be_turned_off(service, clock, skill_id):
    service.auto_start_breaks = False
    service.start(skill_id, "Build")
    clock.advance(minutes=25)
    event = service.tick()
    assert not event.next_started
    assert service.state.status is Status.IDLE
    assert service.state.phase is Phase.SHORT_BREAK


def test_focus_never_auto_starts(service, clock, skill_id):
    service.start(skill_id, "Build")
    clock.advance(minutes=25)
    service.tick()
    clock.advance(minutes=5)
    event = service.tick()
    assert event.phase is Phase.SHORT_BREAK and event.next_phase is Phase.FOCUS
    assert not event.next_started
    assert service.state.status is Status.IDLE


def test_block_ends_at_planned_time_even_if_noticed_late(service, repo, clock, skill_id):
    start = clock.now()
    service.start(skill_id, "Build")
    block_id = service.block_id
    clock.advance(minutes=25, seconds=40)  # UI was frozen for 40 seconds
    event = service.tick()
    assert event.late_seconds == 40
    assert repo.get_block(block_id).ended_at == start + timedelta(minutes=25)
    assert repo.get_block(block_id).elapsed_seconds == 1500


def test_pause_and_resume_persist(service, repo, clock, skill_id):
    service.start(skill_id, "Build")
    clock.advance(minutes=10)
    service.pause()
    block = repo.get_block(service.block_id)
    assert block.status == "paused" and block.remaining_seconds == 900 and block.ends_at is None

    clock.advance(minutes=60)
    assert service.remaining_seconds() == 900
    service.resume()
    block = repo.get_block(service.block_id)
    assert block.status == "running"
    assert block.ends_at == clock.now() + timedelta(minutes=15)


def test_skip_running_focus_records_partial_time(service, repo, clock, skill_id):
    service.start(skill_id, "Debug")
    block_id = service.block_id
    clock.advance(minutes=7)
    service.skip()
    block = repo.get_block(block_id)
    assert block.status == "skipped" and block.elapsed_seconds == 420
    assert service.state.phase is Phase.SHORT_BREAK
    assert service.state.completed_focus == 0
    assert service.pending_recap() is None  # only completed focus blocks ask for a recap


def test_skip_while_idle_advances_phase(service):
    service.skip()
    assert service.state.phase is Phase.SHORT_BREAK
    service.skip()
    assert service.state.phase is Phase.FOCUS


def test_reset_running_block(service, repo, clock, skill_id):
    service.start(skill_id, "Build")
    block_id = service.block_id
    clock.advance(minutes=3)
    service.reset()
    assert repo.get_block(block_id).status == "reset"
    assert service.state.status is Status.IDLE and service.state.phase is Phase.FOCUS
    assert service.remaining_seconds() == 1500


def test_reset_while_idle_restarts_cycle(service, clock, skill_id):
    service.auto_start_breaks = False
    service.start(skill_id, "Build")
    clock.advance(minutes=25)
    service.tick()
    assert service.state.completed_focus == 1
    service.reset()
    assert service.state.phase is Phase.FOCUS and service.state.completed_focus == 0


def test_long_break_after_four_focus_blocks(service, clock, skill_id):
    phases = []
    for _ in range(4):
        service.start(skill_id, "Study")
        clock.advance(minutes=25)
        event = service.tick()  # the break auto-starts
        phases.append(event.next_phase)
        clock.advance(minutes=30)
        # Noticed late, so the break is closed out and nothing auto-starts.
        service.tick()
    assert phases == [Phase.SHORT_BREAK, Phase.SHORT_BREAK, Phase.SHORT_BREAK, Phase.LONG_BREAK]
    assert service.state.completed_focus == 0


# Restart recovery -------------------------------------------------------------


def test_recovery_resumes_running_block_with_correct_remaining(db_path, repo, clock, skill_id):
    service = TimerService(repo, clock)
    service.start(skill_id, "Build")
    block_id = service.block_id
    clock.advance(minutes=10)
    repo.conn.close()  # app closed

    clock.advance(minutes=5)  # app stays closed for 5 minutes
    reopened = restart(db_path, clock)
    assert reopened.tick() is None
    assert reopened.block_id == block_id
    assert reopened.state.status is Status.RUNNING
    assert reopened.remaining_seconds() == 600  # 25 - 10 - 5 minutes


def test_recovery_closes_out_block_that_ended_while_closed(db_path, repo, clock, skill_id):
    start = clock.now()
    service = TimerService(repo, clock)
    service.start(skill_id, "Build")
    block_id = service.block_id
    clock.advance(minutes=10)
    repo.conn.close()

    clock.advance(minutes=120)  # reopened two hours later
    reopened = restart(db_path, clock)
    event = reopened.tick()
    assert event is not None and event.block_id == block_id
    assert not event.next_started  # no break starts by itself two hours late
    assert event.late_seconds > AUTO_START_GRACE_SECONDS
    block = reopened.repo.get_block(block_id)
    assert block.status == "completed"
    assert block.ended_at == start + timedelta(minutes=25)  # planned end, not reopen time
    assert reopened.state.status is Status.IDLE
    assert reopened.state.phase is Phase.SHORT_BREAK
    assert reopened.pending_recap().id == block_id  # the recap prompt still appears


def test_recovery_keeps_paused_block_paused(db_path, repo, clock, skill_id):
    service = TimerService(repo, clock)
    service.start(skill_id, "Build")
    clock.advance(minutes=10)
    service.pause()
    repo.conn.close()

    clock.advance(minutes=600)
    reopened = restart(db_path, clock)
    assert reopened.tick() is None
    assert reopened.state.status is Status.PAUSED
    assert reopened.remaining_seconds() == 900


def test_recovery_keeps_cycle_position_when_idle(db_path, repo, clock, skill_id):
    service = TimerService(repo, clock)
    service.auto_start_breaks = False
    service.start(skill_id, "Build")
    clock.advance(minutes=25)
    service.tick()
    repo.conn.close()

    reopened = restart(db_path, clock)
    assert reopened.state.phase is Phase.SHORT_BREAK
    assert reopened.state.completed_focus == 1


# Recaps -----------------------------------------------------------------------


def finish_focus(service, clock, skill_id) -> int:
    service.start(skill_id, "Build")
    block_id = service.block_id
    clock.advance(minutes=25)
    service.tick()
    return block_id


def test_recap_saved(service, repo, clock, skill_id):
    block_id = finish_focus(service, clock, skill_id)
    assert service.pending_recap().id == block_id
    service.save_recap(block_id, "  wrote the   timer tests \n")
    assert repo.get_block(block_id).recap == "wrote the timer tests"
    assert service.pending_recap() is None
    assert service.skipped_recap_count() == 0


def test_empty_recap_rejected(service, clock, skill_id):
    block_id = finish_focus(service, clock, skill_id)
    with pytest.raises(TimerError):
        service.save_recap(block_id, "   ")
    assert service.pending_recap().id == block_id


def test_skipped_recaps_are_counted(service, clock, skill_id):
    service.auto_start_breaks = False
    first = finish_focus(service, clock, skill_id)
    service.skip_recap(first)
    service.skip()  # skip the break
    second = finish_focus(service, clock, skill_id)
    service.skip_recap(second)
    assert service.pending_recap() is None
    assert service.skipped_recap_count() == 2


def test_recap_prompt_survives_restart(db_path, repo, clock, skill_id):
    service = TimerService(repo, clock)
    block_id = finish_focus(service, clock, skill_id)
    repo.conn.close()  # closed with the recap dialog still open
    reopened = restart(db_path, clock)
    assert reopened.pending_recap().id == block_id


# Presets ----------------------------------------------------------------------


def test_default_presets_exist(repo):
    names = [p.name for p in repo.list_presets()]
    assert names == ["25/5/15", "50/10/30"]


def test_selected_preset_sets_durations(service, repo, clock, skill_id):
    long_preset = next(p for p in repo.list_presets() if p.name == "50/10/30")
    service.select_preset(long_preset.id)
    assert service.remaining_seconds() == 3000
    service.start(skill_id, "Build")
    assert service.active_block().planned_seconds == 3000


def test_changing_preset_does_not_change_running_phase(service, repo, clock, skill_id):
    service.start(skill_id, "Build")
    clock.advance(minutes=5)
    service.select_preset(repo.list_presets()[1].id)
    assert service.remaining_seconds() == 1200


def test_custom_preset_and_edit(service, repo, clock, skill_id):
    preset_id = repo.create_preset("Test", 60, 30, 45, 2)
    service.select_preset(preset_id)
    repo.update_preset(preset_id, "Test", 90, 30, 45, 2)
    assert service.remaining_seconds() == 90
    service.start(skill_id, "Build")
    clock.advance(seconds=90)
    service.tick()
    clock.advance(seconds=30)
    service.tick()
    service.start(skill_id, "Build")
    clock.advance(seconds=90)
    assert service.tick().next_phase is Phase.LONG_BREAK  # after 2 cycles


def test_deleting_selected_preset_falls_back(service, repo):
    preset_id = repo.create_preset("Temp", 60, 30, 45, 2)
    service.select_preset(preset_id)
    service.delete_preset(preset_id)
    assert service.preset_row().name == "25/5/15"


def test_last_preset_cannot_be_deleted(service, repo):
    first, second = repo.list_presets()
    service.delete_preset(first.id)
    with pytest.raises(TimerError):
        service.delete_preset(second.id)


def test_completed_focus_today_counts_only_completed(service, clock, skill_id):
    service.auto_start_breaks = False
    finish_focus(service, clock, skill_id)
    service.skip()
    service.start(skill_id, "Build")
    clock.advance(minutes=3)
    service.skip()
    assert service.completed_focus_today() == 1
