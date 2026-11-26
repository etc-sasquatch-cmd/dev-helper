"""Run a structured task with bounded execution, validation and a local cache."""

import hashlib
import json
import os
import shutil
import subprocess
import time
from collections.abc import Callable
from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory

from pydantic import ValidationError

from focusdeck.agents.contracts import (
    ARTIFACT_REVIEW,
    PROMPTS,
    TONE,
    Output,
    OutputContractError,
    claude_schema,
    validate_output,
)
from focusdeck.db.connection import connect
from focusdeck.db.repo import to_db


class ReviewError(RuntimeError):
    """A short user-facing error with no command output or request content."""


@dataclass(frozen=True)
class Response:
    payload: object
    model: str
    input_tokens: int | None = None
    output_tokens: int | None = None
    validation_issue: str | None = None


@dataclass(frozen=True)
class ReviewRun:
    output: Output
    model: str
    seconds: float
    input_tokens: int | None
    output_tokens: int | None
    cached: bool


def review_request(task: str, digest: dict) -> tuple[str, str, dict]:
    instructions = PROMPTS[task] if task == ARTIFACT_REVIEW else f"{TONE}\n{PROMPTS[task]}"
    payload = json.dumps(digest, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return instructions, payload, claude_schema(task, digest)


def _count(value: object) -> int | None:
    return value if type(value) is int and 0 <= value <= 2**53 else None


def _extract_output(envelope: dict) -> tuple[object, str | None]:
    structured = envelope.get("structured_output")
    candidate = structured if structured is not None else envelope.get("result")
    if isinstance(candidate, str) and candidate.strip():
        try:
            candidate = json.loads(candidate)
        except (ValueError, RecursionError):
            return None, "Claude returned text without a JSON object."
    if isinstance(candidate, dict):
        return candidate, None
    if structured is not None or candidate not in (None, ""):
        return None, "Claude's response data was not a JSON object."
    return None, "Claude returned no structured output."


class ClaudeTransport:
    def __init__(self, folder: Path, model: str = "haiku", timeout: float = 75) -> None:
        self.folder, self.model, self.timeout = folder, model, timeout

    def __call__(self, instructions: str, payload: str, schema: dict) -> Response:
        binary = shutil.which("claude")
        if not binary or (os.name == "nt" and Path(binary).suffix.lower() != ".exe"):
            raise ReviewError("FocusDeck needs the native Claude executable on PATH.")
        command = [
            binary,
            "-p",
            "--safe-mode",
            "--setting-sources",
            "",
            "--tools",
            "",
            "--disallowed-tools",
            "mcp__*",
            "--strict-mcp-config",
            "--mcp-config",
            '{"mcpServers":{}}',
            "--disable-slash-commands",
            "--no-session-persistence",
            "--output-format",
            "json",
            "--model",
            self.model,
            "--system-prompt",
            instructions,
            "--json-schema",
            json.dumps(schema),
        ]
        env = {**os.environ, "FOCUSDECK_AGENT": "1"}
        with TemporaryDirectory(prefix="reviewer-", dir=self.folder) as empty:
            try:
                process = subprocess.run(
                    command,
                    input=payload,
                    capture_output=True,
                    encoding="utf-8",
                    errors="replace",
                    env=env,
                    cwd=empty,
                    timeout=self.timeout,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
            except subprocess.TimeoutExpired as error:
                raise ReviewError("The reviewer timed out.") from error
            except OSError as error:
                raise ReviewError("Could not start the Claude executable.") from error
        try:
            envelope = json.loads(process.stdout)
        except (ValueError, RecursionError):
            if process.returncode == 0:
                return Response(None, self.model, validation_issue="Claude returned invalid JSON.")
            raise ReviewError(
                "Claude returned an error. Check its setup and availability."
            ) from None
        if not isinstance(envelope, dict):
            if process.returncode:
                raise ReviewError("Claude returned an error. Check its setup and availability.")
            return Response(
                None, self.model, validation_issue="Claude returned an invalid result envelope."
            )
        exhausted = envelope.get("subtype") == "error_max_structured_output_retries"
        if not exhausted and (process.returncode or envelope.get("is_error")):
            raise ReviewError("Claude returned an error. Check its setup and availability.")
        usage = envelope.get("usage")
        usage = usage if isinstance(usage, dict) else {}
        models = envelope.get("modelUsage")
        model = next(iter(models), self.model) if isinstance(models, dict) else self.model
        if exhausted:
            output, issue = None, "Claude exhausted its internal structured-output retries."
        else:
            output, issue = _extract_output(envelope)
        return Response(
            output,
            model[:120],
            _count(usage.get("input_tokens")),
            _count(usage.get("output_tokens")),
            issue,
        )


class AgentRunner:
    def __init__(
        self,
        folder: Path,
        transport: Callable[[str, str, dict], Response] | None = None,
        model: str = "haiku",
        namespace: str = "claude",
    ) -> None:
        self.database = folder / "focusdeck.sqlite"
        self.model = model
        self.namespace = namespace
        self.transport = transport if transport is not None else ClaudeTransport(folder, model)

    def run(self, task: str, digest: dict) -> ReviewRun:
        instructions, payload, schema = review_request(task, digest)
        if len(payload) > 48_000:
            raise ReviewError("The reviewer request is too large.")
        material = json.dumps([self.namespace, self.model, task, instructions, schema, payload])
        key = hashlib.sha256(material.encode("utf-8")).hexdigest()
        with closing(connect(self.database)) as conn:
            cached = conn.execute(
                "SELECT * FROM agent_run WHERE cache_key = ? AND response IS NOT NULL "
                "ORDER BY id DESC LIMIT 1",
                (key,),
            ).fetchone()
            if cached:
                try:
                    output = validate_output(task, json.loads(cached["response"]), digest)
                except ValueError:
                    pass
                else:
                    return ReviewRun(
                        output,
                        cached["model"],
                        cached["seconds"],
                        cached["input_tokens"],
                        cached["output_tokens"],
                        True,
                    )

            total_seconds = 0.0
            input_tokens = output_tokens = None
            issues = []
            for _ in range(2):
                start = time.monotonic()
                try:
                    response = self.transport(instructions, payload, schema)
                except ReviewError:
                    conn.execute(
                        "INSERT INTO agent_run (cache_key, task, model, ran_at, seconds) "
                        "VALUES (?, ?, ?, ?, ?)",
                        (key, task, self.model, to_db(datetime.now(UTC)), time.monotonic() - start),
                    )
                    raise
                seconds = time.monotonic() - start
                total_seconds += seconds
                if response.input_tokens is not None:
                    input_tokens = (input_tokens or 0) + response.input_tokens
                if response.output_tokens is not None:
                    output_tokens = (output_tokens or 0) + response.output_tokens
                issue = response.validation_issue
                encoded = None
                if issue is None:
                    try:
                        output = validate_output(task, response.payload, digest)
                    except ValidationError:
                        issue = "The JSON object did not match the response schema."
                    except OutputContractError as error:
                        issue = str(error)
                    else:
                        encoded = output.model_dump_json()
                if issue:
                    issues.append(issue)
                conn.execute(
                    "INSERT INTO agent_run (cache_key, task, model, ran_at, seconds, "
                    "input_tokens, output_tokens, response, validation_issue) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        key,
                        task,
                        response.model,
                        to_db(datetime.now(UTC)),
                        seconds,
                        response.input_tokens,
                        response.output_tokens,
                        encoded,
                        issue,
                    ),
                )
                if encoded:
                    return ReviewRun(
                        output,
                        response.model,
                        total_seconds,
                        input_tokens,
                        output_tokens,
                        False,
                    )
        raise ReviewError("Two invalid responses. " + " ".join(dict.fromkeys(issues)))
