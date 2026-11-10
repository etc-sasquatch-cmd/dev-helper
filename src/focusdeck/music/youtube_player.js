// FocusDeck's only JavaScript: the official YouTube IFrame Player API, nothing else.
// Python calls fdLoad / fdPlay / fdPause / fdSetVolume. Player events go back to Python
// through NiceGUI's emitEvent().

const fd = {
  player: null,
  ready: false,
  apiRequested: false,
  wanted: null, // {kind, id}: the newest thing Python asked for
  volume: 50,
  errorsInARow: 0,
};

function fdApplyWanted() {
  const w = fd.wanted;
  if (!fd.ready || !w) return;
  fd.player.setVolume(fd.volume);
  if (w.kind === "playlist") {
    fd.player.setLoop(true);
    fd.player.loadPlaylist({ listType: "playlist", list: w.id });
  } else {
    fd.player.loadVideoById(w.id);
  }
}

function fdOnState(e) {
  if (e.data === YT.PlayerState.PLAYING) fd.errorsInARow = 0;
  // A single video is background music, so start it again when it ends.
  if (e.data === YT.PlayerState.ENDED && fd.wanted && fd.wanted.kind === "video") {
    fd.player.seekTo(0);
    fd.player.playVideo();
  }
  emitEvent("yt_state", { state: e.data });
}

function fdOnError(e) {
  fd.errorsInARow += 1;
  // One bad video should not stop a whole playlist: move on, but give up after three.
  const skip = fd.wanted && fd.wanted.kind === "playlist" && fd.errorsInARow <= 3;
  if (skip) fd.player.nextVideo();
  emitEvent("yt_error", { code: e.data, skipped: Boolean(skip) });
}

function fdCreatePlayer() {
  fd.player = new YT.Player("fd-player", {
    width: "100%",
    height: "100%",
    // origin tells YouTube who is embedding it. Without it the player reports error 153.
    playerVars: { origin: window.location.origin, playsinline: 1 },
    events: {
      onReady: () => {
        fd.ready = true;
        emitEvent("yt_ready", {});
        fdApplyWanted();
      },
      onStateChange: fdOnState,
      onError: fdOnError,
      onAutoplayBlocked: () => emitEvent("yt_autoplay_blocked", {}),
    },
  });
}

function fdLoad(kind, id, volume) {
  fd.wanted = { kind: kind, id: id };
  fd.volume = volume;
  fd.errorsInARow = 0;
  if (fd.ready) {
    fdApplyWanted();
    return;
  }
  if (fd.apiRequested) return; // still loading: onReady will pick up fd.wanted
  fd.apiRequested = true;
  window.onYouTubeIframeAPIReady = fdCreatePlayer;
  const tag = document.createElement("script");
  tag.src = "https://www.youtube.com/iframe_api";
  tag.onerror = () => {
    fd.apiRequested = false;
    tag.remove();
    emitEvent("yt_unavailable", {});
  };
  document.head.appendChild(tag);
}

function fdPlay() {
  if (fd.ready) fd.player.playVideo();
}

function fdPause() {
  if (fd.ready) fd.player.pauseVideo();
}

function fdSetVolume(percent) {
  fd.volume = percent;
  if (fd.ready) fd.player.setVolume(percent);
}
