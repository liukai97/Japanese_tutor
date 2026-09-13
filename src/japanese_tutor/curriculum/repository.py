"""Bounded read-only curriculum services. No arbitrary SQL or learner writes."""

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from japanese_tutor.curriculum.build_db import DATABASE_VERSION, normalize
from japanese_tutor.schemas.curriculum import (
    Concept,
    ConceptRelation,
    Dialogue,
    ExerciseExample,
)
from japanese_tutor.schemas.retrieval import SearchQuery
from japanese_tutor.schemas.source import Section

RELATION_TYPES = set(ConceptRelation.model_fields["relation_type"].annotation.__args__)
TASK_TYPES = set(ExerciseExample.model_fields["task_type"].annotation.__args__)


def _ids(values: list[str]) -> list[str]:
    if (
        not isinstance(values, list)
        or not 1 <= len(values) <= 100
        or any(not isinstance(v, str) or not v for v in values)
    ):
        raise ValueError("Provide between 1 and 100 nonempty IDs")
    return list(dict.fromkeys(values))


def _bounds(limit, offset):
    if (
        type(limit) is not int
        or not 1 <= limit <= 100
        or type(offset) is not int
        or not 0 <= offset <= 10000
    ):
        raise ValueError("limit must be 1..100 and offset must be 0..10000")


