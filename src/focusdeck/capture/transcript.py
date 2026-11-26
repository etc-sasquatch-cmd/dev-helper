"""Bounded, defensive extraction from supported Claude Code transcript records."""

import re
from datetime import UTC, datetime
from pathlib import Path

HOME_STYLE = re.compile(
    r"(?i)(?:[a-z]:[\\/]+users[\\/]+|/home/|/users/)"
    r"(?:[^\\/\r\n\"'<>]+(?=[\\/])|[^\\/\s\"']+)"
)
EMAIL = re.compile(r"(?i)\b[A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,}\b")
CREDENTIALS = re.compile(
    r"(?i)\b(?:[a-z0-9]+[_-])*(?:password|passwd|token|api[ _-]?key|secret|"
    r"connection[ _-]?string)[\"']?\s*[:=]\s*(?:\"[^\"]*\"|'[^']*'|[^\s,;]+)"
)
AUTH = re.compile(r"(?i)\b(?:bearer|basic)\s+[a-z0-9_./+=-]+")
URI = re.compile(
    r"(?i)\b(?:postgres(?:ql)?|mysql|mongodb(?:\+srv)?|redis|mssql)://[^\s\"']+|[a-z][a-z0-9+.-]*://[^/\s\"']+@[^\s\"']+"
)
CONNECTION_FIELDS = re.compile(
    r"(?i)\b(?:server|data source|host|address)\s*=\s*[^;\r\n\"']+;"
    r"(?:\s*(?:database|initial catalog|user id|uid|password|pwd|port|"
    r"integrated security|encrypt|trustservercertificate)\s*=\s*[^;\r\n\"']+;?)+"
)
TOKEN = re.compile(
    r"\b(?:sk-[A-Za-z0-9_-]{8,}|gh[pousr]_[A-Za-z0-9_]{8,}|github_pat_[A-Za-z0-9_]{8,}|AKIA[A-Z0-9]{16}|eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+)\b"
)
PEM = re.compile(
    r"-----BEGIN [^-\n]*PRIVATE KEY-----[\s\S]*?(?:-----END [^-\n]*PRIVATE KEY-----|$)"
)
CHECK_COMMAND = re.compile(
    r"(?i)(?:^|\s|[;&|])(?:pytest|ruff|mypy|eslint|prettier|cargo\s+(?:test|clippy)|npm\s+(?:test|run\s+(?:test|lint))|go\s+test|dotnet\s+test)(?=$|\s|[;&|])"
)
USAGE_FIELDS = (
    "input_tokens",
    "output_tokens",
    "cache_creation_input_tokens",
    "cache_read_input_tokens",
)
FILE_TOOLS = {"Write", "Edit", "MultiEdit", "NotebookEdit"}


def redact(text: str, limit: int) -> str:
    # Work on the complete field before truncation so a boundary cannot cut a secret in half.
    for pattern in (PEM, URI, CONNECTION_FIELDS, AUTH, CREDENTIALS, TOKEN, EMAIL):
        text = pattern.sub("[redacted]", text)
    home = str(Path.home())
    if len(home) > 3:
        for variant in {home, home.replace("\\", "/")}:
            text = re.sub(re.escape(variant) + r"(?=$|[\\/\s\"'])", "~", text, flags=re.I)
    text = HOME_STYLE.sub("~", text)
    return " ".join(text.split())[:limit]


def transcript_timestamp(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        result = datetime.fromisoformat(value)
        return result.astimezone(UTC) if result.tzinfo is not None else None
    except (ValueError, OverflowError):
        return None


def extract(record: dict, session_id: str) -> dict | None:
    kind = record.get("type")
    ts = transcript_timestamp(record.get("timestamp"))
    recorded_session = record.get("sessionId") or record.get("session_id")
    message = record.get("message")
    if (
        kind not in ("user", "assistant")
        or ts is None
        or not isinstance(message, dict)
        or recorded_session != session_id
    ):
        return None
    content = message.get("content")
    result = {"prompt": None, "tools": [], "failures": 0, "files": [], "checks": [], "usage": {}}
    if kind == "user" and isinstance(content, str):
        result["prompt"] = redact(content, 240) or None
        return result if result["prompt"] else None
    blocks = content if isinstance(content, list) else []
    excerpts = []
    for block in blocks:
        if not isinstance(block, dict):
            continue
        block_type = block.get("type")
        if kind == "user" and block_type == "text" and isinstance(block.get("text"), str):
            excerpts.append(block["text"])
        if block_type == "tool_result" and block.get("is_error") is True:
            result["failures"] += 1
        name = block.get("name")
        if kind != "assistant" or block_type != "tool_use" or not isinstance(name, str):
            continue
        result["tools"].append(redact(name, 128))
        params = block.get("input")
        if not isinstance(params, dict):
            continue
        file_path = params.get("file_path", params.get("notebook_path"))
        if name in FILE_TOOLS and isinstance(file_path, str):
            result["files"].append(redact(file_path, 320))
        command = params.get("command")
        if (
            name in ("Bash", "PowerShell")
            and isinstance(command, str)
            and CHECK_COMMAND.search(command)
        ):
            result["checks"].append(redact(command, 320))
    if excerpts:
        result["prompt"] = redact("\n".join(excerpts), 240) or None
    usage = message.get("usage")
    if kind == "assistant" and isinstance(usage, dict):
        result["usage"] = {
            key: usage[key]
            for key in USAGE_FIELDS
            if type(usage.get(key)) is int and usage[key] >= 0
        }
    return result if any(result.values()) else None
