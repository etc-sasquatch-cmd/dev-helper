"""Build reviewer inputs from a fixed vocabulary and numeric signals."""

import json
import re
import subprocess
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from focusdeck.capture.policy import Policy, project_keys
from focusdeck.core.calendar import BUCKETS
from focusdeck.core.day import build_day
from focusdeck.core.timer import WORK_TYPES
from focusdeck.db.day import DayRepo
from focusdeck.db.repo import from_db, to_db
from focusdeck.db.study import StudyRepo

PUBLIC_TOPICS = {
    "acid": "ACID database properties",
    "transactions": "database transactions",
    "database transactions": "database transactions",
    "sql": "SQL fundamentals",
    "joins": "SQL joins",
    "indexes": "database indexes",
    "normalization": "relational database normalization",
    "postgresql": "PostgreSQL fundamentals",
    "http": "HTTP fundamentals",
    "http basics": "HTTP fundamentals",
    "https": "HTTPS fundamentals",
    "dns": "DNS fundamentals",
    "rest": "REST API fundamentals",
    "rest apis": "REST API fundamentals",
    "api design": "web API design",
    "caching": "application caching",
    "redis": "Redis fundamentals",
    "load balancing": "load balancing",
    "message queues": "message queues",
    "authentication": "web authentication",
    "oauth": "OAuth fundamentals",
    "git": "Git version control",
    "docker": "container fundamentals",
    "tcp/ip": "TCP/IP fundamentals",
    "unit testing": "unit testing",
    "integration testing": "integration testing",
}
TOOL_BUCKETS = {
    "Edit": "edit",
    "MultiEdit": "edit",
    "Write": "edit",
    "NotebookEdit": "edit",
    "Bash": "shell",
    "PowerShell": "shell",
    "Read": "read",
    "Grep": "read",
    "Glob": "read",
}
CORRECTION = re.compile(r"(?i)\b(?:wrong|instead|revert|undo|try again|still broken|redo)\b")
TEST = re.compile(r"(?i)\b(?:pytest|test|unittest)\b")
LINT = re.compile(r"(?i)\b(?:ruff|lint|eslint|mypy|clippy)\b")
FINGERPRINT = re.compile(r"[a-f0-9]{64}")


@dataclass(frozen=True)
class Request:
    task: str
    day: date
    period: str
    digest: dict
    sessions: dict[str, str]
    labels: dict[str, str]
    node_id: int | None = None


def _policy_sessions(store: DayRepo, policy: Policy) -> dict[str, str]:
    result = {}
    for row in store.conn.execute("SELECT id, project_keys, detail_allowed FROM claude_session"):
        keys = json.loads(row["project_keys"])
        if not keys or not all(isinstance(key, str) and FINGERPRINT.fullmatch(key) for key in keys):
            continue
        mode = policy.project_mode(keys)
        if mode:
            result[row["id"]] = mode if row["detail_allowed"] else "metadata"
    return result


def _scope(moment: datetime, windows) -> bool:
    return any(start <= moment < end for start, end, _ in windows)


def _minutes(seconds: float) -> float:
    return round(seconds / 60, 2)


def _signals(store: DayRepo, session: str, windows, detail: bool) -> dict:
    first, last = to_db(windows[0][0]), to_db(windows[-1][1])
    events = Counter()
    tools = Counter()
    for row in store.conn.execute(
        "SELECT * FROM claude_event WHERE session_id = ? AND occurred_at >= ? AND occurred_at < ?",
        (session, first, last),
    ):
        if _scope(from_db(row["occurred_at"]), windows):
            events["events"] += 1
            events["prompts"] += row["event"] == "UserPromptSubmit"
            events["failures"] += row["result"] == "failure"
            if row["tool"]:
                tools[TOOL_BUCKETS.get(row["tool"], "other")] += 1
    result = {**events, "tools": dict(tools), "thin": sum(tools.values()) < 2}
    if not detail:
        return result
    indicators = Counter()
    for row in store.conn.execute(
        "SELECT * FROM claude_transcript_record "
        "WHERE session_id = ? AND occurred_at >= ? AND occurred_at < ?",
        (session, first, last),
    ):
        if not _scope(from_db(row["occurred_at"]), windows):
            continue
        details = json.loads(row["details"])
        prompt = details.get("prompt")
        if isinstance(prompt, str):
            indicators["short_prompt_count"] += len(prompt.split()) <= 4
            indicators["possible_correction_count"] += bool(CORRECTION.search(prompt))
        for check in details.get("checks", ()):
            indicators["test_commands"] += bool(TEST.search(check))
            indicators["lint_commands"] += bool(LINT.search(check))
    return {**result, **indicators}


