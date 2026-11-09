"""Helper for spike 1: checks the spike window is topmost and samples CPU of its process tree.

Run while spike_window.py is open:
    .venv\\Scripts\\python.exe spikes\\measure_window.py [--seconds 30]
Standard library only. Throwaway code.
"""

import argparse
import ctypes
import json
import subprocess
import time
from pathlib import Path

TITLE = "FocusDeck spike window"
OUT = Path(__file__).parent / "out"
GWL_EXSTYLE = -20
WS_EX_TOPMOST = 0x00000008


def is_topmost() -> bool | None:
    user32 = ctypes.windll.user32
    user32.FindWindowW.restype = ctypes.c_void_p
    hwnd = user32.FindWindowW(None, TITLE)
    if not hwnd:
        return None
    user32.GetWindowLongW.argtypes = [ctypes.c_void_p, ctypes.c_int]
    return bool(user32.GetWindowLongW(hwnd, GWL_EXSTYLE) & WS_EX_TOPMOST)


def tree_cpu_seconds(root_pid: int) -> tuple[float, int]:
    """Total CPU seconds used so far by root_pid and all its descendants."""
    script = (
        "Get-CimInstance Win32_Process | "
        "Select-Object ProcessId,ParentProcessId,KernelModeTime,UserModeTime | ConvertTo-Json"
    )
    raw = subprocess.run(
        ["powershell", "-NoProfile", "-Command", script],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    procs = json.loads(raw)
    children: dict[int, list[int]] = {}
    cpu: dict[int, float] = {}
    for p in procs:
        children.setdefault(p["ParentProcessId"], []).append(p["ProcessId"])
        # Times are in 100-nanosecond units.
        cpu[p["ProcessId"]] = (int(p["KernelModeTime"]) + int(p["UserModeTime"])) / 1e7
    total, count, stack, seen = 0.0, 0, [root_pid], set()
    while stack:
        pid = stack.pop()
        if pid in seen or pid not in cpu:
            continue
        seen.add(pid)
        total += cpu[pid]
        count += 1
        stack.extend(children.get(pid, []))
    return total, count


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seconds", type=float, default=30)
    args = parser.parse_args()

    pid = int((OUT / "window.pid").read_text(encoding="utf-8"))
    print(f"topmost flag set: {is_topmost()}")

    start_cpu, n = tree_cpu_seconds(pid)
    start = time.monotonic()
    time.sleep(args.seconds)
    end_cpu, n2 = tree_cpu_seconds(pid)
    elapsed = time.monotonic() - start

    cores = ctypes.windll.kernel32.GetActiveProcessorCount(0xFFFF)
    one_core_pct = (end_cpu - start_cpu) / elapsed * 100
    print(f"processes in tree: {n} -> {n2}")
    print(f"idle CPU over {elapsed:.0f}s: {one_core_pct:.2f}% of one core")
    print(f"                    = {one_core_pct / cores:.2f}% of the whole CPU ({cores} cores)")
    print(f"topmost flag still set: {is_topmost()}")


if __name__ == "__main__":
    main()
