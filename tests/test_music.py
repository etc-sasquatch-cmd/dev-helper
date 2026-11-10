import pytest

from focusdeck.core.timer import Status
from focusdeck.db import connection
from focusdeck.db.connection import connect, migrate
from focusdeck.db.repo import Repo
from focusdeck.music.controller import MusicController
from focusdeck.music.links import LinkError, parse_youtube_link
from focusdeck.music.provider import MusicSource
from focusdeck.music.youtube import YouTubeProvider, describe_error

VIDEO = "M7lc1UVf-VE"
PLAYLIST = "PLabcdefghijklmnopqrstuvwxyz012345"

LOFI = MusicSource(1, "Lo-fi", "youtube", "video", VIDEO)
JAZZ = MusicSource(2, "Jazz", "youtube", "playlist", PLAYLIST)


# links ------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        VIDEO,
        f"https://www.youtube.com/watch?v={VIDEO}",
        f"https://www.youtube.com/watch?v={VIDEO}&t=42s",
        f"http://youtube.com/watch?feature=share&v={VIDEO}",
        f"https://youtu.be/{VIDEO}?si=abc",
        f"youtu.be/{VIDEO}",
        f"https://m.youtube.com/watch?v={VIDEO}",
        f"https://music.youtube.com/watch?v={VIDEO}",
        f"https://www.youtube.com/live/{VIDEO}?feature=share",
        f"https://www.youtube.com/embed/{VIDEO}",
        f"https://www.youtube.com/shorts/{VIDEO}",
        f"  https://www.youtube.com/watch?v={VIDEO}  ",
        # An automatic mix cannot be embedded, so the single video is used.
        f"https://www.youtube.com/watch?v={VIDEO}&list=RD{VIDEO}",
        f"https://www.youtube.com/watch?v={VIDEO}&list=WL",
    ],
)
def test_video_links(text):
    assert parse_youtube_link(text) == ("video", VIDEO)


@pytest.mark.parametrize(
    "text",
    [
        f"https://www.youtube.com/playlist?list={PLAYLIST}",
        f"https://www.youtube.com/watch?v={VIDEO}&list={PLAYLIST}&index=3",
        f"https://music.youtube.com/playlist?list={PLAYLIST}",
        f"https://youtu.be/{VIDEO}?list={PLAYLIST}",
    ],
)
def test_playlist_links(text):
    assert parse_youtube_link(text) == ("playlist", PLAYLIST)


@pytest.mark.parametrize(
    "text",
    [
        "",
        "   ",
        "lofi beats",
        "https://example.com/watch?v=" + VIDEO,
        "https://notyoutube.com/watch?v=" + VIDEO,
        "https://www.youtube.com/",
        "https://www.youtube.com/watch?v=short",
        "https://www.youtube.com/watch?v=" + VIDEO + "');alert(1);//",
        "https://www.youtube.com/playlist?list=WL",
        "https://www.youtube.com/playlist?list=RDabcdefghijk",
        "https://open.spotify.com/playlist/37i9dQZF1DX8Uebhn9wzrS",
    ],
)
def test_bad_links_are_rejected(text):
    with pytest.raises(LinkError):
        parse_youtube_link(text)


# storage ----------------------------------------------------------------------


def test_sources_roundtrip_sorted_by_name(repo):
    repo.create_music_source("Zen", "youtube", "playlist", PLAYLIST)
    new_id = repo.create_music_source("  Lo-fi   beats ", "youtube", "video", VIDEO)
    sources = repo.list_music_sources()
    assert [s.name for s in sources] == ["Lo-fi beats", "Zen"]
    assert sources[0] == MusicSource(new_id, "Lo-fi beats", "youtube", "video", VIDEO)


def test_source_validation(repo):
    repo.create_music_source("Lo-fi", "youtube", "video", VIDEO)
    with pytest.raises(ValueError, match="name"):
        repo.create_music_source(" ", "youtube", "video", "abcdefghijk")
    with pytest.raises(ValueError, match="already has that name"):
        repo.create_music_source("LO-FI", "youtube", "video", "abcdefghijk")
    with pytest.raises(ValueError, match="already saved"):
        repo.create_music_source("Other name", "youtube", "video", VIDEO)
    with pytest.raises(ValueError, match="unknown kind"):
        repo.create_music_source("Other name", "soundcloud", "video", "abcdefghijk")
    assert len(repo.list_music_sources()) == 1


