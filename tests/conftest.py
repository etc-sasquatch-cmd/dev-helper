from datetime import UTC, datetime, timedelta

import pytest

from focusdeck.core.service import TimerService
from focusdeck.db.connection import open_database
from focusdeck.db.repo import Repo


class FakeClock:
    """A clock that only moves when the test says so."""

    def __init__(self, start: datetime | None = None) -> None:
        self._now = start or datetime(2026, 11, 9, 15, 0, 0, tzinfo=UTC)

    def now(self) -> datetime:
        return self._now

    def advance(self, seconds: float = 0, *, minutes: float = 0) -> None:
        self._now += timedelta(seconds=seconds, minutes=minutes)


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def db_path(tmp_path):
    return tmp_path / "test.sqlite"


@pytest.fixture
def repo(db_path):
    conn = open_database(db_path)
    yield Repo(conn)
    conn.close()


@pytest.fixture
def service(repo, clock) -> TimerService:
    return TimerService(repo, clock)


@pytest.fixture
def skill_id(repo) -> int:
    return repo.create_skill("Python")
