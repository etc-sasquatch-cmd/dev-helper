"""M0 spike 3: a Claude Code hook that proves hooks fire on Windows via a direct python.exe path.

Standard library only. Always exits 0. Only appends one line. Records no prompt or tool content.
Throwaway code.
"""

import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path


def main() -> None:
    if os.environ.get("FOCUSDECK_AGENT") == "1":
        return
    try:
        # Read bytes and strip a BOM: some Windows shells prepend one when piping.
        payload = json.loads(sys.stdin.buffer.read().decode("utf-8-sig") or "{}")
    except Exception:
        payload = {}
    line = {
        "ts": datetime.now(UTC).isoformat(timespec="seconds"),
        "event": payload.get("hook_event_name"),
        "tool": payload.get("tool_name"),
        "has_session_id": bool(payload.get("session_id")),
        "has_transcript_path": bool(payload.get("transcript_path")),
        "keys": sorted(payload.keys()),
    }
    out = Path(__file__).parent / "out"
    out.mkdir(exist_ok=True)
    with (out / "hook.log").open("a", encoding="utf-8") as f:
        f.write(json.dumps(line) + "\n")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
    sys.exit(0)
