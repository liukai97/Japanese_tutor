"""Maintenance CLI for Japanese Tutor.

The CLI intentionally exposes infrastructure operations only. Teaching strategy and
activity selection belong to Codex, not to this module.
"""

import json
import sqlite3
from pathlib import Path
from typing import Annotated

import typer
from pydantic import ValidationError

from japanese_tutor.curriculum.extract import (
    accept_extraction,
    load_document,
    prepare_extraction,
    source_view,
)
from japanese_tutor.ids import document_id
from japanese_tutor.importer.manifest import identify_lesson, scan_materials
from japanese_tutor.importer.pipeline import import_pdf, write_import
from japanese_tutor.schemas.curriculum import SemanticCurriculum
from japanese_tutor.schemas.source import LessonDocument
from japanese_tutor.verification.codex import accept_verification, prepare_verification
from japanese_tutor.verification.report import write_report

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
        typer.Option(help="Directory containing curriculum.sql and learner.sql.", file_okay=False),
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


@app.command("manifest")
def manifest_command(
    materials: Annotated[Path, typer.Argument(exists=True, file_okay=False)] = PROJECT_ROOT
    / "materials",
    output: Annotated[Path, typer.Option(dir_okay=False)] = DEFAULT_DATA_DIR / "manifest.json",
    series: Annotated[str, typer.Option(help="Stable textbook series key.")] = "liangshuang",
) -> None:
    """Inventory local PDFs, including edition hashes, page sizes and font statistics."""
    try:
        manifests = scan_materials(materials, series)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
            json.dumps([m.model_dump(mode="json") for m in manifests], ensure_ascii=False, indent=2)
            + "\n",
            encoding="utf-8",
        )
    except (OSError, ValueError, RuntimeError) as error:
        typer.echo(f"Manifest failed: {error}", err=True)
        raise typer.Exit(code=1) from error
    typer.echo(f"Inventoried {len(manifests)} PDFs: {output.resolve()}")


@app.command("import")
def import_command(
    pdf: Annotated[Path, typer.Argument(exists=True, dir_okay=False)],
    output_dir: Annotated[Path | None, typer.Option(file_okay=False)] = None,
    series: Annotated[str, typer.Option(help="Stable textbook series key.")] = "liangshuang",
    lesson_number: Annotated[int | None, typer.Option(min=1)] = None,
    debug_layout: Annotated[bool, typer.Option(help="Write disposable geometry caches.")] = False,
) -> None:
    """Convert a text-layer PDF to Japanese-aware JSON, a quality report and preview."""
    try:
        number = identify_lesson(pdf, lesson_number)
        destination = output_dir or DEFAULT_DATA_DIR / document_id(series, number)
        document, report = import_pdf(
            pdf,
            series=series,
            number=number,
            debug_dir=destination / "debug" if debug_layout else None,
        )
        write_import(document, report, destination, source_pdf=pdf)
    except (OSError, ValueError, RuntimeError) as error:
        typer.echo(f"Import failed: {error}", err=True)
        raise typer.Exit(code=1) from error
    pending = sum(i["severity"] == "review_required" for i in report.issues)
    typer.echo(
        f"Imported {document.manifest.page_count} pages, {len(document.sections)} sections; "
        f"{pending} observations require review."
    )
    typer.echo(f"Document: {(destination / 'lesson_document.json').resolve()}")
    typer.echo(f"Review: {(destination / 'review_required.md').resolve()}")


@app.command("validate")
def validate_command(
    document: Annotated[Path, typer.Argument(exists=True, dir_okay=False)],
) -> None:
    """Validate the document version, structure, ruby ranges and source references."""
    try:
        result = LessonDocument.model_validate_json(document.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValidationError) as error:
        typer.echo(f"Validation failed: {error}", err=True)
        raise typer.Exit(code=1) from error
    typer.echo(f"Valid {result.metadata.schema_version}: {result.manifest.document_id}")


@app.command("show-source")
def show_source_command(
    document: Annotated[Path, typer.Argument(exists=True, dir_okay=False)],
    section: Annotated[str | None, typer.Option()] = None,
    page: Annotated[int | None, typer.Option(min=1)] = None,
) -> None:
    """Return validated document/page/section evidence as JSON."""
    try:
        result = source_view(load_document(document), section, page)
    except (OSError, ValueError) as error:
        typer.echo(f"Source failed: {error}", err=True)
        raise typer.Exit(code=1) from error
    typer.echo(json.dumps(result, ensure_ascii=False, indent=2))


@app.command("extract")
def extract_command(
    document: Annotated[Path, typer.Argument(exists=True, dir_okay=False)],
    output_dir: Annotated[Path | None, typer.Option(file_okay=False)] = None,
    submission: Annotated[Path | None, typer.Option(exists=True, dir_okay=False)] = None,
) -> None:
    """Prepare Codex extraction or accept its submission; never invoke a model."""
    output = output_dir or document.parent / "semantic"
    try:
        source = load_document(document)
        if submission is None:
            prepare_extraction(source, output)
            typer.echo(f"Awaiting Codex extraction: {output / 'extraction_tasks.json'}")
        else:
            result = accept_extraction(source, submission, output)
            report = write_report(source, result, None, output)
            typer.echo(f"Accepted {len(result.objects)} objects; verification pending.")
            typer.echo(f"Review entries: {report['summary']['review_entries']}")
    except (OSError, ValueError) as error:
        typer.echo(f"Extraction failed: {error}", err=True)
        raise typer.Exit(code=1) from error


@app.command("verify")
def verify_command(
    document: Annotated[Path, typer.Argument(exists=True, dir_okay=False)],
    curriculum: Annotated[Path, typer.Argument(exists=True, dir_okay=False)],
    output_dir: Annotated[Path | None, typer.Option(file_okay=False)] = None,
    submission: Annotated[Path | None, typer.Option(exists=True, dir_okay=False)] = None,
) -> None:
    """Prepare independent Codex verification or accept results and report gates."""
    output = output_dir or curriculum.parent
    try:
        source = load_document(document)
        semantic = SemanticCurriculum.model_validate_json(curriculum.read_text(encoding="utf-8"))
        if submission is None:
            prepare_verification(source, semantic, output)
            result = None
            typer.echo(f"Awaiting independent Codex verification: {output}")
        else:
            result = accept_verification(source, semantic, submission, output)
        report = write_report(source, semantic, result, output)
        typer.echo(
            f"Status: {report['status']}; {report['summary']['review_entries']} review entries."
        )
    except (OSError, ValueError) as error:
        typer.echo(f"Verification failed: {error}", err=True)
        raise typer.Exit(code=1) from error


def main() -> None:
    """Run the maintenance CLI."""

    app()


if __name__ == "__main__":
    main()