def test_profile_starts_silent_and_maps_each_phase(repo):
    profile = repo.get_profile()
    assert (profile.focus_source_id, profile.short_break_source_id) == (None, None)
    a = repo.create_music_source("A", "youtube", "video", VIDEO)
    b = repo.create_music_source("B", "youtube", "playlist", PLAYLIST)
    repo.set_profile_source("focus", a)
    repo.set_profile_source("long_break", b)
    profile = repo.get_profile()
    assert profile.source_id_for("focus") == a
    assert profile.source_id_for("short_break") is None
    assert profile.source_id_for("long_break") == b
    repo.set_profile_source("focus", None)
    assert repo.get_profile().source_id_for("focus") is None


def test_profile_rejects_unknown_phase(repo):
    with pytest.raises(KeyError):
        repo.set_profile_source("focus_source_id = 1; --", None)


def test_deleting_a_source_silences_the_phases_that_used_it(repo):
    a = repo.create_music_source("A", "youtube", "video", VIDEO)
    repo.set_profile_source("focus", a)
    repo.set_profile_source("short_break", a)
    repo.delete_music_source(a)
    profile = repo.get_profile()
    assert profile.focus_source_id is None and profile.short_break_source_id is None
    assert repo.list_music_sources() == []


def test_upgrade_from_m1_keeps_data_and_drops_saved_window_sizes(db_path, tmp_path, monkeypatch):
    real_dir = connection.MIGRATIONS_DIR
    m1_only = tmp_path / "m1"
    m1_only.mkdir()
    first = real_dir / "0001_timer.sql"
    (m1_only / first.name).write_text(first.read_text(encoding="utf-8"), encoding="utf-8")

    conn = connect(db_path)
    monkeypatch.setattr(connection, "MIGRATIONS_DIR", m1_only)
    assert migrate(conn) == 1
    repo = Repo(conn)
    skill = repo.create_skill("Python")
    repo.set_setting("ui.window.size.normal", "[340, 560]")
    repo.set_setting("ui.window.size.compact", "[300, 250]")
    repo.set_setting("ui.window.position", "[10, 20]")

    monkeypatch.setattr(connection, "MIGRATIONS_DIR", real_dir)
    assert migrate(conn) >= 2
    assert [s.id for s in repo.list_skills()] == [skill]
    assert repo.get_setting("ui.window.size.normal") is None
    assert repo.get_setting("ui.window.size.compact") is None
    assert repo.get_setting("ui.window.position") == "[10, 20]"
    assert repo.get_profile().focus_source_id is None
    conn.close()


# controller -------------------------------------------------------------------


class FakeProvider:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.available = True

    def is_available(self) -> bool:
        return self.available

    def play(self, source: MusicSource) -> None:
        self.calls.append(f"play {source.name}")

    def pause(self) -> None:
        self.calls.append("pause")

    def resume(self) -> None:
        self.calls.append("resume")

    def set_volume(self, percent: int) -> None:
        self.calls.append(f"volume {percent}")


@pytest.fixture
def provider() -> FakeProvider:
    return FakeProvider()


@pytest.fixture
def music(provider) -> MusicController:
    return MusicController(provider)


def test_nothing_happens_while_idle_or_silent(music, provider):
    music.sync(Status.IDLE, LOFI)
    music.sync(Status.RUNNING, None)
    music.sync(Status.PAUSED, None)
    assert provider.calls == []


def test_running_phase_plays_its_source_once(music, provider):
    for _ in range(5):  # the UI calls sync twice a second
        music.sync(Status.RUNNING, LOFI)
    assert provider.calls == ["play Lo-fi"]


def test_pause_and_resume_follow_the_timer(music, provider):
    music.sync(Status.RUNNING, LOFI)
    music.sync(Status.PAUSED, LOFI)
    music.sync(Status.PAUSED, LOFI)
    music.sync(Status.RUNNING, LOFI)
    assert provider.calls == ["play Lo-fi", "pause", "resume"]


def test_next_phase_with_the_same_source_keeps_playing(music, provider):
    music.sync(Status.RUNNING, LOFI)  # focus
    music.sync(Status.RUNNING, LOFI)  # break started by itself, same source
    assert provider.calls == ["play Lo-fi"]


