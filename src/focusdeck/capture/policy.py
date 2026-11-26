"""Shared, standard-library capture policy. The hook loads it without SQLite."""

import hashlib
import json
import os
import tempfile
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path

POLICY_FILE = "capture-policy.json"


def canonical(path: Path) -> str:
    return os.path.normcase(str(path.resolve()))


def path_key(path: str) -> str:
    return hashlib.sha256(path.encode("utf-8")).hexdigest()


def project_keys(cwd: str) -> list[str]:
    path = Path(canonical(Path(cwd)))
    return [path_key(str(parent)) for parent in (path, *path.parents)]


def atomic_write(path: Path, contents: bytes, validate: Callable[[], None] | None = None) -> None:
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as stream:
        temporary = Path(stream.name)
        try:
            stream.write(contents)
            stream.flush()
            os.fsync(stream.fileno())
        except BaseException:
            stream.close()
            temporary.unlink(missing_ok=True)
            raise
    try:
        for attempt in range(5):
            if validate is not None:
                validate()
            try:
                os.replace(temporary, path)
                break
            except PermissionError as error:
                # Windows readers and scanners can briefly hold a file without delete sharing.
                if getattr(error, "winerror", None) not in (5, 32, 33) or attempt == 4:
                    raise
                time.sleep(0.05)
    finally:
        temporary.unlink(missing_ok=True)


@dataclass(frozen=True)
class Policy:
    reviewed: bool = False
    enabled: bool = False
    excluded: tuple[str, ...] = ()
    metadata_only: tuple[str, ...] = ()
    gap_minutes: int = 5

    def mode(self, keys: list[str]) -> str | None:
        if not self.reviewed or not self.enabled:
            return None
        return self.project_mode(keys)

    def project_mode(self, keys: list[str]) -> str | None:
        if not self.reviewed:
            return None
        if any(path_key(path) in keys for path in self.excluded):
            return None
        if any(path_key(path) in keys for path in self.metadata_only):
            return "metadata"
        return "full"

    def save(self, folder: Path) -> None:
        atomic_write(folder / POLICY_FILE, json.dumps(asdict(self), indent=2).encode("utf-8"))

    @classmethod
    def load(cls, folder: Path) -> "Policy":
        try:
            payload = json.loads((folder / POLICY_FILE).read_text(encoding="utf-8"))
            if type(payload["reviewed"]) is not bool or type(payload["enabled"]) is not bool:
                return cls()
            if type(payload["gap_minutes"]) is not int or not 1 <= payload["gap_minutes"] <= 60:
                return cls()
            for key in ("excluded", "metadata_only"):
                paths = payload[key]
                if not isinstance(paths, list) or any(
                    not isinstance(path, str) or not Path(path).is_absolute() for path in paths
                ):
                    return cls()
                payload[key] = tuple(paths)
            return cls(**payload)
        except (OSError, ValueError, TypeError, KeyError):
            return cls()


def clean_paths(text: str) -> tuple[str, ...]:
    paths = []
    for line in text.splitlines():
        if line.strip():
            path = Path(line.strip()).expanduser()
            if not path.is_absolute():
                raise ValueError("Enter absolute project paths, one per line.")
            paths.append(canonical(path))
    return tuple(dict.fromkeys(paths))