def _checkout_counts(root: Path, selected: str, policy: Policy, windows) -> dict:
    root = root.resolve()
    relative = Path(selected)
    path = (root / relative).resolve()
    git_dir = path / ".git"
    if relative.is_absolute() or not path.is_relative_to(root) or not git_dir.is_dir():
        raise ValueError("Choose a checkout relative to the FocusDeck workspace.")
    if not git_dir.resolve().is_relative_to(root):
        raise ValueError("Choose a checkout whose Git directory is inside this workspace.")
    if policy.project_mode(project_keys(str(path))) != "full":
        raise ValueError("Project context requires full capture permission for this checkout.")
    count = 0
    for start, end, _ in windows:
        try:
            result = subprocess.run(
                [
                    "git",
                    "-C",
                    str(path),
                    "rev-list",
                    "--count",
                    f"--since={to_db(start)}",
                    f"--before={to_db(end)}",
                    "HEAD",
                ],
                capture_output=True,
                text=True,
                timeout=3,
                check=True,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            count += int(result.stdout.strip())
        except (OSError, ValueError, subprocess.SubprocessError):
            raise ValueError("Could not read local Git activity for this checkout.") from None
    rules = path / "CLAUDE.md"
    context = {"commit_count": count}
    if rules.resolve().is_relative_to(root) and rules.is_file():
        with rules.open("r", encoding="utf-8", errors="replace") as stream:
            text = stream.read(50_000)
        context["mentions_tests"] = bool(TEST.search(text))
        context["mentions_lint"] = bool(LINT.search(text))
        context["mentions_scope"] = bool(re.search(r"(?i)\b(?:scope|milestone|approval)\b", text))
    return context


def quiz_topics(store: DayRepo) -> dict[int, str]:
    return {
        row["id"]: row["title"]
        for row in StudyRepo(store.repo).nodes()
        if row["title"].casefold() in PUBLIC_TOPICS and row["state"] != "skipped"
    }


def prepare(
    store: DayRepo,
    folder: Path,
    day: date,
    task: str,
    period: str = "Day",
    *,
    node_id: int | None = None,
    checkout: str = "",
) -> Request:
    if task == "Recall quiz":
        topics = quiz_topics(store)
        if node_id not in topics:
            raise ValueError("Choose a roadmap topic with a supported public subject.")
        attempts = store.conn.execute(
            "SELECT count(*) FROM quiz_attempt WHERE node_id = ?", (node_id,)
        ).fetchone()[0]
        digest = {
            "public_topic": PUBLIC_TOPICS[topics[node_id].casefold()],
            "attempt": attempts + 1,
        }
        return Request(task, day, period, digest, {}, {}, node_id)
    if period not in ("Day", *BUCKETS):
        raise ValueError("Choose a day or quarter.")
    policy = Policy.load(folder)
    allowed = _policy_sessions(store, policy)
    report = build_day(store, day, allowed_sessions=set(allowed))
    windows = [window for window in report.windows if period == "Day" or window[2] == period]
    if not windows:
        raise ValueError("That quarter did not occur on this local date.")
    summary = report.total if period == "Day" else report.quarters[period]
    metrics = {
        "M1": {"active_minutes": _minutes(sum(summary.work.values()))},
        "M2": {
            work: _minutes(seconds) for work, seconds in summary.work.items() if work in WORK_TYPES
        },
        "M3": {
            "completed_focus": summary.completed,
            "recap_count": len(summary.recaps),
            "evidence_count": len(summary.evidence),
        },
    }
    labels = {"M1": "Active time", "M2": "Work breakdown", "M3": "Focus, recaps, and evidence"}
    sessions = {}
    session_details = {}
    minutes = defaultdict(float)
    for row in store.claude_intervals(day, windows[0][0], windows[-1][1]):
        session = row["session_id"]
        if session in allowed:
            a, b = from_db(row["started_at"]), from_db(row["ended_at"])
            minutes[session] += sum(
                max(0, (min(end, b) - max(start, a)).total_seconds()) for start, end, _ in windows
            )
    for session, seconds in sorted(minutes.items(), key=lambda item: (-item[1], item[0]))[:30]:
        if not seconds:
            continue
        alias = f"S{len(sessions) + 1}"
        sessions[alias] = session
        labels[alias] = f"Claude session {len(sessions)}"
        session_details[alias] = {
            "minutes_before_overlap_adjustment": _minutes(seconds),
            **_signals(store, session, windows, allowed[session] == "full"),
        }
    if checkout.strip() and task == "Work Critic":
        metrics["M4"] = _checkout_counts(Path.cwd(), checkout, policy, windows)
        labels["M4"] = "Selected checkout indicators"
    digest = {
        "period": period,
        "metrics": metrics,
        "sessions": session_details,
        "thin": summary.completed == 0
        and not any(not detail["thin"] for detail in session_details.values()),
    }
    if task == "Study Judge":
        study = StudyRepo(store.repo)
        shares = {row.skill: row for row in report.allocation}
        skills, topics = {}, {}
        linked = defaultdict(list)
        for index, skill in enumerate(store.repo.list_skills()[:16], 1):
            alias = f"K{index}"
            labels[alias] = skill.name
            row = shares.get(skill.name)
            skills[alias] = {
                "target": row.target if row else None,
                "actual": row.actual if row else None,
                "gap": row.gap if row else None,
                "minutes_in_period": _minutes(summary.skills[skill.name]),
            }
            for node in study.node_links(skill.id):
                linked[node].append(alias)
        for index, topic in enumerate(
            sorted(study.nodes(), key=lambda node: node["state"] != "claimed")[:30], 1
        ):
            alias = f"N{index}"
            labels[alias] = topic["title"]
            attempts = store.conn.execute(
                "SELECT correct FROM quiz_attempt WHERE node_id = ? ORDER BY id DESC LIMIT 1",
                (topic["id"],),
            ).fetchone()
            topics[alias] = {
                "state": topic["state"],
                "last_quiz_correct": attempts[0] if attempts else None,
                "linked_skills": linked[topic["id"]],
                "evidence_counts": dict(
                    store.conn.execute(
                        "SELECT kind, count(*) FROM node_evidence WHERE node_id = ? GROUP BY kind",
                        (topic["id"],),
                    )
                ),
            }
        digest.update(skills=skills, topics=topics)
    return Request(task, day, period, digest, sessions, labels)