def _short(text: str, query: str = "", size: int = 180) -> str:
    # Indexing normalizes strings, but evidence snippets always retain source spelling.
    position = text.find(query) if query else -1
    start = max(0, position - size // 3) if position >= 0 else 0
    end = start + size
    return ("…" if start else "") + text[start:end] + ("…" if end < len(text) else "")


class CurriculumRepository:
    def __init__(self, database: Path):
        self.database = Path(database).resolve()

    @contextmanager
    def _connect(self):
        connection = None
        try:
            connection = sqlite3.connect(self.database.as_uri() + "?mode=ro", uri=True)
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA query_only = ON")
            version = connection.execute(
                "SELECT value FROM metadata WHERE key='database_version'"
            ).fetchone()
            if version is None or version[0] != DATABASE_VERSION:
                raise ValueError("Unsupported curriculum database; rebuild from approved artifacts")
            yield connection
        except sqlite3.Error as error:
            raise ValueError(f"Curriculum database unavailable or invalid: {error}") from error
        finally:
            if connection is not None:
                connection.close()

    def _object(self, connection, identifier):
        row = connection.execute("SELECT * FROM objects WHERE id=?", (identifier,)).fetchone()
        if row is None:
            raise ValueError(f"Unknown curriculum object: {identifier}")
        value = dict(row)
        value["supports"] = json.loads(value.pop("supports_json"))
        kind = value["kind"]
        if kind == "concept":
            payload = dict(
                connection.execute("SELECT * FROM concepts WHERE id=?", (identifier,)).fetchone()
            )
            for field in ("surface", "reading", "meaning"):
                payload.pop(field)
            payload["data"] = json.loads(payload.pop("data_json"))
            value.update(payload)
            model = Concept
        elif kind == "relation":
            value.update(
                dict(
                    connection.execute(
                        "SELECT * FROM concept_relations WHERE id=?", (identifier,)
                    ).fetchone()
                )
            )
            model = ConceptRelation
        elif kind == "dialogue":
            value.update(
                dict(
                    connection.execute(
                        "SELECT * FROM dialogues WHERE id=?", (identifier,)
                    ).fetchone()
                )
            )
            value["utterances"] = [
                {
                    "speaker": u["speaker"],
                    "text": json.loads(u["text_json"]),
                    "stage_direction": u["stage_direction"],
                }
                for u in connection.execute(
                    "SELECT * FROM utterances WHERE dialogue_id=? ORDER BY position", (identifier,)
                )
            ]
            model = Dialogue
        elif kind == "exercise_example":
            payload = dict(
                connection.execute(
                    "SELECT * FROM exercise_examples WHERE id=?", (identifier,)
                ).fetchone()
            )
            payload["source_text"] = json.loads(payload.pop("source_text_json"))
            value.update(payload)
            value["target_concept_ids"] = [
                r[0]
                for r in connection.execute(
                    "SELECT concept_id FROM exercise_targets WHERE exercise_id=? ORDER BY position",
                    (identifier,),
                )
            ]
            model = ExerciseExample
        else:
            raise ValueError(f"Unsupported curriculum object: {kind}")
        return model.model_validate_json(json.dumps(value, ensure_ascii=False)).model_dump(
            mode="json"
        )

    def get_concepts(self, concept_ids: list[str]) -> list[dict]:
        with self._connect() as connection:
            result = [self._object(connection, identifier) for identifier in _ids(concept_ids)]
            if any(obj["kind"] != "concept" for obj in result):
                raise ValueError("Requested ID is not a concept")
            return result

    def get_concept_sources(self, concept_ids: list[str]) -> list[dict]:
        # Supports carry short literal anchors; the original rich source is fetched separately.
        with self._connect() as connection:
            result = []
            for identifier in _ids(concept_ids):
                row = connection.execute(
                    "SELECT o.supports_json FROM objects o JOIN concepts c ON c.id=o.id "
                    "WHERE c.id=?",
                    (identifier,),
                ).fetchone()
                if row is None:
                    raise ValueError(f"Unknown concept: {identifier}")
                result.append({"concept_id": identifier, "supports": json.loads(row[0])})
            return result

    def get_source(self, document_id: str, section_id: str, page: int | None = None) -> dict:
        if page is not None and (type(page) is not int or page < 1):
            raise ValueError("Page must be a positive integer")
        with self._connect() as connection:
            row = connection.execute(
                "SELECT s.section_json FROM sections s JOIN lessons l ON l.id=s.lesson_id "
                "WHERE s.id=? AND l.document_id=?",
                (section_id, document_id),
            ).fetchone()
            if row is None:
                raise ValueError("Unknown document/section")
            section = Section.model_validate_json(row[0]).model_dump(mode="json")
            if page is not None:
                if page not in {p["page"] for p in section["pages"]}:
                    raise ValueError("Page is outside the requested section")
                # Keep full tables on the requested page to preserve row/cell alignment.
                section["blocks"] = [
                    b
                    for b in section["blocks"]
                    if any(
                        ref["page"] == page
                        for ref in (
                            b["content"]["sources"] if b["kind"] == "text" else b["sources"]
                        )
                    )
                ]
            return section

    def get_lesson_outline(self, lesson_id: str) -> dict:
        with self._connect() as connection:
            lesson = connection.execute("SELECT * FROM lessons WHERE id=?", (lesson_id,)).fetchone()
            if lesson is None:
                raise ValueError(f"Unknown lesson: {lesson_id}")
            sections = []
            for row in connection.execute(
                "SELECT id,type,title FROM sections WHERE lesson_id=? ORDER BY position",
                (lesson_id,),
            ):
                item = dict(row)
                item["pages"] = [
                    p[0]
                    for p in connection.execute(
                        "SELECT page FROM section_pages WHERE section_id=? ORDER BY page",
                        (row["id"],),
                    )
                ]
                item["concept_ids"] = [
                    r[0]
                    for r in connection.execute(
                        "SELECT DISTINCT concept_id FROM concept_sources WHERE section_id=? "
                        "ORDER BY concept_id",
                        (row["id"],),
                    )
                ]
                sections.append(item)
            return {
                "lesson_id": lesson_id,
                "document_id": lesson["document_id"],
                "book_id": lesson["book_id"],
                "sections": sections,
            }

    def get_related_concepts(
        self,
        concept_ids: list[str],
        relation_types: list[str] | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> list[dict]:
        identifiers = _ids(concept_ids)
        _bounds(limit, offset)
        self.get_concepts(identifiers)
        if relation_types is not None and not set(relation_types) <= RELATION_TYPES:
            raise ValueError("Unknown relation type")
        if relation_types == []:
            return []
        marks = ",".join("?" for _ in identifiers)
        where = f"(source_id IN ({marks}) OR target_id IN ({marks}))"
        params = identifiers * 2
        if relation_types is not None:
            where += " AND relation_type IN (" + ",".join("?" for _ in relation_types) + ")"
            params += relation_types
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT id FROM concept_relations WHERE " + where + " ORDER BY id LIMIT ? OFFSET ?",
                [*params, limit, offset],
            ).fetchall()
            # Direction remains source_id -> target_id even when found by an incoming edge.
            return [self._object(connection, row[0]) for row in rows]

    def get_exercise_examples(
        self, lesson_id: str, task_types: list[str] | None = None, limit: int = 20, offset: int = 0
    ) -> list[dict]:
        _bounds(limit, offset)
        if task_types is not None and not set(task_types) <= TASK_TYPES:
            raise ValueError("Unknown exercise task type")
        with self._connect() as connection:
            if (
                connection.execute("SELECT 1 FROM lessons WHERE id=?", (lesson_id,)).fetchone()
                is None
            ):
                raise ValueError(f"Unknown lesson: {lesson_id}")
            where = "o.lesson_id=?"
            params = [lesson_id]
            if task_types is not None:
                if not task_types:
                    return []
                where += " AND e.task_type IN (" + ",".join("?" for _ in task_types) + ")"
                params += task_types
            rows = connection.execute(
                "SELECT e.id FROM exercise_examples e JOIN objects o ON o.id=e.id WHERE "
                + where
                + " ORDER BY e.id LIMIT ? OFFSET ?",
                [*params, limit, offset],
            ).fetchall()
            return [self._object(connection, row[0]) for row in rows]

    def search_textbook(
        self,
        query: str,
        learned_only: bool = False,
        lesson_range: list[str] | None = None,
        concept_types: list[str] | None = None,
        limit: int = 10,
        *,
        allowed_lesson_ids: list[str] | None = None,
        source_page: int | None = None,
        offset: int = 0,
    ) -> list[dict]:
        request = SearchQuery(
            query=query,
            learned_only=learned_only,
            lesson_range=lesson_range,
            concept_types=concept_types,
            limit=limit,
            offset=offset,
            allowed_lesson_ids=allowed_lesson_ids,
            source_page=source_page,
        )
        terms = list(dict.fromkeys(normalize(request.query).split()))
        long_terms = [term for term in terms if len(term) >= 3]
        short_terms = [term for term in terms if len(term) < 3]
        where = []
        params = []
        join = ""
        score = "0.0"
        if long_terms:
            join = " JOIN textbook_fts ON textbook_fts.rowid=e.id"
            where.append("textbook_fts MATCH ?")
            # Always a literal phrase, never caller-provided FTS syntax.
            params.append(" AND ".join('"' + t.replace('"', '""') + '"' for t in long_terms))
            score = "bm25(textbook_fts)"
        for term in short_terms:
            where.append("(instr(normalize(e.title),?)>0 OR instr(normalize(e.body),?)>0)")
            params += [term, term]
        for field, values in (
            ("lesson_id", request.lesson_range),
            ("lesson_id", request.allowed_lesson_ids if request.learned_only else None),
            ("concept_type", request.concept_types),
        ):
            if values is not None:
                if not values:
                    return []
                where.append(f"e.{field} IN (" + ",".join("?" for _ in values) + ")")
                params += values
        if request.source_page is not None:
            where.append("e.page=?")
            params.append(request.source_page)
        with self._connect() as connection:
            connection.create_function("normalize", 1, normalize, deterministic=True)
            # Deduplicate objects before pagination. Source passages use section + page.
            sql = (
                "WITH hits AS MATERIALIZED (SELECT e.*, " + score + " AS score "
                "FROM search_entries e" + join + " WHERE " + " AND ".join(where) + "), "
                "ranked AS (SELECT *, row_number() OVER (PARTITION BY object_id,kind,"
                "CASE WHEN kind='source' THEN page ELSE 0 END ORDER BY score,id) AS n FROM hits) "
                "SELECT * FROM ranked WHERE n=1 ORDER BY score,object_id,page LIMIT ? OFFSET ?"
            )
            rows = connection.execute(sql, [*params, request.limit, request.offset]).fetchall()
            return [
                {
                    "id": r["object_id"],
                    "kind": r["kind"],
                    "lesson_id": r["lesson_id"],
                    "concept_type": r["concept_type"],
                    "title": r["title"],
                    "snippet": _short(r["body"], request.query),
                    "source_snippet": _short(r["source_quote"], request.query),
                    "source": {
                        "document_id": r["document_id"],
                        "page": r["page"],
                        "section_id": r["section_id"],
                    },
                }
                for r in rows
            ]
