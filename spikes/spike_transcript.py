"""M0 spike 4: parse a real Claude Code transcript read-only and print counts only.

Run: .venv\\Scripts\\python.exe spikes\\spike_transcript.py [path-to-session.jsonl]
With no path it uses the newest transcript for this repo.
Prints structure and counts. Never prints prompt text, tool input, tool output, or file names.
Standard library only. Throwaway code.
"""

import json
import sys
from collections import Counter
from pathlib import Path


def newest_transcript() -> Path:
    encoded = str(Path(__file__).resolve().parent.parent).replace(":", "-").replace("\\", "-")
    folder = Path.home() / ".claude" / "projects" / encoded
    files = sorted(folder.glob("*.jsonl"), key=lambda p: p.stat().st_mtime)
    if not files:
        raise SystemExit("no transcript found for this project")
    return files[-1]


def main() -> None:
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else newest_transcript()

    record_types: Counter[str] = Counter()
    block_types: Counter[str] = Counter()
    tool_names: Counter[str] = Counter()
    top_keys: Counter[str] = Counter()
    usage_keys: Counter[str] = Counter()
    lines = bad = non_object = with_ts = tool_errors = 0
    first_ts = last_ts = None

    with path.open("r", encoding="utf-8", errors="replace") as f:  # read-only
        for raw in f:
            raw = raw.strip()
            if not raw:
                continue
            lines += 1
            try:
                rec = json.loads(raw)
            except json.JSONDecodeError:
                bad += 1
                continue
            if not isinstance(rec, dict):
                non_object += 1
                continue

            record_types[str(rec.get("type"))] += 1
            top_keys.update(rec.keys())

            ts = rec.get("timestamp")
            if isinstance(ts, str):
                with_ts += 1
                first_ts = first_ts or ts
                last_ts = ts

            message = rec.get("message")
            if not isinstance(message, dict):
                continue
            usage = message.get("usage")
            if isinstance(usage, dict):
                usage_keys.update(usage.keys())
            content = message.get("content")
            if not isinstance(content, list):
                continue
            for block in content:
                if not isinstance(block, dict):
                    continue
                block_types[str(block.get("type"))] += 1
                if block.get("type") == "tool_use":
                    tool_names[str(block.get("name"))] += 1
                if block.get("type") == "tool_result" and block.get("is_error"):
                    tool_errors += 1

    print(f"file size bytes : {path.stat().st_size}")
    print(f"lines           : {lines}")
    print(f"bad JSON lines  : {bad}")
    print(f"non-object lines: {non_object}")
    print(f"with timestamp  : {with_ts}")
    print(f"first timestamp : {first_ts}")
    print(f"last timestamp  : {last_ts}")
    print(f"tool errors     : {tool_errors}")
    for title, counter in [
        ("record types", record_types),
        ("content block types", block_types),
        ("tool calls by tool name", tool_names),
        ("top-level keys", top_keys),
        ("usage keys", usage_keys),
    ]:
        print(f"\n{title}:")
        for name, count in counter.most_common():
            print(f"  {count:6d}  {name}")


if __name__ == "__main__":
    main()
