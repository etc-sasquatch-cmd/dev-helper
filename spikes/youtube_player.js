// M0 spike 2: official YouTube IFrame Player API, nothing else.
// Reports player events to Python through NiceGUI's emitEvent().

let fdPlayer = null;

function fdCreatePlayer(videoId) {
  const tag = document.createElement("script");
  tag.src = "https://www.youtube.com/iframe_api";
  document.head.appendChild(tag);

  window.onYouTubeIframeAPIReady = () => {
    fdPlayer = new YT.Player("fd-player", {
      width: 480,
      height: 270,
      videoId: videoId,
      playerVars: { origin: window.location.origin, playsinline: 1 },
      events: {
        onReady: () => emitEvent("yt_ready", { origin: window.location.origin }),
        onStateChange: (e) => emitEvent("yt_state", { state: e.data }),
        onError: (e) => emitEvent("yt_error", { code: e.data }),
        onAutoplayBlocked: () => emitEvent("yt_autoplay_blocked", {}),
      },
    });
  };
}

function fdPlay() {
  if (fdPlayer) fdPlayer.playVideo();
}

function fdPause() {
  if (fdPlayer) fdPlayer.pauseVideo();
}
