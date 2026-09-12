"""Maintenance CLI for Japanese Tutor.

The CLI intentionally exposes infrastructure operations only. Teaching strategy and
activity selection belong to Codex, not to this module.
"""

import sqlite3
from pathlib import Path
from typing import Annotated

import typer

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DATA_DIR = PROJECT_ROOT / "data" / "generated"
DEFAULT_SQL_DIR = PROJECT_ROOT / "sql"

app = typer.Typer(
    help="Maintain Japanese Tutor curriculum and learner data.",
    no_args_is_help=True,
)


@app.callback()
def cli() -> None:
    """Maintain Japanese Tutor infrastructure without selecting activities."""


def _apply_sql(database_path: Path, migration_path: Path) -> None:
    """Create or open a database and apply one migration script."""

    if not migration_path.is_file():
        raise FileNotFoundError(f"SQL migration not found: {migration_path}")

    database_path.parent.mkdir(parents=True, exist_ok=True)
    migration = migration_path.read_text(encoding="utf-8")
    with sqlite3.connect(database_path) as connection:
        connection.executescript(migration)


@app.command("build-db")
def build_db(
    curriculum_db: Annotated[
        Path,
        typer.Option(help="Path to the rebuildable curriculum database.", dir_okay=False),
    ] = DEFAULT_DATA_DIR / "curriculum.db",
    learner_db: Annotated[
        Path,
        typer.Option(help="Path to the independent learner database.", dir_okay=False),
    ] = DEFAULT_DATA_DIR / "learner.db",
    sql_dir: Annotated[
        Path,
        typer.Option(
            help="Directory containing curriculum.sql and learner.sql.", file_okay=False
        ),
    ] = DEFAULT_SQL_DIR,
) -> None:
    """Initialize or migrate the separate curriculum and learner databases."""

    try:
        _apply_sql(curriculum_db, sql_dir / "curriculum.sql")
        _apply_sql(learner_db, sql_dir / "learner.sql")
    except (OSError, sqlite3.Error) as error:
        typer.echo(f"Database initialization failed: {error}", err=True)
        raise typer.Exit(code=1) from error

    typer.echo(f"Curriculum database ready: {curriculum_db.resolve()}")
    typer.echo(f"Learner database ready: {learner_db.resolve()}")


def main() -> None:
    """Run the maintenance CLI."""

    app()


if __name__ == "__main__":
    main()
