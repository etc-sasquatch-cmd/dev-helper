"""M0 spike 5: call `claude -p` from Python with stdin input and FOCUSDECK_AGENT=1.

Run: .venv\\Scripts\\python.exe spikes\\spike_agent.py
Sends one tiny synthetic prompt (no real data) and checks the JSON that comes back.
Standard library only. Throwaway code.
"""

import json
import os
import shutil
import subprocess
import tempfile
import time

PROMPT = (
    "This is a connectivity test with synthetic data. "
    "A user logged 3 focus blocks of 25 minutes each. "
    "Reply with the block count and a one-word verdict."
)
SCHEMA = {
    "type": "object",
    "properties": {
        "blocks": {"type": "integer"},
        "verdict": {"type": "string"},
    },
    "required": ["blocks", "verdict"],
    "additionalProperties": False,
}


def main() -> None:
    claude = shutil.which("claude")
    if not claude:
        raise SystemExit("claude executable not found on PATH")

    env = dict(os.environ, FOCUSDECK_AGENT="1")
    cmd = [
        claude,
        "-p",
        "--output-format", "json",
        "--json-schema", json.dumps(SCHEMA),
        "--tools", "",
        "--model", "haiku",
        "--no-session-persistence",
    ]  # fmt: skip

    # Empty working directory so no project CLAUDE.md or project settings are loaded.
    with tempfile.TemporaryDirectory(prefix="focusdeck-agent-") as empty_dir:
        start = time.monotonic()
        proc = subprocess.run(
            cmd,
            input=PROMPT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=env,
            cwd=empty_dir,
            timeout=180,
        )
        elapsed = time.monotonic() - start

    print(f"exit code   : {proc.returncode}")
    print(f"wall seconds: {elapsed:.1f}")
    if proc.stderr.strip():
        print(f"stderr      : {proc.stderr.strip()[:500]}")

    try:
        envelope = json.loads(proc.stdout)
    except json.JSONDecodeError:
        print("stdout was NOT valid JSON. First 500 chars:")
        print(proc.stdout[:500])
        raise SystemExit(1) from None

    print("stdout is valid JSON")
    print(f"envelope keys: {sorted(envelope.keys())}")
    for key in ("type", "subtype", "is_error", "duration_ms", "num_turns", "total_cost_usd"):
        print(f"  {key}: {envelope.get(key)}")
    print(f"  usage: {envelope.get('usage')}")
    print(f"  models: {sorted((envelope.get('modelUsage') or {}).keys())}")

    structured = envelope.get("structured_output")
    print(f"structured_output: {structured}")
    print(f"result (text)    : {str(envelope.get('result'))[:200]}")

    ok = (
        proc.returncode == 0
        and isinstance(structured, dict)
        and isinstance(structured.get("blocks"), int)
        and isinstance(structured.get("verdict"), str)
    )
    print(f"\nSPIKE 5 {'PASS' if ok else 'FAIL'}")
    raise SystemExit(0 if ok else 1)


if __name__ == "__main__":
    main()
