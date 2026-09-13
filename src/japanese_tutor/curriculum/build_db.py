"""Compile approved artifacts into a fresh database; never infer curriculum facts."""

import json
import os
import sqlite3
import tempfile
import unicodedata
from pathlib import Path

from japanese_tutor.curriculum.extract import artifact_hash, load_document, section_texts
from japanese_tutor.schemas.curriculum import (
    Concept,
    ConceptRelation,
    Dialogue,
    ExerciseExample,
    LexicalData,
    SemanticCurriculum,
    TopicData,
)
from japanese_tutor.schemas.retrieval import BuildInputs, CurriculumApproval
from japanese_tutor.schemas.verification import VerificationSubmission
from japanese_tutor.verification.report import build_report

DATABASE_VERSION = "1"
DEFAULT_SQL = Path(__file__).resolve().parents[3] / "sql" / "curriculum.sql"


def normalize(text: str) -> str:
    return unicodedata.normalize("NFKC", text).casefold()


def dump(value) -> str:
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def content_strings(value) -> list[str]:
    """Index content fields without serializing IDs, source refs or schema keys."""
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [text for item in value for text in content_strings(item)]
    if isinstance(value, dict):
        excluded = {"kind", "status", "sources", "source_indices", "range", "reading_range"}
        return [
            text
            for key, item in value.items()
            if key not in excluded
            for text in content_strings(item)
        ]
    return []


def _search_entry(connection, identifier, kind, lesson, category, title, body, quote, ref):
    cursor = connection.execute(
        "INSERT INTO search_entries(object_id,kind,lesson_id,concept_type,title,body,"
        "source_quote,document_id,page,section_id) VALUES (?,?,?,?,?,?,?,?,?,?)",
        (
            identifier,
            kind,
            lesson,
            category,
            title,
            body,
            quote,
            ref.document_id,
            ref.page,
            ref.section_id,
        ),
    )
    connection.execute(
        "INSERT INTO textbook_fts(rowid,title,body) VALUES (?,?,?)",
        (cursor.lastrowid, normalize(title), normalize(body)),
    )


def _concept_entries(connection, obj, lesson):
    # Topic local source indices preserve which page supports each note/example/table.
    groups = {}
    for index, support in enumerate(obj.supports):
        groups.setdefault(support.source, []).append(index)
    for ref, indices in groups.items():
        quotes = list(dict.fromkeys(obj.supports[i].quote for i in indices))
        strings = [obj.summary, *quotes]
        if isinstance(obj.data, TopicData):
            for item in [*obj.data.notes, *obj.data.examples, *obj.data.tables]:
                if set(item.source_indices).intersection(indices):
                    strings.extend(content_strings(item.model_dump(mode="json")))
            # Forms have no local anchors: include only forms witnessed on this source.
            strings.extend(form for form in obj.data.forms if any(form in q for q in quotes))
        elif isinstance(obj.data, LexicalData):
            strings.extend(content_strings(obj.data.notation.model_dump(mode="json")))
            if ref in obj.data.source_text.sources:
                strings.extend(
                    [obj.data.meaning or "", obj.data.jlpt or "", obj.data.part_of_speech or ""]
                )
            for variant in obj.data.variants:
                if ref in variant.sources:
                    strings.extend(content_strings(variant.model_dump(mode="json")))
        else:
            strings.extend(content_strings(obj.data.model_dump(mode="json")))
        _search_entry(
            connection,
            obj.id,
            obj.kind,
            lesson,
            obj.category,
            obj.title,
            "\n".join(strings),
            "\n".join(quotes),
            ref,
        )


def _insert_lesson(connection, document, curriculum, position):
    manifest = document.manifest
    lesson = curriculum.lesson.id
    connection.execute(
        "INSERT INTO documents VALUES (?,?,?)",
        (manifest.document_id, dump(manifest), dump(document.metadata)),
    )
    connection.execute(
        "INSERT INTO lessons VALUES (?,?,?,?,?)",
        (lesson, manifest.document_id, curriculum.book.id, curriculum.book.title, position),
    )
    for index, section in enumerate(document.sections):
        connection.execute(
            "INSERT INTO sections VALUES (?,?,?,?,?,?)",
            (section.section_id, lesson, index, section.section_type, section.title, dump(section)),
        )
        connection.executemany(
            "INSERT INTO section_pages VALUES (?,?,?)",
            [(section.section_id, p.document_id, p.page) for p in section.pages],
        )
        for text in section_texts(section):
            body = "\n".join(content_strings(text.model_dump(mode="json")))
            for ref in text.sources:
                _search_entry(
                    connection,
                    section.section_id,
                    "source",
                    lesson,
                    None,
                    section.title,
                    body,
                    text.text,
                    ref,
                )
    # Register all concepts before inserting exercise targets and relations.
    for obj in curriculum.objects:
        connection.execute(
            "INSERT INTO objects VALUES (?,?,?,?,?,?,?)",
            (
                obj.id,
                lesson,
                obj.kind,
                obj.provenance,
                obj.confidence,
                obj.status,
                dump([s.model_dump(mode="json") for s in obj.supports]),
            ),
        )
        if isinstance(obj, Concept):
            lexical = obj.data if isinstance(obj.data, LexicalData) else None
            connection.execute(
                "INSERT INTO concepts VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    obj.id,
                    lesson,
                    obj.category,
                    obj.subtype,
                    obj.title,
                    obj.summary,
                    lexical.notation.surface if lexical else None,
                    lexical.notation.reading if lexical else None,
                    lexical.meaning if lexical else None,
                    dump(obj.data),
                ),
            )
            refs = {
                (s.source.document_id, s.source.page, s.source.section_id) for s in obj.supports
            }
            connection.executemany(
                "INSERT INTO concept_sources VALUES (?,?,?,?)",
                [(obj.id, *ref) for ref in sorted(refs)],
            )
    for obj in curriculum.objects:
        if isinstance(obj, Concept):
            _concept_entries(connection, obj, lesson)
        elif isinstance(obj, Dialogue):
            connection.execute(
                "INSERT INTO dialogues VALUES (?,?,?)", (obj.id, obj.title, obj.setting)
            )
            connection.executemany(
                "INSERT INTO utterances VALUES (?,?,?,?,?)",
                [
                    (obj.id, i, u.speaker, dump(u.text), u.stage_direction)
                    for i, u in enumerate(obj.utterances)
                ],
            )
            for support in obj.supports:
                body = "\n".join(
                    u.text.text for u in obj.utterances if support.source in u.text.sources
                )
                _search_entry(
                    connection,
                    obj.id,
                    obj.kind,
                    lesson,
                    None,
                    obj.title,
                    body,
                    support.quote,
                    support.source,
                )
        elif isinstance(obj, ExerciseExample):
            connection.execute(
                "INSERT INTO exercise_examples VALUES (?,?,?,?,?)",
                (
                    obj.id,
                    obj.title,
                    obj.task_type,
                    obj.progression,
                    dump([t.model_dump(mode="json") for t in obj.source_text]),
                ),
            )
            connection.executemany(
                "INSERT INTO exercise_targets VALUES (?,?,?)",
                [(obj.id, target, i) for i, target in enumerate(obj.target_concept_ids)],
            )
            for support in obj.supports:
                _search_entry(
                    connection,
                    obj.id,
                    obj.kind,
                    lesson,
                    None,
                    obj.title,
                    "\n".join(t.text for t in obj.source_text if support.source in t.sources),
                    support.quote,
                    support.source,
                )
        elif isinstance(obj, ConceptRelation):
            connection.execute(
                "INSERT INTO concept_relations VALUES (?,?,?,?)",
                (obj.id, obj.source_id, obj.target_id, obj.relation_type),
            )
        else:
            raise ValueError(f"Unsupported database object kind: {obj.kind}")


