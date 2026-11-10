"""Turn a pasted YouTube link into a video or playlist ID. Pure text handling, no network."""

import re
from urllib.parse import parse_qs, urlparse

VIDEO_ID = re.compile(r"[A-Za-z0-9_-]{11}")
PLAYLIST_ID = re.compile(r"[A-Za-z0-9_-]{12,}")
HOSTS = {"youtube.com", "youtu.be", "youtube-nocookie.com"}
PATH_PREFIXES = ("/live/", "/embed/", "/shorts/", "/v/")
# Lists that belong to the signed-in viewer. The embedded player cannot open them.
PERSONAL_LISTS = {"WL", "LL"}


class LinkError(ValueError):
    """The text is not a YouTube link this app can play."""


def _video(candidate: str) -> tuple[str, str]:
    if not VIDEO_ID.fullmatch(candidate):
        raise LinkError("that link does not contain a valid YouTube video ID")
    return "video", candidate


def _host(netloc: str) -> str:
    host = netloc.lower().split(":")[0]
    for prefix in ("www.", "m.", "music."):
        host = host.removeprefix(prefix)
    return host


def parse_youtube_link(text: str) -> tuple[str, str]:
    """Return ("video", id) or ("playlist", id). Raises LinkError for anything else.

    A link that names both a video and a playlist is treated as the playlist, except for
    auto-generated mixes (IDs starting with "RD"), which cannot be embedded: there the
    single video is used.
    """
    text = text.strip()
    if not text:
        raise LinkError("paste a YouTube link")
    if VIDEO_ID.fullmatch(text):
        return "video", text
    if "//" not in text:
        text = "https://" + text
    url = urlparse(text)
    host = _host(url.netloc)
    if host not in HOSTS:
        raise LinkError("that is not a YouTube link")

    query = parse_qs(url.query)
    video = query.get("v", [""])[0]
    playlist = query.get("list", [""])[0]
    path = url.path

    if host == "youtu.be":
        video = path.strip("/").split("/")[0]
    else:
        for prefix in PATH_PREFIXES:
            if path.startswith(prefix):
                video = path[len(prefix) :].split("/")[0]

    if playlist in PERSONAL_LISTS and not video:
        raise LinkError("Watch Later and Liked lists are private and cannot be played here")
    usable_playlist = (
        playlist
        and playlist not in PERSONAL_LISTS
        and not playlist.startswith("RD")
        and PLAYLIST_ID.fullmatch(playlist)
    )
    if usable_playlist:
        return "playlist", playlist
    if video:
        return _video(video)
    if playlist:
        raise LinkError("that kind of playlist (an automatic mix) cannot be played here")
    raise LinkError("that link does not point to a video or playlist")
