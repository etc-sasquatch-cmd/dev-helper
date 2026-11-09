import sqlite3
from datetime import datetime

import pytest

from focusdeck.db import connection
from focusdeck.db.connection import connect, migrate, open_database
from focusdeck.db.repo import to_db


def test_database_uses_wal_and_foreign_keys(db_path):
    conn = open_database(db_path)
    assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    conn.close()


def test_migrations_set_version_and_are_idempotent(db_path):
    conn = connect(db_path)
    latest = migrate(conn)
    assert latest >= 1
    assert conn.execute("PRAGMA user_version").fetchone()[0] == latest
    assert migrate(conn) == latest  # second run changes nothing
    assert conn.execute("SELECT COUNT(*) FROM phase_preset").fetchone()[0] == 2
    conn.close()


def test_failed_migration_leaves_database_unchanged(db_path, tmp_path, monkeypatch):
    conn = connect(db_path)
    latest = migrate(conn)
    bad_dir = tmp_path / "migrations"
    bad_dir.mkdir()
    (bad_dir / f"{latest + 1:04d}_bad.sql").write_text(
        "CREATE TABLE half_done (id INTEGER); THIS IS NOT SQL;", encoding="utf-8"
    )
    monkeypatch.setattr(connection, "MIGRATIONS_DIR", bad_dir)
    with pytest.raises(sqlite3.Error):
        migrate(conn)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == latest
    tables = conn.execute("SELECT name FROM sqlite_master WHERE name = 'half_done'").fetchall()
    assert tables == []
    conn.close()


def test_only_one_active_block_allowed(repo, clock):
    now = clock.now()
    args = dict(phase="focus", skill_id=None, work_type=None, planned_seconds=60,
                started_at=now, ends_at=now)  # fmt: skip
    repo.insert_running_block(**args)
    with pytest.raises(sqlite3.IntegrityError):
        repo.insert_running_block(**args)


def test_naive_timestamps_are_rejected():
    with pytest.raises(ValueError):
        to_db(datetime(2026, 11, 9, 15, 0, 0))


def test_settings_roundtrip(repo):
    assert repo.get_setting("missing", "fallback") == "fallback"
    repo.set_setting("k", "1")
    repo.set_setting("k", "2")
    assert repo.get_setting("k") == "2"


def test_skill_names_are_unique_ignoring_case(repo):
    first = repo.create_skill("Python")
    assert repo.create_skill("  python ") == first
    assert [s.name for s in repo.list_skills()] == ["Python"]


def test_transaction_rolls_back_on_error(repo):
    with pytest.raises(RuntimeError):
        with repo.transaction():
            repo.set_setting("k", "inside")
            raise RuntimeError
    assert repo.get_setting("k") is None


def test_data_dir_honours_override(tmp_path, monkeypatch):
    monkeypatch.setenv("FOCUSDECK_DATA_DIR", str(tmp_path / "custom"))
    assert connection.data_dir() == tmp_path / "custom"
    assert (tmp_path / "custom").is_dir()
