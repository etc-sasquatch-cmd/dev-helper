"""SQLite connection and numbered migrations."""

import os
import sqlite3
from pathlib import Path

MIGRATIONS_DIR = Path(__file__).parent / "migrations"


def data_dir() -> Path:
    """Folder for user data: %LOCALAPPDATA%\\FocusDeck.

    Set FOCUSDECK_DATA_DIR to use a different folder, for example a scratch folder
    when trying things out without touching real data.
    """
    override = os.environ.get("FOCUSDECK_DATA_DIR")
    if override:
        path = Path(override)
    else:
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        path = Path(base) / "FocusDeck"
    path.mkdir(parents=True, exist_ok=True)
    return path


def connect(db_path: Path | str) -> sqlite3.Connection:
    # isolation_level=None: no implicit transactions. Writes commit immediately unless
    # wrapped in an explicit BEGIN, which the code does wherever several must succeed together.
    conn = sqlite3.connect(db_path, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA synchronous = NORMAL")
    return conn


def _migration_files() -> list[tuple[int, Path]]:
    files = []
    for path in MIGRATIONS_DIR.glob("*.sql"):
        files.append((int(path.name.split("_", 1)[0]), path))
    return sorted(files)


def migrate(conn: sqlite3.Connection) -> int:
    """Apply any migrations newer than the database's version. Returns the new version."""
    current = conn.execute("PRAGMA user_version").fetchone()[0]
    for number, path in _migration_files():
        if number <= current:
            continue
        sql = path.read_text(encoding="utf-8")
        # One transaction per migration: it applies fully or not at all.
        try:
            conn.executescript(f"BEGIN;\n{sql}\nPRAGMA user_version = {number};\nCOMMIT;")
        except sqlite3.Error:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
        current = number
    return current


def open_database(db_path: Path | str | None = None) -> sqlite3.Connection:
    conn = connect(db_path if db_path is not None else data_dir() / "focusdeck.sqlite")
    migrate(conn)
    return conn
