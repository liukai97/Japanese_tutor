import sqlite3
from pathlib import Path

from typer.testing import CliRunner

from japanese_tutor.cli import DEFAULT_SQL_DIR, app

runner = CliRunner()


def test_help_exposes_maintenance_commands_only() -> None:
    result = runner.invoke(app, ["--help"])

    assert result.exit_code == 0
    assert "build-db" in result.stdout
    assert "quiz" not in result.stdout
    assert "next" not in result.stdout


def test_build_db_creates_separate_empty_databases(tmp_path: Path) -> None:
    curriculum_db = tmp_path / "curriculum.db"
    learner_db = tmp_path / "learner.db"
    sql_dir = tmp_path / "sql"
    sql_dir.mkdir()
    (sql_dir / "curriculum.sql").write_text("", encoding="utf-8")
    (sql_dir / "learner.sql").write_text(
        (DEFAULT_SQL_DIR / "learner.sql").read_text(encoding="utf-8"), encoding="utf-8"
    )

    result = runner.invoke(
        app,
        [
            "build-db",
            "--curriculum-db",
            str(curriculum_db),
            "--learner-db",
            str(learner_db),
            "--sql-dir",
            str(sql_dir),
        ],
    )

    assert result.exit_code == 0
    assert curriculum_db.is_file()
    assert learner_db.is_file()
    assert _table_names(curriculum_db) == []
    assert "learning_evidence" in _table_names(learner_db)
    with sqlite3.connect(learner_db) as connection:
        assert connection.execute("SELECT count(*) FROM learning_evidence").fetchone()[0] == 0
        assert connection.execute("SELECT count(*) FROM concept_state").fetchone()[0] == 0


def _table_names(database_path: Path) -> list[str]:
    with sqlite3.connect(database_path) as connection:
        rows = connection.execute(
            "SELECT name FROM sqlite_schema WHERE type = 'table' ORDER BY name"
        ).fetchall()
    return [row[0] for row in rows]
