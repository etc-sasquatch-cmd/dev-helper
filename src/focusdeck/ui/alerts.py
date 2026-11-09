"""Phase-end sound and Windows desktop notification. Standard library only.

Both are best effort: a failure here is reported to the caller and never stops the timer.
"""

import ctypes
import math
import struct
import threading
import time
import wave
from ctypes import wintypes
from pathlib import Path

# Sound ------------------------------------------------------------------------

SAMPLE_RATE = 22050


def _write_chime(path: Path, notes: tuple[float, ...]) -> None:
    """Write a short, soft chime: each note is a sine wave that fades out."""
    frames = bytearray()
    note_seconds = 0.38
    for frequency in notes:
        count = int(SAMPLE_RATE * note_seconds)
        for i in range(count):
            t = i / SAMPLE_RATE
            fade = math.exp(-5.0 * t) * min(1.0, i / 200)  # soft attack, exponential decay
            sample = 0.45 * fade * math.sin(2 * math.pi * frequency * t)
            frames += struct.pack("<h", int(sample * 32767))
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(SAMPLE_RATE)
        wav.writeframes(bytes(frames))


CHIMES = {
    # Rising notes when focus ends, falling notes when a break ends.
    "focus_end": (523.25, 659.25, 783.99),
    "break_end": (783.99, 587.33),
}


def play_sound(kind: str, cache_dir: Path) -> bool:
    """Play the chime without blocking. Returns False if it could not be played."""
    try:
        import winsound

        path = cache_dir / f"chime_{kind}.wav"
        if not path.exists():
            _write_chime(path, CHIMES[kind])
        winsound.PlaySound(
            str(path), winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT
        )
        return True
    except Exception:
        return False


# Notification -------------------------------------------------------------------

NIM_ADD, NIM_MODIFY, NIM_DELETE = 0, 1, 2
NIF_ICON, NIF_TIP, NIF_INFO = 0x2, 0x4, 0x10
NIIF_INFO = 0x1
IDI_INFORMATION = 32516
BALLOON_SECONDS = 8


class _NotifyIconData(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("hWnd", wintypes.HWND),
        ("uID", wintypes.UINT),
        ("uFlags", wintypes.UINT),
        ("uCallbackMessage", wintypes.UINT),
        ("hIcon", wintypes.HICON),
        ("szTip", wintypes.WCHAR * 128),
        ("dwState", wintypes.DWORD),
        ("dwStateMask", wintypes.DWORD),
        ("szInfo", wintypes.WCHAR * 256),
        ("uVersion", wintypes.UINT),
        ("szInfoTitle", wintypes.WCHAR * 64),
        ("dwInfoFlags", wintypes.DWORD),
        ("guidItem", ctypes.c_byte * 16),
        ("hBalloonIcon", wintypes.HICON),
    ]


_last_result: bool | None = None


def last_notification_result() -> bool | None:
    """True if Windows accepted the latest notification, False if refused, None if unknown."""
    return _last_result


def _show_balloon(title: str, message: str) -> None:
    """Add a temporary tray icon, show a notification from it, then remove it.

    Windows needs a window to own the tray icon, so a hidden one is created here.
    It lives only as long as this thread.
    """
    global _last_result
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    shell32 = ctypes.WinDLL("shell32", use_last_error=True)
    user32.CreateWindowExW.restype = wintypes.HWND
    user32.CreateWindowExW.argtypes = [
        wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
        ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
        wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID,
    ]  # fmt: skip
    user32.DestroyWindow.argtypes = [wintypes.HWND]
    user32.LoadIconW.restype = wintypes.HICON
    user32.LoadIconW.argtypes = [wintypes.HINSTANCE, wintypes.LPCWSTR]
    user32.PeekMessageW.argtypes = [
        ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT, wintypes.UINT
    ]  # fmt: skip
    shell32.Shell_NotifyIconW.restype = wintypes.BOOL
    shell32.Shell_NotifyIconW.argtypes = [wintypes.DWORD, ctypes.POINTER(_NotifyIconData)]

    hwnd = user32.CreateWindowExW(0, "STATIC", "FocusDeck", 0, 0, 0, 0, 0, None, None, None, None)
    if not hwnd:
        _last_result = False
        return
    data = _NotifyIconData()
    data.cbSize = ctypes.sizeof(_NotifyIconData)
    data.hWnd = hwnd
    data.uID = 1
    data.uFlags = NIF_ICON | NIF_TIP | NIF_INFO
    data.hIcon = user32.LoadIconW(None, ctypes.cast(IDI_INFORMATION, wintypes.LPCWSTR))
    data.szTip = "FocusDeck"
    data.szInfo = message[:255]
    data.szInfoTitle = title[:63]
    data.dwInfoFlags = NIIF_INFO
    try:
        _last_result = bool(shell32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(data)))
        # Keep the hidden window responsive while the notification is on screen.
        deadline = time.monotonic() + BALLOON_SECONDS
        msg = wintypes.MSG()
        while time.monotonic() < deadline:
            while user32.PeekMessageW(ctypes.byref(msg), hwnd, 0, 0, 1):
                pass
            time.sleep(0.1)
    finally:
        shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(data))
        user32.DestroyWindow(hwnd)


def notify(title: str, message: str) -> None:
    """Show a Windows notification without blocking the caller."""

    def run() -> None:
        global _last_result
        try:
            _show_balloon(title, message)
        except Exception:
            _last_result = False

    threading.Thread(target=run, name="focusdeck-notify", daemon=True).start()
