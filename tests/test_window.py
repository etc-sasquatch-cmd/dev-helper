from focusdeck.ui import window

SCREEN = (0, 0, 1920, 1080)


def test_defaults_when_nothing_saved(repo):
    geometry = window.load_geometry(repo, SCREEN)
    assert geometry.position is None
    assert geometry.size == window.DEFAULT_SIZE_NORMAL
    assert geometry.on_top is True
    assert geometry.compact is False


def test_position_and_size_roundtrip(repo):
    window.save_position_and_size(repo, (1500, 200), (360, 820), compact=False)
    geometry = window.load_geometry(repo, SCREEN)
    assert geometry.position == (1500, 200)
    assert geometry.size == (360, 820)


def test_compact_and_normal_sizes_are_kept_separately(repo):
    window.save_position_and_size(repo, (10, 10), (360, 820), compact=False)
    window.save_position_and_size(repo, (10, 10), (300, 480), compact=True)
    assert window.load_size(repo, compact=False) == (360, 820)
    assert window.load_size(repo, compact=True) == (300, 480)


def test_minimized_position_is_not_saved(repo):
    window.save_position_and_size(repo, (100, 100), (360, 820), compact=False)
    window.save_position_and_size(repo, (-32000, -32000), (160, 28), compact=False)
    assert window.load_geometry(repo, SCREEN).position == (100, 100)


def test_one_pixel_rounding_drift_is_not_saved(repo):
    window.save_position_and_size(repo, (-2066, 333), (340, 800), compact=False)
    window.save_position_and_size(repo, (-2066, 332), (340, 800), compact=False)
    assert window.load_geometry(repo, (-3840, 0, 5760, 1080)).position == (-2066, 333)
    window.save_position_and_size(repo, (-2066, 330), (340, 800), compact=False)
    assert window.load_geometry(repo, (-3840, 0, 5760, 1080)).position == (-2066, 330)


def test_offscreen_position_is_ignored(repo):
    # Saved on a second monitor that is no longer connected.
    window.save_position_and_size(repo, (2500, 300), (360, 820), compact=False)
    assert window.load_geometry(repo, SCREEN).position is None


def test_second_monitor_to_the_left_is_valid(repo):
    window.save_position_and_size(repo, (-1500, 300), (360, 820), compact=False)
    assert window.load_geometry(repo, (-1920, 0, 3840, 1080)).position == (-1500, 300)


def test_corrupt_settings_fall_back_to_defaults(repo):
    repo.set_setting(window.KEY_POSITION, "not json")
    repo.set_setting(window.KEY_SIZE_NORMAL, "[1, 2]")
    geometry = window.load_geometry(repo, SCREEN)
    assert geometry.position is None
    assert geometry.size == window.DEFAULT_SIZE_NORMAL
