from datetime import timedelta

import pytest

from focusdeck.core.allocation import allocation
from focusdeck.core.timer import TimerError
from focusdeck.db import connection
from focusdeck.db.repo import Repo, to_db
from focusdeck.db.study import StudyRepo
from focusdeck.roadmaps.importer import load_roadmap


@pytest.fixture
def topics(tmp_path):
    content = tmp_path / "backend" / "content"
    content.mkdir(parents=True)
    (content / "http-basics@node_1.md.md").write_text("Synthetic topic", encoding="utf-8")
    (content / "http-basics@node_1.md").write_text("Same topic ID", encoding="utf-8")
    (content / "readme.md").touch()
    return load_roadmap(tmp_path, "backend")


def test_roadmap_reimport_preserves_evidence_and_links(repo, service, clock, skill_id, topics):
    study = StudyRepo(repo)
    roadmap_id = study.import_roadmap(*topics)
    assert len(study.nodes()) == 1
    node = study.nodes()[0]
    assert node["title"] == "Http basics"
    assert node["state"] == "not_started"
    assert node["url"] == "https://roadmap.sh/backend"

    with pytest.raises(TimerError, match="linked"):
        service.start(skill_id, "Study", node["id"])
    study.save_links(skill_id, [node["id"]], ["  C:/project  ", ""])
    service.start(skill_id, "Study", node["id"])
    assert service.active_block().node_id == node["id"]
    clock.advance(minutes=25)
    event = service.tick()
    assert study.nodes(skill_id)[0]["state"] == "learning"
    service.save_recap(event.block_id, "Explained request headers.")
    service.save_recap(event.block_id, "Explained request and response headers.")
    assert study.nodes(skill_id)[0]["state"] == "claimed"
    assert repo.conn.execute("SELECT count(*) FROM node_evidence").fetchone()[0] == 2

    assert study.import_roadmap(*topics) == roadmap_id
    assert study.node_links(skill_id) == [node["id"]]
    assert study.project_paths(skill_id) == ["C:/project"]
    study.set_skipped(node["id"], True)
    assert study.nodes()[0]["state"] == "skipped"
    study.set_skipped(node["id"], False)
    assert study.nodes()[0]["state"] == "claimed"
    # M7 will supply this evidence after the Judge grades a recall quiz.
    repo.conn.execute(
        "INSERT INTO node_evidence (node_id, kind, recorded_at) VALUES (?, 'passed_quiz', ?)",
        (node["id"], to_db(clock.now())),
    )
    assert study.nodes()[0]["state"] == "verified"


def test_yaml_and_import_boundary(tmp_path):
    path = tmp_path / "starter.yaml"
    path.write_text(
        "id: starter\nname: Starter\nnodes:\n"
        "  - id: http\n    title: HTTP\n    url: https://example.com/http\n",
        encoding="utf-8",
    )
    assert load_roadmap(tmp_path, "starter.yaml")[1].nodes[0].title == "HTTP"
    with pytest.raises(ValueError, match="inside the roadmap folder"):
        load_roadmap(tmp_path, "../outside.yaml")
    with pytest.raises(ValueError, match="inside the roadmap folder"):
        load_roadmap(tmp_path, str(path))
    path.write_text(path.read_text(encoding="utf-8").replace("https:", "javascript:"))
    with pytest.raises(ValueError, match="HTTP or HTTPS"):
        load_roadmap(tmp_path, "starter.yaml")


def test_target_history_and_threshold(repo, clock, skill_id):
    study = StudyRepo(repo)
    sql = repo.create_skill("SQL")
    start = clock.now()
    with pytest.raises(ValueError, match="100"):
        study.save_targets({skill_id: 50, sql: 49}, start)
    with pytest.raises(ValueError, match="every skill"):
        study.save_targets({skill_id: 100}, start)
    study.save_targets({skill_id: 50, sql: 50}, start)

    def log_focus(skill, started, seconds):
        ended = started + timedelta(seconds=seconds)
        block = repo.insert_running_block(
            phase="focus",
            skill_id=skill,
            work_type="Study",
            planned_seconds=seconds,
            started_at=started,
            ends_at=ended,
        )
        repo.finish_block(block, status="completed", ended_at=ended, elapsed_seconds=seconds)
        return ended

    first_end = log_focus(skill_id, start, 3600)
    assert allocation(study, first_end, 7)[1][0].gap is None
    changed = first_end + timedelta(seconds=1)
    study.save_targets({skill_id: 100, sql: 0}, changed)
    second_end = log_focus(sql, changed, 3600)
    assert study.targets_at(first_end) == {skill_id: 50, sql: 50}
    total, rows = allocation(study, second_end, 7)
    assert total == 7200
    assert [(row.target, row.actual, row.gap) for row in rows] == [
        (75, 50, -25),
        (25, 50, 25),
    ]


def test_m4_migration_keeps_existing_running_block(db_path):
    conn = connection.connect(db_path)
    try:
        for version, path in connection._migration_files():
            if version <= 2:
                conn.executescript(path.read_text(encoding="utf-8"))
        conn.execute("PRAGMA user_version = 2")
        conn.execute("INSERT INTO skill VALUES (11, 'Existing')")
        conn.execute(
            """
            INSERT INTO pomodoro_block
                (phase, status, skill_id, planned_seconds, started_at, ends_at)
            VALUES ('focus', 'running', 11, 60,
                '2026-11-09T15:00:00+00:00', '2026-11-09T15:01:00+00:00')
            """
        )
        assert connection.migrate(conn) >= 3
        block = Repo(conn).active_block()
        assert block.skill_id == 11 and block.node_id is None
        assert Repo(conn).get_profile().focus_source_id is None
    finally:
        conn.close()