def build_curriculum_database(
    inputs_path: Path, destination: Path, sql_path: Path = DEFAULT_SQL
) -> dict:
    """Manifest paths are relative to the manifest; approval binds exact model hashes."""
    inputs = BuildInputs.model_validate_json(inputs_path.read_text(encoding="utf-8"))
    bundles = []
    provenance = []
    identifiers = set()
    input_paths = {inputs_path.resolve(), sql_path.resolve()}
    for item in inputs.lessons:
        paths = {
            name: (inputs_path.parent / value).resolve()
            for name, value in item.model_dump().items()
        }
        input_paths.update(paths.values())
        document = load_document(paths["document"])
        curriculum = SemanticCurriculum.model_validate_json(
            paths["curriculum"].read_text(encoding="utf-8")
        )
        verification = VerificationSubmission.model_validate_json(
            paths["verification"].read_text(encoding="utf-8")
        )
        approval = CurriculumApproval.model_validate_json(
            paths["approval"].read_text(encoding="utf-8")
        )
        report = build_report(document, curriculum, verification)
        if report["status"] != "automatically_verified":
            raise ValueError(f"Curriculum has blocking review entries: {curriculum.lesson.id}")
        if (
            approval.document_sha256 != document.manifest.document_sha256
            or approval.source_artifact_sha256 != artifact_hash(document)
            or approval.curriculum_sha256 != artifact_hash(curriculum)
        ):
            raise ValueError("Stale human approval: artifact hashes do not match")
        if curriculum.lesson.id in identifiers:
            raise ValueError("Duplicate lesson in build inputs")
        identifiers.add(curriculum.lesson.id)
        bundles.append((document, curriculum))
        provenance.append(
            {
                "lesson_id": curriculum.lesson.id,
                "semantic_metadata": curriculum.metadata.model_dump(mode="json"),
                "verification_sha256": artifact_hash(verification),
                "approval": approval.model_dump(mode="json"),
            }
        )
    if destination.resolve() in input_paths:
        raise ValueError("Database destination would overwrite a build input")
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(dir=destination.parent, suffix=".db.tmp")
    os.close(descriptor)
    connection = None
    try:
        with sqlite3.connect(temporary) as connection:
            connection.executescript(sql_path.read_text(encoding="utf-8"))
            connection.execute(
                "INSERT INTO metadata VALUES (?,?)", ("database_version", DATABASE_VERSION)
            )
            connection.execute(
                "INSERT INTO metadata VALUES (?,?)", ("build_inputs", dump(provenance))
            )
            for index, (document, curriculum) in enumerate(bundles):
                _insert_lesson(connection, document, curriculum, index)
            if connection.execute("PRAGMA foreign_key_check").fetchall():
                raise ValueError("Curriculum foreign key check failed")
            if connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise ValueError("Curriculum integrity check failed")
            connection.execute("INSERT INTO textbook_fts(textbook_fts) VALUES ('integrity-check')")
            counts = {
                table: connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
                for table in (
                    "lessons",
                    "sections",
                    "concepts",
                    "dialogues",
                    "exercise_examples",
                    "concept_relations",
                    "search_entries",
                )
            }
        # sqlite's context manager commits but does not close the Windows file handle.
        connection.close()
        if any(Path(str(destination) + suffix).exists() for suffix in ("-wal", "-shm", "-journal")):
            raise ValueError(
                "Existing curriculum has active journal files; close readers/writers "
                "and checkpoint before rebuilding"
            )
        os.replace(temporary, destination)
        return {"database": str(destination.resolve()), "counts": counts}
    finally:
        if connection is not None:
            connection.close()
        if os.path.exists(temporary):
            os.unlink(temporary)
