"""M0 spike 2: YouTube IFrame player in the native window, driven from Python via JS.

Run: .venv\\Scripts\\python.exe spikes\\spike_youtube.py [--auto] [--video ID]
--auto runs a scripted play -> pause -> play sequence and then closes the window.
Every player event is appended to spikes/out/youtube.log.
Throwaway code.
"""

import argparse
from datetime import UTC, datetime
from pathlib import Path

from nicegui import app, ui

HERE = Path(__file__).parent
OUT = HERE / "out"
LOG = OUT / "youtube.log"
STATES = {-1: "unstarted", 0: "ended", 1: "playing", 2: "paused", 3: "buffering", 5: "cued"}


def log(message: str) -> None:
    stamp = datetime.now(UTC).strftime("%H:%M:%S.%f")[:-3]
    with LOG.open("a", encoding="utf-8") as f:
        f.write(f"{stamp} {message}\n")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--auto", action="store_true")
    parser.add_argument("--video", default="jfKfPfyJRdk")
    args = parser.parse_args()

    OUT.mkdir(exist_ok=True)
    LOG.write_text("", encoding="utf-8")
    app.add_static_files("/spike-static", HERE)
    app.native.window_args["on_top"] = True

    @ui.page("/")
    def index() -> None:
        # YouTube needs a Referer from a real http origin, otherwise it shows error 153.
        ui.add_head_html('<meta name="referrer" content="strict-origin-when-cross-origin">')
        ui.add_head_html('<script src="/spike-static/youtube_player.js"></script>')

        status = ui.label("Player: loading")
        ui.html('<div id="fd-player"></div>', sanitize=False)
        with ui.row():
            ui.button("Play", on_click=lambda: ui.run_javascript("fdPlay()"))
            ui.button("Pause", on_click=lambda: ui.run_javascript("fdPause()"))
            ui.button("Close", on_click=app.shutdown)

        def on_ready(e) -> None:
            log(f"ready origin={e.args.get('origin')}")
            status.text = "Player: ready"
            if args.auto:
                log("python -> play")
                ui.run_javascript("fdPlay()")

        def on_state(e) -> None:
            name = STATES.get(e.args.get("state"), str(e.args.get("state")))
            log(f"state {name}")
            status.text = f"Player: {name}"

        def on_error(e) -> None:
            log(f"ERROR code={e.args.get('code')}")
            status.text = f"Player error {e.args.get('code')}"

        def on_blocked(_e) -> None:
            log("autoplay blocked")
            status.text = "Autoplay blocked: press Play"

        ui.on("yt_ready", on_ready)
        ui.on("yt_state", on_state)
        ui.on("yt_error", on_error)
        ui.on("yt_autoplay_blocked", on_blocked)

        ui.timer(
            0.5,
            lambda: ui.run_javascript(f"fdCreatePlayer({args.video!r})"),
            once=True,
            immediate=False,
        )

        if args.auto:

            def pause() -> None:
                log("python -> pause")
                ui.run_javascript("fdPause()")

            def play() -> None:
                log("python -> play")
                ui.run_javascript("fdPlay()")

            ui.timer(14, pause, once=True, immediate=False)
            ui.timer(19, play, once=True, immediate=False)
            ui.timer(26, app.shutdown, once=True, immediate=False)

    ui.run(
        native=True,
        reload=False,
        title="FocusDeck spike YouTube",
        window_size=(540, 460),
        show_welcome_message=False,
    )


if __name__ in {"__main__", "__mp_main__"}:
    main()
