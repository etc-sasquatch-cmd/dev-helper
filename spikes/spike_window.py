"""M0 spike 1: NiceGUI native window opens, stays on top, idles at low CPU.

Run: .venv\\Scripts\\python.exe spikes\\spike_window.py [--seconds N]
With --seconds the window closes itself after N seconds (used for unattended checks).
Throwaway code.
"""

import argparse
import os
from pathlib import Path

from nicegui import app, ui

TITLE = "FocusDeck spike window"
OUT = Path(__file__).parent / "out"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seconds", type=float, default=0)
    args = parser.parse_args()

    OUT.mkdir(exist_ok=True)
    (OUT / "window.pid").write_text(str(os.getpid()), encoding="utf-8")

    app.native.window_args["on_top"] = True

    @ui.page("/")
    def index() -> None:
        ui.label("FocusDeck spike: native window").classes("text-lg")
        ui.label("This window should stay above other windows.")
        ui.button("Close", on_click=app.shutdown)
        if args.seconds > 0:
            ui.timer(args.seconds, app.shutdown, once=True, immediate=False)

    ui.run(
        native=True,
        reload=False,
        title=TITLE,
        window_size=(360, 240),
        show_welcome_message=False,
    )


if __name__ in {"__main__", "__mp_main__"}:
    main()
