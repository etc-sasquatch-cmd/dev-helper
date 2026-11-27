"""Entry point for the portable Windows executable."""

import ctypes
import multiprocessing
import os
import winreg

WEBVIEW2_KEY = r"SOFTWARE\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"


def has_webview2() -> bool:
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        try:
            with winreg.OpenKey(
                hive, WEBVIEW2_KEY, 0, winreg.KEY_READ | winreg.KEY_WOW64_32KEY
            ) as key:
                version, kind = winreg.QueryValueEx(key, "pv")
        except OSError:
            continue
        if kind == winreg.REG_SZ and version.strip() not in ("", "0.0.0.0"):
            return True
    return False


def main() -> None:
    if not has_webview2():
        ctypes.windll.user32.MessageBoxW(
            None,
            "FocusDeck needs the Microsoft Edge WebView2 Evergreen Runtime. "
            "Install it from Microsoft's WebView2 download page, then reopen FocusDeck.",
            "FocusDeck",
            0x10,
        )
        raise SystemExit(1)

    from focusdeck.db.connection import data_dir

    folder = data_dir()
    os.environ.setdefault("NICEGUI_STORAGE_PATH", str(folder / "nicegui"))
    os.environ.setdefault("MPLCONFIGDIR", str(folder / "matplotlib"))

    from focusdeck.ui import app as widget

    widget.app.native.start_args["storage_path"] = str(folder / "webview")
    widget.run()


if __name__ == "__main__":
    multiprocessing.freeze_support()
    main()