def test_next_phase_with_a_different_source_switches(music, provider):
    music.sync(Status.RUNNING, LOFI)
    music.sync(Status.RUNNING, JAZZ)
    assert provider.calls == ["play Lo-fi", "play Jazz"]


def test_silent_phase_and_idle_stop_the_music(music, provider):
    music.sync(Status.RUNNING, LOFI)
    music.sync(Status.RUNNING, None)  # silent break
    music.sync(Status.RUNNING, JAZZ)
    music.sync(Status.IDLE, LOFI)  # phase ended, focus is waiting to be started
    assert provider.calls == ["play Lo-fi", "pause", "play Jazz", "pause"]


def test_same_source_after_a_stop_resumes_instead_of_restarting(music, provider):
    music.sync(Status.RUNNING, LOFI)
    music.sync(Status.IDLE, LOFI)
    music.sync(Status.RUNNING, LOFI)
    assert provider.calls == ["play Lo-fi", "pause", "resume"]


def test_dialog_over_the_player_pauses_and_closing_it_resumes(music, provider):
    music.sync(Status.RUNNING, LOFI)
    music.set_covered(True)
    music.sync(Status.RUNNING, LOFI)
    assert provider.calls == ["play Lo-fi", "pause"]
    music.set_covered(False)
    assert provider.calls == ["play Lo-fi", "pause", "resume"]


def test_phase_change_while_covered_plays_the_new_source_after_uncovering(music, provider):
    music.sync(Status.RUNNING, LOFI)
    music.set_covered(True)
    music.sync(Status.RUNNING, JAZZ)
    music.set_covered(False)
    assert provider.calls == ["play Lo-fi", "pause", "play Jazz"]


def test_failed_source_is_not_retried_until_something_changes(music, provider):
    music.sync(Status.RUNNING, LOFI)
    music.on_error()
    music.sync(Status.RUNNING, LOFI)
    music.sync(Status.RUNNING, LOFI)
    assert provider.calls == ["play Lo-fi"]
    music.sync(Status.PAUSED, LOFI)  # nothing is playing, so nothing to pause
    music.sync(Status.RUNNING, LOFI)  # the user resumed: one more try
    assert provider.calls == ["play Lo-fi", "play Lo-fi"]


def test_failed_source_does_not_block_a_different_one(music, provider):
    music.sync(Status.RUNNING, LOFI)
    music.on_error()
    music.sync(Status.RUNNING, JAZZ)
    assert provider.calls == ["play Lo-fi", "play Jazz"]


def test_unavailable_provider_is_left_alone_then_catches_up(music, provider):
    provider.available = False
    music.sync(Status.RUNNING, LOFI)
    assert provider.calls == []
    provider.available = True
    music.sync(Status.RUNNING, LOFI)
    assert provider.calls == ["play Lo-fi"]


# YouTube provider -------------------------------------------------------------


def test_youtube_provider_sends_quoted_commands():
    sent: list[str] = []
    yt = YouTubeProvider(sent.append, volume=30)
    assert yt.is_available() is False
    yt.play(LOFI)
    yt.play(JAZZ)
    yt.pause()
    yt.resume()
    yt.set_volume(140)
    assert sent == [
        f'fdLoad("video", "{VIDEO}", 30)',
        f'fdLoad("playlist", "{PLAYLIST}", 30)',
        "fdPause()",
        "fdPlay()",
        "fdSetVolume(100)",
    ]


def test_youtube_provider_cannot_be_tricked_into_running_stored_text():
    sent: list[str] = []
    evil = MusicSource(9, "x", "youtube", "video", '");alert(1);("')
    YouTubeProvider(sent.append).play(evil)
    assert sent == ['fdLoad("video", "\\");alert(1);(\\"", 50)']


def test_youtube_provider_rejects_other_providers():
    with pytest.raises(ValueError):
        YouTubeProvider(lambda _: None).play(MusicSource(1, "x", "spotify", "playlist", "abc"))


def test_error_messages():
    assert describe_error(150) == describe_error(101)
    assert "outside youtube.com" in describe_error(150)
    assert "restricted" in describe_error(151)
    assert "Referer" in describe_error(153)
    assert describe_error(999) == "YouTube player error 999."
    assert describe_error(None) == "YouTube player error None."
